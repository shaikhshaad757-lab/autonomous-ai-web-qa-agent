"""Logging setup with automatic secret redaction.

Any secret values registered via `register_secret()` are scrubbed from every
log record before it's emitted, so even a careless `logger.info(some_dict)`
elsewhere in the codebase can't leak a password into the console or into
reports/logs/*.log.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

_REDACTED = "***REDACTED***"
_secret_values: set[str] = set()


def register_secret(*values: str) -> None:
    for v in values:
        if v and len(v) >= 3:  # avoid redacting trivially short/common substrings
            _secret_values.add(v)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = msg
        for secret in _secret_values:
            if secret in redacted:
                redacted = redacted.replace(secret, _REDACTED)
        # also catch common "password=..." / "pwd=..." patterns defensively
        redacted = re.sub(
            r"(password|pwd|pass)\s*[:=]\s*\S+",
            r"\1=***REDACTED***",
            redacted,
            flags=re.IGNORECASE,
        )
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(log_dir: Path, level: int = logging.INFO) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("hms_qa_agent")
    logger.setLevel(level)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(RedactingFilter())
    logger.addHandler(console)

    file_handler = logging.FileHandler(log_dir / "run.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.addFilter(RedactingFilter())
    logger.addHandler(file_handler)

    return logger
