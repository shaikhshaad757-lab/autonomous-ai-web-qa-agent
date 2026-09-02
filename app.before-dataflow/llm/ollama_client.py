"""Thin abstraction over a local Ollama instance.

Design rules (per project spec):
- Always request structured JSON output and validate it with Pydantic.
- Never allow raw LLM output to execute arbitrary shell commands.
- Never send credentials to the model.
- Fail loudly (raise) rather than silently returning made-up data if the
  model output doesn't validate — callers must handle LLMValidationError.
"""
from __future__ import annotations

import json
import logging
from typing import Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger("hms_qa_agent.llm")

T = TypeVar("T", bound=BaseModel)

_JSON_SYSTEM_SUFFIX = (
    "\n\nRespond with ONLY a single valid JSON object matching the requested "
    "schema. No prose, no markdown code fences, no explanation before or "
    "after the JSON."
)


class LLMValidationError(RuntimeError):
    """Raised when the model's JSON output doesn't validate against the
    expected Pydantic schema, even after one retry."""


class OllamaClient:
    def __init__(
        self,
        host: str,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        timeout_seconds: int = 60,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

    def _strip_code_fences(self, text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    async def _raw_generate(self, prompt: str) -> str:
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "")

    async def generate_structured(self, prompt: str, schema: Type[T]) -> T:
        """Call Ollama and validate the JSON response against `schema`.

        Retries once with a corrective prompt if the first response fails to
        parse or validate.
        """
        full_prompt = prompt + _JSON_SYSTEM_SUFFIX
        last_error: Exception | None = None

        for attempt in range(2):
            try:
                raw = await self._raw_generate(full_prompt)
                cleaned = self._strip_code_fences(raw)
                parsed = json.loads(cleaned)
                return schema.model_validate(parsed)
            except (json.JSONDecodeError, ValidationError, httpx.HTTPError) as exc:
                last_error = exc
                logger.warning("Ollama structured output attempt %d failed: %s", attempt + 1, exc)
                full_prompt = (
                    prompt
                    + _JSON_SYSTEM_SUFFIX
                    + f"\n\nYour previous response was invalid JSON or did not match "
                    f"the schema. Error: {exc}. Try again, JSON ONLY."
                )

        raise LLMValidationError(
            f"Model did not return valid JSON matching {schema.__name__} after 2 attempts: {last_error}"
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.host}/api/tags")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False
