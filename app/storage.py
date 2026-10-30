"""Persistent application storage and a conservative one-time legacy migration."""
import argparse
import json
import logging
import os
from pathlib import Path
import tempfile

LOG = logging.getLogger('bitcoin-dca.storage')
DATA_DIR = Path('/webui-data')
LEGACY_DIR = Path('/data')
STATE_PATH = DATA_DIR / 'state.json'
HEALTH_PATH = DATA_DIR / 'worker-health.json'
MARKER_NAME = 'storage-layout.json'
LEGACY_FILES = ('state.json', 'config.json', 'activity.json', 'dashboard.json', 'previous-account-state.json')


def _read(path):
    data = json.loads(path.read_text())
    if not isinstance(data, (dict, list)) or (path.name != 'activity.json' and not isinstance(data, dict)):
        raise ValueError('Invalid persistent file: ' + path.name)
    return data


def _write(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix='.storage-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def prepare_storage(data_dir=None, legacy_dir=None):
    """Finish migration before workers start; never overwrite existing volume data."""
    destination = Path(data_dir) if data_dir is not None else DATA_DIR
    source = Path(legacy_dir) if legacy_dir is not None else LEGACY_DIR
    destination.mkdir(parents=True, exist_ok=True)
    marker = destination / MARKER_NAME
    state = destination / 'state.json'
    configuration = destination / 'config.json'
    if not marker.exists():
        # Validate all candidates before writing any files. A missing source state
        # must not turn an existing account into a fresh account with no pending orders.
        candidates = {name: _read(source / name) for name in LEGACY_FILES
                      if (source / name).exists() and (name == 'state.json' or not (destination / name).exists())}
        for name in ('state.json', 'config.json'):
            if (destination / name).exists(): _read(destination / name)
        if state.exists() and 'state.json' in candidates and _read(state) != candidates['state.json']:
            raise RuntimeError('Conflicting scheduling state in the volume and legacy directory. Resolve the conflict before starting the bot.')
        saved = _read(configuration) if configuration.exists() else candidates.get('config.json', {})
        has_history = (destination / 'activity.sqlite3').exists() or (destination / 'activity.json').exists()
        if not state.exists() and 'state.json' not in candidates and (saved.get('complete') or has_history):
            raise RuntimeError('Existing account data found without scheduling state. Migrate the old state directory with storage.py --from before removing the old mount.')
        for name, data in candidates.items():
            target = destination / name
            if not target.exists():
                _write(target, data)
                LOG.info('Legacy persistent file migrated to the named volume: %s', name)
        if not state.exists(): _write(state, {})
        _write(marker, {'version': 1})
        LOG.info('Persistent storage ready: all runtime data uses the named web UI volume')
    else:
        if _read(marker).get('version') != 1:
            raise RuntimeError('Unsupported persistent storage layout. Restore a compatible volume backup.')
        if not state.exists():
            raise RuntimeError('Persistent scheduling state is missing. Restore the volume backup before starting the bot.')
        _read(state)


def main():
    parser = argparse.ArgumentParser(description='Migrate stopped bot state into its named volume.')
    parser.add_argument('--from', dest='source', required=True, help='Legacy state directory mounted read-only')
    parser.add_argument('--to', dest='destination', default=str(DATA_DIR))
    args = parser.parse_args()
    if not Path(args.source).is_dir(): parser.error('Legacy source directory is unavailable.')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    prepare_storage(args.destination, args.source)


if __name__ == '__main__': main()
