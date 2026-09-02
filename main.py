#!/usr/bin/env python3
"""HMS AI QA Agent — CLI entry point (Phase 1).

Usage:
    python main.py

Reads target/credentials from .env (see .env.example). Requires an explicit
typed authorization confirmation before any browser automation starts.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.orchestrator.orchestrator import Orchestrator
from app.utils.config import load_app_config, load_secrets

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIRMATION_PHRASE = "I confirm this is an authorized test environment."


def confirm_authorization() -> bool:
    print(
        "\nThis agent will log in to and interact with the configured HMS URL.\n"
        "Only run this against a system you own or are explicitly authorized to test.\n"
    )
    typed = input(f'Type exactly: "{CONFIRMATION_PHRASE}"\n> ').strip()
    return typed == CONFIRMATION_PHRASE


async def main() -> int:
    try:
        secrets = load_secrets()
    except RuntimeError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    app_config = load_app_config()

    if secrets.environment == "PRODUCTION":
        print(
            "WARNING: HMS_ENVIRONMENT is set to PRODUCTION. Destructive/mutating "
            "tests are force-disabled for this run in every phase.\n"
        )

    if not confirm_authorization():
        print("Authorization not confirmed. Exiting without running any tests.")
        return 1

    orchestrator = Orchestrator(secrets=secrets, app_config=app_config, project_root=PROJECT_ROOT)
    result = await orchestrator.run()

    print("\n" + "=" * 60)
    print(f"Run ID:      {result['run_id']}")
    print(f"Status:      {result['status']}")
    print(f"Report dir:  {result['report_dir']}")
    if result["status"] == "completed":
        print(f"Open report: {result['report_dir']}/report.html")
    print("=" * 60)

    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
