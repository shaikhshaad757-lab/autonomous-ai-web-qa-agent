"""Regression tests for Ollama resilience (app/llm/ollama_client.py) and
its consumer (app/agents/analyzer/root_cause_analyzer.py).

Covers every failure mode required by the project spec:
  - hard timeout / no indefinite hang
  - connection failure / unavailable server
  - invalid JSON
  - invalid structured output (schema mismatch)
  - empty response
  - model/server error (non-2xx)
  - retries are bounded and configurable
  - a bug's root_cause/recommended_fix/confidence are set to the
    "unavailable" sentinel on failure, and analyze_bugs() itself never
    raises regardless of what the client does.

None of these tests talk to a real Ollama instance — OllamaClient's
internal _raw_generate() is monkeypatched per-test so behavior is exact
and fast.
"""
from __future__ import annotations
from typing import Optional

import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

from app.llm.ollama_client import LLMValidationError, OllamaClient


class _Schema(BaseModel):
    evidence_sufficient: bool
    root_cause: str
    recommended_fix: str
    confidence: Optional[int] = None


def _client(max_retries: int = 1) -> OllamaClient:
    return OllamaClient(
        host="http://localhost:11434",
        model="test-model",
        timeout_seconds=1,
        connect_timeout_seconds=1,
        max_retries=max_retries,
    )


@pytest.mark.asyncio
async def test_hard_timeout_never_hangs_past_ceiling(monkeypatch):
    """A hung HTTP request must be bounded by the client's hard ceiling."""
    client = _client(max_retries=0)

    async def _never_returns(*args, **kwargs):
        while True:
            await asyncio.sleep(0.1)

    monkeypatch.setattr(httpx.AsyncClient, "post", _never_returns)

    with pytest.raises(LLMValidationError):
        await asyncio.wait_for(
            client.generate_structured("prompt", _Schema),
            timeout=30,
        )


@pytest.mark.asyncio
async def test_connection_failure_raises_llm_validation_error(monkeypatch):
    client = _client(max_retries=0)

    async def _raise_connect_error(prompt: str) -> str:
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(client, "_raw_generate", _raise_connect_error)

    with pytest.raises(LLMValidationError) as excinfo:
        await client.generate_structured("prompt", _Schema)

    # The error message must include the exception type name — this is
    # the fix for the previously-empty "attempt 1 failed:" log lines.
    assert "ConnectError" in str(excinfo.value)


@pytest.mark.asyncio
async def test_server_unavailable_5xx_raises_llm_validation_error(monkeypatch):
    client = _client(max_retries=0)

    async def _raise_status_error(prompt: str) -> str:
        request = httpx.Request("POST", "http://localhost:11434/api/generate")
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("Service unavailable", request=request, response=response)

    monkeypatch.setattr(client, "_raw_generate", _raise_status_error)

    with pytest.raises(LLMValidationError):
        await client.generate_structured("prompt", _Schema)


@pytest.mark.asyncio
async def test_invalid_json_raises_llm_validation_error(monkeypatch):
    client = _client(max_retries=0)

    async def _return_garbage(prompt: str) -> str:
        return "this is not json at all {{{"

    monkeypatch.setattr(client, "_raw_generate", _return_garbage)

    with pytest.raises(LLMValidationError):
        await client.generate_structured("prompt", _Schema)


@pytest.mark.asyncio
async def test_schema_mismatch_raises_llm_validation_error(monkeypatch):
    client = _client(max_retries=0)

    async def _return_wrong_shape(prompt: str) -> str:
        # Valid JSON, but missing required fields for _Schema.
        return json.dumps({"unrelated_key": "value"})

    monkeypatch.setattr(client, "_raw_generate", _return_wrong_shape)

    with pytest.raises(LLMValidationError):
        await client.generate_structured("prompt", _Schema)


@pytest.mark.asyncio
async def test_empty_response_raises_llm_validation_error(monkeypatch):
    client = _client(max_retries=0)

    async def _return_empty(prompt: str) -> str:
        return ""

    monkeypatch.setattr(client, "_raw_generate", _return_empty)

    with pytest.raises(LLMValidationError) as excinfo:
        await client.generate_structured("prompt", _Schema)

    assert "empty" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_valid_response_succeeds_first_attempt(monkeypatch):
    client = _client(max_retries=1)
    calls = []

    async def _return_valid(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({
            "evidence_sufficient": True,
            "root_cause": "cause",
            "recommended_fix": "fix",
            "confidence": 90,
        })

    monkeypatch.setattr(client, "_raw_generate", _return_valid)

    result = await client.generate_structured("prompt", _Schema)
    assert result.root_cause == "cause"
    assert len(calls) == 1  # no retry needed


@pytest.mark.asyncio
async def test_retries_are_bounded_by_max_retries(monkeypatch):
    """max_retries=2 should mean exactly 3 total attempts, never more,
    never fewer, when every attempt fails."""
    client = _client(max_retries=2)
    call_count = {"n": 0}

    async def _always_fail(prompt: str) -> str:
        call_count["n"] += 1
        raise httpx.ConnectError("still down")

    monkeypatch.setattr(client, "_raw_generate", _always_fail)

    with pytest.raises(LLMValidationError):
        await client.generate_structured("prompt", _Schema)

    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_recovers_on_second_attempt(monkeypatch):
    """First attempt fails, second (retry) succeeds — retries actually
    give the model a second chance rather than just existing as config."""
    client = _client(max_retries=1)
    call_count = {"n": 0}

    async def _fail_then_succeed(prompt: str) -> str:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ReadTimeout("timed out")
        return json.dumps({
            "evidence_sufficient": True,
            "root_cause": "cause",
            "recommended_fix": "fix",
            "confidence": 70,
        })

    monkeypatch.setattr(client, "_raw_generate", _fail_then_succeed)

    result = await client.generate_structured("prompt", _Schema)
    assert result.confidence == 70
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_health_check_returns_false_on_connection_failure(monkeypatch):
    client = _client()

    async def _raise(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", _raise)

    assert await client.health_check() is False


# ---------------------------------------------------------------------
# root_cause_analyzer.py integration — Ollama failures must never
# propagate up and fail the QA run.
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyze_bug_never_raises_on_client_failure(monkeypatch):
    from app.agents.analyzer.bug_analyzer import Bug
    from app.agents.analyzer.root_cause_analyzer import (
        UNAVAILABLE_FIX,
        UNAVAILABLE_ROOT_CAUSE,
        analyze_bug,
    )

    client = _client(max_retries=0)

    async def _always_fail(prompt: str) -> str:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(client, "_raw_generate", _always_fail)

    bug = Bug(
        bug_ref="BUG-0001", title="t", module="m", severity="P2", url="https://x",
        steps=["a"], expected="e", actual="a", reproducibility="2/2",
        screenshot_path=None, console_errors=[], network_errors=[],
    )

    status = await analyze_bug(client, bug)

    assert status == "unavailable"
    assert bug.root_cause == UNAVAILABLE_ROOT_CAUSE
    assert bug.recommended_fix == UNAVAILABLE_FIX
    assert bug.confidence is None


@pytest.mark.asyncio
async def test_analyze_bugs_batch_never_raises_and_processes_all(monkeypatch):
    from app.agents.analyzer.bug_analyzer import Bug
    from app.agents.analyzer.root_cause_analyzer import analyze_bugs

    client = _client(max_retries=0)

    async def _always_fail(prompt: str) -> str:
        raise httpx.ReadTimeout("stalled")

    monkeypatch.setattr(client, "_raw_generate", _always_fail)

    bugs = [
        Bug(
            bug_ref=f"BUG-{i:04d}", title="t", module="m", severity="P2", url="https://x",
            steps=["a"], expected="e", actual="a", reproducibility="2/2",
            screenshot_path=None, console_errors=[], network_errors=[],
        )
        for i in range(3)
    ]

    counts = await analyze_bugs(client, bugs, max_bugs=10, ollama_reachable=True)

    assert counts["unavailable"] == 3
    assert all(b.root_cause is not None for b in bugs)


@pytest.mark.asyncio
async def test_analyze_bugs_skips_cleanly_when_ollama_unreachable():
    """When the orchestrator's health check already found Ollama down,
    analyze_bugs must skip calling it entirely rather than making
    guaranteed-to-fail calls for every bug."""
    from app.agents.analyzer.bug_analyzer import Bug
    from app.agents.analyzer.root_cause_analyzer import analyze_bugs

    client = _client(max_retries=0)
    bugs = [
        Bug(
            bug_ref="BUG-0001", title="t", module="m", severity="P2", url="https://x",
            steps=["a"], expected="e", actual="a", reproducibility="2/2",
            screenshot_path=None, console_errors=[], network_errors=[],
        )
    ]

    counts = await analyze_bugs(client, bugs, max_bugs=10, ollama_reachable=False)

    assert counts["unavailable"] == 1
    assert counts["analyzed"] == 0
