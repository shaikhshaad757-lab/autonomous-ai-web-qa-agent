from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hms_qa_agent.dataflow")

TEST_MARKER = "QA_AUTO_TEST"

DANGEROUS_WORDS = (
    "delete", "remove", "destroy", "discharge",
    "cancel", "payment", "pay", "refund",
    "approve", "reject", "prescribe", "send", "publish",
)

SAFE_SAVE_WORDS = (
    "save", "add", "create", "submit",
    "update", "register", "continue",
)


@dataclass
class DataFlowResult:
    url: str
    status: str
    detail: str
    field_values: Dict[str, str] = field(default_factory=dict)
    observed_values: List[str] = field(default_factory=list)
    persistence_checks: Dict[str, Any] = field(default_factory=dict)
    network_events: List[Dict[str, Any]] = field(default_factory=list)
    console_errors: List[str] = field(default_factory=list)
    evidence: Optional[str] = None


class DataFlowAgent:
    """
    Controlled TEST-environment data-flow verification.

    Workflow:
        discover -> fill -> save -> verify -> refresh -> verify

    Destructive controls are never automatically clicked.
    """

    def __init__(
        self,
        browser,
        screenshot_dir: Optional[str] = None,
        enabled: bool = False,
    ):
        self.browser = browser
        self.screenshot_dir = screenshot_dir
        self.enabled = enabled

    async def run_page(self, url: str) -> DataFlowResult:
        if not self.enabled:
            return DataFlowResult(
                url=url,
                status="skipped",
                detail="Data-flow testing disabled.",
            )

        page = self.browser.page

        if page is None:
            return DataFlowResult(
                url=url,
                status="failed",
                detail="Browser page unavailable.",
            )

        try:
            self.browser.monitor.clear()

            await self.browser.goto(url)
            await page.wait_for_timeout(1000)

            fields = await self._discover_fields()

            if not fields:
                return DataFlowResult(
                    url=url,
                    status="skipped",
                    detail="No suitable editable fields found.",
                )

            values = {}

            for item in fields:
                value = self._test_value(
                    item["name"],
                    item["type"],
                )

                if value is None:
                    continue

                try:
                    locator = page.locator(
                        item["selector"]
                    ).first

                    await locator.fill(value)

                    values[item["name"]] = value

                except Exception as exc:
                    logger.debug(
                        "Could not fill %s: %s",
                        item["name"],
                        exc,
                    )

            if not values:
                return DataFlowResult(
                    url=url,
                    status="skipped",
                    detail="No fields could safely be filled.",
                )

            save_button = await self._find_safe_save_button()

            if save_button is None:
                return DataFlowResult(
                    url=url,
                    status="skipped",
                    detail=(
                        "Test data prepared, but no safe save/create "
                        "control was found. Nothing submitted."
                    ),
                    field_values=values,
                )

            before_url = page.url

            await save_button.click()

            try:
                await page.wait_for_load_state(
                    "networkidle",
                    timeout=7000,
                )
            except Exception:
                pass

            await page.wait_for_timeout(1500)

            after_url = page.url

            first_observation = await self._verify_values(values)

            success_message = await self._find_status_messages()

            # Refresh is deliberately used only after the save.
            await page.reload(
                wait_until="domcontentloaded",
            )

            await page.wait_for_timeout(1200)

            refresh_observation = await self._verify_values(values)

            persistence = {
                "url_changed": before_url != after_url,
                "saved_values_visible": bool(first_observation),
                "success_message": success_message,
                "values_after_refresh": bool(refresh_observation),
                "field_matches_after_refresh": refresh_observation,
            }

            console_errors = [
                event.text
                for event in self.browser.monitor.console_errors()
            ]

            failed_requests = [
                {
                    "url": event.url,
                    "method": event.method,
                    "status": event.status,
                    "failure": event.failure_text,
                }
                for event in self.browser.monitor.failed_requests()
            ]

            evidence = await self._screenshot(url)

            if refresh_observation:
                status = "passed"
                detail = (
                    "Test values were submitted and remained "
                    "visible after page refresh."
                )
            elif first_observation:
                status = "failed"
                detail = (
                    "Test values appeared after save but could "
                    "not be verified after refresh."
                )
            else:
                status = "failed"
                detail = (
                    "Save was attempted but submitted test values "
                    "could not be verified."
                )

            return DataFlowResult(
                url=url,
                status=status,
                detail=detail,
                field_values=values,
                observed_values=first_observation,
                persistence_checks=persistence,
                network_events=failed_requests,
                console_errors=console_errors,
                evidence=evidence,
            )

        except Exception as exc:
            logger.exception(
                "Data-flow test failed on %s",
                url,
            )

            return DataFlowResult(
                url=url,
                status="failed",
                detail=f"Data-flow test failed: {exc}",
            )

    async def _discover_fields(self) -> List[Dict[str, Any]]:
        page = self.browser.page

        return await page.evaluate(
            """
            () => {
                const elements = [
                    ...document.querySelectorAll(
                        'input:not([type="hidden"]), textarea'
                    )
                ];

                return elements
                    .filter(el => {
                        const type =
                            (el.type || 'text').toLowerCase();

                        return ![
                            'password',
                            'file',
                            'checkbox',
                            'radio',
                            'submit',
                            'button',
                            'reset'
                        ].includes(type);
                    })
                    .filter(el => {
                        const style =
                            window.getComputedStyle(el);

                        const visible =
                            style.display !== 'none' &&
                            style.visibility !== 'hidden' &&
                            el.getClientRects().length > 0;

                        return (
                            visible &&
                            !el.disabled &&
                            !el.readOnly
                        );
                    })
                    .map((el, index) => ({
                        selector:
                            el.id
                                ? '#' + CSS.escape(el.id)
                                : el.name
                                    ? `[name="${CSS.escape(el.name)}"]`
                                    : `input:nth-of-type(${index + 1})`,
                        name:
                            el.name ||
                            el.id ||
                            el.placeholder ||
                            `field_${index + 1}`,
                        type:
                            (el.type || 'text').toLowerCase(),
                        placeholder:
                            el.placeholder || '',
                        required:
                            !!el.required
                    }));
            }
            """
        )

    async def _find_safe_save_button(self):
        page = self.browser.page

        buttons = page.locator(
            "button, input[type='submit'], input[type='button']"
        )

        count = await buttons.count()

        for index in range(count):
            button = buttons.nth(index)

            try:
                if not await button.is_visible():
                    continue

                if await button.is_disabled():
                    continue

                tag = await button.evaluate(
                    "el => el.tagName.toLowerCase()"
                )

                if tag == "button":
                    text = await button.inner_text()
                else:
                    text = await button.get_attribute("value")

                text = (text or "").strip().lower()

                if not text:
                    continue

                if any(
                    word in text
                    for word in DANGEROUS_WORDS
                ):
                    continue

                if any(
                    word in text
                    for word in SAFE_SAVE_WORDS
                ):
                    return button

            except Exception:
                continue

        return None

    async def _verify_values(
        self,
        values: Dict[str, str],
    ) -> Dict[str, bool]:
        page = self.browser.page

        result = {}

        for name, value in values.items():
            try:
                count = await page.get_by_text(
                    value,
                    exact=False,
                ).count()

                result[name] = count > 0

            except Exception:
                result[name] = False

        return result

    async def _find_status_messages(self) -> List[str]:
        page = self.browser.page

        return await page.evaluate(
            """
            () => {
                const selectors = [
                    '[role="alert"]',
                    '[role="status"]',
                    '.alert',
                    '.toast',
                    '.notification',
                    '.success',
                    '.error'
                ];

                const output = [];

                for (const selector of selectors) {
                    for (const el of document.querySelectorAll(selector)) {
                        const text =
                            (el.innerText || el.textContent || '')
                                .trim();

                        if (text) {
                            output.push(text.slice(0, 300));
                        }
                    }
                }

                return [...new Set(output)].slice(0, 20);
            }
            """
        )

    def _test_value(
        self,
        name: str,
        field_type: str,
    ) -> Optional[str]:

        normalized = re.sub(
            r"[^a-z0-9]+",
            " ",
            name.lower(),
        ).strip()

        if field_type == "email":
            return "qa.auto.test+001@example.com"

        if "phone" in normalized or "mobile" in normalized:
            return "9999999999"

        if "name" in normalized:
            return f"{TEST_MARKER} Patient"

        if "address" in normalized:
            return f"{TEST_MARKER} Test Address"

        if "city" in normalized:
            return "QA Test City"

        if "description" in normalized:
            return f"{TEST_MARKER} Description"

        if "note" in normalized:
            return f"{TEST_MARKER} Note"

        if "amount" in normalized or "price" in normalized:
            return "1"

        if "age" in normalized:
            return "30"

        return f"{TEST_MARKER} {normalized[:30]}"

    async def _screenshot(
        self,
        url: str,
    ) -> Optional[str]:

        if not self.screenshot_dir:
            return None

        safe = re.sub(
            r"[^a-zA-Z0-9_-]+",
            "_",
            url,
        )[-100:]

        path = (
            f"{self.screenshot_dir}/"
            f"dataflow_{safe}.png"
        )

        try:
            await self.browser.screenshot(path)
            return path
        except Exception:
            return None
