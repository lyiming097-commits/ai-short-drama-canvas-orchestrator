from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .domain import CreateModelProvider, CreateModelRoute, ModelProvider, ModelRoute, ModelUsage, UpdateModelProvider, UsageRecord, new_id
from .repositories import InMemoryRepository
from .security import CredentialVault


class ModelGatewayAdmin:
    """Provider and routing management for the admin console.

    Provider-specific generation adapters remain behind ModelGateway. This
    service owns credentials, routes, connection checks, and observability.
    """

    def __init__(self, repo: InMemoryRepository, vault: CredentialVault) -> None:
        self.repo = repo
        self.vault = vault

    @staticmethod
    def normalize_api_base(value: str) -> str:
        base = value.strip().rstrip("/")
        if not base:
            return base
        parsed = urlsplit(base)
        hostname = (parsed.hostname or "").lower()
        netloc = parsed.netloc
        if hostname == "platform.deepseek.com":
            netloc = parsed.netloc.replace("platform.deepseek.com", "api.deepseek.com")
        path = parsed.path.rstrip("/")
        for suffix in ("/chat/completions", "/models"):
            if path.endswith(suffix):
                path = path[: -len(suffix)]
                break
        return urlunsplit((parsed.scheme, netloc, path, "", "")).rstrip("/")

    @staticmethod
    def public(provider: ModelProvider) -> dict[str, Any]:
        data = provider.model_dump(mode="json")
        data.pop("encrypted_api_key", None)
        data["api_base"] = ModelGatewayAdmin.normalize_api_base(data["api_base"])
        return data

    def create_provider(self, body: CreateModelProvider) -> dict[str, Any]:
        provider = ModelProvider(
            name=body.name,
            provider_type=body.provider_type,
            capabilities=body.capabilities,
            api_base=self.normalize_api_base(body.api_base),
            model_name=body.model_name,
            encrypted_api_key=self.vault.encrypt(body.api_key),
            masked_api_key=self.vault.mask(body.api_key),
            enabled=body.enabled,
            priority=body.priority,
        )
        return self.public(self.repo.create_provider(provider))

    def list_providers(self) -> list[dict[str, Any]]:
        return [self.public(item) for item in self.repo.list_providers()]

    def update_provider(self, provider_id: str, body: UpdateModelProvider) -> dict[str, Any]:
        current = self.repo.get_provider(provider_id)
        if current is None:
            raise KeyError("model provider not found")
        changes = body.model_dump(exclude_none=True)
        api_key = changes.pop("api_key", None)
        if "api_base" in changes:
            changes["api_base"] = self.normalize_api_base(str(changes["api_base"]))
        if api_key:
            changes["encrypted_api_key"] = self.vault.encrypt(api_key)
            changes["masked_api_key"] = self.vault.mask(api_key)
        return self.public(self.repo.update_provider(provider_id, **changes))

    def delete_provider(self, provider_id: str) -> None:
        if self.repo.get_provider(provider_id) is None:
            raise KeyError("model provider not found")
        self.repo.delete_provider(provider_id)

    def create_route(self, body: CreateModelRoute) -> dict[str, Any]:
        if self.repo.get_provider(body.provider_id) is None:
            raise KeyError("model provider not found")
        route = ModelRoute(**body.model_dump())
        provider = self.repo.get_provider(route.provider_id)
        self.repo.create_route(route)
        return route.model_dump(mode="json") | {"provider": provider.name if provider else "unknown"}

    def list_routes(self) -> list[dict[str, Any]]:
        providers = {item.id: item for item in self.repo.list_providers()}
        return [route.model_dump(mode="json") | {"provider": providers.get(route.provider_id).name if providers.get(route.provider_id) else "unknown"} for route in self.repo.list_routes()]

    def resolve_provider(self, capability: str) -> tuple[ModelProvider, str, str] | None:
        providers = {item.id: item for item in self.repo.list_providers() if item.enabled}
        routes = [route for route in self.repo.list_routes() if route.enabled and route.capability == capability]
        for route in routes:
            provider = providers.get(route.provider_id)
            if provider:
                return provider, self.vault.decrypt(provider.encrypted_api_key), route.model_name
        candidates = sorted(providers.values(), key=lambda item: (item.provider_type == "mock", item.priority, -item.updated_at.timestamp()))
        for provider in candidates:
            if capability in provider.capabilities:
                return provider, self.vault.decrypt(provider.encrypted_api_key), provider.model_name
        return None

    def test_connection(self, provider_id: str) -> dict[str, Any]:
        return self.test_connection_detailed(provider_id)

    def test_connection_detailed(self, provider_id: str) -> dict[str, Any]:
        return self._probe_connection(provider_id)
        provider = self.repo.get_provider(provider_id)
        if provider is None:
            raise KeyError("model provider not found")
        if provider.provider_type == "mock":
            return {"ok": True, "provider_id": provider.id, "message": "模拟供应商连接正常", "latency_ms": 1}
        api_key = self.vault.decrypt(provider.encrypted_api_key)
        started = time.perf_counter()
        endpoint = provider.api_base
        if provider.provider_type in {"openai", "openai_compatible"}:
            endpoint = f"{endpoint}/models"
        try:
            response = httpx.get(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "X-API-Key": api_key},
                timeout=10.0,
            )
            latency_ms = round((time.perf_counter() - started) * 1000)
            ok = 200 <= response.status_code < 300
            return {
                "ok": ok,
                "provider_id": provider.id,
                "endpoint": endpoint,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
                "message": "连接成功" if ok else f"供应商返回 HTTP {response.status_code}",
            }
        except httpx.HTTPError as exc:
            return {
                "ok": False,
                "provider_id": provider.id,
                "endpoint": endpoint,
                "error_type": exc.__class__.__name__,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "message": f"连接失败：{exc.__class__.__name__}",
            }

    def usage_summary(self, run_id: str | None = None) -> dict[str, Any]:
        records = self.repo.list_usage(run_id)
        total_input = sum(item.input_tokens for item in records)
        total_output = sum(item.output_tokens for item in records)
        estimated = round(sum(item.estimated_cost for item in records), 6)
        actual_values = [item.actual_cost for item in records if item.actual_cost is not None]
        by_model: dict[str, dict[str, Any]] = {}
        for item in records:
            key = f"{item.provider}/{item.model}"
            bucket = by_model.setdefault(key, {"provider": item.provider, "model": item.model, "calls": 0, "tokens": 0, "cost": 0.0})
            bucket["calls"] += 1
            bucket["tokens"] += item.total_tokens
            bucket["cost"] = round(bucket["cost"] + (item.actual_cost if item.actual_cost is not None else item.estimated_cost), 6)
        return {
            "calls": len(records),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "estimated_cost": estimated,
            "actual_cost": round(sum(actual_values), 6) if actual_values else None,
            "by_model": list(by_model.values()),
            "records": [item.model_dump(mode="json") for item in records[-100:]],
        }

    def _probe_connection(self, provider_id: str) -> dict[str, Any]:
        provider = self.repo.get_provider(provider_id)
        if provider is None:
            raise KeyError("model provider not found")
        if provider.provider_type == "mock":
            return {"ok": True, "provider_id": provider.id, "endpoint": provider.api_base, "message": "mock provider is ready"}

        base = self.normalize_api_base(provider.api_base)
        api_key = self.vault.decrypt(provider.encrypted_api_key)
        headers = {"Authorization": f"Bearer {api_key}", "X-API-Key": api_key}
        endpoint = f"{base}/models" if provider.provider_type in {"openai", "openai_compatible"} else base
        started = time.perf_counter()
        try:
            response = httpx.get(endpoint, headers=headers, timeout=10.0, follow_redirects=True, trust_env=False)
            if response.status_code in {404, 405, 501} and provider.provider_type in {"openai", "openai_compatible"}:
                endpoint = f"{base}/chat/completions"
                response = httpx.post(
                    endpoint,
                    headers={**headers, "Content-Type": "application/json"},
                    json={"model": provider.model_name, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                    timeout=20.0,
                    follow_redirects=True,
                    trust_env=False,
                )
            latency_ms = round((time.perf_counter() - started) * 1000)
            status = response.status_code
            if 200 <= status < 300:
                return {"ok": True, "provider_id": provider.id, "endpoint": endpoint, "status_code": status, "latency_ms": latency_ms, "message": "Connection succeeded."}
            if status in {401, 403}:
                message = "The server is reachable, but the API key is invalid or unauthorized."
            elif status in {400, 422}:
                message = "The server is reachable, but it rejected the model name or request parameters."
            elif status == 404:
                message = "The server is reachable, but this API path was not found. Check the provider API Base URL."
            else:
                message = f"The provider returned HTTP {status}."
            return {"ok": False, "provider_id": provider.id, "endpoint": endpoint, "status_code": status, "latency_ms": latency_ms, "message": message}
        except httpx.ConnectError:
            message = "Cannot connect to the host. Check the API Base URL, DNS, outbound TCP 443, or HTTPS proxy."
            error_type = "ConnectError"
        except httpx.TimeoutException:
            message = "Connection timed out. Check the network route or HTTPS proxy."
            error_type = "Timeout"
        except httpx.ProxyError:
            message = "The configured proxy could not be reached or rejected the request."
            error_type = "ProxyError"
        except httpx.ConnectTimeout:
            message = "Timed out while opening the network connection. Check firewall and proxy settings."
            error_type = "ConnectTimeout"
        except httpx.HTTPError as exc:
            message = f"HTTP transport error: {exc.__class__.__name__}."
            error_type = exc.__class__.__name__
        return {"ok": False, "provider_id": provider.id, "endpoint": endpoint, "error_type": error_type, "latency_ms": round((time.perf_counter() - started) * 1000), "message": message}

    def record_usage(self, run_id: str | None, usage: ModelUsage, capability: str, status: str = "succeeded") -> UsageRecord:
        record = UsageRecord(
            run_id=run_id,
            provider_id=usage.provider_id,
            provider=usage.provider,
            capability=usage.capability or capability,
            model=usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.input_tokens + usage.output_tokens,
            estimated_cost=usage.estimated_cost,
            actual_cost=usage.actual_cost,
            latency_ms=usage.latency_ms,
            status=status,
        )
        return self.repo.record_usage(record)

