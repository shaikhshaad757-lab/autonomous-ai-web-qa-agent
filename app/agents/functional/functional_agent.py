"""Deep safe functional QA agent for HMS.

READ-ONLY / SAFE TESTING ONLY.

This agent:
- audits every discovered page
- checks inputs and validation metadata
- checks accessibility metadata
- checks buttons and interactive controls
- checks tables
- checks hidden/off-screen/empty elements
- checks duplicate IDs
- checks broken images
- checks missing image alt text
- checks document overflow
- checks console errors/warnings
- checks network failures
- checks 4xx/5xx responses
- checks slow requests
- captures evidence screenshots

It does NOT:
- create records
- update records
- delete records
- submit arbitrary forms
- make payments
- discharge patients
- execute destructive controls
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.agents.discovery.discovery_agent import DiscoveredPage
from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("hms_qa_agent.functional")


DANGEROUS_WORDS = (
    "delete",
    "remove",
    "destroy",
    "discharge",
    "cancel",
    "payment",
    "pay",
    "refund",
    "approve",
    "reject",
    "save",
    "create",
    "add",
    "update",
    "edit",
    "submit",
    "confirm",
    "checkout",
    "prescribe",
    "prescription",
    "send",
    "publish",
)


@dataclass
class TestCase:
    name: str
    category: str
    target_url: str
    element_type: Optional[str] = None
    selector: Optional[str] = None
    test_data: Optional[Dict[str, Any]] = None
    destructive: bool = False


@dataclass
class TestResult:
    test_case: TestCase
    status: str  # passed | failed | skipped
    detail: str = ""
    console_errors: List[str] = field(default_factory=list)
    failed_requests: List[str] = field(default_factory=list)
    screenshot_path: Optional[str] = None


class FunctionalAgent:

    def __init__(
        self,
        browser: BrowserManager,
        screenshot_dir: str | None = None,
    ) -> None:
        self.browser = browser
        self.screenshot_dir = screenshot_dir

    # ============================================================
    # TEST GENERATION
    # ============================================================

    def generate_smoke_tests(
        self,
        pages: List[DiscoveredPage],
    ) -> List[TestCase]:

        return [
            TestCase(
                name=(
                    "Smoke: page loads cleanly — "
                    f"{page.title or page.url}"
                ),
                category="smoke",
                target_url=page.url,
            )
            for page in pages
        ]

    def generate_functional_tests(
        self,
        pages: List[DiscoveredPage],
    ) -> List[TestCase]:

        tests: List[TestCase] = []

        for page in pages:

            # ----------------------------------------------------
            # PAGE DEEP AUDIT
            # ----------------------------------------------------

            tests.append(
                TestCase(
                    name=f"Deep page audit — {page.title or page.url}",
                    category="deep_page_audit",
                    target_url=page.url,
                )
            )

            # ----------------------------------------------------
            # INPUTS
            # ----------------------------------------------------

            for index, element in enumerate(
                getattr(page, "inputs", []) or []
            ):

                metadata = (
                    element
                    if isinstance(element, dict)
                    else {}
                )

                name = (
                    metadata.get("name")
                    or metadata.get("id")
                    or metadata.get("placeholder")
                    or f"input-{index + 1}"
                )

                input_type = str(
                    metadata.get("type", "text")
                ).lower()

                tests.append(
                    TestCase(
                        name=f"Input audit: {name}",
                        category="input_audit",
                        target_url=page.url,
                        element_type="input",
                        test_data={
                            "field_name": name,
                        },
                    )
                )

                if input_type in {
                    "text",
                    "email",
                    "tel",
                    "number",
                    "search",
                    "url",
                }:
                    tests.append(
                        TestCase(
                            name=(
                                f"Empty validation audit: "
                                f"{name}"
                            ),
                            category="empty_input_validation",
                            target_url=page.url,
                            element_type="input",
                            test_data={
                                "field_name": name,
                                "input_type": input_type,
                            },
                        )
                    )

                if input_type == "email":
                    tests.append(
                        TestCase(
                            name=(
                                f"Email validation audit: "
                                f"{name}"
                            ),
                            category="invalid_email_validation",
                            target_url=page.url,
                            element_type="input",
                            test_data={
                                "field_name": name,
                            },
                        )
                    )

                if input_type == "tel":
                    tests.append(
                        TestCase(
                            name=(
                                f"Phone boundary audit: "
                                f"{name}"
                            ),
                            category="phone_boundary",
                            target_url=page.url,
                            element_type="input",
                            test_data={
                                "field_name": name,
                            },
                        )
                    )

                if metadata.get("required"):
                    tests.append(
                        TestCase(
                            name=(
                                f"Required field audit: "
                                f"{name}"
                            ),
                            category="required_field_audit",
                            target_url=page.url,
                            element_type="input",
                            test_data={
                                "field_name": name,
                            },
                        )
                    )

                max_length = metadata.get("maxLength")

                if (
                    isinstance(max_length, int)
                    and max_length > 0
                ):
                    tests.append(
                        TestCase(
                            name=(
                                f"Max-length audit: "
                                f"{name}"
                            ),
                            category="maxlength_audit",
                            target_url=page.url,
                            element_type="input",
                            test_data={
                                "field_name": name,
                                "max_length": max_length,
                            },
                        )
                    )

            # ----------------------------------------------------
            # TEXTAREAS
            # ----------------------------------------------------

            for index, element in enumerate(
                getattr(page, "textareas", []) or []
            ):

                metadata = (
                    element
                    if isinstance(element, dict)
                    else {}
                )

                name = (
                    metadata.get("name")
                    or metadata.get("id")
                    or metadata.get("placeholder")
                    or f"textarea-{index + 1}"
                )

                tests.append(
                    TestCase(
                        name=f"Textarea audit: {name}",
                        category="textarea_audit",
                        target_url=page.url,
                        element_type="textarea",
                        test_data={
                            "field_name": name,
                        },
                    )
                )

                if metadata.get("required"):
                    tests.append(
                        TestCase(
                            name=(
                                f"Required textarea audit: "
                                f"{name}"
                            ),
                            category="required_field_audit",
                            target_url=page.url,
                            element_type="textarea",
                            test_data={
                                "field_name": name,
                            },
                        )
                    )

            # ----------------------------------------------------
            # SELECTS
            # ----------------------------------------------------

            for index, element in enumerate(
                getattr(page, "selects", []) or []
            ):

                metadata = (
                    element
                    if isinstance(element, dict)
                    else {}
                )

                name = (
                    metadata.get("name")
                    or metadata.get("id")
                    or f"select-{index + 1}"
                )

                tests.append(
                    TestCase(
                        name=f"Select audit: {name}",
                        category="select_audit",
                        target_url=page.url,
                        element_type="select",
                        test_data={
                            "field_name": name,
                        },
                    )
                )

            # ----------------------------------------------------
            # BUTTONS
            # ----------------------------------------------------

            for index, button in enumerate(
                getattr(page, "buttons", []) or []
            ):

                metadata = (
                    button
                    if isinstance(button, dict)
                    else {}
                )

                text = (
                    metadata.get("text")
                    or metadata.get("aria_label")
                    or metadata.get("title")
                    or ""
                )

                text = self._clean_text(text)
                dangerous = self._is_dangerous(text)

                tests.append(
                    TestCase(
                        name=(
                            f"Button audit #{index + 1}: "
                            f"{text[:60] or 'unnamed'}"
                        ),
                        category=(
                            "dangerous_action_audit"
                            if dangerous
                            else "button_audit"
                        ),
                        target_url=page.url,
                        element_type="button",
                        destructive=dangerous,
                    )
                )

            # ----------------------------------------------------
            # CHECKBOXES
            # ----------------------------------------------------

            for index, _ in enumerate(
                getattr(page, "checkboxes", []) or []
            ):
                tests.append(
                    TestCase(
                        name=f"Checkbox audit #{index + 1}",
                        category="checkbox_audit",
                        target_url=page.url,
                        element_type="checkbox",
                    )
                )

            # ----------------------------------------------------
            # RADIOS
            # ----------------------------------------------------

            for index, _ in enumerate(
                getattr(page, "radios", []) or []
            ):
                tests.append(
                    TestCase(
                        name=f"Radio audit #{index + 1}",
                        category="radio_audit",
                        target_url=page.url,
                        element_type="radio",
                    )
                )

            # ----------------------------------------------------
            # TABLES
            # ----------------------------------------------------

            for index, _ in enumerate(
                getattr(page, "tables", []) or []
            ):
                tests.append(
                    TestCase(
                        name=f"Table audit #{index + 1}",
                        category="table_audit",
                        target_url=page.url,
                        element_type="table",
                    )
                )

            # ----------------------------------------------------
            # DEEP FINDINGS
            # ----------------------------------------------------

            deep = getattr(
                page,
                "deep",
                {},
            ) or {}

            deep_categories = (
                (
                    "empty_elements",
                    "Empty visible element audit",
                ),
                (
                    "hidden_interactive",
                    "Hidden interactive element audit",
                ),
                (
                    "offscreen_interactive",
                    "Off-screen interactive element audit",
                ),
                (
                    "missing_labels",
                    "Missing label audit",
                ),
                (
                    "broken_images",
                    "Broken image audit",
                ),
                (
                    "missing_alt",
                    "Missing image alt audit",
                ),
                (
                    "duplicate_ids",
                    "Duplicate ID audit",
                ),
                (
                    "overflow",
                    "Layout overflow audit",
                ),
            )

            for key, label in deep_categories:

                findings = deep.get(key, []) or []

                if findings:
                    tests.append(
                        TestCase(
                            name=(
                                f"{label} — "
                                f"{len(findings)} finding(s)"
                            ),
                            category="deep_finding_audit",
                            target_url=page.url,
                            test_data={
                                "finding_type": key,
                                "count": len(findings),
                            },
                        )
                    )

            # ----------------------------------------------------
            # CONSOLE / NETWORK
            # ----------------------------------------------------

            if getattr(
                page,
                "console_errors",
                [],
            ):
                tests.append(
                    TestCase(
                        name=(
                            f"Console error audit — "
                            f"{len(page.console_errors)} finding(s)"
                        ),
                        category="console_error_audit",
                        target_url=page.url,
                    )
                )

            if getattr(
                page,
                "console_warnings",
                [],
            ):
                tests.append(
                    TestCase(
                        name=(
                            f"Console warning audit — "
                            f"{len(page.console_warnings)} finding(s)"
                        ),
                        category="console_warning_audit",
                        target_url=page.url,
                    )
                )

            if getattr(
                page,
                "failed_requests",
                [],
            ):
                tests.append(
                    TestCase(
                        name=(
                            f"Network failure audit — "
                            f"{len(page.failed_requests)} finding(s)"
                        ),
                        category="network_failure_audit",
                        target_url=page.url,
                    )
                )

            if getattr(
                page,
                "client_errors",
                [],
            ):
                tests.append(
                    TestCase(
                        name=(
                            f"HTTP 4xx audit — "
                            f"{len(page.client_errors)} finding(s)"
                        ),
                        category="http_4xx_audit",
                        target_url=page.url,
                    )
                )

            if getattr(
                page,
                "server_errors",
                [],
            ):
                tests.append(
                    TestCase(
                        name=(
                            f"HTTP 5xx audit — "
                            f"{len(page.server_errors)} finding(s)"
                        ),
                        category="http_5xx_audit",
                        target_url=page.url,
                    )
                )

            if getattr(
                page,
                "slow_requests",
                [],
            ):
                tests.append(
                    TestCase(
                        name=(
                            f"Slow network audit — "
                            f"{len(page.slow_requests)} finding(s)"
                        ),
                        category="slow_network_audit",
                        target_url=page.url,
                    )
                )

        return tests

    # ============================================================
    # EXECUTION
    # ============================================================

    async def execute(
        self,
        test_case: TestCase,
        attempt_index: int = 0,
    ) -> TestResult:

        try:
            await self.browser.goto(
                test_case.target_url
            )
        except Exception as exc:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail=f"Navigation error: {exc}",
            )

        page = self.browser.page

        if page is None:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail="Browser page unavailable.",
            )

        await self._wait_for_ui()

        # Never execute state-changing/destructive controls.
        if test_case.destructive:
            return TestResult(
                test_case=test_case,
                status="skipped",
                detail=(
                    "Potentially destructive or "
                    "state-changing action detected. "
                    "Action was NOT executed."
                ),
            )

        handlers = {
            "smoke": self._run_smoke_test,
            "deep_page_audit": self._run_deep_page_audit,
            "input_audit": self._run_element_audit,
            "textarea_audit": self._run_element_audit,
            "select_audit": self._run_element_audit,
            "button_audit": self._run_element_audit,
            "checkbox_audit": self._run_element_audit,
            "radio_audit": self._run_element_audit,
            "table_audit": self._run_element_audit,
            "empty_input_validation": self._run_empty_input_check,
            "invalid_email_validation": self._run_invalid_email_check,
            "phone_boundary": self._run_phone_boundary_check,
            "required_field_audit": self._run_required_field_check,
            "maxlength_audit": self._run_maxlength_check,
            "deep_finding_audit": self._run_deep_finding_audit,
            "console_error_audit": self._run_console_audit,
            "console_warning_audit": self._run_console_audit,
            "network_failure_audit": self._run_network_audit,
            "http_4xx_audit": self._run_network_audit,
            "http_5xx_audit": self._run_network_audit,
            "slow_network_audit": self._run_network_audit,
        }

        handler = handlers.get(
            test_case.category
        )

        if handler is None:
            return TestResult(
                test_case=test_case,
                status="skipped",
                detail="No executable handler for this category.",
            )

        return await handler(
            test_case,
            attempt_index,
        )

    # ============================================================
    # SMOKE
    # ============================================================

    async def _run_smoke_test(
        self,
        test_case: TestCase,
        attempt_index: int,
    ) -> TestResult:

        errors = [
            e.text
            for e in self.browser.monitor.console_errors()
        ]

        failed = [
            e.url
            for e in self.browser.monitor.failed_requests()
        ]

        if errors or failed:
            screenshot = await self._failure_screenshot(
                test_case,
                attempt_index,
            )

            return TestResult(
                test_case=test_case,
                status="failed",
                detail=(
                    f"{len(errors)} console error(s), "
                    f"{len(failed)} failed request(s)"
                ),
                console_errors=errors,
                failed_requests=failed,
                screenshot_path=screenshot,
            )

        return TestResult(
            test_case=test_case,
            status="passed",
            detail="Page loaded without captured failures.",
        )

    # ============================================================
    # DEEP PAGE
    # ============================================================

    async def _run_deep_page_audit(
        self,
        test_case: TestCase,
        attempt_index: int,
    ) -> TestResult:

        page = self.browser.page

        if page is None:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail="Browser page unavailable.",
            )

        try:
            result = await page.evaluate(
                """
                () => {
                    const all = [...document.querySelectorAll('*')];

                    const visible = el => {
                        const s = getComputedStyle(el);
                        const r = el.getBoundingClientRect();

                        return (
                            s.display !== 'none' &&
                            s.visibility !== 'hidden' &&
                            parseFloat(s.opacity || '1') > 0 &&
                            r.width > 0 &&
                            r.height > 0
                        );
                    };

                    const interactive = all.filter(
                        el =>
                            el.matches(
                                'a[href],button,input,select,textarea,' +
                                '[role="button"],[role="link"],' +
                                '[role="tab"],[role="checkbox"],' +
                                '[role="radio"],[role="combobox"],' +
                                '[role="textbox"],[role="switch"]'
                            )
                    );

                    const unnamed = interactive.filter(el => {
                        const text = (
                            el.innerText ||
                            el.getAttribute('aria-label') ||
                            el.getAttribute('title') ||
                            el.value ||
                            ''
                        ).trim();

                        return !text;
                    });

                    const emptyInputs = all.filter(el => {
                        if (!el.matches(
                            'input,textarea,select'
                        )) return false;

                        if (el.disabled) return false;

                        return (
                            !el.value &&
                            (
                                el.required ||
                                el.getAttribute(
                                    'aria-required'
                                ) === 'true'
                            )
                        );
                    });

                    const ids = {};

                    all.forEach(el => {
                        if (el.id) {
                            ids[el.id] =
                                (ids[el.id] || 0) + 1;
                        }
                    });

                    const duplicateIds =
                        Object.entries(ids)
                            .filter(
                                ([, count]) => count > 1
                            );

                    const brokenImages = [
                        ...document.images
                    ].filter(
                        img =>
                            img.complete &&
                            img.src &&
                            img.naturalWidth === 0
                    );

                    return {
                        total_elements: all.length,
                        interactive: interactive.length,
                        unnamed_interactive: unnamed.length,
                        required_empty_inputs:
                            emptyInputs.length,
                        duplicate_ids:
                            duplicateIds.length,
                        broken_images:
                            brokenImages.length,
                        horizontal_overflow:
                            document.documentElement.scrollWidth >
                            document.documentElement.clientWidth
                    };
                }
                """
            )

            findings = []

            for key, value in result.items():

                if key == "total_elements":
                    continue

                if value:
                    findings.append(
                        f"{key}={value}"
                    )

            if findings:
                return TestResult(
                    test_case=test_case,
                    status="failed",
                    detail=(
                        "Deep audit findings: "
                        + ", ".join(findings)
                    ),
                    screenshot_path=(
                        await self._failure_screenshot(
                            test_case,
                            attempt_index,
                        )
                    ),
                )

            return TestResult(
                test_case=test_case,
                status="passed",
                detail="Deep page audit found no immediate issues.",
            )

        except Exception as exc:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail=f"Deep page audit failed: {exc}",
            )

    # ============================================================
    # ELEMENT AUDIT
    # ============================================================

    async def _run_element_audit(
        self,
        test_case: TestCase,
        attempt_index: int,
    ) -> TestResult:

        page = self.browser.page

        selector_map = {
            "input": "input",
            "textarea": "textarea",
            "select": "select",
            "button": (
                "button,"
                'input[type="button"],'
                'input[type="submit"]'
            ),
            "checkbox": (
                'input[type="checkbox"],'
                '[role="checkbox"]'
            ),
            "radio": (
                'input[type="radio"],'
                '[role="radio"]'
            ),
            "table": "table",
            "interactive": (
                'a[href],button,input,select,textarea,'
                '[role="button"],[role="link"],'
                '[role="tab"],[role="menuitem"],'
                '[role="checkbox"],[role="radio"],'
                '[role="combobox"],[role="switch"],'
                '[role="textbox"]'
            ),
        }

        selector = selector_map.get(
            test_case.element_type or ""
        )

        if not selector:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail="Unknown element type.",
            )

        try:
            count = await page.locator(
                selector
            ).count()

            if count == 0:
                return TestResult(
                    test_case=test_case,
                    status="failed",
                    detail=(
                        f"Expected {test_case.element_type} "
                        "but none were found."
                    ),
                    screenshot_path=(
                        await self._failure_screenshot(
                            test_case,
                            attempt_index,
                        )
                    ),
                )

            return TestResult(
                test_case=test_case,
                status="passed",
                detail=(
                    f"Found {count} "
                    f"{test_case.element_type} element(s)."
                ),
            )

        except Exception as exc:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail=f"Element inspection failed: {exc}",
            )

    # ============================================================
    # DEEP FINDINGS
    # ============================================================

    async def _run_deep_finding_audit(
        self,
        test_case: TestCase,
        attempt_index: int,
    ) -> TestResult:

        finding_type = (
            test_case.test_data or {}
        ).get("finding_type")

        page = self.browser.page

        if page is None:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail="Browser page unavailable.",
            )

        # Finding existence is intentionally reported as a
        # finding, not automatically classified as a software bug.
        return TestResult(
            test_case=test_case,
            status="failed",
            detail=(
                f"Detected deep inspection finding type: "
                f"{finding_type}. "
                "Requires evidence/classification."
            ),
            screenshot_path=(
                await self._failure_screenshot(
                    test_case,
                    attempt_index,
                )
            ),
        )

    # ============================================================
    # CONSOLE
    # ============================================================

    async def _run_console_audit(
        self,
        test_case: TestCase,
        attempt_index: int,
    ) -> TestResult:

        errors = self.browser.monitor.console_errors()

        warnings = self.browser.monitor.console_warnings()

        if test_case.category == "console_error_audit":
            count = len(errors)
            kind = "console error"

        else:
            count = len(warnings)
            kind = "console warning"

        if count:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail=(
                    f"Captured {count} {kind}(s). "
                    "Requires classification/reproduction."
                ),
                console_errors=[
                    e.text
                    for e in errors
                ],
                screenshot_path=(
                    await self._failure_screenshot(
                        test_case,
                        attempt_index,
                    )
                ),
            )

        return TestResult(
            test_case=test_case,
            status="passed",
            detail=f"No {kind}s captured.",
        )

    # ============================================================
    # NETWORK
    # ============================================================

    async def _run_network_audit(
        self,
        test_case: TestCase,
        attempt_index: int,
    ) -> TestResult:

        monitor = self.browser.monitor

        if test_case.category == "http_5xx_audit":
            findings = monitor.server_errors()
            label = "HTTP 5xx"

        elif test_case.category == "http_4xx_audit":
            findings = monitor.client_errors()
            label = "HTTP 4xx"

        elif test_case.category == "slow_network_audit":
            findings = monitor.slow_requests()
            label = "slow network"

        else:
            findings = monitor.failed_requests()
            label = "network failure"

        if findings:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail=(
                    f"Detected {len(findings)} "
                    f"{label} finding(s). "
                    "Requires endpoint-level classification."
                ),
                failed_requests=[
                    e.url
                    for e in findings
                ],
                screenshot_path=(
                    await self._failure_screenshot(
                        test_case,
                        attempt_index,
                    )
                ),
            )

        return TestResult(
            test_case=test_case,
            status="passed",
            detail=f"No {label} findings captured.",
        )

    # ============================================================
    # EMPTY FIELD
    # ============================================================

    async def _run_empty_input_check(
        self,
        test_case: TestCase,
        attempt_index: int,
    ) -> TestResult:

        field_name = (
            test_case.test_data or {}
        ).get("field_name")

        locator = await self._find_field(
            field_name
        )

        if locator is None:
            return TestResult(
                test_case=test_case,
                status="skipped",
                detail=(
                    "Target field could not be uniquely located."
                ),
            )

        try:
            required = await locator.get_attribute(
                "required"
            )

            aria_required = await locator.get_attribute(
                "aria-required"
            )

            value_missing = await locator.evaluate(
                """
                el => {
                    try {
                        return !!el.validity.valueMissing;
                    } catch (_) {
                        return false;
                    }
                }
                """
            )

            if (
                required is not None
                or aria_required == "true"
                or value_missing
            ):
                return TestResult(
                    test_case=test_case,
                    status="passed",
                    detail=(
                        "Required/empty validation metadata "
                        "is exposed."
                    ),
                )

            return TestResult(
                test_case=test_case,
                status="passed",
                detail=(
                    "Field inspected. No required constraint "
                    "was exposed."
                ),
            )

        except Exception as exc:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail=f"Empty validation inspection failed: {exc}",
            )

    # ============================================================
    # EMAIL
    # ============================================================

    async def _run_invalid_email_check(
        self,
        test_case: TestCase,
        attempt_index: int,
    ) -> TestResult:

        field_name = (
            test_case.test_data or {}
        ).get("field_name")

        locator = await self._find_field(
            field_name
        )

        if locator is None:
            return TestResult(
                test_case=test_case,
                status="skipped",
                detail="Email field could not be uniquely located.",
            )

        try:
            input_type = await locator.get_attribute(
                "type"
            )

            pattern = await locator.get_attribute(
                "pattern"
            )

            if input_type == "email" or pattern:
                return TestResult(
                    test_case=test_case,
                    status="passed",
                    detail=(
                        "Email validation rule exposed "
                        f"(type=email={input_type == 'email'}, "
                        f"pattern={bool(pattern)})."
                    ),
                )

            return TestResult(
                test_case=test_case,
                status="failed",
                detail=(
                    "Email field found but no native "
                    "validation rule exposed."
                ),
                screenshot_path=(
                    await self._failure_screenshot(
                        test_case,
                        attempt_index,
                    )
                ),
            )

        except Exception as exc:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail=f"Email validation inspection failed: {exc}",
            )

    # ============================================================
    # PHONE
    # ============================================================

    async def _run_phone_boundary_check(
        self,
        test_case: TestCase,
        attempt_index: int,
    ) -> TestResult:

        field_name = (
            test_case.test_data or {}
        ).get("field_name")

        locator = await self._find_field(
            field_name
        )

        if locator is None:
            return TestResult(
                test_case=test_case,
                status="skipped",
                detail="Phone field could not be located.",
            )

        try:
            min_length = await locator.get_attribute(
                "minlength"
            )

            max_length = await locator.get_attribute(
                "maxlength"
            )

            pattern = await locator.get_attribute(
                "pattern"
            )

            return TestResult(
                test_case=test_case,
                status="passed",
                detail=(
                    "Phone metadata inspected: "
                    f"minlength={min_length}, "
                    f"maxlength={max_length}, "
                    f"pattern={'present' if pattern else 'none'}."
                ),
            )

        except Exception as exc:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail=f"Phone boundary inspection failed: {exc}",
            )

    # ============================================================
    # REQUIRED
    # ============================================================

    async def _run_required_field_check(
        self,
        test_case: TestCase,
        attempt_index: int,
    ) -> TestResult:

        field_name = (
            test_case.test_data or {}
        ).get("field_name")

        locator = await self._find_field(
            field_name
        )

        if locator is None:
            return TestResult(
                test_case=test_case,
                status="skipped",
                detail="Required field could not be located.",
            )

        try:
            required = await locator.get_attribute(
                "required"
            )

            aria_required = await locator.get_attribute(
                "aria-required"
            )

            if (
                required is not None
                or aria_required == "true"
            ):
                return TestResult(
                    test_case=test_case,
                    status="passed",
                    detail="Required constraint exposed.",
                )

            return TestResult(
                test_case=test_case,
                status="failed",
                detail=(
                    "Field was marked for required-field "
                    "testing but no required metadata was exposed."
                ),
                screenshot_path=(
                    await self._failure_screenshot(
                        test_case,
                        attempt_index,
                    )
                ),
            )

        except Exception as exc:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail=f"Required-field inspection failed: {exc}",
            )

    # ============================================================
    # MAX LENGTH
    # ============================================================

    async def _run_maxlength_check(
        self,
        test_case: TestCase,
        attempt_index: int,
    ) -> TestResult:

        data = test_case.test_data or {}

        field_name = data.get("field_name")
        expected_max = data.get("max_length")

        locator = await self._find_field(
            field_name
        )

        if locator is None:
            return TestResult(
                test_case=test_case,
                status="skipped",
                detail="Target field could not be located.",
            )

        try:
            actual_max = await locator.get_attribute(
                "maxlength"
            )

            if actual_max is None:
                return TestResult(
                    test_case=test_case,
                    status="failed",
                    detail=(
                        "Expected maxlength metadata was "
                        "not exposed."
                    ),
                    screenshot_path=(
                        await self._failure_screenshot(
                            test_case,
                            attempt_index,
                        )
                    ),
                )

            if (
                expected_max is not None
                and str(actual_max)
                != str(expected_max)
            ):
                return TestResult(
                    test_case=test_case,
                    status="failed",
                    detail=(
                        f"Expected maxlength={expected_max}, "
                        f"actual={actual_max}."
                    ),
                    screenshot_path=(
                        await self._failure_screenshot(
                            test_case,
                            attempt_index,
                        )
                    ),
                )

            return TestResult(
                test_case=test_case,
                status="passed",
                detail=f"maxlength={actual_max} confirmed.",
            )

        except Exception as exc:
            return TestResult(
                test_case=test_case,
                status="failed",
                detail=f"Max-length inspection failed: {exc}",
            )

    # ============================================================
    # LOCATOR
    # ============================================================

    async def _find_field(
        self,
        field_name: Optional[str],
    ):

        page = self.browser.page

        if page is None or not field_name:
            return None

        value = str(field_name)

        selectors = [
            (
                "input",
                "name",
            ),
            (
                "textarea",
                "name",
            ),
            (
                "select",
                "name",
            ),
            (
                "input",
                "id",
            ),
            (
                "textarea",
                "id",
            ),
            (
                "select",
                "id",
            ),
        ]

        for tag, attribute in selectors:

            try:
                locator = page.locator(
                    f'{tag}[{attribute}="{value}"]'
                )

                count = await locator.count()

                if count == 1:
                    return locator.first

            except Exception:
                continue

        return None

    # ============================================================
    # WAIT
    # ============================================================

    async def _wait_for_ui(self) -> None:

        page = self.browser.page

        if page is None:
            return

        try:
            await page.wait_for_load_state(
                "networkidle",
                timeout=5000,
            )
        except Exception:
            pass

        try:
            await page.wait_for_timeout(
                700
            )
        except Exception:
            pass

    # ============================================================
    # SCREENSHOT
    # ============================================================

    async def _failure_screenshot(
        self,
        test_case: TestCase,
        attempt_index: int,
    ) -> Optional[str]:

        if not self.screenshot_dir:
            return None

        safe_name = re.sub(
            r"[^a-zA-Z0-9_-]+",
            "_",
            test_case.name,
        )[:100]

        path = (
            f"{self.screenshot_dir}/"
            f"failure_{safe_name}_"
            f"{attempt_index}.png"
        )

        try:
            await self.browser.screenshot(
                path
            )
            return path
        except Exception as exc:
            logger.warning(
                "Could not save failure screenshot: %s",
                exc,
            )
            return None

    # ============================================================
    # HELPERS
    # ============================================================

    @staticmethod
    def _clean_text(
        value: str,
    ) -> str:

        value = value or ""

        value = re.sub(
            r"\s+",
            " ",
            value,
        )

        return value.strip()

    @staticmethod
    def _is_dangerous(
        text: str,
    ) -> bool:

        normalized = FunctionalAgent._clean_text(
            text
        ).lower()

        if not normalized:
            return False

        return any(
            word in normalized
            for word in DANGEROUS_WORDS
        )
