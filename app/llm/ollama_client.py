"""Thin abstraction over a local Ollama instance.

Design rules (per project spec):
- Always request structured JSON output and validate it with Pydantic.
- Never allow raw LLM output to execute arbitrary shell commands.
- Never send credentials to the model.
- Fail loudly (raise LLMValidationError) rather than silently returning
  made-up data if the model output doesn't validate — callers must handle
  LLMValidationError. Callers (root_cause_analyzer.py) catch this and mark
  the bug's analysis "unavailable" rather than letting it propagate.

Reliability rules (Slice: Ollama resilience):
- Every request has a HARD ceiling enforced by asyncio.wait_for on top of
  httpx's own connect/read timeouts, so a hung read (proxy quirk, model
  stall, etc.) can never block the run indefinitely even if httpx's own
  timeout somehow doesn't fire.
- Retries are bounded and configurable (max_retries), not hardcoded.
- Every failure is logged with BOTH the exception type name and the
  message, because many httpx/asyncio timeout exceptions have an empty
  str() — logging only "%s" on the exception produces the useless
  "attempt 1 failed:" (nothing after the colon) seen in past runs.
- Distinct failure modes (timeout, connection refused/unreachable,
  non-2xx status, invalid JSON, schema mismatch, empty response) get
  distinct, readable log messages so on-call debugging doesn't require
  re-deriving what went wrong from a bare exception repr.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger("hms_qa_agent.llm")

T = TypeVar("T", bound=BaseModel)

_JSON_SYSTEM_SUFFIX = (
    "\n\nRespond with ONLY a single valid JSON object matching the requested "
    "schema. No prose, no markdown code fences, no explanation before or "
    "after the JSON."
)

# Small, deliberately conservative defaults. A QA run analyzing up to 10
# bugs must never turn into a multi-minute-per-bug hang: worst case here
# is (connect + read + slack) * (max_retries + 1) per bug, i.e. with the
# defaults below, (5 + 20 + 5) * 2 = 60s per bug, 10 minutes worst case
# for all 10 — versus the ~9 minutes-per-bug previously observed with a
# 60s httpx timeout and no hard ceiling.
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_RETRIES = 1
_HARD_TIMEOUT_SLACK_SECONDS = 5.0


class LLMValidationError(RuntimeError):
    """Raised when the model's JSON output doesn't validate against the
    expected Pydantic schema, even after all retries — or when every
    retry failed for some other reason (timeout, connection error,
    non-2xx status, empty response). The message always includes the
    underlying exception's type name, since many timeout/connection
    exceptions stringify to an empty message on their own."""


class OllamaClient:
    def __init__(
        self,
        host: str,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        timeout_seconds: int = int(DEFAULT_READ_TIMEOUT_SECONDS),
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        # Never allow a negative/garbage config value to disable retries
        # in a way that wasn't intended.
        self.max_retries = max(0, max_retries)

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

    def _hard_ceiling_seconds(self) -> float:
        return (
            self.connect_timeout_seconds
            + self.timeout_seconds
            + _HARD_TIMEOUT_SLACK_SECONDS
        )

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
        timeout = httpx.Timeout(
            connect=self.connect_timeout_seconds,
            read=self.timeout_seconds,
            write=self.timeout_seconds,
            pool=self.connect_timeout_seconds,
        )

        async def _do_request() -> str:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data.get("response", "")

        # Hard ceiling on top of httpx's own timeout. httpx timeouts are
        # generally reliable, but this is defense-in-depth against any
        # proxy/streaming edge case where a read technically progresses
        # (resetting httpx's per-read timeout) without ever completing.
        task = asyncio.ensure_future(_do_request())
        try:
            done, _ = await asyncio.wait(
                {task},
                timeout=self._hard_ceiling_seconds(),
            )

            if not done:
                task.cancel()
                raise LLMValidationError(
                    f"Ollama request exceeded hard timeout of "
                    f"{self._hard_ceiling_seconds():.0f}s"
                )

            return await task

        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            raise
        except LLMValidationError:
            if not task.done():
                task.cancel()
            raise
        except Exception:
            if not task.done():
                task.cancel()
            raise

    @staticmethod
    def _describe(exc: BaseException) -> str:
        """Exception type name + message, since many timeout/connection
        exceptions have an empty str() on their own — this is what fixes
        the previously-seen 'attempt 1 failed:' log lines with nothing
        after the colon."""
        text = str(exc).strip()
        return f"{type(exc).__name__}: {text}" if text else f"{type(exc).__name__} (no message)"

    async def generate_structured(self, prompt: str, schema: Type[T]) -> T:
        """Call Ollama and validate the JSON response against `schema`.

        Retries up to `self.max_retries` additional times (configurable,
        default 1 — i.e. 2 total attempts) with a corrective prompt if a
        response fails to parse or validate, or if the request itself
        fails (timeout, connection error, non-2xx status).

        Raises LLMValidationError if every attempt fails. Never raises
        any other exception type — callers only need to catch
        LLMValidationError.
        """
        full_prompt = prompt + _JSON_SYSTEM_SUFFIX
        last_exc: Optional[BaseException] = None
        total_attempts = self.max_retries + 1

        for attempt in range(1, total_attempts + 1):
            try:
                raw = await self._raw_generate(full_prompt)
                cleaned = self._strip_code_fences(raw)
                if not cleaned:
                    raise LLMValidationError("Ollama returned an empty response.")
                parsed = json.loads(cleaned)
                return schema.model_validate(parsed)

            except asyncio.TimeoutError as exc:
                last_exc = exc
                logger.warning(
                    "Ollama structured output attempt %d/%d timed out after ~%.0fs "
                    "(host=%s model=%s): %s",
                    attempt, total_attempts, self._hard_ceiling_seconds(),
                    self.host, self.model, self._describe(exc),
                )

            except httpx.ConnectError as exc:
                last_exc = exc
                logger.warning(
                    "Ollama structured output attempt %d/%d failed — cannot connect "
                    "(is Ollama running at %s?): %s",
                    attempt, total_attempts, self.host, self._describe(exc),
                )

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code if exc.response is not None else "?"
                logger.warning(
                    "Ollama structured output attempt %d/%d failed — server returned "
                    "HTTP %s (host=%s model=%s): %s",
                    attempt, total_attempts, status, self.host, self.model, self._describe(exc),
                )

            except httpx.HTTPError as exc:
                # Catch-all for other httpx failure modes (pool timeout,
                # protocol errors, etc.) not covered above.
                last_exc = exc
                logger.warning(
                    "Ollama structured output attempt %d/%d failed — network error: %s",
                    attempt, total_attempts, self._describe(exc),
                )

            except json.JSONDecodeError as exc:
                last_exc = exc
                logger.warning(
                    "Ollama structured output attempt %d/%d failed — invalid JSON: %s",
                    attempt, total_attempts, self._describe(exc),
                )

            except ValidationError as exc:
                last_exc = exc
                logger.warning(
                    "Ollama structured output attempt %d/%d failed — response did not "
                    "match %s schema: %s",
                    attempt, total_attempts, schema.__name__, exc,
                )

            except LLMValidationError as exc:
                last_exc = exc
                logger.warning(
                    "Ollama structured output attempt %d/%d failed — %s",
                    attempt, total_attempts, self._describe(exc),
                )

            except Exception as exc:  # noqa: BLE001 - must never let a novel error type escape
                last_exc = exc
                logger.warning(
                    "Ollama structured output attempt %d/%d failed — unexpected error: %s",
                    attempt, total_attempts, self._describe(exc),
                )

            if attempt < total_attempts:
                full_prompt = (
                    prompt
                    + _JSON_SYSTEM_SUFFIX
                    + "\n\nYour previous response was invalid JSON or did not match "
                    f"the schema. Error: {last_exc}. Try again, JSON ONLY."
                )

        raise LLMValidationError(
            f"Model did not return valid JSON matching {schema.__name__} after "
            f"{total_attempts} attempt(s): {self._describe(last_exc) if last_exc else 'unknown error'}"
        )

    async def health_check(self) -> bool:
        timeout = httpx.Timeout(
            connect=self.connect_timeout_seconds, read=5.0, write=5.0,
            pool=self.connect_timeout_seconds,
        )
        try:
            async def _do_check() -> bool:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(f"{self.host}/api/tags")
                    return resp.status_code == 200

            return await asyncio.wait_for(
                _do_check(), timeout=self.connect_timeout_seconds + 5.0 + _HARD_TIMEOUT_SLACK_SECONDS
            )
        except asyncio.TimeoutError as exc:
            logger.info("Ollama health check timed out: %s", self._describe(exc))
            return False
        except httpx.HTTPError as exc:
            logger.info("Ollama health check failed: %s", self._describe(exc))
            return False
        except Exception as exc:  # noqa: BLE001
            logger.info("Ollama health check failed unexpectedly: %s", self._describe(exc))
            return False