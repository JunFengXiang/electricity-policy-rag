"""Small OpenAI-compatible LLM client used by the local QA service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class LlmSettings:
    provider: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_context_chunks: int

    @classmethod
    def from_env(cls) -> "LlmSettings":
        load_env_file()
        return cls(
            provider=os.getenv("LLM_PROVIDER", "openai_compatible"),
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            api_key=os.getenv("LLM_API_KEY", ""),
            model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.2") or "0.2"),
            max_context_chunks=max(1, int(os.getenv("LLM_MAX_CONTEXT_CHUNKS", "8") or "8")),
        )


def chat_completion(messages: list[dict[str, str]], settings: LlmSettings | None = None, timeout: float = 60.0) -> str:
    settings = settings or LlmSettings.from_env()
    if settings.provider != "openai_compatible":
        raise RuntimeError(f"暂不支持的 LLM_PROVIDER：{settings.provider}")
    if not settings.api_key:
        raise RuntimeError("未配置 LLM_API_KEY")

    url = f"{settings.base_url}/chat/completions"
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": settings.temperature,
    }
    headers = {"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    return data["choices"][0]["message"]["content"].strip()
