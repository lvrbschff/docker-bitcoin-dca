"""Volume-only runtime paths and atomic initialization of new storage."""
import json
import logging
import os
from pathlib import Path
import tempfile

LOG = logging.getLogger('bitcoin-dca.storage')
DATA_DIR = Path('/webui-data')
STATE_PATH = DATA_DIR / 'state.json'
HEALTH_PATH = DATA_DIR / 'worker-health.json'


def _read(path):
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
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


def prepare_storage(data_dir=None):
    """Validate existing volume state or initialize an empty first-run volume."""
    destination = Path(data_dir) if data_dir is not None else DATA_DIR
    destination.mkdir(parents=True, exist_ok=True)
    state = destination / 'state.json'
    if state.exists():
        _read(state)
        return
    configuration = destination / 'config.json'
    saved = _read(configuration) if configuration.exists() else {}
    has_history = (destination / 'activity.sqlite3').exists() or (destination / 'activity.json').exists()
    if saved.get('complete') or has_history:
        raise RuntimeError('Persistent scheduling state is missing. Restore the volume backup before starting the bot.')
    _write(state, {})
    LOG.info('Persistent storage initialized in the named web UI volume')
