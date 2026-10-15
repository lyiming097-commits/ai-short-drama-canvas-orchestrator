from __future__ import annotations

from celery import Celery

from .config import get_settings

settings = get_settings()
celery_app = Celery(
    "short_drama",
    broker=settings.rabbitmq_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"confirm_publish": True},
    task_default_queue="media",
    task_default_exchange="short_drama",
    task_default_exchange_type="direct",
    task_default_routing_key="media",
    task_routes={"app.tasks.generate_media_task": {"queue": "media"}},
)

