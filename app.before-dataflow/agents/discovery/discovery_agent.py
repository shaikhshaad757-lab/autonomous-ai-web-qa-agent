"""Deep autonomous application discovery.

Safe/read-only discovery:
- follows same-origin GET links
- inspects DOM/UI structure
- records forms and controls
- records hidden/empty/off-screen/accessibility findings
- records console/network findings
- saves screenshots

Destructive routes/actions are skipped.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set
from urllib.parse import urljoin, urlparse

from app.browser.browser_manager import BrowserManager

logger = logging.getLogger(
    "hms_qa_agent.discovery"
)


SKIP_PATH_HINTS = (
    "logout",
    "sign-out",
    "signout",
    "delete",
    "remove",
    "destroy",
)


@dataclass
class DiscoveredPage:

    url: str
    title: str
    depth: int

    forms: List[dict] = field(
        default_factory=list
    )

    links: List[dict] = field(
        default_factory=list
    )

    inputs: List[dict] = field(
        default_factory=list
    )

    textareas: List[dict] = field(
        default_factory=list
    )

    selects: List[dict] = field(
        default_factory=list
    )

    buttons: List[dict] = field(
        default_factory=list
    )

    checkboxes: List[dict] = field(
        default_factory=list
    )

    radios: List[dict] = field(
        default_factory=list
    )

    tables: List[dict] = field(
        default_factory=list
    )

    interactive_elements: List[dict] = field(
        default_factory=list
    )

    deep: Dict[str, Any] = field(
        default_factory=dict
    )

    screenshot_path: str = ""

    console_errors: List[str] = field(
        default_factory=list
    )

    console_warnings: List[str] = field(
        default_factory=list
    )

    failed_requests: List[dict] = field(
        default_factory=list
    )

    client_errors: List[dict] = field(
        default_factory=list
    )

    server_errors: List[dict] = field(
        default_factory=list
    )

    slow_requests: List[dict] = field(
        default_factory=list
    )


class DiscoveryAgent:

    def __init__(
        self,
        browser: BrowserManager,
        base_url: str,
        max_pages: int = 40,
        max_depth: int = 4,
        screenshot_dir: str | None = None,
    ) -> None:

        self.browser = browser
        self.base_url = base_url

        self.base_origin = urlparse(
            base_url
        ).netloc

        self.max_pages = max_pages
        self.max_depth = max_depth
        self.screenshot_dir = screenshot_dir

        self.visited: Set[str] = set()

        self.pages: List[
            DiscoveredPage
        ] = []

    # ------------------------------------------------------------
    # URL HELPERS
    # ------------------------------------------------------------

    def _same_origin(
        self,
        url: str,
    ) -> bool:

        try:
            return (
                urlparse(url).netloc
                == self.base_origin
            )

        except ValueError:
            return False

    def _should_skip(
        self,
        url: str,
    ) -> bool:

        lower = url.lower()

        return any(
            hint in lower
            for hint in SKIP_PATH_HINTS
        )

    def _normalize(
        self,
        href: str,
        current_url: str,
    ) -> str:

        absolute = urljoin(
            current_url,
            href,
        )

        parsed = urlparse(
            absolute
        )

        return parsed._replace(
            fragment=""
        ).geturl()

    # ------------------------------------------------------------
    # DEEP FINDING LOG
    # ------------------------------------------------------------

    def _finding_count(
        self,
        page: DiscoveredPage,
    ) -> int:

        deep = page.deep or {}

        total = 0

        total += len(
            deep.get(
                "empty_elements",
                []
            )
        )

        total += len(
            deep.get(
                "hidden_interactive",
                []
            )
        )

        total += len(
            deep.get(
                "offscreen_interactive",
                []
            )
        )

        total += len(
            deep.get(
                "missing_labels",
                []
            )
        )

        total += len(
            deep.get(
                "broken_images",
                []
            )
        )

        total += len(
            deep.get(
                "missing_alt",
                []
            )
        )

        total += len(
            deep.get(
                "duplicate_ids",
                []
            )
        )

        total += len(
            deep.get(
                "overflow",
                []
            )
        )

        total += len(
            page.console_errors
        )

        total += len(
            page.failed_requests
        )

        total += len(
            page.client_errors
        )

        total += len(
            page.server_errors
        )

        return total

    # ------------------------------------------------------------
    # CRAWL
    # ------------------------------------------------------------

    async def crawl(
        self,
        start_url: str,
    ) -> List[DiscoveredPage]:

        queue: List[
            tuple[str, int]
        ] = [
            (start_url, 0)
        ]

        while (
            queue
            and len(self.pages)
            < self.max_pages
        ):

            url, depth = queue.pop(0)

            if (
                url in self.visited
                or depth > self.max_depth
            ):
                continue

            if self._should_skip(url):

                logger.debug(
                    "Skipping %s "
                    "(destructive route hint)",
                    url,
                )

                continue

            self.visited.add(url)

            try:

                await self.browser.goto(
                    url
                )

            except Exception as exc:

                logger.warning(
                    "Failed to navigate to %s: %s",
                    url,
                    exc,
                )

                continue

            try:

                metadata = (
                    await self.browser
                    .extract_page_metadata()
                )

            except Exception as exc:

                logger.warning(
                    "Metadata extraction failed "
                    "for %s: %s",
                    url,
                    exc,
                )

                continue

            screenshot_path = ""

            if self.screenshot_dir:

                safe_name = (
                    f"page_{len(self.pages):03d}.png"
                )

                screenshot_path = (
                    f"{self.screenshot_dir}/"
                    f"{safe_name}"
                )

                try:

                    await self.browser.screenshot(
                        screenshot_path
                    )

                except Exception as exc:

                    logger.warning(
                        "Screenshot failed for %s: %s",
                        url,
                        exc,
                    )

                    screenshot_path = ""

            page_record = DiscoveredPage(

                url=url,

                title=metadata.get(
                    "title",
                    "",
                ),

                depth=depth,

                forms=metadata.get(
                    "forms",
                    [],
                ),

                links=metadata.get(
                    "links",
                    [],
                ),

                inputs=metadata.get(
                    "inputs",
                    [],
                ),

                textareas=metadata.get(
                    "textareas",
                    [],
                ),

                selects=metadata.get(
                    "selects",
                    [],
                ),

                buttons=metadata.get(
                    "buttons",
                    [],
                ),

                checkboxes=metadata.get(
                    "checkboxes",
                    [],
                ),

                radios=metadata.get(
                    "radios",
                    [],
                ),

                tables=metadata.get(
                    "tables",
                    [],
                ),

                deep=metadata.get(
                    "deep",
                    {},
                ),

                screenshot_path=screenshot_path,

                console_errors=metadata.get(
                    "console_errors",
                    [],
                ),

                console_warnings=metadata.get(
                    "console_warnings",
                    [],
                ),

                failed_requests=metadata.get(
                    "failed_requests",
                    [],
                ),

                client_errors=metadata.get(
                    "client_errors",
                    [],
                ),

                server_errors=metadata.get(
                    "server_errors",
                    [],
                ),

                slow_requests=metadata.get(
                    "slow_requests",
                    [],
                ),
            )

            # Build a normalized interactive inventory.
            page_record.interactive_elements = []

            for button in page_record.buttons:

                page_record.interactive_elements.append(
                    {
                        "type": "button",
                        **button,
                    }
                )

            for link in page_record.links:

                page_record.interactive_elements.append(
                    {
                        "type": "link",
                        **link,
                    }
                )

            for field in page_record.inputs:

                page_record.interactive_elements.append(
                    {
                        "type": "input",
                        **field,
                    }
                )

            for field in page_record.selects:

                page_record.interactive_elements.append(
                    {
                        "type": "select",
                        **field,
                    }
                )

            for field in page_record.textareas:

                page_record.interactive_elements.append(
                    {
                        "type": "textarea",
                        **field,
                    }
                )

            page_record.interactive_elements.extend(
                {
                    "type": "checkbox",
                    **item,
                }
                for item in page_record.checkboxes
            )

            page_record.interactive_elements.extend(
                {
                    "type": "radio",
                    **item,
                }
                for item in page_record.radios
            )

            self.pages.append(
                page_record
            )

            deep = (
                page_record.deep
                or {}
            )

            logger.info(
                (
                    "Discovered [%d/%d] depth=%d %s "
                    "(%s) — links=%d forms=%d "
                    "inputs=%d buttons=%d "
                    "selects=%d checkboxes=%d "
                    "radios=%d tables=%d "
                    "interactive=%d "
                    "deep_findings=%d "
                    "console_errors=%d "
                    "warnings=%d "
                    "network_failures=%d "
                    "4xx=%d 5xx=%d slow=%d"
                ),
                len(self.pages),
                self.max_pages,
                depth,
                url,
                page_record.title,
                len(page_record.links),
                len(page_record.forms),
                len(page_record.inputs),
                len(page_record.buttons),
                len(page_record.selects),
                len(page_record.checkboxes),
                len(page_record.radios),
                len(page_record.tables),
                len(page_record.interactive_elements),
                self._finding_count(
                    page_record
                ),
                len(page_record.console_errors),
                len(page_record.console_warnings),
                len(page_record.failed_requests),
                len(page_record.client_errors),
                len(page_record.server_errors),
                len(page_record.slow_requests),
            )

            # Queue only safe same-origin GET destinations.
            for link in page_record.links:

                href = link.get(
                    "href"
                )

                if not href:
                    continue

                normalized = self._normalize(
                    href,
                    url,
                )

                if (
                    self._same_origin(
                        normalized
                    )
                    and normalized
                    not in self.visited
                    and not self._should_skip(
                        normalized
                    )
                    and normalized.startswith(
                        (
                            "http://",
                            "https://",
                        )
                    )
                ):

                    queue.append(
                        (
                            normalized,
                            depth + 1,
                        )
                    )

        return self.pages

    # ------------------------------------------------------------
    # SITE MAP
    # ------------------------------------------------------------

    def build_site_map(
        self,
    ) -> Dict[str, Any]:

        return {

            "base_url":
                self.base_url,

            "pages_discovered":
                len(self.pages),

            "pages": [

                {
                    "url": page.url,

                    "title":
                        page.title,

                    "depth":
                        page.depth,

                    "form_count":
                        len(page.forms),

                    "link_count":
                        len(page.links),

                    "input_count":
                        len(page.inputs),

                    "textarea_count":
                        len(page.textareas),

                    "select_count":
                        len(page.selects),

                    "button_count":
                        len(page.buttons),

                    "checkbox_count":
                        len(page.checkboxes),

                    "radio_count":
                        len(page.radios),

                    "table_count":
                        len(page.tables),

                    "interactive_count":
                        len(
                            page.interactive_elements
                        ),

                    "deep_findings":
                        page.deep,

                    "console_error_count":
                        len(
                            page.console_errors
                        ),

                    "console_warning_count":
                        len(
                            page.console_warnings
                        ),

                    "failed_request_count":
                        len(
                            page.failed_requests
                        ),

                    "client_error_count":
                        len(
                            page.client_errors
                        ),

                    "server_error_count":
                        len(
                            page.server_errors
                        ),

                    "slow_request_count":
                        len(
                            page.slow_requests
                        ),

                    "screenshot_path":
                        page.screenshot_path,
                }

                for page in self.pages
            ],
        }
