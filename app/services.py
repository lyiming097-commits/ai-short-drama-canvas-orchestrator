from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from threading import Thread
from typing import Any

from .config import Settings
from .domain import (
    ApprovalAction,
    ApprovalKind,
    Asset,
    CanvasBinding,
    MediaTask,
    MediaType,
    ModelUsage,
    Project,
    RunRecord,
    RunStatus,
    Shot,
    TaskStatus,
    utc_now,
)
from .graph import WorkflowBuilder, WorkflowState
from .gateway_admin import ModelGatewayAdmin
from .providers import AssetStore, ModelGateway
from .repositories import InMemoryRepository


MEDIA_MODELS = {
    MediaType.IMAGE: "seedream",
    MediaType.VIDEO: "seedance",
    MediaType.AUDIO: "tts",
}


class StudioService:
    def __init__(
        self,
        repo: InMemoryRepository,
        gateway: ModelGateway,
        asset_store: AssetStore,
        settings: Settings,
        gateway_admin: ModelGatewayAdmin | None = None,
    ) -> None:
        self.repo = repo
        self.gateway = gateway
        self.asset_store = asset_store
        self.settings = settings
        self.gateway_admin = gateway_admin
        self.graph = WorkflowBuilder(
            gateway=gateway,
            max_revision_attempts=settings.max_revision_attempts,
            task_factory=self._create_media_tasks,
            event_callback=self._event,
        )

    def _event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.repo.append_event(run_id, event_type, payload)
        usage_payload = payload.get("usage")
        if usage_payload and self.gateway_admin:
            agent = payload.get("agent", "")
            capability = "vlm" if "review" in str(agent) else "llm"
            self.gateway_admin.record_usage(run_id, ModelUsage.model_validate(usage_payload), capability)

    def create_project(self, *, name: str, canvas_id: str, owner_id: str) -> Project:
        project = Project(name=name, canvas_id=canvas_id, owner_id=owner_id)
        return self.repo.create_project(project)

    def _state_for_run(self, run: RunRecord) -> WorkflowState:
        persisted = deepcopy(run.state)
        if persisted:
            return persisted  # type: ignore[return-value]
        return {
            "run_id": run.id,
            "project_id": run.project_id,
            "canvas_id": run.canvas_id,
            "user_prompt": run.prompt,
            "status": RunStatus.RUNNING.value,
            "stage": "intake",
            "pending_approval": None,
            "pending_action": None,
            "pending_feedback": None,
            "script_revision_count": 0,
            "storyboard_revision_count": 0,
            "media_task_ids": [],
            "media_tasks_created": False,
            "media_completed": False,
            "error": None,
        }

    def _persist_state(self, run_id: str, state: WorkflowState) -> RunRecord:
        status = RunStatus(state.get("status", RunStatus.RUNNING.value))
        pending = state.get("pending_approval")
        stage = state.get("stage", "intake")
        run = self.repo.update_run(
            run_id,
            state=deepcopy(state),
            status=status,
            stage=stage,
            pending_approval=ApprovalKind(pending) if pending else None,
            error=state.get("error"),
        )
        self.repo.append_event(
            run_id,
            "run_state_changed",
            {"status": status.value, "stage": stage, "pending_approval": pending},
        )
        self._sync_canvas(run, state)
        return run

    def _sync_canvas(self, run: RunRecord, state: WorkflowState) -> None:
        static_content = [
            ("script", "script", state.get("script")),
            ("characters", "characters", state.get("characters")),
            ("scenes", "scenes", state.get("scenes")),
        ]
        for node_id, key, content in static_content:
            if content is not None:
                binding = CanvasBinding(
                    canvas_id=run.canvas_id,
                    node_id=f"{run.id}:{node_id}",
                    run_id=run.id,
                    prompt=json.dumps(content, ensure_ascii=False),
                )
                self.repo.upsert_binding(binding)
                self.repo.append_event(
                    run.id,
                    "canvas_content_updated",
                    {"node_id": binding.node_id, "content_type": key},
                )
        for shot in state.get("shots", []):
            node_id = f"{run.id}:{shot['id']}"
            binding = self.repo.get_binding(run.canvas_id, node_id) or CanvasBinding(
                canvas_id=run.canvas_id,
                node_id=node_id,
                run_id=run.id,
                shot_id=shot["id"],
            )
            binding.prompt = shot.get("video_prompt") or shot.get("image_prompt")
            self.repo.upsert_binding(binding)

    def create_run_record(self, project_id: str, prompt: str) -> RunRecord:
        """Create the persisted shell returned before the graph starts running."""
        project = self.repo.get_project(project_id)
        if project is None:
            raise KeyError("project not found")
        run = RunRecord(project_id=project.id, canvas_id=project.canvas_id, prompt=prompt)
        self.repo.create_run(run)
        self.repo.append_event(run.id, "run_started", {"prompt": prompt})
        self._persist_state(run.id, self._state_for_run(run))
        return self.repo.get_run(run.id) or run

    def _execute_run(self, run_id: str, auto_approve: bool = False) -> RunRecord:
        run = self.repo.get_run(run_id)
        if run is None:
            raise KeyError("run not found")
        state = self._state_for_run(run)
        try:
            result = self.graph.run(state)
            while auto_approve and result.get("status") == RunStatus.WAITING_USER.value:
                result = self._resume(result, ApprovalAction.APPROVE, None)
            persisted = self._persist_state(run.id, result)
            if persisted.status == RunStatus.GENERATING_MEDIA:
                self._dispatch_media_tasks(run.id)
            return persisted
        except Exception as exc:
            state["status"] = RunStatus.FAILED.value
            state["error"] = str(exc)
            self._persist_state(run.id, state)
            raise

    def run_in_background(self, run_id: str, auto_approve: bool = False) -> None:
        try:
            self._execute_run(run_id, auto_approve)
        except Exception:
            # The failure and error message are already persisted for polling clients.
            return

    def start_run(self, project_id: str, prompt: str, auto_approve: bool = False) -> RunRecord:
        run = self.create_run_record(project_id, prompt)
        return self._execute_run(run.id, auto_approve)

    def _resume(
        self,
        state: WorkflowState,
        action: ApprovalAction,
        feedback: str | None,
    ) -> WorkflowState:
        resumed = deepcopy(state)
        resumed["pending_action"] = action.value
        resumed["pending_feedback"] = feedback
        resumed["status"] = RunStatus.RUNNING.value
        return self.graph.run(resumed)

    def approve(self, run_id: str, action: ApprovalAction, feedback: str | None = None) -> RunRecord:
        action = ApprovalAction(action)
        run = self.repo.get_run(run_id)
        if run is None:
            raise KeyError("run not found")
        if run.status != RunStatus.WAITING_USER:
            raise ValueError("run is not waiting for user input")
        if action == ApprovalAction.REVISE and not (feedback and feedback.strip()):
            raise ValueError("feedback is required when requesting a revision")
        self.repo.append_event(
            run_id,
            "user_decision",
            {
                "approval": (
                    run.pending_approval.value
                    if hasattr(run.pending_approval, "value")
                    else run.pending_approval
                ),
                "action": action.value,
                "feedback": feedback,
            },
        )
        if action == ApprovalAction.REJECT:
            state = self._state_for_run(run)
            state["status"] = RunStatus.CANCELED.value
            state["error"] = "rejected by user"
        else:
            state = self._resume(self._state_for_run(run), action, feedback)
        return self._persist_state(run_id, state)

    def queue_approval(self, run_id: str, action: ApprovalAction, feedback: str | None = None) -> RunRecord:
        """Persist a user decision and let the API worker resume the graph."""
        action = ApprovalAction(action)
        run = self.repo.get_run(run_id)
        if run is None:
            raise KeyError("run not found")
        if run.status != RunStatus.WAITING_USER:
            raise ValueError("run is not waiting for user input")
        if action == ApprovalAction.REVISE and not (feedback and feedback.strip()):
            raise ValueError("feedback is required when requesting a revision")
        self.repo.append_event(
            run_id,
            "user_decision",
            {
                "approval": run.pending_approval.value if hasattr(run.pending_approval, "value") else run.pending_approval,
                "action": action.value,
                "feedback": feedback,
            },
        )
        if action == ApprovalAction.REJECT:
            state = self._state_for_run(run)
            state["status"] = RunStatus.CANCELED.value
            state["error"] = "rejected by user"
        else:
            state = self._state_for_run(run)
            state["pending_action"] = action.value
            state["pending_feedback"] = feedback
            state["status"] = RunStatus.RUNNING.value
        return self._persist_state(run_id, state)

    def _dispatch_media_tasks(self, run_id: str) -> None:
        for task in self.repo.list_tasks(run_id):
            if task.status != TaskStatus.QUEUED:
                continue
            if self.settings.use_celery:
                from .tasks import generate_media_task

                generate_media_task.delay(task.id)
            else:
                Thread(target=self.generate_media_task, args=(task.id,), daemon=True).start()

    def cancel(self, run_id: str) -> RunRecord:
        run = self.repo.get_run(run_id)
        if run is None:
            raise KeyError("run not found")
        for task in self.repo.list_tasks(run_id):
            if task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYABLE_FAILED}:
                self.repo.update_task(task.id, status=TaskStatus.CANCELED, error="run canceled")
        state = self._state_for_run(run)
        state["status"] = RunStatus.CANCELED.value
        state["error"] = "canceled by user"
        self.repo.append_event(run_id, "run_canceled", {})
        return self._persist_state(run_id, state)

    def _create_media_tasks(self, state: WorkflowState) -> list[str]:
        run_id = state["run_id"]
        existing = self.repo.list_tasks(run_id)
        if existing:
            return [task.id for task in existing]
        task_ids: list[str] = []
        shots = [Shot.model_validate(item) for item in state.get("shots", [])]
        for shot in shots:
            # Each shot produces independent idempotent tasks. The providers can be
            # swapped behind the gateway without changing the workflow state model.
            for media_type in (MediaType.IMAGE, MediaType.VIDEO, MediaType.AUDIO):
                usage = self.gateway.estimate_media(media_type, shot)
                seed = "|".join(
                    (run_id, shot.id, shot.image_prompt, shot.video_prompt, shot.voice_text, media_type.value, usage.model)
                )
                idempotency_key = hashlib.sha256(seed.encode("utf-8")).hexdigest()
                existing_task = self.repo.find_task_by_key(idempotency_key)
                if existing_task:
                    task_ids.append(existing_task.id)
                    continue
                task = MediaTask(
                    run_id=run_id,
                    shot_id=shot.id,
                    media_type=media_type,
                    provider=usage.provider,
                    model=usage.model,
                    idempotency_key=idempotency_key,
                )
                self.repo.create_task(task)
                self.repo.append_event(
                    run_id,
                    "media_task_queued",
                    {
                        "task_id": task.id,
                        "shot_id": shot.id,
                        "media_type": media_type.value,
                        "model": usage.model,
                        "estimated_cost": usage.estimated_cost,
                    },
                )
                task_ids.append(task.id)
        return task_ids

    def generate_media_task(self, task_id: str) -> MediaTask:
        task = self.repo.get_task(task_id)
        if task is None:
            raise KeyError("media task not found")
        media_type = MediaType(task.media_type)
        if task.status == TaskStatus.SUCCEEDED:
            return task
        run = self.repo.get_run(task.run_id)
        if run is None:
            raise KeyError("run not found")
        state = self._state_for_run(run)
        shot_data = next((item for item in state.get("shots", []) if item["id"] == task.shot_id), None)
        if shot_data is None:
            raise ValueError("shot associated with the media task was not found")
        shot = Shot.model_validate(shot_data)
        task = self.repo.update_task(task.id, status=TaskStatus.RUNNING, attempt=task.attempt + 1, error=None)
        self.repo.append_event(
            task.run_id,
            "media_task_started",
            {"task_id": task.id, "shot_id": task.shot_id, "attempt": task.attempt},
        )
        try:
            content, content_type, usage = self.gateway.generate_media(
                task.model_copy(update={"media_type": media_type}), shot
            )
            if self.gateway_admin:
                self.gateway_admin.record_usage(task.run_id, usage, media_type.value)
            suffix = {MediaType.IMAGE: "png", MediaType.VIDEO: "mp4", MediaType.AUDIO: "mp3"}[media_type]
            asset = Asset(
                run_id=task.run_id,
                shot_id=task.shot_id,
                media_type=media_type,
                storage_key=f"project/{run.project_id}/run/{run.id}/shot/{task.shot_id}/{task.id}.{suffix}",
                content_type=content_type,
                source_task_id=task.id,
                metadata={
                    "provider": usage.provider,
                    "model": usage.model,
                    "actual_cost": usage.actual_cost or usage.estimated_cost,
                    "attempt": task.attempt,
                },
            )
            self.asset_store.put(asset.storage_key, content, content_type)
            asset.url = self.asset_store.presign(asset.storage_key, content_type)
            self.repo.create_asset(asset)
            task = self.repo.update_task(task.id, status=TaskStatus.SUCCEEDED, asset_id=asset.id, provider_task_id=None)
            self._fill_canvas(run, asset, shot)
            self.repo.append_event(
                run.id,
                "media_task_succeeded",
                {
                    "task_id": task.id,
                    "asset": asset.model_dump(mode="json"),
                    "usage": usage.model_dump(),
                },
            )
            self._refresh_run_media_status(run.id)
            return task
        except Exception as exc:
            status = TaskStatus.RETRYABLE_FAILED if task.attempt < 3 else TaskStatus.FAILED
            task = self.repo.update_task(task.id, status=status, error=str(exc))
            self.repo.append_event(
                run.id,
                "media_task_failed",
                {"task_id": task.id, "status": status.value, "error": str(exc)},
            )
            self._refresh_run_media_status(run.id)
            return task

    def _fill_canvas(self, run: RunRecord, asset: Asset, shot: Shot) -> None:
        node_id = f"{run.id}:{shot.id}"
        binding = self.repo.get_binding(run.canvas_id, node_id) or CanvasBinding(
            canvas_id=run.canvas_id,
            node_id=node_id,
            run_id=run.id,
            shot_id=shot.id,
        )
        if asset.id not in binding.asset_ids:
            binding.asset_ids.append(asset.id)
        binding.stale = False
        binding.updated_at = utc_now()
        self.repo.upsert_binding(binding)
        self.repo.append_event(
            run.id,
            "canvas_asset_attached",
            {
                "canvas_id": run.canvas_id,
                "node_id": node_id,
                "shot_id": shot.id,
                "asset_id": asset.id,
                    "media_type": MediaType(asset.media_type).value,
                "url": asset.url,
            },
        )

    def _refresh_run_media_status(self, run_id: str) -> None:
        run = self.repo.get_run(run_id)
        if run is None:
            return
        tasks = self.repo.list_tasks(run_id)
        if not tasks:
            return
        if any(task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING} for task in tasks):
            return
        state = self._state_for_run(run)
        if any(task.status in {TaskStatus.RETRYABLE_FAILED, TaskStatus.FAILED} for task in tasks):
            state["status"] = RunStatus.PARTIAL_FAILED.value
            state["error"] = "one or more media tasks need retry or review"
            self._persist_state(run_id, state)
            return
        state["media_completed"] = True
        state["status"] = RunStatus.RUNNING.value
        result = self.graph.run(state)
        self._persist_state(run_id, result)

    def retry_media_task(self, run_id: str, shot_id: str, media_type: MediaType | None = None) -> list[MediaTask]:
        if media_type is not None:
            media_type = MediaType(media_type)
        run = self.repo.get_run(run_id)
        if run is None:
            raise KeyError("run not found")
        matching = [
            task
            for task in self.repo.list_tasks(run_id)
            if task.shot_id == shot_id
            and (media_type is None or task.media_type == media_type)
            and task.status in {TaskStatus.RETRYABLE_FAILED, TaskStatus.FAILED}
        ]
        if not matching:
            raise ValueError("no failed task matched this shot and media type")
        retried = []
        for task in matching:
            task = self.repo.update_task(task.id, status=TaskStatus.QUEUED, error=None)
            self.repo.append_event(run_id, "media_task_queued", {"task_id": task.id, "retry": True})
            retried.append(task)
        self.repo.update_run(run_id, status=RunStatus.GENERATING_MEDIA, error=None)
        return retried

    def mark_upstream_changed(self, run_id: str, changed_node_id: str) -> list[CanvasBinding]:
        run = self.repo.get_run(run_id)
        if run is None:
            raise KeyError("run not found")
        bindings = self.repo.list_bindings(
            run.canvas_id, run_id=run_id
        )
        affected: list[CanvasBinding] = []
        for binding in bindings:
            if binding.node_id == changed_node_id:
                continue
            if binding.shot_id is not None:
                binding.stale = True
                self.repo.upsert_binding(binding)
                affected.append(binding)
        if affected:
            self.repo.append_event(
                run_id,
                "assets_marked_stale",
                {"source_node_id": changed_node_id, "node_ids": [item.node_id for item in affected]},
            )
        return affected

