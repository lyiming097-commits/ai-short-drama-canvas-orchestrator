from __future__ import annotations

import json
import time
from typing import Any, TypeVar, get_args, get_origin

import httpx
from pydantic import BaseModel, ValidationError

from .domain import (
    CharacterCard,
    CreativeBrief,
    MediaTask,
    MediaType,
    ModelUsage,
    ReviewResult,
    SceneCard,
    ScriptBeat,
    ScriptDocument,
    Shot,
)
from .gateway_admin import ModelGatewayAdmin
from .providers import MockModelGateway

T = TypeVar("T", bound=BaseModel)


class RoutedModelGateway:
    """Routes structured creative-workflow calls to the configured LLM."""

    def __init__(self, admin: ModelGatewayAdmin, fallback: MockModelGateway | None = None) -> None:
        self.admin = admin
        self.fallback = fallback or MockModelGateway()

    def _complete(self, operation: str, prompt: str, schema: str) -> tuple[dict[str, Any], ModelUsage] | None:
        resolved = self.admin.resolve_provider("llm")
        if resolved is None:
            return None
        provider, api_key, model = resolved
        if provider.provider_type not in {"openai", "openai_compatible"}:
            raise RuntimeError(f"Provider type '{provider.provider_type}' has no text generation adapter")

        started = time.perf_counter()
        try:
            response = httpx.post(
                f"{self.admin.normalize_api_base(provider.api_base)}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": f"你是短剧画布素材创作助手。只返回合法 JSON，不要 Markdown。{schema}"},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.4,
                    "max_tokens": 2048,
                },
                timeout=60.0,
                trust_env=False,
            )
            if not response.is_success:
                detail = response.text.replace("\n", " ").strip()[:300]
                raise RuntimeError(f"Model provider returned HTTP {response.status_code}: {detail}")
            payload = response.json()
            choice = payload["choices"][0]
            if choice.get("finish_reason") == "length":
                raise RuntimeError(f"{operation} response exceeded the output token limit")
            content = choice["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            result = self._parse_json_content(content)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(f"{operation} model request failed ({exc.__class__.__name__})") from exc

        usage_data = payload.get("usage") or {}
        usage = ModelUsage(
            provider=provider.name,
            provider_id=provider.id,
            capability="llm",
            model=model,
            input_tokens=int(usage_data.get("prompt_tokens", usage_data.get("input_tokens", 0)) or 0),
            output_tokens=int(usage_data.get("completion_tokens", usage_data.get("output_tokens", 0)) or 0),
            actual_cost=usage_data.get("cost"),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return result, usage

    @staticmethod
    def _json(value: BaseModel | list[BaseModel]) -> str:
        if isinstance(value, list):
            return json.dumps([item.model_dump(mode="json") for item in value], ensure_ascii=False)
        return value.model_dump_json()

    @staticmethod
    def _parse_json_content(content: Any) -> Any:
        """Parse strict JSON plus fenced or prose-wrapped gateway responses."""
        text = str(content).strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
            end = max(text.rfind("}"), text.rfind("]"))
            if not starts or end <= min(starts):
                raise
            return json.loads(text[min(starts) : end + 1])

    @staticmethod
    def _unwrap_payload(data: Any, key: str | None = None) -> Any:
        if not isinstance(data, dict):
            return data
        for alias in (key, "data", "result", "output"):
            if alias and alias in data and isinstance(data[alias], (dict, list)):
                return data[alias]
        return data

    def _single(self, operation: str, prompt: str, schema: str, model: type[T]) -> tuple[T, ModelUsage] | None:
        model_schema = json.dumps(model.model_json_schema(), ensure_ascii=False)
        result = self._complete(operation, prompt, f"{schema} Match this JSON Schema exactly: {model_schema}")
        if result is None:
            return None
        data, usage = result
        try:
            return model.model_validate(data), usage
        except ValidationError:
            normalized = self._normalize_model(
                self._unwrap_payload(data, model.__name__.removesuffix("Document").lower()), model
            )
            try:
                return model.model_validate(normalized), usage
            except ValidationError as normalized_exc:
                details = "; ".join(
                    f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                    for item in normalized_exc.errors(include_url=False)[:8]
                )
                raise RuntimeError(f"{operation} returned invalid structured data: {details}") from normalized_exc

    @staticmethod
    def _normalize_model(data: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise TypeError(f"expected object for {model.__name__}")
        normalized = dict(data)
        aliases = {
            "duration": "target_duration_seconds",
            "target_duration": "target_duration_seconds",
            "duration_seconds": "target_duration_seconds",
            "description_logline": "logline",
            "summary": "logline",
            "name": "title",
            "style": "tone",
            "character_list": "characters",
            "scene_list": "scenes",
            "shot_list": "shots",
        }
        for source, target in aliases.items():
            if target not in normalized and source in normalized:
                normalized[target] = normalized[source]
        if model is ScriptDocument:
            if "beats" not in normalized and isinstance(normalized.get("scenes"), list):
                normalized["beats"] = normalized["scenes"]
            normalized.setdefault("version", 1)
            normalized.setdefault("tone", "")
            normalized.setdefault("target_duration_seconds", 30)
        if model is ScriptBeat:
            for source, target in {
                "scene": "location",
                "setting": "location",
                "period": "time",
                "description": "action",
                "event": "action",
                "lines": "dialogue",
            }.items():
                if target not in normalized and source in normalized:
                    normalized[target] = normalized[source]
        for name, field in model.model_fields.items():
            value = normalized.get(name)
            annotation = field.annotation
            if get_origin(annotation) is list and isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except ValueError:
                    parsed = None
                value = parsed if isinstance(parsed, list) else [value]
            if get_origin(annotation) is list and value is None:
                value = []
            if get_origin(annotation) is list and isinstance(value, list):
                item_type = get_args(annotation)[0]
                if isinstance(item_type, type) and issubclass(item_type, BaseModel):
                    value = [
                        RoutedModelGateway._normalize_model(item, item_type)
                        if isinstance(item, dict)
                        else item
                        for item in value
                    ]
            elif isinstance(value, dict) and isinstance(annotation, type) and issubclass(annotation, BaseModel):
                value = RoutedModelGateway._normalize_model(value, annotation)
            normalized[name] = value
        return normalized

    @classmethod
    def _validated_list(cls, data: dict[str, Any], key: str, model: type[T], operation: str, usage: ModelUsage) -> tuple[list[T], ModelUsage]:
        payload = cls._unwrap_payload(data, key)
        if isinstance(payload, list):
            values = payload
        elif isinstance(payload, dict):
            values = payload.get(key) or payload.get(key.rstrip("s"))
        else:
            values = None
        if not isinstance(values, list):
            raise RuntimeError(f"{operation} returned an invalid {key} collection")
        try:
            return [model.model_validate(item) for item in values], usage
        except ValidationError:
            try:
                normalized_values = [cls._normalize_model(item, model) for item in values if isinstance(item, dict)]
                if len(normalized_values) != len(values):
                    raise TypeError(f"{key} must contain objects")
                return [model.model_validate(item) for item in normalized_values], usage
            except ValidationError as exc:
                details = "; ".join(
                    f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                    for item in exc.errors(include_url=False)[:8]
                )
                raise RuntimeError(f"{operation} returned invalid structured data: {details}") from exc

    def analyze_story(self, prompt: str) -> tuple[CreativeBrief, ModelUsage]:
        result = self._single("story analysis", f"理解以下剧情创意并整理成创作简报：\n{prompt}", "字段：title, logline, genre, tone, target_duration_seconds, requirements。", CreativeBrief)
        return result if result is not None else self.fallback.analyze_story(prompt)

    def generate_script(self, brief: CreativeBrief, feedback: str | None = None) -> tuple[ScriptDocument, ModelUsage]:
        prompt = f"根据创作简报生成短剧剧本。\n简报：{self._json(brief)}\n修改意见：{feedback or '无'}"
        result = self._single("script generation", prompt, "字段：version, title, logline, tone, target_duration_seconds, beats。每个 beat 字段为 id, location, time, action, dialogue。", ScriptDocument)
        return result if result is not None else self.fallback.generate_script(brief, feedback)

    def generate_characters(self, script: ScriptDocument) -> tuple[list[CharacterCard], ModelUsage]:
        prompt = f"根据剧本设计主要角色卡，角色 id 使用稳定的英文标识。\n剧本：{self._json(script)}"
        result = self._complete("character design", prompt, "返回对象 {characters:[...]}；角色字段：id, name, age, appearance, clothing, personality, voice。")
        if result is None:
            return self.fallback.generate_characters(script)
        data, usage = result
        return self._validated_list(data, "characters", CharacterCard, "character design", usage)

    def generate_scenes(self, script: ScriptDocument) -> tuple[list[SceneCard], ModelUsage]:
        prompt = f"根据剧本设计场景卡，每个场景分配稳定英文 id。\n剧本：{self._json(script)}"
        result = self._complete("scene design", prompt, "返回对象 {scenes:[...]}；场景字段：id, name, description, time, weather, lighting, key_objects。")
        if result is None:
            return self.fallback.generate_scenes(script)
        data, usage = result
        return self._validated_list(data, "scenes", SceneCard, "scene design", usage)

    def generate_storyboard(self, script: ScriptDocument, characters: list[CharacterCard], scenes: list[SceneCard], feedback: str | None = None) -> tuple[list[Shot], ModelUsage]:
        prompt = f"根据剧本拆分画布分镜。scene_id 和 character_ids 必须引用给定 id。\n剧本：{self._json(script)}\n角色：{self._json(characters)}\n场景：{self._json(scenes)}\n修改意见：{feedback or '无'}"
        result = self._complete("storyboard generation", prompt, "返回对象 {shots:[...]}；分镜字段：id, sequence, scene_id, character_ids, duration_seconds, shot_size, camera_motion, action, dialogue。")
        if result is None:
            return self.fallback.generate_storyboard(script, characters, scenes, feedback)
        data, usage = result
        return self._validated_list(data, "shots", Shot, "storyboard generation", usage)

    def optimize_prompts(self, shots: list[Shot], characters: list[CharacterCard], scenes: list[SceneCard]) -> tuple[list[Shot], ModelUsage]:
        prompt = f"为画布中的每个分镜补充可直接用于图片生成的 image_prompt，并保留其余字段和 id。视频提示词仅作为可选字段，不代表要生成视频。\n分镜：{self._json(shots)}\n角色：{self._json(characters)}\n场景：{self._json(scenes)}"
        result = self._complete("visual prompt optimization", prompt, "返回对象 {shots:[完整分镜对象...]}；image_prompt 必须包含主体外观、服装、场景、光线和构图。")
        if result is None:
            return self.fallback.optimize_prompts(shots, characters, scenes)
        data, usage = result
        return self._validated_list(data, "shots", Shot, "visual prompt optimization", usage)

    def review_script(self, script: ScriptDocument) -> tuple[ReviewResult, ModelUsage]:
        result = self._single("script review", f"检查短剧剧本的可执行性、人物动机和场景连贯性。\n{self._json(script)}", "字段：decision(pass/revise/human_review), score, issues, summary。", ReviewResult)
        return result if result is not None else self.fallback.review_script(script)

    def review_storyboard(self, shots: list[Shot], characters: list[CharacterCard], scenes: list[SceneCard]) -> tuple[ReviewResult, ModelUsage]:
        prompt = f"检查分镜是否引用有效角色和场景、画面提示是否可执行。\n分镜：{self._json(shots)}\n角色：{self._json(characters)}\n场景：{self._json(scenes)}"
        result = self._single("storyboard review", prompt, "字段：decision(pass/revise/human_review), score, issues, summary。issue 字段：code, message, target_id, suggestion。", ReviewResult)
        return result if result is not None else self.fallback.review_storyboard(shots, characters, scenes)

    def estimate_media(self, media_type: MediaType, shot: Shot) -> ModelUsage:
        return self.fallback.estimate_media(media_type, shot)

    def generate_media(self, task: MediaTask, shot: Shot) -> tuple[bytes, str, ModelUsage]:
        return self.fallback.generate_media(task, shot)
