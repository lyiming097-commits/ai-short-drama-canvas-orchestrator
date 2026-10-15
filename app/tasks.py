from __future__ import annotations

from .celery_app import celery_app
from .main import studio


@celery_app.task(name="app.tasks.generate_media_task", bind=True, max_retries=0)
def generate_media_task(_self, task_id: str) -> dict:
    result = studio.generate_media_task(task_id)
    return result.model_dump(mode="json")

