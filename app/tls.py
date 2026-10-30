"""Persistent local CA and renewable HTTPS certificate; never serve plain HTTP."""
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
from storage import DATA_DIR

LOG = logging.getLogger('bitcoin-dca.tls')


def certificate_hosts(value):
    hosts = ['localhost', '127.0.0.1', '::1']
    for name in value.split(','):
        name = name.strip().lower()
        if not name: continue
        try: name = str(ipaddress.ip_address(name))
        except ValueError:
            if len(name) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in name.split('.')):
                raise ValueError('WEB_UI_TLS_HOSTS must contain IP addresses or hostnames, separated by commas.') from None
        if name not in hosts: hosts.append(name)
    return hosts


def _openssl(*args):
    return subprocess.run(['openssl', *map(str, args)], check=True, capture_output=True, timeout=30)


def https_context(hosts, directory=None):
    directory = Path(directory) if directory is not None else DATA_DIR / 'tls'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    ca, ca_key = directory/'ca.pem', directory/'ca-key.pem'
    cert, key, metadata = directory/'server.pem', directory/'server-key.pem', directory/'hosts.json'
    if ca.exists() != ca_key.exists():
        raise RuntimeError('Incomplete HTTPS CA files. Restore the TLS directory from the volume backup.')
    with tempfile.TemporaryDirectory(prefix='.tls-', dir=directory) as staging:
        stage = Path(staging)
        if not ca.exists():
            _openssl('req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-sha256', '-days', '3650',
                     '-subj', '/CN=Bitcoin DCA Dashboard Local CA', '-addext', 'basicConstraints=critical,CA:TRUE',
                     '-addext', 'keyUsage=critical,keyCertSign,cRLSign', '-keyout', stage/'ca-key.pem', '-out', stage/'ca.pem')
            for name in ('ca-key.pem','ca.pem'):
                os.chmod(stage/name, 0o600);os.replace(stage/name, directory/name)
            LOG.info('Local HTTPS certificate authority created in the persistent volume')
        try: reusable = cert.exists() and key.exists() and metadata.exists() and json.loads(metadata.read_text()) == hosts
        except (ValueError,OSError): reusable = False
        if reusable:
            reusable = subprocess.run(['openssl','x509','-in',str(cert),'-checkend','2592000','-noout'],capture_output=True,timeout=10).returncode == 0
        if reusable:
            try: ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert,key)
            except ssl.SSLError: reusable = False
        if not reusable:
            names=[]
            for host in hosts:
                try: ipaddress.ip_address(host);kind='IP'
                except ValueError: kind='DNS'
                names.append(kind+':'+host)
            extensions = stage/'extensions.cnf'
            extensions.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName='+','.join(names)+'\n')
            _openssl('req','-new','-newkey','rsa:2048','-nodes','-sha256','-subj','/CN=Bitcoin DCA Dashboard',
                     '-keyout',stage/'server-key.pem','-out',stage/'server.csr')
            _openssl('x509','-req','-in',stage/'server.csr','-CA',ca,'-CAkey',ca_key,
                     '-set_serial',str(int.from_bytes(os.urandom(16),'big')),'-days','365','-sha256',
                     '-extfile',extensions,'-out',stage/'server.pem')
            for name in ('server-key.pem','server.pem'):
                os.chmod(stage/name,0o600);os.replace(stage/name,directory/name)
            (stage/'hosts.json').write_text(json.dumps(hosts));os.chmod(stage/'hosts.json',0o600);os.replace(stage/'hosts.json',metadata)
            LOG.info('HTTPS certificate prepared for configured local addresses; import tls/ca.pem into trusted certificate authorities')
    for path in (ca,ca_key,cert,key,metadata):os.chmod(path,0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert,key)
    return context


def renew_certificates(handler, hosts, stop):
    """Refresh certificates daily without changing active TLS connections."""
    while not stop.wait(86400):
        try: handler.tls_context = https_context(hosts)
        except Exception:
            LOG.error('HTTPS certificate renewal failed; keeping the current certificate and retrying in 24 hours')
