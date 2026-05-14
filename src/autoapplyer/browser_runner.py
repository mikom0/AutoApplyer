from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Iterable, Optional, Set

from autoapplyer.config import resolve_application_url, resolve_local_path
from autoapplyer.field_resolution import FieldResolver, ResolvedField, display_value, normalize_label
from autoapplyer.llm import AnswerGenerator
from autoapplyer.models import (
    ApplicationRunContext,
    CandidateProfile,
    EmployerConfig,
    FieldEvent,
    FieldMapping,
    FreeTextFieldConfig,
    JobApplicationContext,
    LLMMode,
)
from autoapplyer.writing_assistant import WritingAssistant

logger = logging.getLogger(__name__)


class ReviewGateReached(RuntimeError):
    """Raised internally when the browser reaches a final manual-review boundary."""


class WorkdayLikeRunner:
    def __init__(
        self,
        profile: CandidateProfile,
        employer_config: EmployerConfig,
        employer_config_path: Path,
        answer_generator: AnswerGenerator,
        writing_assistant: Optional[WritingAssistant] = None,
        llm_mode: LLMMode = "off",
        dry_run: bool = False,
        headed: bool = True,
        storage_state: Optional[Path] = None,
        keep_open: bool = True,
        artifacts_dir: Path = Path("runs"),
        resume_path_override: Optional[Path] = None,
    ):
        self.profile = profile
        self.employer_config = employer_config
        self.employer_config_path = employer_config_path
        self.answer_generator = answer_generator
        self.writing_assistant = writing_assistant
        self.llm_mode = llm_mode
        self.dry_run = dry_run
        self.headed = headed
        self.storage_state = storage_state
        self.keep_open = keep_open
        self.artifacts_dir = artifacts_dir
        self.resume_path_override = resume_path_override
        self.resolver = FieldResolver(profile, employer_config)
        self._filled_keys: Set[str] = set()
        self._resume_uploaded = False

    def run(self) -> ApplicationRunContext:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        run_id = time.strftime("%Y%m%d-%H%M%S")
        run_dir = self.artifacts_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        job_url = resolve_application_url(self.employer_config_path, self.employer_config.application_url)
        run_context = ApplicationRunContext(
            run_id=run_id,
            employer=self.employer_config.employer_name,
            job_url=job_url,
            job_title=self.employer_config.job_title,
            dry_run=self.dry_run,
        )

        logger.info("Starting run %s for %s", run_id, self.employer_config.employer_name)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not self.headed)
            context_kwargs = {}
            if self.storage_state and self.storage_state.exists():
                context_kwargs["storage_state"] = str(self.storage_state)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.goto(job_url)

            try:
                self._walk_flow(page, run_context, run_dir)
            except ReviewGateReached:
                if self.keep_open and self.headed:
                    logger.info("Manual review gate reached. Browser will remain open for review.")
                else:
                    logger.info("Manual review gate reached. Closing browser as requested.")
            except PlaywrightTimeoutError as exc:
                run_context.add_event(
                    FieldEvent(
                        label="browser",
                        status="unresolved",
                        source="system",
                        message=f"Playwright timeout: {exc}",
                    )
                )
                logger.exception("Playwright timeout")
            finally:
                screenshot_path = run_dir / "final-page.png"
                try:
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    run_context.artifact_paths["final_screenshot"] = str(screenshot_path)
                except Exception as exc:  # pragma: no cover - best-effort artifact
                    logger.warning("Could not save final screenshot: %s", exc)
                run_json = run_dir / "run.json"
                run_context.artifact_paths["run_json"] = str(run_json)
                run_context.save_json(run_json)

                if self.keep_open and self.headed and not self.dry_run:
                    logger.info("Leaving browser open. Close it manually when review is complete.")
                    try:
                        page.pause()
                    except Exception as exc:
                        logger.info("Browser review session ended: %s", exc)
                else:
                    context.close()
                    browser.close()

        return run_context

    def _walk_flow(self, page, run_context: ApplicationRunContext, run_dir: Path) -> None:
        if self.employer_config.ats_family == "generic_form":
            self._click_form_opener(page, run_context)
            self._attempt_resume_upload(page, run_context)
            self._attempt_free_text_fields(page, run_context)
            self._attempt_auto_detect_fields(page, run_context)
            self._attempt_configured_fields(page, run_context, self.employer_config.field_mappings)
            self._raise_if_submit_visible(page, run_context)
            run_context.add_event(
                FieldEvent(
                    label="review",
                    status="review_gate",
                    source="system",
                    message="Generic form pass complete. Stopping for manual review before any final submission.",
                )
            )
            return

        self._click_form_opener(page, run_context)
        max_steps = max(len(self.employer_config.steps), 1) + 5
        for step_index in range(max_steps):
            self._attempt_resume_upload(page, run_context)
            self._attempt_free_text_fields(page, run_context)
            self._attempt_auto_detect_fields(page, run_context)
            self._attempt_configured_fields(page, run_context, self.employer_config.field_mappings)
            if step_index < len(self.employer_config.steps):
                self._attempt_configured_fields(page, run_context, self.employer_config.steps[step_index].field_mappings)

            if self._raise_if_submit_visible(page, run_context):
                return
            if not self._click_next(page, step_index):
                run_context.add_event(
                    FieldEvent(
                        label="navigation",
                        status="skipped",
                        source="system",
                        message="No visible next/review button found; leaving page for manual review.",
                    )
                )
                return
            page.wait_for_timeout(700)

        run_context.add_event(
            FieldEvent(
                label="navigation",
                status="unresolved",
                source="system",
                message="Maximum step count reached before a submit boundary was found.",
            )
        )

    def _attempt_auto_detect_fields(self, page, run_context: ApplicationRunContext) -> None:
        try:
            label_locators = page.locator("label").all()
        except Exception as exc:
            logger.debug("Could not enumerate labels: %s", exc)
            return

        name_parts = (self.profile.personal.full_name or "").split(None, 1)
        first_name = name_parts[0] if name_parts else ""
        last_name = name_parts[1] if len(name_parts) > 1 else ""
        name_aliases = {
            "first name": first_name,
            "firstname": first_name,
            "given name": first_name,
            "last name": last_name,
            "lastname": last_name,
            "surname": last_name,
            "family name": last_name,
        }

        for label_locator in label_locators:
            try:
                if not label_locator.is_visible():
                    continue
                text = (label_locator.text_content() or "").strip()
                if not text or len(text) > 80:
                    continue
                normalized = normalize_label(text)
                if not normalized or normalized in self._filled_keys:
                    continue

                resolved = self.resolver.resolve_label(text)
                if not resolved and normalized in name_aliases and name_aliases[normalized]:
                    resolved = ResolvedField(
                        label=text,
                        value=name_aliases[normalized],
                        source="alias",
                        profile_path="personal.full_name",
                    )
                if not resolved:
                    continue

                if self._fill_any_label(page, [text], resolved, run_context):
                    self._filled_keys.add(normalized)
            except Exception as exc:
                logger.debug("Auto-detect skip for label: %s", exc)

    def _attempt_configured_fields(
        self,
        page,
        run_context: ApplicationRunContext,
        mappings: Iterable[FieldMapping],
    ) -> None:
        for mapping in mappings:
            labels = mapping.labels or ([mapping.profile_path] if mapping.profile_path else [])
            if not labels:
                continue
            fill_key = "|".join(normalize_label(label) for label in labels)
            if fill_key in self._filled_keys:
                continue

            resolved = self.resolver.resolve_mapping(mapping)
            if not resolved and mapping.llm_allowed:
                generated = self.answer_generator.generate(
                    question_text=labels[0],
                    profile=self.profile,
                    max_chars=mapping.max_chars,
                )
                if generated:
                    run_context.generated_answers.append(generated)
                    run_context.add_event(
                        FieldEvent(
                            label=labels[0],
                            status="generated",
                            source="llm",
                            value_preview=_preview(generated.answer),
                            message=f"LLM mode: {self.llm_mode}",
                        )
                    )
                    if self.llm_mode == "autofill_with_review":
                        resolved = ResolvedField(
                            label=labels[0],
                            value=generated.answer,
                            source="llm",
                            max_chars=mapping.max_chars,
                            llm_allowed=True,
                        )

            if not resolved:
                if mapping.required:
                    run_context.add_event(
                        FieldEvent(
                            label=labels[0],
                            status="unresolved",
                            source="system",
                            message="Required configured field could not be resolved from the profile or LLM.",
                        )
                    )
                continue

            if self._fill_any_label(page, labels, resolved, run_context):
                self._filled_keys.add(fill_key)

    def _attempt_free_text_fields(self, page, run_context: ApplicationRunContext) -> None:
        for field_config in self.employer_config.free_text_fields:
            labels = field_config.label_match
            fill_key = "free_text|" + "|".join(normalize_label(label) for label in labels)
            if fill_key in self._filled_keys:
                continue
            if not field_config.generation.enabled:
                continue
            visible_label = self._first_visible_label(page, labels)
            if not visible_label:
                if field_config.required:
                    run_context.add_event(
                        FieldEvent(
                            label=labels[0],
                            status="unresolved",
                            source="system",
                            message="Configured generated free-text field was not found on the page.",
                        )
                    )
                continue

            if self.dry_run:
                run_context.add_event(
                    FieldEvent(
                        label=visible_label,
                        status="skipped",
                        source="llm",
                        message="Dry run: this field would require generated application text; no generation or fill performed.",
                    )
                )
                self._filled_keys.add(fill_key)
                continue

            if not self.writing_assistant:
                run_context.add_event(
                    FieldEvent(
                        label=visible_label,
                        status="unresolved",
                        source="llm",
                        message="Generation is enabled for this field, but no writing assistant is configured.",
                    )
                )
                self._filled_keys.add(fill_key)
                continue

            try:
                generated = self.writing_assistant.generate_application_text(
                    job_context=self._job_context(run_context),
                    applicant_profile=self.profile,
                    field_label=visible_label,
                    field_type=field_config.type,
                    max_words=field_config.generation.max_words,
                )
                saved_path = self.writing_assistant.save_generated_text(
                    generated,
                    job_context=self._job_context(run_context),
                    timestamp=run_context.run_id,
                )
                run_context.generated_application_texts.append(generated)
                run_context.artifact_paths[f"generated_text_{normalize_label(visible_label)}"] = str(saved_path)
                run_context.add_event(
                    FieldEvent(
                        label=visible_label,
                        status="generated",
                        source="llm",
                        value_preview=_preview(generated.text),
                        message=f"Generated application text saved to {saved_path}",
                    )
                )
                if field_config.generation.insert_into_form:
                    resolved = ResolvedField(
                        label=visible_label,
                        value=generated.text,
                        source="llm",
                        llm_allowed=True,
                    )
                    self._fill_any_label(page, [visible_label], resolved, run_context)
                else:
                    run_context.add_event(
                        FieldEvent(
                            label=visible_label,
                            status="skipped",
                            source="llm",
                            message="Generated text was not inserted because insert_into_form is false.",
                        )
                    )
            except Exception as exc:
                logger.warning("Generated free-text field failed for %s: %s", visible_label, exc)
                run_context.add_event(
                    FieldEvent(
                        label=visible_label,
                        status="unresolved",
                        source="llm",
                        message=f"Generation failed; field left blank: {exc}",
                    )
                )
            finally:
                self._filled_keys.add(fill_key)

    def _first_visible_label(self, page, labels: Iterable[str]) -> Optional[str]:
        for label in labels:
            try:
                locator = self._locator_for_label(page, label)
                if locator and locator.count() and locator.is_visible():
                    return label
            except Exception as exc:
                logger.debug("Could not inspect label %s: %s", label, exc)
        return None

    def _job_context(self, run_context: ApplicationRunContext) -> JobApplicationContext:
        return JobApplicationContext(
            employer_name=self.employer_config.employer_name,
            job_title=self.employer_config.job_title,
            job_url=run_context.job_url,
            job_description_text=self.employer_config.job_description_text,
        )

    def _fill_any_label(
        self,
        page,
        labels: Iterable[str],
        resolved: ResolvedField,
        run_context: ApplicationRunContext,
    ) -> bool:
        value = display_value(resolved.value)
        for label in labels:
            try:
                locator = self._locator_for_label(page, label)
                if not locator or locator.count() == 0 or not locator.is_visible():
                    continue
                tag_name = locator.evaluate("element => element.tagName.toLowerCase()")
                input_type = locator.evaluate("element => element.getAttribute('type') || ''")
                if self.dry_run:
                    run_context.add_event(
                        FieldEvent(
                            label=label,
                            status="skipped",
                            source=resolved.source,  # type: ignore[arg-type]
                            value_preview=_preview(value),
                            message="Dry run: field was not modified.",
                        )
                    )
                    return True
                if tag_name == "select":
                    locator.select_option(label=value)
                elif input_type in {"checkbox", "radio"}:
                    desired = value.lower() in {"true", "yes", "1"}
                    if desired:
                        locator.check()
                    else:
                        locator.uncheck()
                else:
                    locator.fill(value)
                run_context.add_event(
                    FieldEvent(
                        label=label,
                        status="filled",
                        source=resolved.source,  # type: ignore[arg-type]
                        value_preview=_preview(value),
                    )
                )
                logger.info("Filled %s from %s", label, resolved.source)
                return True
            except Exception as exc:
                logger.debug("Could not fill label %s: %s", label, exc)
        return False

    def _locator_for_label(self, page, label: str):
        candidates = [
            lambda: page.get_by_label(label, exact=False).first,
            lambda: page.get_by_placeholder(label, exact=False).first,
            lambda: page.locator(f"input[aria-label*='{_css_attr(label)}'], textarea[aria-label*='{_css_attr(label)}']").first,
            lambda: page.locator(f"input[name*='{_name_fragment(label)}' i], textarea[name*='{_name_fragment(label)}' i]").first,
            lambda: page.locator(f"input[id*='{_name_fragment(label)}' i], textarea[id*='{_name_fragment(label)}' i]").first,
        ]
        first_match = None
        for candidate in candidates:
            try:
                locator = candidate()
                if not locator.count():
                    continue
                if first_match is None:
                    first_match = locator
                if locator.is_visible():
                    return locator
            except Exception:
                continue
        return first_match

    def _attempt_resume_upload(self, page, run_context: ApplicationRunContext) -> None:
        if self._resume_uploaded:
            return
        if self.resume_path_override is not None:
            resume_path = self.resume_path_override
            if not resume_path.is_absolute():
                resume_path = resolve_local_path(Path.cwd(), str(resume_path))
        else:
            resume_path = resolve_local_path(Path.cwd(), self.profile.documents.resume_path)
        if not resume_path.exists():
            run_context.add_event(
                FieldEvent(
                    label="resume",
                    status="unresolved",
                    source="profile",
                    value_preview=str(resume_path),
                    message="Resume file does not exist.",
                )
            )
            self._resume_uploaded = True
            return

        for selector in self.employer_config.resume_upload.selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                if self.dry_run:
                    run_context.add_event(
                        FieldEvent(
                            label="resume",
                            status="skipped",
                            source="profile",
                            value_preview=str(resume_path),
                            message="Dry run: file was not uploaded.",
                        )
                    )
                    self._resume_uploaded = True
                    return
                locator.set_input_files(str(resume_path))
                run_context.add_event(
                    FieldEvent(
                        label="resume",
                        status="filled",
                        source="profile",
                        value_preview=str(resume_path),
                    )
                )
                logger.info("Uploaded resume with selector %s", selector)
                self._resume_uploaded = True
                return
            except Exception as exc:
                logger.debug("Could not upload resume with selector %s: %s", selector, exc)

    def _click_next(self, page, step_index: int) -> bool:
        button_texts = ["Next", "Continue", "Review"]
        if step_index < len(self.employer_config.steps):
            button_texts = self.employer_config.steps[step_index].next_button_text
        for text in button_texts:
            if _is_submit_text(text, self.employer_config.review_gate.submit_texts):
                raise ReviewGateReached("Configured next text is a submit boundary")
            pattern = re.compile(re.escape(text), re.IGNORECASE)
            button = page.get_by_role("button", name=pattern).first
            try:
                if button.count() and button.is_visible() and button.is_enabled():
                    if self.dry_run:
                        logger.info("Dry run: would click %s", text)
                        return False
                    button.click()
                    logger.info("Clicked %s", text)
                    return True
            except Exception as exc:
                logger.debug("Could not click %s: %s", text, exc)
        return False

    def _click_form_opener(self, page, run_context: ApplicationRunContext) -> None:
        for text in self.employer_config.start_button_texts:
            if _is_submit_text(text, self.employer_config.review_gate.submit_texts):
                run_context.add_event(
                    FieldEvent(
                        label=text,
                        status="unresolved",
                        source="system",
                        message="Configured form opener matches submit boundary text; refusing to click it.",
                    )
                )
                return
            pattern = re.compile(re.escape(text), re.IGNORECASE)
            for role in ("button", "link"):
                candidate = page.get_by_role(role, name=pattern).first
                try:
                    if candidate.count() and candidate.is_visible() and candidate.is_enabled():
                        candidate.click()
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=4000)
                        except Exception:
                            pass
                        page.wait_for_timeout(1200)
                        run_context.add_event(
                            FieldEvent(
                                label=text,
                                status="clicked",
                                source="system",
                                message=f"Clicked form opener ({role}) to reveal the application fields.",
                            )
                        )
                        logger.info("Clicked form opener %s (%s)", text, role)
                        return
                except Exception as exc:
                    logger.debug("Could not click form opener %s (%s): %s", text, role, exc)

    def _raise_if_submit_visible(self, page, run_context: ApplicationRunContext) -> bool:
        for text in self.employer_config.review_gate.submit_texts:
            pattern = re.compile(rf"^{re.escape(text)}$", re.IGNORECASE)
            try:
                button = page.get_by_role("button", name=pattern).first
                if button.count() and button.is_visible():
                    run_context.add_event(
                        FieldEvent(
                            label=text,
                            status="review_gate",
                            source="system",
                            message="Submit-like button is visible. Stopping for manual review.",
                        )
                    )
                    raise ReviewGateReached(text)
            except ReviewGateReached:
                raise
            except Exception as exc:
                logger.debug("Submit boundary check failed for %s: %s", text, exc)
        return False


def _is_submit_text(text: str, submit_texts: Iterable[str]) -> bool:
    normalized = normalize_label(text)
    return any(normalize_label(submit_text) == normalized for submit_text in submit_texts)


def _preview(value: str, limit: int = 120) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _css_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _name_fragment(value: str) -> str:
    normalized = normalize_label(value).replace(" ", "")
    if "fullname" in normalized:
        return "name"
    if "emailaddress" in normalized:
        return "email"
    return normalized
