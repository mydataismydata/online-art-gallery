"""Background download jobs. One job = one artist from one source.
Jobs each get a thread, but a global semaphore serializes actual work so we
never hammer a site (or the disk) from several jobs at once."""
import itertools
import threading
import time
import traceback

_jobs = []
_jobs_lock = threading.Lock()
_run_slot = threading.Semaphore(1)
_ids = itertools.count(1)


class Job:
    def __init__(self, source, query, opts):
        self.id = next(_ids)
        self.source = source
        self.query = query
        self.opts = opts or {}
        self.status = "queued"  # queued | running | done | error | cancelled
        self.message = ""
        self.found = 0     # works matched at the source
        self.saved = 0
        self.skipped = 0   # already in library
        self.failed = 0
        self.log_lines = []
        self.created = time.time()
        self.finished = None
        self._cancel = threading.Event()

    def log(self, msg):
        line = "%s  %s" % (time.strftime("%H:%M:%S"), msg)
        with _jobs_lock:
            self.log_lines.append(line)
            if len(self.log_lines) > 500:
                del self.log_lines[: len(self.log_lines) - 500]
        try:
            print("[job %d] %s" % (self.id, msg), flush=True)
        except Exception:
            pass  # console encoding quirks must never kill a job

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self):
        return self._cancel.is_set()

    def to_dict(self, tail=12):
        with _jobs_lock:
            log_tail = self.log_lines[-tail:] if tail else []
        return {
            "id": self.id,
            "source": self.source,
            "query": self.query,
            "opts": self.opts,
            "status": self.status,
            "message": self.message,
            "found": self.found,
            "saved": self.saved,
            "skipped": self.skipped,
            "failed": self.failed,
            "log": log_tail,
            "created": self.created,
            "finished": self.finished,
        }


def jobs():
    with _jobs_lock:
        return list(reversed(_jobs))


def get(jid):
    with _jobs_lock:
        for j in _jobs:
            if j.id == jid:
                return j
    return None


def start(source_id, query, opts=None):
    from .sources import get_source

    module = get_source(source_id)  # raises KeyError for unknown source
    job = Job(source_id, query, opts)
    with _jobs_lock:
        _jobs.append(job)
    t = threading.Thread(target=_run, args=(job, module), daemon=True)
    t.start()
    return job


def _run(job, module):
    with _run_slot:
        if job.cancelled:
            job.status = "cancelled"
            job.finished = time.time()
            return
        job.status = "running"
        job.log("Starting: %s — \"%s\"" % (module.LABEL, job.query))
        try:
            module.run(job)
            job.status = "cancelled" if job.cancelled else "done"
            job.log("Finished: %d saved, %d already had, %d failed."
                    % (job.saved, job.skipped, job.failed))
        except Exception as e:
            job.status = "error"
            job.message = str(e)
            job.log("ERROR: %s" % e)
            job.log(traceback.format_exc(limit=4))
        job.finished = time.time()
        from .. import library
        library.invalidate()
