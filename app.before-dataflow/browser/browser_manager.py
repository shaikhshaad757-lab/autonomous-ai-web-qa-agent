"""Playwright browser manager with deep QA telemetry.

Safe/read-only inspection:
- login
- console messages
- page exceptions
- network requests/responses
- failed requests
- response status codes
- request timing
- resource timing
- screenshots

No destructive action is executed automatically.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

logger = logging.getLogger("hms_qa_agent.browser")


EMAIL_SELECTORS = [
    'input[type="email"]',
    'input[name*="email" i]',
    'input[name*="username" i]',
    'input[id*="email" i]',
    'input[id*="username" i]',
    'input[autocomplete="username"]',
]

PASSWORD_SELECTORS = [
    'input[type="password"]',
]

SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Log in")',
    'button:has-text("Login")',
    'button:has-text("Sign in")',
]


@dataclass
class ConsoleEvent:
    type: str
    text: str
    location: Optional[str] = None


@dataclass
class NetworkEvent:
    url: str
    method: str
    status: Optional[int] = None
    ok: Optional[bool] = None
    failure_text: Optional[str] = None
    resource_type: Optional[str] = None
    duration_ms: Optional[float] = None


@dataclass
class PageMonitor:
    console_events: List[ConsoleEvent] = field(default_factory=list)
    network_events: List[NetworkEvent] = field(default_factory=list)

    def console_errors(self) -> List[ConsoleEvent]:
        return [
            e for e in self.console_events
            if e.type == "error"
        ]

    def console_warnings(self) -> List[ConsoleEvent]:
        return [
            e for e in self.console_events
            if e.type == "warning"
        ]

    def failed_requests(self) -> List[NetworkEvent]:
        return [
            e for e in self.network_events
            if e.failure_text
            or (
                e.status is not None
                and e.status >= 400
            )
        ]

    def server_errors(self) -> List[NetworkEvent]:
        return [
            e for e in self.network_events
            if e.status is not None
            and e.status >= 500
        ]

    def client_errors(self) -> List[NetworkEvent]:
        return [
            e for e in self.network_events
            if e.status is not None
            and 400 <= e.status < 500
        ]

    def slow_requests(
        self,
        threshold_ms: float = 2000,
    ) -> List[NetworkEvent]:
        return [
            e for e in self.network_events
            if e.duration_ms is not None
            and e.duration_ms >= threshold_ms
        ]

    def clear(self) -> None:
        self.console_events.clear()
        self.network_events.clear()


class BrowserManager:

    def __init__(
        self,
        headless: bool = True,
        viewport: Optional[Dict[str, int]] = None,
        navigation_timeout_ms: int = 15000,
        action_timeout_ms: int = 8000,
    ) -> None:

        self.headless = headless
        self.viewport = viewport or {
            "width": 1440,
            "height": 900,
        }

        self.navigation_timeout_ms = (
            navigation_timeout_ms
        )

        self.action_timeout_ms = (
            action_timeout_ms
        )

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

        self.page: Optional[Page] = None

        self.monitor = PageMonitor()

        self._request_start_times: Dict[int, float] = {}

    async def start(self) -> None:

        self._playwright = (
            await async_playwright().start()
        )

        self._browser = await (
            self._playwright.chromium.launch(
                headless=self.headless
            )
        )

        self._context = await (
            self._browser.new_context(
                viewport=self.viewport
            )
        )

        self._context.set_default_navigation_timeout(
            self.navigation_timeout_ms
        )

        self._context.set_default_timeout(
            self.action_timeout_ms
        )

        self.page = await (
            self._context.new_page()
        )

        self._attach_monitors(
            self.page
        )

    # ------------------------------------------------------------
    # TELEMETRY
    # ------------------------------------------------------------

    def _attach_monitors(
        self,
        page: Page,
    ) -> None:

        page.on(
            "console",
            lambda msg: self._record_console(
                msg
            ),
        )

        page.on(
            "pageerror",
            lambda exc: self.monitor.console_events.append(
                ConsoleEvent(
                    type="pageerror",
                    text=f"Uncaught exception: {exc}",
                )
            ),
        )

        page.on(
            "request",
            self._on_request,
        )

        page.on(
            "requestfailed",
            self._on_request_failed,
        )

        page.on(
            "response",
            self._on_response,
        )

    def _record_console(
        self,
        msg: Any,
    ) -> None:

        try:
            location = str(
                msg.location
            )
        except Exception:
            location = None

        self.monitor.console_events.append(
            ConsoleEvent(
                type=msg.type,
                text=msg.text,
                location=location,
            )
        )

    def _on_request(
        self,
        request: Any,
    ) -> None:

        self._request_start_times[
            id(request)
        ] = time.monotonic()

    def _on_request_failed(
        self,
        request: Any,
    ) -> None:

        start = self._request_start_times.pop(
            id(request),
            None,
        )

        duration = (
            (time.monotonic() - start) * 1000
            if start is not None
            else None
        )

        failure = request.failure

        if isinstance(failure, dict):
            failure_text = (
                failure.get("errorText")
                or str(failure)
            )
        else:
            failure_text = (
                str(failure)
                if failure
                else "unknown failure"
            )

        self.monitor.network_events.append(
            NetworkEvent(
                url=request.url,
                method=request.method,
                failure_text=failure_text,
                resource_type=request.resource_type,
                duration_ms=duration,
            )
        )

    def _on_response(
        self,
        response: Any,
    ) -> None:

        request = response.request

        start = self._request_start_times.pop(
            id(request),
            None,
        )

        duration = (
            (time.monotonic() - start) * 1000
            if start is not None
            else None
        )

        self.monitor.network_events.append(
            NetworkEvent(
                url=response.url,
                method=request.method,
                status=response.status,
                ok=response.ok,
                resource_type=request.resource_type,
                duration_ms=duration,
            )
        )

    # ------------------------------------------------------------
    # NAVIGATION
    # ------------------------------------------------------------

    async def goto(
        self,
        url: str,
    ) -> None:

        assert self.page is not None

        self.monitor.clear()

        await self.page.goto(
            url,
            wait_until="domcontentloaded",
        )

        try:
            await self.page.wait_for_timeout(
                500
            )
        except Exception:
            pass

    # ------------------------------------------------------------
    # LOGIN
    # ------------------------------------------------------------

    async def detect_and_fill_login(
        self,
        email: str,
        password: str,
    ) -> bool:

        assert self.page is not None

        page = self.page

        logger.info(
            "Checking HMS login page: %s",
            page.url,
        )

        # Some HMS pages expose User/Admin choices.
        try:
            admin_button = page.get_by_role(
                "button",
                name="Admin",
            ).first

            if await admin_button.count() > 0:

                logger.info(
                    "Admin login option detected. "
                    "Selecting Admin."
                )

                await admin_button.click()

                await page.wait_for_timeout(
                    500
                )

        except Exception:
            pass

        password_locator = None

        try:

            password_locator = page.locator(
                'input[type="password"]'
            ).first

            await password_locator.wait_for(
                state="visible",
                timeout=10000,
            )

        except Exception:

            password_locator = None

        if password_locator is None:

            logger.info(
                "No password field detected on %s.",
                page.url,
            )

            return False

        email_locator = None

        for selector in EMAIL_SELECTORS:

            try:

                locator = page.locator(
                    selector
                ).first

                if await locator.count() > 0:

                    email_locator = locator

                    break

            except Exception:

                continue

        if email_locator is None:

            logger.warning(
                "Password field found but no "
                "email/username field detected."
            )

            return False

        await email_locator.fill(
            email
        )

        await password_locator.fill(
            password
        )

        logger.info(
            "Login credentials filled successfully."
        )

        submitted = False

        for selector in SUBMIT_SELECTORS:

            try:

                locator = page.locator(
                    selector
                ).first

                if await locator.count() > 0:

                    logger.info(
                        "Clicking Sign in."
                    )

                    await locator.click()

                    submitted = True

                    break

            except Exception:

                continue

        if not submitted:

            await password_locator.press(
                "Enter"
            )

        try:

            await page.wait_for_load_state(
                "domcontentloaded",
                timeout=10000,
            )

        except Exception:

            pass

        logger.info(
            "Login submission completed. "
            "Current URL: %s",
            page.url,
        )

        return True

    async def verify_login_success(
        self,
        pre_login_url: str,
    ) -> bool:
        """Wait for the HMS redirect/authenticated page and verify login."""

        assert self.page is not None
        page = self.page

        logger.info(
            "Waiting for HMS authentication/redirect..."
        )

        # Give the frontend/API enough time to finish authentication.
        # Some HMS builds redirect asynchronously after the Sign in click.
        for _ in range(20):
            try:
                await page.wait_for_timeout(500)
            except Exception:
                pass

            current_url = page.url.lower()

            # Strong positive indicators from the observed HMS flow.
            if (
                "/welcome" in current_url
                or "/dashboard" in current_url
            ):
                logger.info(
                    "LOGIN SUCCESSFUL. Authenticated HMS page detected."
                )
                return True

            # If the URL changed away from the login page, inspect it.
            if current_url != pre_login_url.lower():
                try:
                    password_visible = False

                    for selector in PASSWORD_SELECTORS:
                        locator = page.locator(selector).first

                        if (
                            await locator.count() > 0
                            and await locator.is_visible()
                        ):
                            password_visible = True
                            break

                    if not password_visible:
                        logger.info(
                            "LOGIN SUCCESSFUL. "
                            "Login URL changed and password form disappeared."
                        )
                        return True

                except Exception:
                    pass

        logger.warning(
            "Login verification timed out. Current URL: %s",
            page.url,
        )

        return False

    async def extract_page_metadata(self):
        """Extract safe metadata required by the discovery agent."""
        assert self.page is not None
        page = self.page

        title = await page.title()

        links = await page.eval_on_selector_all(
            "a[href]",
            """els => els.map(e => ({
                href: e.href,
                text: (e.textContent || '').trim().slice(0, 120)
            }))"""
        )

        forms = await page.eval_on_selector_all(
            "form",
            """forms => forms.map(f => ({
                action: f.action || null,
                method: f.method || 'get',
                inputs: Array.from(
                    f.querySelectorAll('input,select,textarea')
                ).map(i => ({
                    name: i.name || null,
                    id: i.id || null,
                    type: i.type || i.tagName.toLowerCase(),
                    required: !!i.required
                }))
            }))"""
        )

        buttons = await page.eval_on_selector_all(
            "button,input[type=submit],input[type=button]",
            """els => els.map(e => ({
                text: (
                    e.innerText ||
                    e.textContent ||
                    e.value ||
                    ''
                ).trim().slice(0, 120),
                type: e.type || null,
                id: e.id || null,
                name: e.name || null,
                disabled: !!e.disabled
            }))"""
        )

        inputs = await page.eval_on_selector_all(
            "input",
            """els => els.map(e => ({
                type: e.type || 'text',
                name: e.name || null,
                id: e.id || null,
                placeholder: e.placeholder || null,
                required: !!e.required,
                disabled: !!e.disabled,
                visible: !!(
                    e.offsetWidth ||
                    e.offsetHeight ||
                    e.getClientRects().length
                )
            }))"""
        )

        selects = await page.eval_on_selector_all(
            "select",
            """els => els.map(e => ({
                name: e.name || null,
                id: e.id || null,
                required: !!e.required,
                disabled: !!e.disabled,
                options: e.options.length
            }))"""
        )

        checkboxes = await page.eval_on_selector_all(
            'input[type="checkbox"],[role="checkbox"]',
            """els => els.map(e => ({
                name: e.name || null,
                id: e.id || null,
                checked: !!e.checked,
                disabled: !!e.disabled
            }))"""
        )

        radios = await page.eval_on_selector_all(
            'input[type="radio"],[role="radio"]',
            """els => els.map(e => ({
                name: e.name || null,
                id: e.id || null,
                checked: !!e.checked,
                disabled: !!e.disabled
            }))"""
        )

        tables = await page.eval_on_selector_all(
            "table",
            """els => els.map(e => ({
                rows: e.rows ? e.rows.length : 0,
                columns: e.rows && e.rows.length
                    ? e.rows[0].cells.length
                    : 0
            }))"""
        )

        interactive = await page.eval_on_selector_all(
            """
            a[href],button,input,select,textarea,
            [role="button"],[role="link"],[role="tab"],
            [role="checkbox"],[role="radio"],
            [role="combobox"],[role="textbox"],[role="switch"]
            """,
            """els => els.length"""
        )

        return {
            "title": title,
            "links": links,
            "forms": forms,
            "buttons": buttons,
            "inputs": inputs,
            "selects": selects,
            "checkboxes": checkboxes,
            "radios": radios,
            "tables": tables,
            "interactive_count": interactive,
        }

    async def close(self):
        """Safely close Playwright resources."""
        if self._context:
            await self._context.close()

        if self._browser:
            await self._browser.close()

        if self._playwright:
            await self._playwright.stop()

        self._context = None
        self._browser = None
        self._playwright = None
        self.page = None

