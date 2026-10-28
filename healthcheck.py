"""Docker health check without exchange calls or API credentials."""
import json
import sys
import time
from pathlib import Path

def healthy(path=Path('/data/worker-health.json')):
    try:
        state=json.loads(path.read_text())
        age=time.time()-float(state['heartbeat'])
        return bool(state.get('running')) and 0<=age<600
    except (OSError,ValueError,KeyError,TypeError): return False

if __name__=='__main__':sys.exit(0 if healthy() else 1)
