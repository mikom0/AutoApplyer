from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Iterable, List

from autoapplyer.models import (
    CVDocument,
    CVEducationEntry,
    CVExperienceEntry,
    CVSkillsLine,
)

logger = logging.getLogger(__name__)


TEMPLATE_DIR = Path(__file__).parent / "cv_templates"


def load_template_css(template_name: str = "default") -> str:
    css_path = TEMPLATE_DIR / f"{template_name}.css"
    if not css_path.exists():
        raise FileNotFoundError(f"CV template CSS not found: {css_path}")
    return css_path.read_text(encoding="utf-8")


def render_cv_html(cv: CVDocument, template_name: str = "default") -> str:
    css = load_template_css(template_name)
    body = _render_body(cv)
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\"><head>"
        "<meta charset=\"utf-8\">"
        f"<title>{html.escape(cv.contact.full_name)} CV</title>"
        f"<style>{css}</style>"
        "</head><body>"
        f"{body}"
        "</body></html>"
    )


def _render_body(cv: CVDocument) -> str:
    parts: List[str] = ["<div class=\"cv\">"]
    parts.append(_render_header(cv))
    if cv.experience:
        parts.append(_render_experience_section("Experience", cv.experience))
    if cv.education:
        parts.append(_render_education_section(cv.education))
    if cv.extracurricular:
        parts.append(_render_experience_section("Extracurricular", cv.extracurricular))
    if cv.skills_lines:
        parts.append(_render_skills_section(cv.skills_section_title, cv.skills_lines))
    parts.append("</div>")
    return "".join(parts)


def _render_header(cv: CVDocument) -> str:
    contact_bits = [
        html.escape(cv.contact.phone),
        html.escape(cv.contact.email),
        f"LinkedIn: <a href=\"{html.escape(cv.contact.linkedin_url, quote=True)}\">{html.escape(cv.contact.linkedin_label)}</a>",
    ]
    return (
        "<header class=\"header\">"
        f"<h1 class=\"name\">{html.escape(cv.contact.full_name)}</h1>"
        f"<div class=\"contact-line\">{' | '.join(contact_bits)}</div>"
        "</header>"
    )


def _render_experience_section(title: str, entries: Iterable[CVExperienceEntry]) -> str:
    parts = [
        "<section class=\"section\">",
        f"<h2 class=\"section-header\">{html.escape(title)}</h2>",
        "<hr class=\"section-rule\">",
    ]
    for entry in entries:
        parts.append(_render_experience_entry(entry))
    parts.append("</section>")
    return "".join(parts)


def _render_experience_entry(entry: CVExperienceEntry) -> str:
    if entry.location:
        title_html = (
            f"{html.escape(entry.company)} {html.escape(entry.role)} "
            f"&ndash; <span class=\"entry-location\">{html.escape(entry.location)}</span>"
        )
    else:
        title_html = (
            f"{html.escape(entry.role)} &ndash; "
            f"<span class=\"entry-location\">{html.escape(entry.company)}</span>"
        )
    headline = (
        "<div class=\"entry-headline\">"
        f"<div class=\"entry-title\">{title_html}</div>"
        f"<div class=\"entry-dates\">{html.escape(entry.date_range)}</div>"
        "</div>"
    )
    bullets = "".join(f"<li>{html.escape(b)}</li>" for b in entry.bullets)
    return (
        "<div class=\"entry\">"
        f"{headline}"
        f"<ul class=\"bullets\">{bullets}</ul>"
        "</div>"
    )


def _render_education_section(entries: Iterable[CVEducationEntry]) -> str:
    parts = [
        "<section class=\"section\">",
        "<h2 class=\"section-header\">Education</h2>",
        "<hr class=\"section-rule\">",
    ]
    for entry in entries:
        parts.append(_render_education_entry(entry))
    parts.append("</section>")
    return "".join(parts)


def _render_education_entry(entry: CVEducationEntry) -> str:
    title_bits = [
        f"{html.escape(entry.institution)} &ndash; {html.escape(entry.qualification)}"
    ]
    if entry.grade:
        title_bits.append(
            f"&ndash; <span class=\"grade\">{html.escape(entry.grade)}</span>"
        )
    title = (
        "<div class=\"education-title\">"
        f"<div>{' '.join(title_bits)}</div>"
        f"<div>{html.escape(entry.date_range)}</div>"
        "</div>"
    )
    details = "".join(
        "<div class=\"education-detail\">"
        f"<span class=\"label\">{html.escape(detail.label)}:</span> "
        f"{html.escape(detail.body)}"
        "</div>"
        for detail in entry.details
    )
    return (
        "<div class=\"education-entry\">"
        f"{title}"
        f"<div class=\"education-details\">{details}</div>"
        "</div>"
    )


def _render_skills_section(title: str, lines: Iterable[CVSkillsLine]) -> str:
    parts = [
        "<section class=\"section\">",
        f"<h2 class=\"section-header\">{html.escape(title)}</h2>",
        "<hr class=\"section-rule\">",
        "<div class=\"skills-block\">",
    ]
    for line in lines:
        parts.append(
            "<div class=\"skills-line\">"
            f"<span class=\"label\">{html.escape(line.label)}:</span> "
            f"{html.escape(line.body)}"
            "</div>"
        )
    parts.append("</div></section>")
    return "".join(parts)


def render_cv_pdf(cv: CVDocument, output_path: Path, template_name: str = "default") -> Path:
    from playwright.sync_api import sync_playwright

    output_path.parent.mkdir(parents=True, exist_ok=True)
    html_content = render_cv_html(cv, template_name=template_name)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.set_content(html_content, wait_until="load")
            page.pdf(
                path=str(output_path),
                format="A4",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                prefer_css_page_size=True,
            )
        finally:
            browser.close()
    logger.info("Rendered CV to %s", output_path)
    return output_path


def render_cv_html_to_file(cv: CVDocument, output_path: Path, template_name: str = "default") -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_cv_html(cv, template_name=template_name), encoding="utf-8")
    return output_path
