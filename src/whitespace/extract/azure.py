"""Azure Foundry extraction using the OpenAI-compatible v1 API."""

from __future__ import annotations

import hashlib
import sys
from copy import deepcopy

import httpx

from whitespace.extract import rules
from whitespace.extract.errors import ExtractorStop
from whitespace.extract.llm import MAX_CHARS, SCHEMA, SYSTEM, LLMExtractor
from whitespace.extract.quota import DEFAULT_RPM, DEFAULT_TPM, TokenRateLimiter, estimated_tokens

DEFAULT_MODEL = "gpt-4.1-mini"
PROMPT_VERSION = "azure-v3"


class _ContentFiltered(Exception):
    """Azure blocked this request because of its content."""


def _is_content_filter(resp: httpx.Response) -> bool:
    if resp.status_code != 400:
        return False
    try:
        payload = resp.json()
    except ValueError:
        return False
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return False
    inner = error.get("innererror") or {}
    code = str(error.get("code") or "").lower()
    inner_code = str(inner.get("code") or "").lower() if isinstance(inner, dict) else ""
    message = str(error.get("message") or "").lower()
    return (
        code in {"content_filter", "contentfilter"}
        or inner_code == "responsibleaipolicyviolation"
        or ("filtered" in message and "content management policy" in message)
    )


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


class AzureExtractor(LLMExtractor):
    """Reuse validation, batching and caching with a Foundry deployment."""

    def __init__(
        self,
        db,
        api_key: str,
        endpoint: str,
        model: str = DEFAULT_MODEL,
        tpm: int = DEFAULT_TPM,
        **kwargs,
    ):
        kwargs.setdefault("rpm", DEFAULT_RPM)
        super().__init__(db, api_key, model=model, **kwargs)
        self.token_limiter = TokenRateLimiter(tpm, self.limiter, sleep=self.sleep)
        self.endpoint = endpoint.rstrip("/")
        if not self.endpoint.endswith("/openai/v1"):
            self.endpoint += "/openai/v1"
        self.version = f"{PROMPT_VERSION}:{model}" + (":long-v1" if self.long_posts else "")
        self.stats.update(filtered_posts=0, filtered_batches=0)
        self.stats.update(prompt_tokens=0, completion_tokens=0, usage_reported=0, usage_missing=0)

    def __call__(self, text: str):
        try:
            return super().__call__(text)
        except _ContentFiltered:
            cleaned = rules.strip_markup(text)[:MAX_CHARS]
            # Remember the skip so resumed runs don't repeatedly send the same blocked post.
            self.db.llm_cache_put(self._key(cleaned), self.model, '{"items": [], "filtered": true}')
            self.stats["filtered_posts"] += 1
            print("Azure content filter: skipped one post.", file=sys.stderr)
            return []

    def _run_chunk(self, chunk: list[int], cleaned: list[str], results: list) -> None:
        try:
            super()._run_chunk(chunk, cleaned, results)
        except _ContentFiltered:
            self.stats["filtered_batches"] += 1
            print("Azure content filter: checking batch posts individually.", file=sys.stderr)
            for i in chunk:
                results[i] = self(cleaned[i])

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
            self.token_limiter.wait(estimated_tokens(body))
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
                    payload = resp.json()
                    usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
                    prompt = usage.get("prompt_tokens") if isinstance(usage, dict) else None
                    completion = usage.get("completion_tokens") if isinstance(usage, dict) else None
                    if (
                        isinstance(prompt, int)
                        and not isinstance(prompt, bool)
                        and prompt >= 0
                        and isinstance(completion, int)
                        and not isinstance(completion, bool)
                        and completion >= 0
                    ):
                        self.stats["prompt_tokens"] += prompt
                        self.stats["completion_tokens"] += completion
                        self.stats["usage_reported"] += 1
                    else:
                        self.stats["usage_missing"] += 1
                    choice = payload["choices"][0]
                    if choice.get("finish_reason") == "content_filter":
                        raise _ContentFiltered
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
            if _is_content_filter(resp):
                raise _ContentFiltered
            raise ExtractorStop(
                f"Azure Foundry API error {resp.status_code}: {resp.text[:300]} "
                "(check AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, and deployment name)"
            )
        raise ExtractorStop(f"giving up after {self.max_retries + 1} attempts: {last}")
