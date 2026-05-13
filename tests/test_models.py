from pathlib import Path

import pytest
from pydantic import ValidationError

from autoapplyer.config import load_employer_config, load_profile
from autoapplyer.models import CandidateProfile, FieldMapping, GeneratedAnswer


ROOT = Path(__file__).resolve().parents[1]


def test_profile_example_validates():
    profile = load_profile(ROOT / "profile.example.yml")
    assert profile.personal.full_name == "Miko Matheron"
    assert profile.documents.resume_path == "CV Miko Matheron.pdf"


def test_employer_example_validates():
    employer = load_employer_config(ROOT / "employers" / "example_workday.yml")
    assert employer.ats_family == "workday_like"
    assert employer.review_gate.require_manual_review is True


def test_profile_rejects_unknown_top_level_key():
    raw = load_profile(ROOT / "profile.example.yml").model_dump()
    raw["unexpected"] = True
    with pytest.raises(ValidationError):
        CandidateProfile.model_validate(raw)


def test_field_mapping_requires_a_source():
    with pytest.raises(ValidationError):
        FieldMapping.model_validate({"labels": ["First name"]})


def test_field_mapping_accepts_value_only():
    fm = FieldMapping.model_validate({"labels": ["Country"], "value": "Switzerland"})
    assert fm.value == "Switzerland"


def test_field_mapping_accepts_llm_allowed():
    fm = FieldMapping.model_validate({"labels": ["Why this role?"], "llm_allowed": True})
    assert fm.llm_allowed is True


def test_generated_answer_confidence_is_bounded():
    with pytest.raises(ValidationError):
        GeneratedAnswer.model_validate(
            {
                "question_text": "Why this role?",
                "answer": "Because it matches my experience.",
                "confidence": 2.0,
                "source_basis": "profile",
                "requires_manual_review": True,
            }
        )

