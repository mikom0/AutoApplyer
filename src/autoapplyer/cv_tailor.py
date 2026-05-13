from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from dotenv import load_dotenv
from pydantic import ValidationError

from autoapplyer.cv_document import render_cv_pdf
from autoapplyer.models import (
    BulletRewriteProposal,
    CVBulletLocation,
    CVDocument,
    CVTailoringConfig,
    JobApplicationContext,
    JobKeywordProfile,
    TailoredCV,
    WritingProfile,
)

logger = logging.getLogger(__name__)


PROMPTS_DIR = Path(__file__).parent / "prompts"
KEYWORD_PROMPT_FILE = PROMPTS_DIR / "cv_keyword_analysis_system.md"
REWRITER_PROMPT_FILE = PROMPTS_DIR / "cv_bullet_rewriter_system.md"


class CVTailorProvider(ABC):
    @abstractmethod
    def analyze_job_description(self, payload: Dict[str, object]) -> JobKeywordProfile:
        raise NotImplementedError

    @abstractmethod
    def rewrite_bullet(self, payload: Dict[str, object]) -> BulletRewriteProposal:
        raise NotImplementedError


class NoOpCVTailorProvider(CVTailorProvider):
    def analyze_job_description(self, payload: Dict[str, object]) -> JobKeywordProfile:
        raise RuntimeError("CV tailor provider is disabled")

    def rewrite_bullet(self, payload: Dict[str, object]) -> BulletRewriteProposal:
        raise RuntimeError("CV tailor provider is disabled")


class OpenAICVTailorProvider(CVTailorProvider):
    def __init__(self, model: Optional[str] = None):
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required when the CV tailor provider is openai")
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model or os.getenv("AUTOAPPLYER_CV_TAILOR_MODEL", "gpt-4.1")

    def analyze_job_description(self, payload: Dict[str, object]) -> JobKeywordProfile:
        content = self._chat(
            system_prompt=_load_text(KEYWORD_PROMPT_FILE),
            user_payload=payload,
            temperature=0.1,
        )
        return parse_keyword_profile(content)

    def rewrite_bullet(self, payload: Dict[str, object]) -> BulletRewriteProposal:
        content = self._chat(
            system_prompt=_load_text(REWRITER_PROMPT_FILE),
            user_payload=payload,
            temperature=0.2,
        )
        return parse_bullet_proposal(content)

    def _chat(self, system_prompt: str, user_payload: Dict[str, object], temperature: float) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, indent=2, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
        )
        return response.choices[0].message.content or "{}"


class CVTailor:
    def __init__(
        self,
        provider: CVTailorProvider,
        cv: CVDocument,
        writing_profile: Optional[WritingProfile] = None,
        output_dir: Path = Path("generated_cvs"),
    ):
        self.provider = provider
        self.cv = cv
        self.writing_profile = writing_profile
        self.output_dir = output_dir

    def tailor(
        self,
        job_context: JobApplicationContext,
        config: CVTailoringConfig,
        timestamp: Optional[str] = None,
    ) -> TailoredCV:
        if not config.enabled:
            raise RuntimeError("cv_tailoring.enabled is false; refusing to tailor")

        timestamp = timestamp or time.strftime("%Y%m%d-%H%M%S")
        keyword_profile = self._analyze(job_context)
        rewrites, rejected = self._rewrite_bullets(keyword_profile, config.max_bullets_to_rewrite)
        tailored_cv = _apply_rewrites(self.cv, rewrites)
        output_pdf = self._render(tailored_cv, job_context, timestamp, config.template)
        readback = _ats_readback(tailored_cv, keyword_profile)
        result = TailoredCV(
            employer_name=job_context.employer_name,
            job_title=job_context.job_title,
            job_url=job_context.job_url,
            keyword_profile=keyword_profile,
            rewrites=rewrites,
            rejected_rewrites=rejected,
            ats_readback_keywords_present=readback["present"],
            ats_readback_keywords_missing=readback["missing"],
            output_pdf_path=str(output_pdf),
        )
        report_path = self._save_report(result, timestamp)
        result.report_path = str(report_path)
        return result

    def _analyze(self, job_context: JobApplicationContext) -> JobKeywordProfile:
        current_terms = sorted(_collect_cv_terms(self.cv))
        payload = {
            "employer_name": job_context.employer_name,
            "job_title": job_context.job_title,
            "job_description_text": job_context.job_description_text or "",
            "current_cv_terms": current_terms,
        }
        return self.provider.analyze_job_description(payload)

    def _rewrite_bullets(
        self,
        keyword_profile: JobKeywordProfile,
        max_bullets: int,
    ) -> tuple[List[BulletRewriteProposal], List[BulletRewriteProposal]]:
        locations = self.cv.iter_bullet_locations()
        ranked = _rank_bullets_for_rewrite(locations, keyword_profile)[:max_bullets]

        accepted: List[BulletRewriteProposal] = []
        rejected: List[BulletRewriteProposal] = []
        for location in ranked:
            payload = self._build_rewrite_payload(location, keyword_profile)
            try:
                proposal = self.provider.rewrite_bullet(payload)
            except (ValueError, ValidationError, json.JSONDecodeError) as exc:
                logger.warning("Bullet %s rewrite failed: %s", location.bullet_id, exc)
                continue

            verdict = _lint_bullet_proposal(
                proposal,
                location=location,
                keyword_profile=keyword_profile,
                writing_profile=self.writing_profile,
            )
            if verdict.rejected_reason:
                proposal.rejected_reason = verdict.rejected_reason
                proposal.warnings = list(set(proposal.warnings + verdict.added_warnings))
                rejected.append(proposal)
                continue
            proposal.warnings = list(set(proposal.warnings + verdict.added_warnings))
            proposal.requires_manual_review = True
            accepted.append(proposal)
        return accepted, rejected

    def _build_rewrite_payload(self, location: CVBulletLocation, keyword_profile: JobKeywordProfile) -> Dict[str, object]:
        claim_rules: Dict[str, object] = {}
        voice_rules: Dict[str, object] = {}
        if self.writing_profile:
            claim_rules = {
                "safe_to_make_without_job_confirmation": self.writing_profile.claim_rules.safe_to_make_without_job_confirmation,
                "never_invent": self.writing_profile.claim_rules.never_invent,
            }
            voice_rules = {
                "avoid_phrases": self.writing_profile.writing_voice.avoid_phrases,
                "cadence_rules": self.writing_profile.writing_voice.cadence_rules,
            }
        return {
            "bullet_id": location.bullet_id,
            "original_text": location.original_text,
            "role": location.role,
            "company": location.company,
            "keyword_profile": keyword_profile.model_dump(),
            "claim_rules": claim_rules,
            "voice_rules": voice_rules,
        }

    def _render(self, cv: CVDocument, job_context: JobApplicationContext, timestamp: str, template: str) -> Path:
        employer_slug = _slugify(job_context.employer_name)
        job_slug = _slugify(job_context.job_title or "application")
        pdf_path = self.output_dir / f"{employer_slug}_{job_slug}_{timestamp}.pdf"
        render_cv_pdf(cv, pdf_path, template_name=template)
        return pdf_path

    def _save_report(self, tailored: TailoredCV, timestamp: str) -> Path:
        employer_slug = _slugify(tailored.employer_name)
        job_slug = _slugify(tailored.job_title or "application")
        path = self.output_dir / f"{employer_slug}_{job_slug}_{timestamp}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_report_markdown(tailored), encoding="utf-8")
        return path


def build_cv_tailor(
    provider_name: str,
    cv: CVDocument,
    writing_profile: Optional[WritingProfile] = None,
    output_dir: Path = Path("generated_cvs"),
) -> CVTailor:
    if provider_name == "off":
        return CVTailor(NoOpCVTailorProvider(), cv, writing_profile, output_dir)
    if provider_name == "openai":
        return CVTailor(OpenAICVTailorProvider(), cv, writing_profile, output_dir)
    raise ValueError(f"Unsupported CV tailor provider: {provider_name}")


def parse_keyword_profile(content: str) -> JobKeywordProfile:
    try:
        parsed = json.loads(content)
        return JobKeywordProfile.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"Keyword analysis response did not match JobKeywordProfile schema: {exc}") from exc


def parse_bullet_proposal(content: str) -> BulletRewriteProposal:
    try:
        parsed = json.loads(content)
        return BulletRewriteProposal.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"Bullet rewrite response did not match BulletRewriteProposal schema: {exc}") from exc


class _LintVerdict:
    def __init__(self, rejected_reason: Optional[str], added_warnings: List[str]):
        self.rejected_reason = rejected_reason
        self.added_warnings = added_warnings


def _lint_bullet_proposal(
    proposal: BulletRewriteProposal,
    location: CVBulletLocation,
    keyword_profile: JobKeywordProfile,
    writing_profile: Optional[WritingProfile],
) -> _LintVerdict:
    warnings: List[str] = []
    original = location.original_text
    proposed = proposal.proposed_text

    if proposal.original_text.strip() != original.strip():
        return _LintVerdict("proposal.original_text does not match the bullet we sent", warnings)

    if "—" in proposed:
        return _LintVerdict("proposed text contains em dash, which is banned", warnings)

    if len(proposed) > int(len(original) * 1.15) + 4:
        return _LintVerdict("proposed text exceeds 15% length growth budget", warnings)

    if writing_profile:
        lowered = proposed.lower()
        banned = [p for p in writing_profile.writing_voice.avoid_phrases if p.lower() in lowered]
        if banned:
            return _LintVerdict(f"proposed text contains banned phrases: {', '.join(banned)}", warnings)

        original_tokens = _content_tokens(original)
        proposed_tokens = _content_tokens(proposed)
        new_tokens = proposed_tokens - original_tokens
        if new_tokens:
            for category in writing_profile.claim_rules.never_invent:
                for token in new_tokens:
                    if _token_is_in_category(token, category) and token not in {
                        kw.lower() for kw in keyword_profile.required_keywords + keyword_profile.preferred_keywords
                    }:
                        warnings.append(f"new token '{token}' looks like it may invent a {category} claim")

    if not proposal.keywords_incorporated and proposal.source_basis != "existing_phrasing":
        warnings.append("proposal claims a non-trivial rewrite but lists no keywords_incorporated")

    if proposed.strip().lower() == original.strip().lower() and proposal.source_basis != "existing_phrasing":
        warnings.append("proposed_text is identical to original but source_basis is not 'existing_phrasing'")

    return _LintVerdict(None, warnings)


_NEVER_INVENT_HINTS: Dict[str, List[str]] = {
    "employers": ["bank", "fund", "firm", "llc", "ltd", "plc", "capital", "partners"],
    "grades": ["first", "2:1", "2.1", "distinction", "honors", "summa", "magna"],
    "deal experience": ["acquisition", "ipo", "lbo", "merger", "buyout"],
    "financial modelling experience": ["dcf", "lbo", "comps", "wacc"],
    "metrics": [r"\d+%", r"\d+x", r"\d+\s*bps"],
    "languages": ["english", "french", "spanish", "german", "mandarin", "arabic", "italian"],
    "technical tools": ["python", "vba", "matlab", "r", "sql", "bloomberg", "factset", "excel"],
    "visa status": ["visa", "sponsorship", "tier 2", "tier 5", "skilled worker"],
}


def _token_is_in_category(token: str, category: str) -> bool:
    hints = _NEVER_INVENT_HINTS.get(category, [])
    for hint in hints:
        if hint.startswith("\\") or any(c in hint for c in "%\\d"):
            if re.search(hint, token):
                return True
        elif hint in token:
            return True
    return False


_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+/&.-]*")


def _content_tokens(text: str) -> set[str]:
    stop = {"and", "the", "a", "an", "of", "in", "to", "for", "on", "with", "by", "at", "as", "is", "are", "was", "were", "be"}
    return {t.lower() for t in _TOKEN_PATTERN.findall(text) if t.lower() not in stop and len(t) > 1}


def _rank_bullets_for_rewrite(
    locations: Sequence[CVBulletLocation],
    keyword_profile: JobKeywordProfile,
) -> List[CVBulletLocation]:
    target_terms = [
        kw.lower() for kw in keyword_profile.required_keywords + keyword_profile.preferred_keywords + keyword_profile.domain_terms
    ]
    scored: List[tuple[float, int, CVBulletLocation]] = []
    for index, location in enumerate(locations):
        lowered = location.original_text.lower()
        overlap = sum(1 for term in target_terms if term and term in lowered)
        missing = sum(1 for term in target_terms if term and term not in lowered)
        score = overlap * 1.0 + missing * 0.5
        scored.append((-score, index, location))
    scored.sort()
    return [loc for _, _, loc in scored]


def _apply_rewrites(cv: CVDocument, rewrites: Sequence[BulletRewriteProposal]) -> CVDocument:
    by_id: Dict[str, str] = {r.bullet_id: r.proposed_text for r in rewrites}
    if not by_id:
        return cv.model_copy(deep=True)
    new_cv = cv.model_copy(deep=True)
    for section_name in ("experience", "extracurricular"):
        entries = getattr(new_cv, section_name)
        for entry in entries:
            new_bullets: List[str] = []
            for index, bullet in enumerate(entry.bullets):
                bullet_id = f"{entry.bullet_id_prefix}-{index}"
                new_bullets.append(by_id.get(bullet_id, bullet))
            entry.bullets = new_bullets
    return new_cv


def _ats_readback(cv: CVDocument, keyword_profile: JobKeywordProfile) -> Dict[str, List[str]]:
    haystack = _flatten_cv_text(cv).lower()
    targets = list({*keyword_profile.required_keywords, *keyword_profile.preferred_keywords})
    present = sorted(t for t in targets if t and t.lower() in haystack)
    missing = sorted(t for t in targets if t and t.lower() not in haystack)
    return {"present": present, "missing": missing}


def _flatten_cv_text(cv: CVDocument) -> str:
    parts: List[str] = []
    for section in (cv.experience, cv.extracurricular):
        for entry in section:
            parts.append(entry.role)
            parts.append(entry.company)
            if entry.location:
                parts.append(entry.location)
            parts.extend(entry.bullets)
    for entry in cv.education:
        parts.extend([entry.institution, entry.qualification, entry.grade or ""])
        for detail in entry.details:
            parts.append(detail.body)
    for line in cv.skills_lines:
        parts.append(line.body)
    return " \n ".join(parts)


def _collect_cv_terms(cv: CVDocument) -> set[str]:
    return _content_tokens(_flatten_cv_text(cv))


def _render_report_markdown(tailored: TailoredCV) -> str:
    lines: List[str] = []
    lines.append("# Tailored CV Report\n")
    lines.append(f"Employer: {tailored.employer_name}\n")
    lines.append(f"Job title: {tailored.job_title or 'Unknown'}\n")
    lines.append(f"Job URL: {tailored.job_url}\n")
    lines.append(f"Output PDF: {tailored.output_pdf_path}\n")
    lines.append("\n## Job Keyword Profile\n")
    kp = tailored.keyword_profile
    if kp.summary:
        lines.append(f"_{kp.summary}_\n")
    lines.append(f"- Required: {', '.join(kp.required_keywords) or 'none'}")
    lines.append(f"- Preferred: {', '.join(kp.preferred_keywords) or 'none'}")
    lines.append(f"- Seniority signals: {', '.join(kp.seniority_signals) or 'none'}")
    lines.append(f"- Domain terms: {', '.join(kp.domain_terms) or 'none'}")
    if kp.exact_phrasings:
        lines.append("- Exact phrasings:")
        for canonical, phrasing in kp.exact_phrasings.items():
            lines.append(f"  - {canonical} → {phrasing}")
    lines.append("\n## Accepted Rewrites\n")
    if not tailored.rewrites:
        lines.append("_No rewrites accepted._")
    for rewrite in tailored.rewrites:
        lines.append(f"### {rewrite.bullet_id} (confidence {rewrite.confidence:.2f}, basis: {rewrite.source_basis})")
        lines.append(f"- **Before:** {rewrite.original_text}")
        lines.append(f"- **After:**  {rewrite.proposed_text}")
        if rewrite.keywords_incorporated:
            lines.append(f"- Keywords incorporated: {', '.join(rewrite.keywords_incorporated)}")
        if rewrite.warnings:
            lines.append(f"- Warnings: {', '.join(rewrite.warnings)}")
        if rewrite.notes:
            lines.append(f"- Notes: {rewrite.notes}")
        lines.append("")
    if tailored.rejected_rewrites:
        lines.append("\n## Rejected Rewrites\n")
        for rewrite in tailored.rejected_rewrites:
            lines.append(f"### {rewrite.bullet_id}")
            lines.append(f"- **Original:** {rewrite.original_text}")
            lines.append(f"- **Rejected proposal:** {rewrite.proposed_text}")
            lines.append(f"- **Reason:** {rewrite.rejected_reason}")
            lines.append("")
    lines.append("\n## ATS Readback\n")
    lines.append(f"- Keywords present in rendered CV: {', '.join(tailored.ats_readback_keywords_present) or 'none'}")
    lines.append(f"- Keywords still missing: {', '.join(tailored.ats_readback_keywords_missing) or 'none'}")
    lines.append("")
    lines.append("**This CV requires manual review before sending.**")
    return "\n".join(lines)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "application"


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")
