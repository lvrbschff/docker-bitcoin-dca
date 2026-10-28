"""Single account worker: queued web actions, periodic checks and bounded health age."""
import logging
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from activity import write_json_atomic

LOG=logging.getLogger('bitcoin-dca.worker')
HEALTH_PATH=Path('/data/worker-health.json')


class Worker:
    def __init__(self, cycle, action, startup, interval, startup_delay, on_error=None):
        self.on_error=on_error
        self.last_error=None
        self.last_success=None
        self.cycle=cycle
        self.action=action
        self.startup=startup
        self.interval=interval
        self.startup_delay=startup_delay
        self.queue=queue.Queue(maxsize=16)
        self.jobs={}
        self.lock=threading.RLock()
        self.stop=threading.Event()
        self.thread=None
        self.beat=time.monotonic()
        self.running=False
        self._last_health=0

    def submit(self, action, session, token=None):
        job_id=str(uuid.uuid4())
        with self.lock:
            if not self.running: raise ValueError('Bot worker is unavailable; check container health.')
            self.jobs={k:v for k,v in self.jobs.items() if v['expires']>time.monotonic() or v['state'] in ('queued','running')}
            if len(self.jobs)>=128: raise ValueError('Too many queued or recent requests; try again later.')
            job={'session':session,'state':'queued','expires':time.monotonic()+900}
            self.jobs[job_id]=job
            try:self.queue.put_nowait((job_id,action,session,token))
            except queue.Full:
                self.jobs.pop(job_id)
                raise ValueError('The account worker is busy. Try again shortly.')
        return {'jobId':job_id,'state':'queued'}

    def get_job(self, job_id, session):
        with self.lock:
            job=self.jobs.get(job_id)
            if not job or job['session']!=session or job['expires']<time.monotonic():
                raise ValueError('Request not found or expired. Check exchange status before starting again.')
            return {k:v for k,v in job.items() if k not in ('session','expires')}

    def healthy(self):
        return self.running and time.monotonic()-self.beat<600

    def heartbeat(self):
        self.beat=time.monotonic()
        if self.beat-self._last_health>=5:
            write_json_atomic(HEALTH_PATH,{'heartbeat':time.time(),'running':self.running,'lastError':self.last_error,'lastSuccessfulCheck':self.last_success})
            self._last_health=self.beat

    def start(self):
        self.running=True
        self.thread=threading.Thread(target=self.run, name='bitcoin-account-worker',daemon=True)
        self.thread.start()

    def run(self):
        self.running=True
        due=time.monotonic()+self.startup_delay
        started=False
        try:
            while not self.stop.is_set():
                self.heartbeat()
                try: job_id,action,session,token=self.queue.get(timeout=min(1,max(0,due-time.monotonic())))
                except queue.Empty:
                    if time.monotonic()<due: continue
                    try:
                        if not started:
                            self.startup()
                            started=True
                        wait=self.cycle()
                        self.last_error=None
                        self.last_success=time.time()
                        due=time.monotonic()+min(self.interval,max(1,int(wait or self.interval)))
                    except Exception as exc:
                        self.last_error=str(exc)
                        LOG.exception('Account check failed; worker will retry.')
                        if self.on_error:
                            try:self.on_error()
                            except Exception:LOG.exception('Could not update account-error status.')
                        due=time.monotonic()+self.interval
                    continue
                with self.lock:self.jobs[job_id]['state']='running'
                try:
                    result=self.action(action,session,token)
                    with self.lock:self.jobs[job_id].update(state='completed',result=result)
                    due=min(due,time.monotonic()+1)
                except Exception as exc:
                    LOG.warning('Manual account request failed: %s',exc)
                    with self.lock:self.jobs[job_id].update(state='failed',error=str(exc))
                finally:
                    self.queue.task_done()
                    self.heartbeat()
        finally:
            self.running=False
            write_json_atomic(HEALTH_PATH,{'heartbeat':time.time(),'running':False})


def supervise(worker):
    while not worker.stop.wait(10):
        if not worker.healthy():
            LOG.critical('Account worker stopped or stalled; exiting for Docker restart.')
            os._exit(1)
