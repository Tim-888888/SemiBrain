import os

from celery import Celery


def worker_app(service):
    app = Celery(service, broker=os.environ["SEMIBRAIN_REDIS_URL"])
    app.conf.update(
        task_default_queue="celery_" + service,
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
        task_routes={service + ".*": {"queue": "celery_" + service}},
        beat_schedule={"tick": {"task": service + ".tick", "schedule": 2.0}},
    )
    return app
