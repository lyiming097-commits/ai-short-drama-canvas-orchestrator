from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Iterable

from .domain import (
    Asset,
    CanvasBinding,
    MediaTask,
    ModelProvider,
    ModelRoute,
    Project,
    RunRecord,
    UsageRecord,
    WorkflowEvent,
    utc_now,
)


class InMemoryRepository:
    """Small repository used by the demo and tests.

    The methods mirror the boundaries needed by a PostgreSQL implementation.
    Replacing this class does not require changing the graph or API layer.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self.projects: dict[str, Project] = {}
        self.runs: dict[str, RunRecord] = {}
        self.tasks: dict[str, MediaTask] = {}
        self.assets: dict[str, Asset] = {}
        self.bindings: dict[tuple[str, str], CanvasBinding] = {}
        self.events: dict[str, list[WorkflowEvent]] = {}
        self.providers: dict[str, ModelProvider] = {}
        self.routes: dict[str, ModelRoute] = {}
        self.usage: dict[str, UsageRecord] = {}

    def create_project(self, project: Project) -> Project:
        with self._lock:
            self.projects[project.id] = deepcopy(project)
        return project

    def get_project(self, project_id: str) -> Project | None:
        with self._lock:
            project = self.projects.get(project_id)
            return deepcopy(project) if project else None

    def create_run(self, run: RunRecord) -> RunRecord:
        with self._lock:
            self.runs[run.id] = deepcopy(run)
            self.events[run.id] = []
        return run

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            run = self.runs.get(run_id)
            return deepcopy(run) if run else None

    def update_run(self, run_id: str, **changes: object) -> RunRecord:
        with self._lock:
            current = self.runs[run_id]
            changes["updated_at"] = utc_now()
            updated = current.model_copy(update=changes)
            self.runs[run_id] = deepcopy(updated)
            return deepcopy(updated)

    def append_event(self, run_id: str, event_type: str, payload: dict) -> WorkflowEvent:
        with self._lock:
            stream = self.events.setdefault(run_id, [])
            event = WorkflowEvent(
                run_id=run_id,
                sequence=len(stream) + 1,
                type=event_type,
                payload=deepcopy(payload),
            )
            stream.append(event)
            return deepcopy(event)

    def list_events(self, run_id: str, after: int = 0) -> list[WorkflowEvent]:
        with self._lock:
            return [deepcopy(event) for event in self.events.get(run_id, []) if event.sequence > after]

    def create_task(self, task: MediaTask) -> MediaTask:
        with self._lock:
            self.tasks[task.id] = deepcopy(task)
        return task

    def get_task(self, task_id: str) -> MediaTask | None:
        with self._lock:
            task = self.tasks.get(task_id)
            return deepcopy(task) if task else None

    def find_task_by_key(self, key: str) -> MediaTask | None:
        with self._lock:
            for task in self.tasks.values():
                if task.idempotency_key == key:
                    return deepcopy(task)
        return None

    def list_tasks(self, run_id: str) -> list[MediaTask]:
        with self._lock:
            return [deepcopy(task) for task in self.tasks.values() if task.run_id == run_id]

    def update_task(self, task_id: str, **changes: object) -> MediaTask:
        with self._lock:
            current = self.tasks[task_id]
            changes["updated_at"] = utc_now()
            updated = current.model_copy(update=changes)
            self.tasks[task_id] = deepcopy(updated)
            return deepcopy(updated)

    def create_asset(self, asset: Asset) -> Asset:
        with self._lock:
            self.assets[asset.id] = deepcopy(asset)
        return asset

    def get_asset(self, asset_id: str) -> Asset | None:
        with self._lock:
            asset = self.assets.get(asset_id)
            return deepcopy(asset) if asset else None

    def list_assets(self, run_id: str) -> list[Asset]:
        with self._lock:
            return [deepcopy(asset) for asset in self.assets.values() if asset.run_id == run_id]

    def upsert_binding(self, binding: CanvasBinding) -> CanvasBinding:
        with self._lock:
            key = (binding.canvas_id, binding.node_id)
            self.bindings[key] = deepcopy(binding)
            return deepcopy(binding)

    def get_binding(self, canvas_id: str, node_id: str) -> CanvasBinding | None:
        with self._lock:
            binding = self.bindings.get((canvas_id, node_id))
            return deepcopy(binding) if binding else None

    def list_bindings(self, canvas_id: str, run_id: str | None = None) -> list[CanvasBinding]:
        with self._lock:
            values: Iterable[CanvasBinding] = self.bindings.values()
            if run_id is not None:
                values = [item for item in values if item.run_id == run_id]
            return [deepcopy(item) for item in values if item.canvas_id == canvas_id]

    def create_provider(self, provider: ModelProvider) -> ModelProvider:
        with self._lock:
            self.providers[provider.id] = deepcopy(provider)
        return deepcopy(provider)

    def get_provider(self, provider_id: str) -> ModelProvider | None:
        with self._lock:
            provider = self.providers.get(provider_id)
            return deepcopy(provider) if provider else None

    def list_providers(self) -> list[ModelProvider]:
        with self._lock:
            return sorted((deepcopy(item) for item in self.providers.values()), key=lambda item: item.priority)

    def update_provider(self, provider_id: str, **changes: object) -> ModelProvider:
        with self._lock:
            current = self.providers[provider_id]
            changes["updated_at"] = utc_now()
            updated = current.model_copy(update=changes)
            self.providers[provider_id] = deepcopy(updated)
            return deepcopy(updated)

    def delete_provider(self, provider_id: str) -> None:
        with self._lock:
            self.providers.pop(provider_id, None)
            for route_id, route in list(self.routes.items()):
                if route.provider_id == provider_id:
                    del self.routes[route_id]

    def create_route(self, route: ModelRoute) -> ModelRoute:
        with self._lock:
            self.routes[route.id] = deepcopy(route)
        return deepcopy(route)

    def list_routes(self) -> list[ModelRoute]:
        with self._lock:
            return [deepcopy(item) for item in self.routes.values()]

    def record_usage(self, usage: UsageRecord) -> UsageRecord:
        with self._lock:
            self.usage[usage.id] = deepcopy(usage)
        return deepcopy(usage)

    def list_usage(self, run_id: str | None = None) -> list[UsageRecord]:
        with self._lock:
            values = self.usage.values()
            if run_id is not None:
                values = [item for item in values if item.run_id == run_id]
            return sorted((deepcopy(item) for item in values), key=lambda item: item.created_at)

