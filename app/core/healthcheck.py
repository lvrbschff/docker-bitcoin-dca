"""Docker health check without exchange calls or API credentials."""
import json
import sys
import time
from app.data.storage import HEALTH_PATH

def healthy(path=HEALTH_PATH):
    try:
        state=json.loads(path.read_text())
        age=time.time()-float(state['heartbeat'])
        return bool(state.get('running')) and 0<=age<900
    except (OSError,ValueError,KeyError,TypeError): return False

if __name__=='__main__':sys.exit(0 if healthy() else 1)
