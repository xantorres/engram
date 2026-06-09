"""Pluggable extractor LLM client (OpenAI-compatible).

Points at any ``/v1/chat/completions`` endpoint - LM Studio, Ollama, or a cloud
provider. Local-first by default: the LM Studio address needs no API key.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class ExtractorConfig:
    base_url: str = "http://localhost:1234/v1"  # LM Studio default
    model: str = "local-model"
    api_key: str | None = None
    timeout: float = 60.0
    temperature: float = 0.0


class Extractor:
    """A thin chat-completions client. Inject an ``httpx.Client`` in tests."""

    def __init__(
        self, config: ExtractorConfig | None = None, *, client: httpx.Client | None = None
    ):
        self.config = config or ExtractorConfig()
        self._client = client

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.config.temperature,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        client = self._client or httpx.Client(timeout=self.config.timeout)
        try:
            resp = client.post(
                f"{self.config.base_url}/chat/completions", json=payload, headers=headers
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        finally:
            if self._client is None:
                client.close()
