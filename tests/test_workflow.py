from app.config import Settings
from app.domain import ApprovalAction, CreateModelProvider, CreateModelRoute, ModelCapability, RunStatus, TaskStatus
from app.gateway_admin import ModelGatewayAdmin
from app.providers import InMemoryAssetStore, MockModelGateway
from app.repositories import InMemoryRepository
from app.routed_gateway import RoutedModelGateway
from app.security import CredentialVault
from app.services import StudioService


def make_service() -> StudioService:
    repository = InMemoryRepository()
    admin = ModelGatewayAdmin(repository, CredentialVault())
    return StudioService(
        repo=repository,
        gateway=MockModelGateway(),
        asset_store=InMemoryAssetStore(),
        settings=Settings(max_revision_attempts=2),
        gateway_admin=admin,
    )


def test_full_creation_flow_and_canvas_backfill() -> None:
    studio = make_service()
    project = studio.create_project(name="雨夜来信", canvas_id="canvas-001", owner_id="tester")

    run = studio.start_run(project.id, "女主在雨夜发现失踪哥哥留下的信。")
    assert run.status == RunStatus.WAITING_USER
    assert run.pending_approval == "script"
    assert run.state["script"]["beats"]
    assert run.state.get("characters", []) == []

    run = studio.approve(run.id, ApprovalAction.APPROVE)
    assert run.status == RunStatus.WAITING_USER
    assert run.pending_approval == "storyboard"
    assert len(run.state["shots"]) == 4
    assert all(shot["video_prompt"] for shot in run.state["shots"])

    run = studio.approve(run.id, ApprovalAction.APPROVE)
    assert run.status == RunStatus.GENERATING_MEDIA
    tasks = studio.repo.list_tasks(run.id)
    assert len(tasks) == 4  # one canvas keyframe image per shot; video prompts remain text assets
    assert all(task.status == TaskStatus.QUEUED for task in tasks)

    for task in tasks:
        studio.generate_media_task(task.id)

    completed = studio.repo.get_run(run.id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    assert len(studio.repo.list_assets(run.id)) == len(tasks)
    bindings = studio.repo.list_bindings(project.canvas_id, run_id=run.id)
    shot_bindings = [binding for binding in bindings if binding.shot_id]
    assert len(shot_bindings) == 4
    assert sum(len(binding.asset_ids) for binding in shot_bindings) == len(tasks)
    events = studio.repo.list_events(run.id)
    assert any(event.type == "canvas_asset_attached" for event in events)
    usage = studio.gateway_admin.usage_summary(run.id)
    assert usage["calls"] == 12
    assert usage["total_tokens"] > 0


def test_revision_requires_feedback() -> None:
    studio = make_service()
    project = studio.create_project(name="demo", canvas_id="canvas-002", owner_id="tester")
    run = studio.start_run(project.id, "一个悬疑故事")
    try:
        studio.approve(run.id, ApprovalAction.REVISE)
    except ValueError as exc:
        assert "feedback" in str(exc)
    else:
        raise AssertionError("revision without feedback should fail")


def test_provider_credentials_are_encrypted_and_testable() -> None:
    repository = InMemoryRepository()
    admin = ModelGatewayAdmin(repository, CredentialVault())
    provider = admin.create_provider(
        CreateModelProvider(
            name="本地模拟连接",
            provider_type="mock",
            capabilities=[ModelCapability.LLM, ModelCapability.VIDEO],
            api_base="http://mock.local",
            model_name="demo-model",
            api_key="secret-provider-key",
        )
    )
    assert "encrypted_api_key" not in provider
    assert provider["masked_api_key"] != "secret-provider-key"
    stored = repository.get_provider(provider["id"])
    assert stored is not None
    assert stored.encrypted_api_key != "secret-provider-key"
    assert admin.test_connection(provider["id"])["ok"] is True
    route = admin.create_route(
        CreateModelRoute(capability=ModelCapability.LLM, provider_id=provider["id"], model_name="demo-model")
    )
    assert route["provider"] == "本地模拟连接"
    assert len(admin.list_routes()) == 1


def test_routed_gateway_uses_configured_openai_compatible_provider(monkeypatch) -> None:
    repository = InMemoryRepository()
    admin = ModelGatewayAdmin(repository, CredentialVault())
    provider = admin.create_provider(
        CreateModelProvider(
            name="routed-provider",
            provider_type="openai_compatible",
            capabilities=[ModelCapability.LLM],
            api_base="https://provider.example/v1",
            model_name="story-model",
            api_key="test-only-secret",
        )
    )
    request: dict = {}

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {
                "choices": [{"message": {"content": '{"title":"A","logline":"B","genre":"drama","tone":"warm","target_duration_seconds":30,"requirements":[]}'} }],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            }

    def fake_post(url, **kwargs):
        request.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("app.routed_gateway.httpx.post", fake_post)
    brief, usage = RoutedModelGateway(admin).analyze_story("a short drama idea")

    assert request["url"] == "https://provider.example/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer test-only-secret"
    assert request["json"]["model"] == "story-model"
    assert brief.title == "A"
    assert usage.provider_id == provider["id"]
    assert usage.input_tokens == 12
    assert usage.output_tokens == 8


def test_deepseek_console_host_is_normalized_to_api_host() -> None:
    assert ModelGatewayAdmin.normalize_api_base("https://platform.deepseek.com/v1") == "https://api.deepseek.com/v1"

