from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from .domain import (
    CharacterCard,
    CreativeBrief,
    MediaTask,
    MediaType,
    ModelUsage,
    ReviewResult,
    SceneCard,
    ScriptDocument,
    Shot,
    new_id,
)


class ModelGateway(Protocol):
    """Contract implemented by the platform's existing AI model gateway."""

    def analyze_story(self, prompt: str) -> tuple[CreativeBrief, ModelUsage]: ...

    def generate_script(self, brief: CreativeBrief, feedback: str | None = None) -> tuple[ScriptDocument, ModelUsage]: ...

    def generate_characters(self, script: ScriptDocument) -> tuple[list[CharacterCard], ModelUsage]: ...

    def generate_scenes(self, script: ScriptDocument) -> tuple[list[SceneCard], ModelUsage]: ...

    def generate_storyboard(
        self,
        script: ScriptDocument,
        characters: list[CharacterCard],
        scenes: list[SceneCard],
        feedback: str | None = None,
    ) -> tuple[list[Shot], ModelUsage]: ...

    def optimize_prompts(
        self,
        shots: list[Shot],
        characters: list[CharacterCard],
        scenes: list[SceneCard],
    ) -> tuple[list[Shot], ModelUsage]: ...

    def review_script(self, script: ScriptDocument) -> tuple[ReviewResult, ModelUsage]: ...

    def review_storyboard(
        self,
        shots: list[Shot],
        characters: list[CharacterCard],
        scenes: list[SceneCard],
    ) -> tuple[ReviewResult, ModelUsage]: ...

    def estimate_media(self, media_type: MediaType, shot: Shot) -> ModelUsage: ...

    def generate_media(self, task: MediaTask, shot: Shot) -> tuple[bytes, str, ModelUsage]: ...


class AssetStore(Protocol):
    def put(self, key: str, content: bytes, content_type: str) -> str: ...

    def presign(self, key: str, content_type: str) -> str: ...


@dataclass
class InMemoryAssetStore:
    """Asset store for local development; production can use S3AssetStore."""

    objects: dict[str, bytes]

    def __init__(self) -> None:
        self.objects = {}

    def put(self, key: str, content: bytes, content_type: str) -> str:
        self.objects[key] = content
        return key

    def presign(self, key: str, content_type: str) -> str:
        return f"memory://assets/{key}"


class S3AssetStore:
    def __init__(self, bucket: str, region: str) -> None:
        import boto3

        self.bucket = bucket
        self.client = boto3.client("s3", region_name=region)

    def put(self, key: str, content: bytes, content_type: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content, ContentType=content_type)
        return key

    def presign(self, key: str, content_type: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=900,
        )


class MockModelGateway:
    """Deterministic provider so the entire workflow can be demonstrated offline."""

    provider = "mock-gateway"

    def _usage(self, model: str, input_tokens: int = 300, output_tokens: int = 500) -> ModelUsage:
        return ModelUsage(
            provider=self.provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=round((input_tokens + output_tokens) * 0.000001, 6),
        )

    def analyze_story(self, prompt: str) -> tuple[CreativeBrief, ModelUsage]:
        title = "雨夜来信" if "雨" in prompt else "一封改变命运的信"
        genre = "悬疑短剧" if any(word in prompt for word in ("悬疑", "失踪", "秘密")) else "剧情短剧"
        brief = CreativeBrief(
            title=title,
            logline=prompt.strip(),
            genre=genre,
            tone="写实、电影感、节奏紧凑",
            target_duration_seconds=30,
            requirements=["保持主要角色外观一致", "每个镜头都可独立重试"],
        )
        return brief, self._usage("brief-parser")

    def generate_script(self, brief: CreativeBrief, feedback: str | None = None) -> tuple[ScriptDocument, ModelUsage]:
        title = brief.title
        beats = [
            {
                "id": "beat_01",
                "location": "旧城区巷口",
                "time": "深夜",
                "action": "林夏撑伞走进雨幕，在路灯下发现一封被雨水打湿的信。",
                "dialogue": [],
            },
            {
                "id": "beat_02",
                "location": "旧城区巷口",
                "time": "深夜",
                "action": "她展开信纸，认出失踪三年的哥哥的字迹。",
                "dialogue": ["林夏：这不是他的字……不，这是他的字。"],
            },
            {
                "id": "beat_03",
                "location": "巷口远处",
                "time": "深夜",
                "action": "信中只有一句警告，远处巷口闪过一个黑影。",
                "dialogue": ["旁白：不要相信警察。"],
            },
        ]
        script = ScriptDocument(
            version=2 if feedback else 1,
            title=title,
            logline=brief.logline,
            tone=brief.tone,
            target_duration_seconds=brief.target_duration_seconds,
            beats=beats,
        )
        return script, self._usage("script-writer", 500, 800)

    def generate_characters(self, script: ScriptDocument) -> tuple[list[CharacterCard], ModelUsage]:
        characters = [
            CharacterCard(
                id="character_linxia",
                name="林夏",
                age=24,
                appearance="黑色长发，棕色眼睛，清瘦脸型",
                clothing="米色风衣、深色长裤",
                personality="冷静、敏感，对哥哥失踪一事无法释怀",
                voice="年轻女性，低沉，语速偏慢",
            ),
            CharacterCard(
                id="character_shadow",
                name="远处黑影",
                age=None,
                appearance="身份不明，只呈现模糊轮廓",
                clothing="深色外套",
                personality="沉默、具有威胁感",
                voice="无对白",
            ),
        ]
        return characters, self._usage("character-designer")

    def generate_scenes(self, script: ScriptDocument) -> tuple[list[SceneCard], ModelUsage]:
        scenes = [
            SceneCard(
                id="scene_old_alley",
                name="旧城区巷口",
                description="狭窄的砖墙巷口，地面积水，旧电话亭靠近路灯。",
                time="深夜",
                weather="暴雨",
                lighting="昏黄路灯与蓝色环境光交叠",
                key_objects=["旧电话亭", "信封", "积水", "砖墙"],
            ),
            SceneCard(
                id="scene_alley_distance",
                name="巷口远处",
                description="雨幕遮蔽的狭长巷道，尽头连接另一条昏暗街道。",
                time="深夜",
                weather="暴雨",
                lighting="远处路灯形成逆光剪影",
                key_objects=["巷道", "路灯", "雨幕"],
            ),
        ]
        return scenes, self._usage("scene-designer")

    def generate_storyboard(
        self,
        script: ScriptDocument,
        characters: list[CharacterCard],
        scenes: list[SceneCard],
        feedback: str | None = None,
    ) -> tuple[list[Shot], ModelUsage]:
        shots = [
            Shot(
                id="shot_01",
                sequence=1,
                scene_id="scene_old_alley",
                character_ids=["character_linxia"],
                duration_seconds=6,
                shot_size="远景",
                camera_motion="固定镜头，轻微横摇",
                action="林夏撑伞从画面右侧走入雨夜巷口。",
            ),
            Shot(
                id="shot_02",
                sequence=2,
                scene_id="scene_old_alley",
                character_ids=["character_linxia"],
                duration_seconds=5,
                shot_size="特写",
                camera_motion="从积水缓慢推近信封",
                action="林夏弯腰捡起积水中的信封。",
            ),
            Shot(
                id="shot_03",
                sequence=3,
                scene_id="scene_old_alley",
                character_ids=["character_linxia"],
                duration_seconds=7,
                shot_size="手部特写",
                camera_motion="缓慢推镜",
                action="林夏展开被雨水打湿的信纸。",
                dialogue="林夏：这不是他的字……不，这是他的字。",
            ),
            Shot(
                id="shot_04",
                sequence=4,
                scene_id="scene_alley_distance",
                character_ids=["character_linxia", "character_shadow"],
                duration_seconds=6,
                shot_size="面部近景",
                camera_motion="从林夏面部切到主观视角",
                action="林夏抬头望向巷口，远处黑影一闪而过。",
            ),
        ]
        return shots, self._usage("storyboard-director", 700, 900)

    def optimize_prompts(
        self,
        shots: list[Shot],
        characters: list[CharacterCard],
        scenes: list[SceneCard],
    ) -> tuple[list[Shot], ModelUsage]:
        character_text = "；".join(f"{item.name}：{item.appearance}，{item.clothing}" for item in characters)
        scene_map = {item.id: item for item in scenes}
        optimized: list[Shot] = []
        for shot in shots:
            scene = scene_map[shot.scene_id]
            visual = (
                f"{scene.description} 时间：{scene.time}，天气：{scene.weather}，"
                f"光线：{scene.lighting}。角色约束：{character_text}。"
                f"{shot.action} 景别：{shot.shot_size}。镜头运动：{shot.camera_motion}。"
                "写实电影感，低饱和，保持角色和场景连续性。"
            )
            optimized.append(
                shot.model_copy(
                    update={
                        "image_prompt": visual,
                        "video_prompt": f"{visual} 视频时长约 {shot.duration_seconds} 秒，动作自然，镜头运动稳定。",
                        "voice_text": shot.dialogue,
                    }
                )
            )
        return optimized, self._usage("visual-prompt-optimizer", 800, 1000)

    def review_script(self, script: ScriptDocument) -> tuple[ReviewResult, ModelUsage]:
        if not script.beats:
            return ReviewResult(
                decision="revise",
                score=0.2,
                summary="剧本没有可执行的剧情段落。",
            ), self._usage("script-reviewer", 500, 300)
        return ReviewResult(decision="pass", score=0.94, summary="剧情结构完整，可以进入角色和场景设计。"), self._usage(
            "script-reviewer", 500, 300
        )

    def review_storyboard(
        self,
        shots: list[Shot],
        characters: list[CharacterCard],
        scenes: list[SceneCard],
    ) -> tuple[ReviewResult, ModelUsage]:
        expected_scenes = {scene.id for scene in scenes}
        issues = [
            {"code": "missing_prompt", "message": "分镜缺少视觉 Prompt", "target_id": shot.id}
            for shot in shots
            if not shot.image_prompt or not shot.video_prompt
        ]
        issues.extend(
            {"code": "unknown_scene", "message": "分镜引用了不存在的场景", "target_id": shot.id}
            for shot in shots
            if shot.scene_id not in expected_scenes
        )
        if issues:
            return ReviewResult(decision="revise", score=0.5, issues=issues, summary="存在需要局部修正的分镜。"), self._usage(
                "visual-reviewer", 700, 500
            )
        return ReviewResult(decision="pass", score=0.91, summary="角色、场景和分镜参数满足生成约束。"), self._usage(
            "visual-reviewer", 700, 500
        )

    def estimate_media(self, media_type: MediaType, shot: Shot) -> ModelUsage:
        prices = {MediaType.IMAGE: 0.02, MediaType.VIDEO: 0.18, MediaType.AUDIO: 0.01}
        return ModelUsage(
            provider="mock-gateway",
            model={MediaType.IMAGE: "seedream", MediaType.VIDEO: "seedance", MediaType.AUDIO: "tts"}[media_type],
            estimated_cost=prices[media_type],
        )

    def generate_media(self, task: MediaTask, shot: Shot) -> tuple[bytes, str, ModelUsage]:
        digest = hashlib.sha256(task.idempotency_key.encode()).hexdigest()[:12]
        if task.media_type == MediaType.IMAGE:
            return f"MOCK_IMAGE {shot.id} {digest}\n{shot.image_prompt}".encode(), "image/png", self.estimate_media(
                task.media_type, shot
            )
        if task.media_type == MediaType.VIDEO:
            return f"MOCK_VIDEO {shot.id} {digest}\n{shot.video_prompt}".encode(), "video/mp4", self.estimate_media(
                task.media_type, shot
            )
        return f"MOCK_AUDIO {shot.id} {digest}\n{shot.voice_text}".encode(), "audio/mpeg", self.estimate_media(
            task.media_type, shot
        )

