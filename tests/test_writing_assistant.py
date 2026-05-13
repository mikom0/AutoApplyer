from pathlib import Path

import pytest

from autoapplyer.config import load_profile, load_writing_profile
from autoapplyer.models import GeneratedApplicationText, JobApplicationContext
from autoapplyer.writing_assistant import (
    build_prompt_payload,
    lint_generated_application_text,
    parse_generated_application_text,
)


ROOT = Path(__file__).resolve().parents[1]


def test_writing_profile_example_validates():
    writing_profile = load_writing_profile(ROOT / "writing_profile.example.yml")

    assert "concise" in writing_profile.writing_voice.preferred_style
    assert "deal experience" in writing_profile.claim_rules.never_invent


def test_build_prompt_payload_includes_profiles_and_output_schema():
    profile = load_profile(ROOT / "profile.example.yml")
    writing_profile = load_writing_profile(ROOT / "writing_profile.example.yml")
    job_context = JobApplicationContext(
        employer_name="Walker Hamill",
        job_title="Graduate Investment Associate",
        job_url="https://www.walkerhamill.com/job/graduate-investment-associate/",
        job_description_text="Graduate investment role involving origination, founder conversations, and research.",
    )

    payload = build_prompt_payload(
        job_context=job_context,
        applicant_profile=profile,
        writing_profile=writing_profile,
        field_label="Message",
        field_type="recruiter_message",
        max_words=220,
    )

    assert payload["task"]["field_type"] == "recruiter_message"
    assert payload["job_context"]["employer_name"] == "Walker Hamill"
    assert "writing_profile" in payload


def test_parse_generated_application_text_rejects_overlong_text():
    with pytest.raises(ValueError):
        parse_generated_application_text(
            """
            {
              "field_label": "Message",
              "field_type": "recruiter_message",
              "text": "one two three four five six",
              "confidence": 0.7,
              "source_basis": "mixed",
              "requires_manual_review": true,
              "grounding_notes": ["Used supplied profile"],
              "warnings": []
            }
            """,
            max_words=5,
        )


def test_lint_rejects_banned_phrases():
    writing_profile = load_writing_profile(ROOT / "writing_profile.example.yml")
    generated = GeneratedApplicationText(
        field_label="Message",
        field_type="recruiter_message",
        text="I am thrilled to apply to Walker Hamill for this role.",
        confidence=0.8,
        source_basis="mixed",
        requires_manual_review=True,
    )

    with pytest.raises(ValueError):
        lint_generated_application_text(
            generated,
            writing_profile=writing_profile,
            job_context=JobApplicationContext(
                employer_name="Walker Hamill",
                job_title="Graduate Investment Associate",
                job_url="https://www.walkerhamill.com/job/graduate-investment-associate/",
            ),
            max_words=220,
        )

