"""Azure Foundry extraction using the OpenAI-compatible v1 API."""

from __future__ import annotations

import hashlib
from copy import deepcopy

import httpx

from whitespace.extract.errors import ExtractorStop
from whitespace.extract.gemini import SCHEMA, SYSTEM, GeminiExtractor

DEFAULT_MODEL = "gpt-4.1-mini"
PROMPT_VERSION = "azure-v3"


def _strict_schema(schema: dict) -> dict:
    """Azure structured outputs require additionalProperties on every object."""
    schema = deepcopy(schema)

    def visit(node: dict) -> None:
        if node.get("type") == "object":
            node["additionalProperties"] = False
            for prop in node.get("properties", {}).values():
                visit(prop)
        if node.get("type") == "array":
            visit(node["items"])

    visit(schema)
    return schema


class AzureExtractor(GeminiExtractor):
    """Reuse validation, batching and caching with a Foundry deployment."""

    def __init__(self, db, api_key: str, endpoint: str, model: str = DEFAULT_MODEL, **kwargs):
        super().__init__(db, api_key, model=model, **kwargs)
        self.endpoint = endpoint.rstrip("/")
        if not self.endpoint.endswith("/openai/v1"):
            self.endpoint += "/openai/v1"
        self.version = f"{PROMPT_VERSION}:{model}"

    def _key(self, cleaned: str) -> str:
        return hashlib.sha1(f"{self.version}|{self.endpoint}|{cleaned}".encode()).hexdigest()

    def _body(
        self,
        content: str,
        system: str = SYSTEM,
        schema: dict | None = None,
        max_tokens: int = 1500,
    ) -> dict:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "max_completion_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "extraction",
                    "strict": True,
                    "schema": _strict_schema(schema or SCHEMA),
                },
            },
        }

    def _generate(self, body: dict) -> str:
        last = ""
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.client.post(
                    f"{self.endpoint}/chat/completions",
                    json=body,
                    headers={"api-key": self.api_key, "Content-Type": "application/json"},
                )
            except httpx.TransportError as exc:
                last = f"network error: {exc!r}"
                self.stats["retries"] += 1
                if attempt < self.max_retries:
                    self.sleep(2 ** (attempt + 1))
                continue
            if resp.status_code == 200:
                try:
                    choice = resp.json()["choices"][0]
                    message = choice["message"]
                    text = message.get("content")
                    if (
                        choice.get("finish_reason") != "stop"
                        or not isinstance(text, str)
                        or not text
                    ):
                        raise ValueError("incomplete or refused response")
                    return text
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    raise ExtractorStop(
                        f"Azure Foundry returned an invalid response: {exc}"
                    ) from exc
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"Azure Foundry error {resp.status_code}"
                self.stats["retries"] += 1
                if attempt < self.max_retries:
                    try:
                        wait = float(resp.headers.get("retry-after-ms", "")) / 1000
                    except ValueError:
                        try:
                            wait = float(resp.headers.get("retry-after", ""))
                        except ValueError:
                            wait = 30.0 if resp.status_code == 429 else 2 ** (attempt + 1)
                    self.sleep(max(0, wait))
                continue
            raise ExtractorStop(
                f"Azure Foundry API error {resp.status_code}: {resp.text[:300]} "
                "(check AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, and deployment name)"
            )
        raise ExtractorStop(f"giving up after {self.max_retries + 1} attempts: {last}")
