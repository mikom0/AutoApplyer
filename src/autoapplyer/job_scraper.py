from __future__ import annotations

import logging
import re
from typing import List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


_JD_SELECTORS: List[str] = [
    "[data-automation-id='jobPostingDescription']",
    "[data-automation-id='jobPostingHeader'] ~ div",
    "[data-qa='job-description']",
    "[data-source='description']",
    "#jobDescriptionText",
    "#content .opening",
    ".job__description",
    "section.job-description",
    "div.posting-content",
    "main article",
    "main",
    "[role='main']",
]

_TITLE_SELECTORS: List[str] = [
    "[data-automation-id='jobPostingHeader']",
    "h1[data-qa='posting-name']",
    "h1.posting-headline h2",
    "h1.app-title",
    "h1",
]

_EMPLOYER_HINTS_FROM_TITLE = re.compile(r"^(.+?)\s*[\|\-–]\s*(.+)$")
_WORKDAY_SUBDOMAIN = re.compile(r"^(?P<sub>[^.]+)\.(?P<region>wd\d+)\.myworkdayjobs\.com$")


class ScrapedJobPosting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str
    employer_name: str
    job_title: Optional[str] = None
    job_description_text: str
    extraction_selector: Optional[str] = None
    warnings: List[str] = []


def scrape_job_posting(
    url: str,
    headed: bool = True,
    timeout_ms: int = 15000,
    settle_ms: int = 2500,
) -> ScrapedJobPosting:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    warnings: List[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        try:
            context = browser.new_context()
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                warnings.append("Page load timeout, scraping whatever rendered so far.")
            page.wait_for_timeout(settle_ms)

            jd_text, jd_selector = _extract_first_nonempty(page, _JD_SELECTORS, min_chars=180)
            if not jd_text:
                body_text = (page.inner_text("body") or "").strip()
                jd_text = body_text
                jd_selector = "body"
                if len(body_text) < 200:
                    warnings.append("Scraped JD text is unusually short; the page may require interaction.")

            job_title, _ = _extract_first_nonempty(page, _TITLE_SELECTORS, min_chars=2)
            page_title = page.title() or ""
            employer_name = _infer_employer(url, page_title, job_title)

            return ScrapedJobPosting(
                source_url=url,
                employer_name=employer_name,
                job_title=(job_title or _job_title_from_page_title(page_title)) or None,
                job_description_text=jd_text,
                extraction_selector=jd_selector,
                warnings=warnings,
            )
        finally:
            browser.close()


def _extract_first_nonempty(page, selectors: List[str], min_chars: int) -> tuple[str, Optional[str]]:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            text = (locator.inner_text(timeout=1500) or "").strip()
            if len(text) >= min_chars:
                return text, selector
        except Exception as exc:
            logger.debug("Selector %s failed: %s", selector, exc)
    return "", None


def _infer_employer(url: str, page_title: str, job_title: Optional[str]) -> str:
    host = urlparse(url).hostname or ""
    workday = _WORKDAY_SUBDOMAIN.match(host)
    if workday:
        return workday.group("sub").replace("-", " ").title()

    if page_title:
        match = _EMPLOYER_HINTS_FROM_TITLE.match(page_title)
        if match:
            left, right = match.group(1).strip(), match.group(2).strip()
            if job_title and job_title.lower() in left.lower():
                return right
            if job_title and job_title.lower() in right.lower():
                return left
            return right

    host_parts = host.split(".")
    if len(host_parts) >= 2:
        return host_parts[-2].replace("-", " ").title()
    return "Unknown Employer"


def _job_title_from_page_title(page_title: str) -> Optional[str]:
    if not page_title:
        return None
    match = _EMPLOYER_HINTS_FROM_TITLE.match(page_title)
    if match:
        return match.group(1).strip() or None
    return page_title.strip() or None
