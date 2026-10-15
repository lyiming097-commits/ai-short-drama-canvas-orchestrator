from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Stage(str, Enum):
    INTAKE = "intake"
    SCRIPT = "script"
    CHARACTERS = "characters"
    SCENES = "scenes"
    STORYBOARD = "storyboard"
    PROMPTS = "prompts"
    MEDIA = "media"
    COMPLETED = "completed"


class RunStatus(str, Enum):
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    GENERATING_MEDIA = "generating_media"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCELED = "canceled"


class ApprovalKind(str, Enum):
    SCRIPT = "script"
    STORYBOARD = "storyboard"
    HUMAN_REVIEW = "human_review"


class ApprovalAction(str, Enum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"


class ReviewDecision(str, Enum):
    PASS = "pass"
    REVISE = "revise"
    HUMAN_REVIEW = "human_review"


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILED = "retryable_failed"
    FAILED = "failed"
    CANCELED = "canceled"


class AssetStatus(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    DELETED = "deleted"


class DomainModel(BaseModel):
    model_config = ConfigDict(use_enum_values=True)


class Project(DomainModel):
    id: str = Field(default_factory=lambda: new_id("project"))
    owner_id: str = "demo-user"
    name: str
    canvas_id: str
    created_at: datetime = Field(default_factory=utc_now)


class CreativeBrief(DomainModel):
    title: str
    logline: str
    genre: str
    tone: str
    target_duration_seconds: int = 30
    requirements: list[str] = Field(default_factory=list)


class ScriptBeat(DomainModel):
    id: str
    location: str
    time: str
    action: str
    dialogue: list[str] = Field(default_factory=list)


class ScriptDocument(DomainModel):
    version: int = 1
    title: str
    logline: str
    tone: str
    target_duration_seconds: int
    beats: list[ScriptBeat]


class CharacterCard(DomainModel):
    id: str
    name: str
    age: int | None = None
    appearance: str
    clothing: str
    personality: str
    voice: str
    reference_asset_id: str | None = None
    version: int = 1


class SceneCard(DomainModel):
    id: str
    name: str
    description: str
    time: str
    weather: str
    lighting: str
    key_objects: list[str] = Field(default_factory=list)
    reference_asset_id: str | None = None
    version: int = 1


class Shot(DomainModel):
    id: str
    sequence: int
    scene_id: str
    character_ids: list[str] = Field(default_factory=list)
    duration_seconds: int
    shot_size: str
    camera_motion: str
    action: str
    dialogue: str = ""
    image_prompt: str = ""
    video_prompt: str = ""
    voice_text: str = ""
    reference_asset_ids: list[str] = Field(default_factory=list)
    version: int = 1


class ReviewIssue(DomainModel):
    code: str
    message: str
    target_id: str | None = None
    suggestion: str | None = None


class ReviewResult(DomainModel):
    decision: ReviewDecision
    score: float = 1.0
    issues: list[ReviewIssue] = Field(default_factory=list)
    summary: str = ""


class ModelUsage(DomainModel):
    provider: str
    model: str
    provider_id: str | None = None
    capability: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    actual_cost: float | None = None
    latency_ms: int | None = None


class ModelCapability(str, Enum):
    LLM = "llm"
    VLM = "vlm"
    IMAGE = "image"
    VIDEO = "video"
    TTS = "tts"


class ModelProvider(DomainModel):
    id: str = Field(default_factory=lambda: new_id("provider"))
    name: str
    provider_type: str
    capabilities: list[ModelCapability] = Field(default_factory=list)
    api_base: str
    model_name: str
    encrypted_api_key: str
    masked_api_key: str
    enabled: bool = True
    priority: int = 100
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ModelRoute(DomainModel):
    id: str = Field(default_factory=lambda: new_id("route"))
    capability: ModelCapability
    provider_id: str
    model_name: str
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class UsageRecord(DomainModel):
    id: str = Field(default_factory=lambda: new_id("usage"))
    run_id: str | None = None
    provider_id: str | None = None
    provider: str
    capability: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    actual_cost: float | None = None
    latency_ms: int | None = None
    status: str = "succeeded"
    created_at: datetime = Field(default_factory=utc_now)


class MediaTask(DomainModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    run_id: str
    shot_id: str
    media_type: MediaType
    provider: str
    model: str
    idempotency_key: str
    status: TaskStatus = TaskStatus.QUEUED
    attempt: int = 0
    provider_task_id: str | None = None
    asset_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Asset(DomainModel):
    id: str = Field(default_factory=lambda: new_id("asset"))
    run_id: str
    shot_id: str | None = None
    media_type: MediaType
    storage_key: str
    content_type: str
    url: str | None = None
    status: AssetStatus = AssetStatus.ACTIVE
    source_task_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class CanvasBinding(DomainModel):
    canvas_id: str
    node_id: str
    run_id: str
    shot_id: str | None = None
    asset_ids: list[str] = Field(default_factory=list)
    prompt: str | None = None
    stale: bool = False
    updated_at: datetime = Field(default_factory=utc_now)


class WorkflowEvent(DomainModel):
    id: str = Field(default_factory=lambda: new_id("event"))
    run_id: str
    sequence: int
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class RunRecord(DomainModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    project_id: str
    canvas_id: str
    prompt: str
    status: RunStatus = RunStatus.RUNNING
    stage: Stage = Stage.INTAKE
    pending_approval: ApprovalKind | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CreateProject(DomainModel):
    name: str
    canvas_id: str
    owner_id: str = "demo-user"


class CreateRun(DomainModel):
    prompt: str
    auto_approve: bool = False


class ApprovalRequest(DomainModel):
    action: ApprovalAction
    feedback: str | None = None


class RetryRequest(DomainModel):
    media_type: MediaType | None = None


class CreateModelProvider(DomainModel):
    name: str
    provider_type: str
    capabilities: list[ModelCapability] = Field(default_factory=list)
    api_base: str
    model_name: str
    api_key: str
    enabled: bool = True
    priority: int = 100


class UpdateModelProvider(DomainModel):
    name: str | None = None
    provider_type: str | None = None
    capabilities: list[ModelCapability] | None = None
    api_base: str | None = None
    model_name: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    priority: int | None = None


class CreateModelRoute(DomainModel):
    capability: ModelCapability
    provider_id: str
    model_name: str
    enabled: bool = True

