from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, TypedDict

from .domain import (
    ApprovalAction,
    ApprovalKind,
    CharacterCard,
    CreativeBrief,
    ReviewDecision,
    ReviewResult,
    SceneCard,
    ScriptDocument,
    Shot,
    Stage,
)
from .providers import ModelGateway

try:  # LangGraph is optional for offline tests, but required by production requirements.
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    HAS_LANGGRAPH = True
except ImportError:  # pragma: no cover - exercised only in a dependency-free environment
    MemorySaver = None  # type: ignore[assignment,misc]
    END = "__end__"
    START = "__start__"
    StateGraph = None  # type: ignore[assignment,misc]
    HAS_LANGGRAPH = False


class WorkflowState(TypedDict, total=False):
    run_id: str
    project_id: str
    canvas_id: str
    user_prompt: str
    stage: str
    status: str
    pending_approval: str | None
    pending_action: str | None
    pending_feedback: str | None
    script_revision_count: int
    storyboard_revision_count: int
    brief: dict[str, Any]
    script: dict[str, Any]
    script_review: dict[str, Any]
    characters: list[dict[str, Any]]
    scenes: list[dict[str, Any]]
    shots: list[dict[str, Any]]
    storyboard_review: dict[str, Any]
    media_task_ids: list[str]
    media_tasks_created: bool
    media_completed: bool
    error: str | None


EventCallback = Callable[[str, str, dict[str, Any]], None]
TaskFactory = Callable[[WorkflowState], list[str]]


class WorkflowBuilder:
    """Builds the controlled multi-agent graph and a small fallback runner."""

    def __init__(
        self,
        gateway: ModelGateway,
        max_revision_attempts: int = 2,
        task_factory: TaskFactory | None = None,
        event_callback: EventCallback | None = None,
    ) -> None:
        self.gateway = gateway
        self.max_revision_attempts = max_revision_attempts
        self.task_factory = task_factory or (lambda _state: [])
        self.event_callback = event_callback
        self.graph = self._build_graph() if HAS_LANGGRAPH else None

    def _emit(self, state: WorkflowState, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_callback:
            self.event_callback(state["run_id"], event_type, payload)

    @staticmethod
    def _brief(state: WorkflowState) -> CreativeBrief:
        return CreativeBrief.model_validate(state["brief"])

    @staticmethod
    def _script(state: WorkflowState) -> ScriptDocument:
        return ScriptDocument.model_validate(state["script"])

    @staticmethod
    def _characters(state: WorkflowState) -> list[CharacterCard]:
        return [CharacterCard.model_validate(item) for item in state.get("characters", [])]

    @staticmethod
    def _scenes(state: WorkflowState) -> list[SceneCard]:
        return [SceneCard.model_validate(item) for item in state.get("scenes", [])]

    @staticmethod
    def _shots(state: WorkflowState) -> list[Shot]:
        return [Shot.model_validate(item) for item in state.get("shots", [])]

    def node_entry(self, state: WorkflowState) -> WorkflowState:
        return {}

    def node_analyze(self, state: WorkflowState) -> WorkflowState:
        brief, usage = self.gateway.analyze_story(state["user_prompt"])
        self._emit(state, "agent_completed", {"agent": "story_analyzer", "usage": usage.model_dump()})
        return {"brief": brief.model_dump(), "stage": Stage.SCRIPT.value, "status": "running"}

    def node_script(self, state: WorkflowState) -> WorkflowState:
        feedback = state.get("pending_feedback")
        script, usage = self.gateway.generate_script(self._brief(state), feedback)
        count = state.get("script_revision_count", 0) + 1
        self._emit(
            state,
            "agent_completed",
            {"agent": "script_writer", "revision": count, "usage": usage.model_dump()},
        )
        return {
            "script": script.model_dump(),
            "script_revision_count": count,
            "script_review": {},
            "pending_feedback": None,
            "pending_action": None,
            "pending_approval": None,
            "stage": Stage.SCRIPT.value,
            "status": "running",
        }

    def node_script_review(self, state: WorkflowState) -> WorkflowState:
        result, usage = self.gateway.review_script(self._script(state))
        self._emit(
            state,
            "review_completed",
            {"review": "script", "result": result.model_dump(), "usage": usage.model_dump()},
        )
        return {"script_review": result.model_dump(), "stage": Stage.SCRIPT.value}

    def node_characters(self, state: WorkflowState) -> WorkflowState:
        characters, usage = self.gateway.generate_characters(self._script(state))
        self._emit(state, "agent_completed", {"agent": "character_designer", "usage": usage.model_dump()})
        return {
            "characters": [item.model_dump() for item in characters],
            "pending_approval": None,
            "pending_action": None,
            "pending_feedback": None,
            "stage": Stage.CHARACTERS.value,
            "status": "running",
        }

    def node_scenes(self, state: WorkflowState) -> WorkflowState:
        scenes, usage = self.gateway.generate_scenes(self._script(state))
        self._emit(state, "agent_completed", {"agent": "scene_designer", "usage": usage.model_dump()})
        return {"scenes": [item.model_dump() for item in scenes], "stage": Stage.SCENES.value, "status": "running"}

    def node_storyboard(self, state: WorkflowState) -> WorkflowState:
        feedback = state.get("pending_feedback")
        shots, usage = self.gateway.generate_storyboard(
            self._script(state), self._characters(state), self._scenes(state), feedback
        )
        count = state.get("storyboard_revision_count", 0) + 1
        self._emit(
            state,
            "agent_completed",
            {"agent": "storyboard_director", "revision": count, "usage": usage.model_dump()},
        )
        return {
            "shots": [item.model_dump() for item in shots],
            "storyboard_revision_count": count,
            "storyboard_review": {},
            "pending_feedback": None,
            "pending_action": None,
            "pending_approval": None,
            "stage": Stage.STORYBOARD.value,
            "status": "running",
        }

    def node_prompts(self, state: WorkflowState) -> WorkflowState:
        shots, usage = self.gateway.optimize_prompts(self._shots(state), self._characters(state), self._scenes(state))
        self._emit(state, "agent_completed", {"agent": "visual_prompt_optimizer", "usage": usage.model_dump()})
        return {"shots": [item.model_dump() for item in shots], "stage": Stage.PROMPTS.value, "status": "running"}

    def node_storyboard_review(self, state: WorkflowState) -> WorkflowState:
        result, usage = self.gateway.review_storyboard(self._shots(state), self._characters(state), self._scenes(state))
        self._emit(
            state,
            "review_completed",
            {"review": "storyboard", "result": result.model_dump(), "usage": usage.model_dump()},
        )
        return {"storyboard_review": result.model_dump(), "stage": Stage.STORYBOARD.value}

    def node_pause_script(self, state: WorkflowState) -> WorkflowState:
        self._emit(state, "approval_required", {"approval": ApprovalKind.SCRIPT.value})
        return {
            "pending_approval": ApprovalKind.SCRIPT.value,
            "status": "waiting_user",
            "stage": Stage.SCRIPT.value,
        }

    def node_pause_storyboard(self, state: WorkflowState) -> WorkflowState:
        self._emit(state, "approval_required", {"approval": ApprovalKind.STORYBOARD.value})
        return {
            "pending_approval": ApprovalKind.STORYBOARD.value,
            "status": "waiting_user",
            "stage": Stage.STORYBOARD.value,
        }

    def node_create_media(self, state: WorkflowState) -> WorkflowState:
        task_ids = self.task_factory(state)
        self._emit(state, "media_tasks_created", {"task_ids": task_ids})
        return {
            "media_task_ids": task_ids,
            "media_tasks_created": True,
            "media_completed": False,
            "status": "generating_media",
            "stage": Stage.MEDIA.value,
            "pending_approval": None,
        }

    def node_wait_media(self, state: WorkflowState) -> WorkflowState:
        return {"status": "generating_media", "stage": Stage.MEDIA.value}

    def node_complete(self, state: WorkflowState) -> WorkflowState:
        self._emit(state, "run_completed", {"task_ids": state.get("media_task_ids", [])})
        return {"status": "completed", "stage": Stage.COMPLETED.value, "error": None}

    def route_entry(self, state: WorkflowState) -> str:
        if state.get("status") in {"canceled", "failed"}:
            return END
        if state.get("media_completed"):
            return "complete"
        pending = state.get("pending_approval")
        action = state.get("pending_action")
        if pending == ApprovalKind.SCRIPT.value:
            if action == ApprovalAction.APPROVE.value:
                return "characters"
            if action == ApprovalAction.REVISE.value:
                return "script"
            return "pause_script"
        if pending == ApprovalKind.STORYBOARD.value:
            if action == ApprovalAction.APPROVE.value:
                return "create_media"
            if action == ApprovalAction.REVISE.value:
                return "storyboard"
            return "pause_storyboard"
        if not state.get("brief"):
            return "analyze"
        if not state.get("script"):
            return "script"
        script_review = ReviewResult.model_validate(state.get("script_review", {})) if state.get("script_review") else None
        if script_review is None:
            return "script_review"
        if script_review.decision == ReviewDecision.REVISE.value:
            if state.get("script_revision_count", 0) < self.max_revision_attempts:
                return "script"
            return "pause_script"
        if script_review.decision == ReviewDecision.HUMAN_REVIEW.value:
            return "pause_script"
        # Freeze the upstream creative direction before producing dependent assets.
        # The approval action routes the next invocation directly to characters.
        if not state.get("characters"):
            return "pause_script"
        if not state.get("scenes"):
            return "scenes"
        if not state.get("shots"):
            return "storyboard"
        storyboard_review = (
            ReviewResult.model_validate(state.get("storyboard_review", {})) if state.get("storyboard_review") else None
        )
        if not all(shot.get("image_prompt") and shot.get("video_prompt") for shot in state.get("shots", [])):
            return "prompts"
        if storyboard_review is None:
            return "storyboard_review"
        if storyboard_review.decision == ReviewDecision.REVISE.value:
            if state.get("storyboard_revision_count", 0) < self.max_revision_attempts:
                return "storyboard"
            return "pause_storyboard"
        if storyboard_review.decision == ReviewDecision.HUMAN_REVIEW.value:
            return "pause_storyboard"
        if not state.get("media_tasks_created"):
            return "pause_storyboard"
        return "wait_media"

    def _build_graph(self):
        builder = StateGraph(WorkflowState)
        nodes = {
            "entry": self.node_entry,
            "analyze": self.node_analyze,
            "script": self.node_script,
            "script_review": self.node_script_review,
            "characters": self.node_characters,
            "scenes": self.node_scenes,
            "storyboard": self.node_storyboard,
            "prompts": self.node_prompts,
            "storyboard_review": self.node_storyboard_review,
            "pause_script": self.node_pause_script,
            "pause_storyboard": self.node_pause_storyboard,
            "create_media": self.node_create_media,
            "wait_media": self.node_wait_media,
            "complete": self.node_complete,
        }
        for name, node in nodes.items():
            builder.add_node(name, node)
        builder.add_edge(START, "entry")
        builder.add_conditional_edges(
            "entry",
            self.route_entry,
            {
                "analyze": "analyze",
                "script": "script",
                "script_review": "script_review",
                "characters": "characters",
                "scenes": "scenes",
                "storyboard": "storyboard",
                "prompts": "prompts",
                "storyboard_review": "storyboard_review",
                "pause_script": "pause_script",
                "pause_storyboard": "pause_storyboard",
                "create_media": "create_media",
                "wait_media": "wait_media",
                "complete": "complete",
                END: END,
            },
        )
        builder.add_edge("analyze", "script")
        builder.add_edge("script", "script_review")
        builder.add_edge("script_review", "entry")
        builder.add_edge("characters", "scenes")
        builder.add_edge("scenes", "storyboard")
        builder.add_edge("storyboard", "prompts")
        builder.add_edge("prompts", "storyboard_review")
        builder.add_edge("storyboard_review", "entry")
        for name in ("pause_script", "pause_storyboard", "create_media", "wait_media", "complete"):
            builder.add_edge(name, END)
        return builder.compile(checkpointer=MemorySaver())

    def run(self, state: WorkflowState) -> WorkflowState:
        if self.graph is not None:
            result = self.graph.invoke(
                deepcopy(state),
                config={"configurable": {"thread_id": state["run_id"]}},
            )
            return result
        return self._run_fallback(state)

    def _run_fallback(self, initial: WorkflowState) -> WorkflowState:
        state: WorkflowState = deepcopy(initial)
        handlers = {
            "entry": self.node_entry,
            "analyze": self.node_analyze,
            "script": self.node_script,
            "script_review": self.node_script_review,
            "characters": self.node_characters,
            "scenes": self.node_scenes,
            "storyboard": self.node_storyboard,
            "prompts": self.node_prompts,
            "storyboard_review": self.node_storyboard_review,
            "pause_script": self.node_pause_script,
            "pause_storyboard": self.node_pause_storyboard,
            "create_media": self.node_create_media,
            "wait_media": self.node_wait_media,
            "complete": self.node_complete,
        }
        transitions = {
            "analyze": "script",
            "script": "script_review",
            "script_review": "entry",
            "characters": "scenes",
            "scenes": "storyboard",
            "storyboard": "prompts",
            "prompts": "storyboard_review",
            "storyboard_review": "entry",
        }
        for _ in range(32):
            target = self.route_entry(state)
            if target == END:
                break
            update = handlers[target](state)
            state.update(update)
            if target in {"pause_script", "pause_storyboard", "create_media", "wait_media", "complete"}:
                break
            next_target = transitions.get(target)
            if next_target:
                continue
        else:
            state["status"] = "failed"
            state["error"] = "workflow exceeded maximum node transitions"
        return state

