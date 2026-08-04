"""A BackgroundScheduler whose thread cannot be killed by a failing pass.

APScheduler runs its main loop as ``wait_seconds = self._process_jobs()`` in a
bare thread (``BlockingScheduler._main_loop``, which BackgroundScheduler
inherits). Nothing in that loop catches exceptions, and ``_process_jobs`` writes
to the jobstore — ``jobstore.update_job(job)`` — to stamp each fired job's next
run time. A persistent SQLAlchemy jobstore therefore turns any database error
into a dead scheduler thread: every scheduled job stops for the life of the
process, with no retry and no log entry beyond the traceback.

That is not hypothetical. Three jobstore tables live in ``db/openalgo.db``, the
same file the master-contract download rewrites (~200k ``symtoken`` rows in one
transaction, measured at 124 seconds). A scheduler firing inside that window
waits out ``PRAGMA busy_timeout`` and then gets ``OperationalError: database is
locked``, and the alert engine goes quiet until someone restarts the server.

Failing a pass is recoverable; losing the thread is not. Subclasses of this
scheduler log the failure and retry shortly after, so a transient lock costs one
delayed sweep instead of every future one.
"""

from apscheduler.schedulers.background import BackgroundScheduler

from utils.logging import get_logger

logger = get_logger(__name__)

# How long to wait before the next pass after one fails. A failed pass could not
# stamp next_run_time, so the job stays due and the loop would otherwise spin at
# full speed against the locked database for the whole write. Long enough to be
# a real pause, short enough that jobs resume promptly once the lock clears.
SCHEDULER_RETRY_SECONDS = 5.0


class ResilientBackgroundScheduler(BackgroundScheduler):
    """BackgroundScheduler that survives an unwritable jobstore.

    Behaves exactly like ``BackgroundScheduler`` while healthy: the wait
    returned by a successful pass is passed through untouched.
    """

    def _process_jobs(self):
        """Run one scheduler pass, converting a crash into a retry.

        Returns:
            Seconds the main loop should wait before the next pass.
        """
        try:
            return super()._process_jobs()
        except Exception:
            # Deliberately broad: whatever went wrong, ending the thread is
            # worse than retrying. The traceback goes to log/errors.jsonl.
            logger.exception(
                f"Scheduler pass failed; retrying in {SCHEDULER_RETRY_SECONDS}s. "
                "Jobs are not lost, but any job due in this pass is delayed."
            )
            return SCHEDULER_RETRY_SECONDS
