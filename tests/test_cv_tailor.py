from pathlib import Path
from typing import Dict, List

import pytest

from autoapplyer.config import load_cv_document, load_writing_profile
from autoapplyer.cv_tailor import (
    CVTailor,
    CVTailorProvider,
    _apply_rewrites,
    _ats_readback,
    _lint_bullet_proposal,
    _rank_bullets_for_rewrite,
    parse_bullet_proposal,
    parse_keyword_profile,
)
from autoapplyer.models import (
    BulletRewriteProposal,
    CVTailoringConfig,
    JobApplicationContext,
    JobKeywordProfile,
)


ROOT = Path(__file__).resolve().parents[1]


def _kw_profile(required: List[str] = None, preferred: List[str] = None) -> JobKeywordProfile:
    return JobKeywordProfile(
        required_keywords=required or [],
        preferred_keywords=preferred or [],
    )


def _location():
    cv = load_cv_document(ROOT / "cv.example.yml")
    return cv.iter_bullet_locations()[0]


def test_parse_keyword_profile_rejects_bad_json():
    with pytest.raises(ValueError):
        parse_keyword_profile("not json")


def test_parse_bullet_proposal_validates_schema():
    payload = {
        "bullet_id": "exp-firm-a-0",
        "original_text": "Did a thing.",
        "proposed_text": "Did a thing.",
        "keywords_incorporated": [],
        "source_basis": "existing_phrasing",
        "confidence": 0.5,
        "requires_manual_review": True,
        "warnings": [],
    }
    import json as _json

    proposal = parse_bullet_proposal(_json.dumps(payload))
    assert proposal.bullet_id == "exp-firm-a-0"


def test_lint_rejects_em_dash():
    location = _location()
    proposal = BulletRewriteProposal(
        bullet_id=location.bullet_id,
        original_text=location.original_text,
        proposed_text=location.original_text + " — extra",
        keywords_incorporated=["extra"],
        source_basis="mixed",
        confidence=0.7,
        requires_manual_review=True,
    )
    writing_profile = load_writing_profile(ROOT / "writing_profile.example.yml")
    verdict = _lint_bullet_proposal(proposal, location, _kw_profile(), writing_profile)
    assert verdict.rejected_reason and "em dash" in verdict.rejected_reason


def test_lint_rejects_overgrowth():
    location = _location()
    proposal = BulletRewriteProposal(
        bullet_id=location.bullet_id,
        original_text=location.original_text,
        proposed_text=location.original_text + " " * 200 + "extra extra extra extra",
        keywords_incorporated=["extra"],
        source_basis="mixed",
        confidence=0.7,
        requires_manual_review=True,
    )
    writing_profile = load_writing_profile(ROOT / "writing_profile.example.yml")
    verdict = _lint_bullet_proposal(proposal, location, _kw_profile(), writing_profile)
    assert verdict.rejected_reason and "15%" in verdict.rejected_reason


def test_lint_rejects_mismatched_original_text():
    location = _location()
    proposal = BulletRewriteProposal(
        bullet_id=location.bullet_id,
        original_text="something else entirely",
        proposed_text="rewritten",
        keywords_incorporated=[],
        source_basis="mixed",
        confidence=0.5,
        requires_manual_review=True,
    )
    writing_profile = load_writing_profile(ROOT / "writing_profile.example.yml")
    verdict = _lint_bullet_proposal(proposal, location, _kw_profile(), writing_profile)
    assert verdict.rejected_reason


def test_lint_warns_on_uncited_tool_invention():
    location = _location()
    proposal = BulletRewriteProposal(
        bullet_id=location.bullet_id,
        original_text=location.original_text,
        proposed_text="Used Python and Bloomberg to do the same thing more concisely.",
        keywords_incorporated=[],
        source_basis="mixed",
        confidence=0.5,
        requires_manual_review=True,
    )
    writing_profile = load_writing_profile(ROOT / "writing_profile.example.yml")
    verdict = _lint_bullet_proposal(
        proposal,
        location,
        _kw_profile(required=["communication"]),
        writing_profile,
    )
    assert any("technical tools" in w for w in verdict.added_warnings)


def test_rank_bullets_prioritises_partial_overlap():
    cv = load_cv_document(ROOT / "cv.example.yml")
    locations = cv.iter_bullet_locations()
    keyword_profile = _kw_profile(required=["primary"], preferred=["measurable"])
    ranked = _rank_bullets_for_rewrite(locations, keyword_profile)
    assert ranked
    assert ranked[0].original_text


def test_apply_rewrites_substitutes_text_in_place():
    cv = load_cv_document(ROOT / "cv.example.yml")
    location = cv.iter_bullet_locations()[0]
    rewrite = BulletRewriteProposal(
        bullet_id=location.bullet_id,
        original_text=location.original_text,
        proposed_text="REWRITTEN.",
        keywords_incorporated=[],
        source_basis="reordering",
        confidence=0.9,
        requires_manual_review=True,
    )
    new_cv = _apply_rewrites(cv, [rewrite])
    assert new_cv.experience[0].bullets[0] == "REWRITTEN."
    assert cv.experience[0].bullets[0] != "REWRITTEN."


def test_ats_readback_marks_present_and_missing():
    cv = load_cv_document(ROOT / "cv.example.yml")
    profile = _kw_profile(required=["bullet", "completely-fictional-term"])
    result = _ats_readback(cv, profile)
    assert "bullet" in result["present"]
    assert "completely-fictional-term" in result["missing"]


class _StubProvider(CVTailorProvider):
    def __init__(self, keyword_profile: JobKeywordProfile, rewrites: Dict[str, str]):
        self._keyword_profile = keyword_profile
        self._rewrites = rewrites

    def analyze_job_description(self, payload):
        return self._keyword_profile

    def rewrite_bullet(self, payload):
        original = payload["original_text"]
        proposed = self._rewrites.get(payload["bullet_id"], original)
        return BulletRewriteProposal(
            bullet_id=payload["bullet_id"],
            original_text=original,
            proposed_text=proposed,
            keywords_incorporated=["macro"] if proposed != original else [],
            source_basis="keyword_substitution" if proposed != original else "existing_phrasing",
            confidence=0.8,
            requires_manual_review=True,
        )


def test_tailor_end_to_end_with_stub_provider(tmp_path: Path):
    cv = load_cv_document(ROOT / "cv.example.yml")
    writing_profile = load_writing_profile(ROOT / "writing_profile.example.yml")
    location = cv.iter_bullet_locations()[0]
    provider = _StubProvider(
        _kw_profile(required=["macro", "DCF"]),
        rewrites={location.bullet_id: location.original_text.replace("Concrete", "Macro")},
    )
    tailor = CVTailor(provider, cv, writing_profile, output_dir=tmp_path)
    job_context = JobApplicationContext(
        employer_name="Stub Capital",
        job_title="Analyst",
        job_url="https://example.com",
        job_description_text="Macro analyst seat with DCF and primary research.",
    )
    config = CVTailoringConfig(enabled=True, max_bullets_to_rewrite=3)
    tailored = tailor.tailor(job_context, config)
    assert tailored.output_pdf_path
    pdf_path = Path(tailored.output_pdf_path)
    assert pdf_path.exists()
    assert pdf_path.read_bytes()[:4] == b"%PDF"
    assert tailored.report_path
    assert Path(tailored.report_path).exists()
    assert any(r.proposed_text != r.original_text for r in tailored.rewrites)
    assert "macro" in (kw.lower() for kw in tailored.ats_readback_keywords_present)
