from __future__ import annotations

import json
import os
from urllib import error, request

from app.config import load_env
from app.db import get_setting, set_setting


load_env()

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
SUPPORTED_PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "default_model": "gpt-5-mini",
        "model_env": "OPENAI_MODEL",
        "key_envs": ["OPENAI_API_KEY"],
        "base_url_env": "OPENAI_BASE_URL",
        "default_base_url": DEFAULT_OPENAI_BASE_URL,
    },
    "minimax": {
        "label": "MiniMax",
        "default_model": "MiniMax-M2.7",
        "model_env": "MINIMAX_MODEL",
        "key_envs": ["MINIMAX_API_KEY"],
        "base_url_env": "MINIMAX_BASE_URL",
        "default_base_url": DEFAULT_MINIMAX_BASE_URL,
    },
}


class AIProviderError(RuntimeError):
    def __init__(self, provider: str, message: str, status_code: int | None = None, details: dict | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.details = details or {}


def normalize_provider(provider: str | None) -> str:
    key = (provider or "").strip().lower()
    if key in SUPPORTED_PROVIDERS:
        return key
    return "openai"


def get_provider_meta(provider: str) -> dict:
    normalized = normalize_provider(provider)
    meta = dict(SUPPORTED_PROVIDERS[normalized])
    meta["id"] = normalized
    return meta


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _pick_api_key(meta: dict) -> tuple[str | None, str | None]:
    for env_name in meta["key_envs"]:
        value = _env_value(env_name)
        if value:
            return value, env_name
    return None, None


def list_provider_configs() -> list[dict]:
    providers = []
    for provider in SUPPORTED_PROVIDERS:
        meta = get_provider_meta(provider)
        api_key, key_env = _pick_api_key(meta)
        providers.append(
            {
                "id": provider,
                "label": meta["label"],
                "default_model": meta["default_model"],
                "env_model": _env_value(meta["model_env"]),
                "configured": bool(api_key),
                "active_key_env": key_env,
                "key_envs": meta["key_envs"],
                "base_url": _env_value(meta["base_url_env"]) or meta["default_base_url"],
            }
        )
    return providers


def infer_provider_from_env() -> str:
    provider = _env_value("AI_PROVIDER")
    if provider:
        return normalize_provider(provider)
    if _env_value("MINIMAX_API_KEY") and not _env_value("OPENAI_API_KEY"):
        return "minimax"
    return "openai"


def get_current_ai_config(connection=None, include_api_key: bool = False) -> dict:
    provider = get_setting(connection, "ai_provider") if connection else None
    provider = normalize_provider(provider or infer_provider_from_env())
    meta = get_provider_meta(provider)
    model = get_setting(connection, "ai_model") if connection else None
    model = model or _env_value("AI_MODEL") or _env_value(meta["model_env"]) or meta["default_model"]
    api_key, key_env = _pick_api_key(meta)
    base_url = _env_value(meta["base_url_env"]) or meta["default_base_url"]
    config = {
        "provider": provider,
        "provider_label": meta["label"],
        "model": model,
        "configured": bool(api_key),
        "api_key_env": key_env,
        "base_url": base_url,
        "available_providers": list_provider_configs(),
    }
    if include_api_key:
        config["api_key"] = api_key
    return config


def save_ai_config(connection, provider: str, model: str | None) -> dict:
    normalized = normalize_provider(provider)
    model_value = (model or "").strip() or get_provider_meta(normalized)["default_model"]
    set_setting(connection, "ai_provider", normalized)
    set_setting(connection, "ai_model", model_value)
    return get_current_ai_config(connection)


def _load_json_response(response) -> dict:
    return json.loads(response.read().decode("utf-8"))


def _raise_http_error(provider: str, exc: error.HTTPError) -> None:
    raw = exc.read().decode("utf-8", errors="ignore")
    details = {}
    try:
        details = json.loads(raw)
    except json.JSONDecodeError:
        details = {"raw": raw}
    message = raw[:500] or f"{provider} 请求失败"
    if isinstance(details, dict):
        if provider == "openai":
            message = details.get("error", {}).get("message") or message
        if provider == "minimax":
            message = (
                details.get("base_resp", {}).get("status_msg")
                or details.get("message")
                or details.get("error")
                or message
            )
    raise AIProviderError(provider, message, status_code=exc.code, details=details) from exc


def _extract_json_text(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end >= start:
        return cleaned[start : end + 1]
    return cleaned


def _extract_openai_text(payload: dict) -> str:
    if payload.get("output_text"):
        return str(payload["output_text"])
    parts = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _call_openai(prompt: str, config: dict) -> dict:
    url = f"{config['base_url'].rstrip('/')}/responses"
    body = {
        "model": config["model"],
        "input": prompt,
        "text": {"format": {"type": "json_object"}},
    }
    req = request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            payload = _load_json_response(response)
    except error.HTTPError as exc:
        _raise_http_error("openai", exc)
    except error.URLError as exc:
        raise AIProviderError("openai", f"网络请求失败：{exc.reason}") from exc

    return {
        "provider": "openai",
        "provider_label": "OpenAI",
        "model": payload.get("model") or config["model"],
        "text": _extract_openai_text(payload),
        "raw_payload": payload,
    }


def _call_minimax(prompt: str, config: dict) -> dict:
    url = f"{config['base_url'].rstrip('/')}/text/chatcompletion_v2"
    body = {
        "model": config["model"],
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "name": "FundStrategyCopilot",
                "content": "你是一名谨慎、专业、解释型的基金分析助手，只能基于提供的数据输出 JSON。",
            },
            {
                "role": "user",
                "name": "Investor",
                "content": prompt,
            },
        ],
    }
    req = request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            payload = _load_json_response(response)
    except error.HTTPError as exc:
        _raise_http_error("minimax", exc)
    except error.URLError as exc:
        raise AIProviderError("minimax", f"网络请求失败：{exc.reason}") from exc

    base_resp = payload.get("base_resp", {})
    if base_resp.get("status_code") not in (0, None):
        raise AIProviderError(
            "minimax",
            base_resp.get("status_msg") or "MiniMax 返回异常",
            status_code=base_resp.get("status_code"),
            details=payload,
        )

    choices = payload.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    text = message.get("content", "")
    return {
        "provider": "minimax",
        "provider_label": "MiniMax",
        "model": payload.get("model") or config["model"],
        "text": text,
        "raw_payload": payload,
    }


def request_json_completion(prompt: str, connection=None) -> dict:
    config = get_current_ai_config(connection, include_api_key=True)
    if not config["configured"] or not config.get("api_key"):
        raise AIProviderError(
            config["provider"],
            f"{config['provider_label']} 的 API Key 未配置。",
            details={"configured": False},
        )

    if config["provider"] == "minimax":
        result = _call_minimax(prompt, config)
    else:
        result = _call_openai(prompt, config)

    text = _extract_json_text(result["text"])
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIProviderError(
            config["provider"],
            "模型返回的内容不是合法 JSON。",
            details={"raw_text": result["text"][:1000]},
        ) from exc

    result["parsed"] = parsed
    return result


def test_ai_provider(connection=None) -> dict:
    prompt = '请只返回一个 JSON：{"ok": true, "message": "pong"}'
    result = request_json_completion(prompt, connection)
    return {
        "provider": result["provider"],
        "provider_label": result["provider_label"],
        "model": result["model"],
        "parsed": result["parsed"],
    }
