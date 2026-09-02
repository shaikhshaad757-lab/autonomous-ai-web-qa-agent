#!/usr/bin/env python3
"""Diagnostic-only helper for the HMS AI QA Agent.

Opens the configured HMS_URL in a VISIBLE Chromium browser, waits for
JavaScript rendering to settle, and reports everything relevant to
login-field detection: current URL, page title, password field count,
email/username-like inputs, every <input> element's safe metadata,
buttons/submit controls, and iframes. Saves a full-page screenshot and the
rendered HTML for offline inspection.

This script NEVER logs in, fills a field, clicks anything, or prints/saves
any credential value. It is pure, read-only reconnaissance so login
detection in app/browser/browser_manager.py can be tuned against what your
HMS actually renders.

Usage:
    python scripts/debug_login_page.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils.config import load_secrets  # noqa: E402
from app.utils.logger import register_secret, setup_logging  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[1] / "reports" / "debug"

PASSWORD_SELECTOR = 'input[type="password"]'
EMAIL_LIKE_SELECTORS = [
    'input[type="email"]',
    'input[name*="email" i]',
    'input[name*="username" i]',
    'input[id*="email" i]',
    'input[id*="username" i]',
    'input[autocomplete="username"]',
]
SUBMIT_LIKE_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Log in")',
    'button:has-text("Login")',
    'button:has-text("Sign in")',
]


async def main() -> None:
    # load_secrets() reads HMS_URL / HMS_EMAIL / HMS_PASSWORD from .env.
    # We register the credential VALUES as redaction targets immediately,
    # even though this script never intentionally logs or prints them —
    # this is a belt-and-suspenders guard in case something upstream (e.g.
    # a future edit) accidentally logs a dict that contains them.
    secrets = load_secrets()
    register_secret(secrets.hms_email, secrets.hms_password)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(OUT_DIR)

    logger.info("Target URL (from HMS_URL in .env): %s", secrets.hms_url)
    logger.info("Opening a VISIBLE browser window for inspection...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})

        await page.goto(secrets.hms_url, wait_until="domcontentloaded")

        # 3. Wait for JS rendering / network idle before inspecting anything.
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            logger.info(
                "Page did not reach networkidle within 10s — continuing anyway "
                "(some dashboards poll continuously and never go fully idle)."
            )

        # 4. Current URL (post any redirect).
        logger.info("Current URL: %s", page.url)

        # 5. Page title.
        title = await page.title()
        logger.info("Page title: %s", title)

        # 6. Password field count.
        password_count = await page.locator(PASSWORD_SELECTOR).count()
        logger.info("Password field count: %d", password_count)

        # 7. Email/username-like inputs.
        email_counts = {}
        for sel in EMAIL_LIKE_SELECTORS:
            email_counts[sel] = await page.locator(sel).count()
        logger.info("Email/username-like field counts: %s", email_counts)

        # 8. ALL input elements, safe metadata only (never .value).
        all_inputs = await page.eval_on_selector_all(
            "input",
            """els => els.map(e => ({
                type: e.type || null,
                name: e.name || null,
                id: e.id || null,
                placeholder: e.placeholder || null,
                autocomplete: e.autocomplete || null
            }))""",
        )
        logger.info("All <input> elements (safe metadata only): %s", all_inputs)

        # 9. Buttons / submit controls.
        buttons = await page.eval_on_selector_all(
            "button, input[type=submit], input[type=button]",
            """els => els.map(e => ({
                tag: e.tagName.toLowerCase(),
                type: e.type || null,
                text: (e.textContent || e.value || '').trim().slice(0, 60)
            }))""",
        )
        logger.info("Buttons / submit controls found: %s", buttons)

        matched_submit_selectors = []
        for sel in SUBMIT_LIKE_SELECTORS:
            count = await page.locator(sel).count()
            if count > 0:
                matched_submit_selectors.append((sel, count))
        logger.info("Submit-like selectors that matched: %s", matched_submit_selectors)

        # 10. Iframes.
        frames_info = [
            {"url": f.url, "name": f.name} for f in page.frames if f != page.main_frame
        ]
        logger.info("Iframes on page: %s", frames_info)
        if frames_info:
            logger.info(
                "NOTE: if the login form lives inside one of these iframes, top-level "
                "selectors won't find it — the agent's login detection would need to "
                "search inside the frame instead of (or in addition to) the main page."
            )

        # 11. Screenshot.
        screenshot_path = OUT_DIR / "login_page.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        logger.info("Screenshot saved to: %s", screenshot_path)

        # 12. Rendered HTML.
        html_path = OUT_DIR / "login_page.html"
        html_path.write_text(await page.content(), encoding="utf-8")
        logger.info("HTML saved to: %s", html_path)

        logger.info("Diagnostic complete. Closing browser in 5 seconds...")
        await page.wait_for_timeout(5000)
        await browser.close()

    logger.info("Done. Review the screenshot/HTML in reports/debug/ for full detail.")


if __name__ == "__main__":
    # 14. Exit cleanly regardless of how far we got.
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted — exiting cleanly.")
        sys.exit(0)
