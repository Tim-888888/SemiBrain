import os

from celery import Celery, Task

from semibrain_common.runtime import redis, uid

POLL_LIFETIME = 30
RELEASE_POLL = """if redis.call('get', KEYS[1]) == ARGV[1] then
return redis.call('del', KEYS[1]) else return 0 end"""


class PollTask(Task):
    """Coalesce disposable wakeups; durable jobs and events remain in MongoDB.

    Release the pending marker on start so other worker slots can make progress.
    Expiry recovers a lost publisher or broker message; ownership prevents a late
    delivery from releasing its replacement's marker. Never use for payload tasks.
    """

    abstract = True

    @property
    def pending_key(self):
        return f"celery_{self.app.main}:poll:{self.name}"

    def release_pending(self, task_id):
        return redis().eval(RELEASE_POLL, 1, self.pending_key, task_id)

    def apply_async(self, args=None, kwargs=None, task_id=None, **options):
        if args or kwargs:
            raise ValueError("POLL_TASK_MUST_NOT_CARRY_PAYLOAD")
        task_id = task_id or uid()
        if not redis().set(self.pending_key, task_id, nx=True, ex=POLL_LIFETIME + 5):
            return None
        try:
            return super().apply_async(
                args=(), kwargs={}, task_id=task_id, **{**options, "expires": POLL_LIFETIME}
            )
        except Exception:
            try:
                self.release_pending(task_id)
            except Exception:
                pass  # The bounded marker expires; the next tick can recover.
            raise

    def before_start(self, task_id, args, kwargs):
        self.release_pending(task_id)
        return super().before_start(task_id, args, kwargs)


def worker_app(service):
    app = Celery(service, broker=os.environ["SEMIBRAIN_REDIS_URL"])
    # Old wakeups are retained for rollback but no longer compete with live work.
    # This queue contains no business commands: each poll reads durable service state.
    queue = "celery_" + service + "_poll_v2"
    app.conf.update(
        task_default_queue=queue,
        task_serializer="json",
        accept_content=["json"],
        task_ignore_result=True,
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        broker_connection_retry_on_startup=True,
        worker_hijack_root_logger=False,
        worker_enable_remote_control=False,
        worker_send_task_events=False,
        broker_transport_options={
            "visibility_timeout": 600,
            "global_keyprefix": "celery_" + service + ":",
        },
        task_routes={service + ".*": {"queue": queue}},
        beat_schedule={"tick": {"task": service + ".tick", "schedule": 2.0}},
    )
    return app
