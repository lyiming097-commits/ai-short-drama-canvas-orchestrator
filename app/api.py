from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .domain import (
    ApprovalRequest,
    CreateModelProvider,
    CreateModelRoute,
    CreateProject,
    CreateRun,
    MediaType,
    RetryRequest,
    RunStatus,
    TaskStatus,
    UpdateModelProvider,
)
from .services import StudioService


class ProjectResponse(BaseModel):
    project: dict[str, Any]


router = APIRouter(prefix="/api")
_service: StudioService | None = None


def configure_api(service: StudioService) -> None:
    global _service
    _service = service


def service() -> StudioService:
    if _service is None:
        raise RuntimeError("API service is not configured")
    return _service


def gateway_admin():
    if _service is None or _service.gateway_admin is None:
        raise RuntimeError("model gateway admin is not configured")
    return _service.gateway_admin


def _schedule_queued(background: BackgroundTasks, run_id: str) -> None:
    studio = service()
    for task in studio.repo.list_tasks(run_id):
        if task.status == TaskStatus.QUEUED:
            if studio.settings.use_celery:
                from .tasks import generate_media_task

                generate_media_task.delay(task.id)
            else:
                background.add_task(studio.generate_media_task, task.id)


@router.post("/projects", status_code=201)
def create_project(body: CreateProject) -> dict[str, Any]:
    project = service().create_project(name=body.name, canvas_id=body.canvas_id, owner_id=body.owner_id)
    return project.model_dump(mode="json")


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    project = service().repo.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project.model_dump(mode="json")


@router.post("/projects/{project_id}/runs", status_code=201)
def create_run(project_id: str, body: CreateRun, background: BackgroundTasks) -> dict[str, Any]:
    try:
        run = service().create_run_record(project_id, body.prompt)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    background.add_task(service().run_in_background, run.id, body.auto_approve)
    return _run_detail(run.id)


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return _run_detail(run_id)


def _run_detail(run_id: str) -> dict[str, Any]:
    studio = service()
    run = studio.repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "run": run.model_dump(mode="json"),
        "tasks": [item.model_dump(mode="json") for item in studio.repo.list_tasks(run_id)],
        "assets": [item.model_dump(mode="json") for item in studio.repo.list_assets(run_id)],
        "canvas_bindings": [
            item.model_dump(mode="json") for item in studio.repo.list_bindings(run.canvas_id, run_id=run_id)
        ],
    }


@router.post("/runs/{run_id}/approve")
def approve_run(run_id: str, body: ApprovalRequest, background: BackgroundTasks) -> dict[str, Any]:
    try:
        run = service().queue_approval(run_id, body.action, body.feedback)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if run.status == RunStatus.RUNNING:
        background.add_task(service().run_in_background, run.id, False)
    return _run_detail(run_id)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, Any]:
    try:
        run = service().cancel(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _run_detail(run.id)


@router.post("/runs/{run_id}/shots/{shot_id}/retry")
def retry_shot(
    run_id: str,
    shot_id: str,
    body: RetryRequest,
    background: BackgroundTasks,
) -> dict[str, Any]:
    try:
        tasks = service().retry_media_task(run_id, shot_id, body.media_type)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    for task in tasks:
        if service().settings.use_celery:
            from .tasks import generate_media_task

            generate_media_task.delay(task.id)
        else:
            background.add_task(service().generate_media_task, task.id)
    return _run_detail(run_id)


@router.get("/runs/{run_id}/events")
def get_events(run_id: str, after: int = Query(default=0, ge=0)) -> dict[str, Any]:
    if service().repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    events = service().repo.list_events(run_id, after=after)
    return {"events": [event.model_dump(mode="json") for event in events], "next_sequence": events[-1].sequence if events else after}


@router.get("/canvas/{canvas_id}/runs/{run_id}/bindings")
def canvas_bindings(canvas_id: str, run_id: str) -> dict[str, Any]:
    if service().repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "bindings": [
            binding.model_dump(mode="json") for binding in service().repo.list_bindings(canvas_id, run_id=run_id)
        ]
    }


@router.post("/admin/model-providers", status_code=201)
def create_model_provider(body: CreateModelProvider) -> dict[str, Any]:
    return gateway_admin().create_provider(body)


@router.get("/admin/model-providers")
def list_model_providers() -> dict[str, Any]:
    return {"providers": gateway_admin().list_providers()}


@router.patch("/admin/model-providers/{provider_id}")
def update_model_provider(provider_id: str, body: UpdateModelProvider) -> dict[str, Any]:
    try:
        return gateway_admin().update_provider(provider_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/admin/model-providers/{provider_id}", status_code=204)
def delete_model_provider(provider_id: str) -> None:
    try:
        gateway_admin().delete_provider(provider_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/admin/model-providers/{provider_id}/test")
def test_model_provider(provider_id: str) -> dict[str, Any]:
    try:
        return gateway_admin().test_connection(provider_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/admin/model-routes", status_code=201)
def create_model_route(body: CreateModelRoute) -> dict[str, Any]:
    try:
        return gateway_admin().create_route(body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/admin/model-routes")
def list_model_routes() -> dict[str, Any]:
    return {"routes": gateway_admin().list_routes()}


@router.get("/admin/usage")
def usage_summary(run_id: str | None = None) -> dict[str, Any]:
    return gateway_admin().usage_summary(run_id)


@router.websocket("/ws/runs/{run_id}")
async def run_events(websocket: WebSocket, run_id: str, after: int = 0) -> None:
    studio = service()
    if studio.repo.get_run(run_id) is None:
        await websocket.close(code=4404, reason="run not found")
        return
    await websocket.accept()
    cursor = max(after, 0)
    try:
        while True:
            events = studio.repo.list_events(run_id, after=cursor)
            for event in events:
                await websocket.send_json(event.model_dump(mode="json"))
                cursor = event.sequence
            if not events:
                try:
                    message = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                    if message == "ping":
                        await websocket.send_json({"type": "pong", "sequence": cursor})
                except asyncio.TimeoutError:
                    continue
    except WebSocketDisconnect:
        return

