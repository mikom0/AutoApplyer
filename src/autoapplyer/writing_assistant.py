from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from pydantic import ValidationError

from autoapplyer.models import (
    CandidateProfile,
    GeneratedApplicationText,
    JobApplicationContext,
    WritingFieldType,
    WritingProfile,
)

logger = logging.getLogger(__name__)


class ApplicationTextProvider(ABC):
    @abstractmethod
    def generate(self, prompt_payload: Dict[str, object], max_words: int) -> GeneratedApplicationText:
        raise NotImplementedError


class NoOpApplicationTextProvider(ApplicationTextProvider):
    def generate(self, prompt_payload: Dict[str, object], max_words: int) -> GeneratedApplicationText:
        raise RuntimeError("Writing provider is disabled")


class OpenAIApplicationTextProvider(ApplicationTextProvider):
    def __init__(self, model: Optional[str] = None):
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required when the writing provider is openai")
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model or os.getenv("AUTOAPPLYER_WRITING_MODEL", "gpt-4.1")

    def generate(self, prompt_payload: Dict[str, object], max_words: int) -> GeneratedApplicationText:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": load_system_prompt()},
                {"role": "user", "content": json.dumps(prompt_payload, indent=2)},
            ],
            response_format={"type": "json_object"},
            temperature=0.35,
        )
        content = response.choices[0].message.content or "{}"
        return parse_generated_application_text(content, max_words=max_words)


class WritingAssistant:
    def __init__(
        self,
        provider: ApplicationTextProvider,
        writing_profile: WritingProfile,
        output_dir: Path = Path("generated_messages"),
    ):
        self.provider = provider
        self.writing_profile = writing_profile
        self.output_dir = output_dir

    def generate_application_text(
        self,
        job_context: JobApplicationContext,
        applicant_profile: CandidateProfile,
        field_label: str,
        field_type: WritingFieldType,
        max_words: int,
    ) -> GeneratedApplicationText:
        payload = build_prompt_payload(
            job_context=job_context,
            applicant_profile=applicant_profile,
            writing_profile=self.writing_profile,
            field_label=field_label,
            field_type=field_type,
            max_words=max_words,
        )
        generated = self.provider.generate(payload, max_words=max_words)
        generated = lint_generated_application_text(
            generated,
            writing_profile=self.writing_profile,
            job_context=job_context,
            max_words=max_words,
        )
        return generated

    def save_generated_text(
        self,
        generated: GeneratedApplicationText,
        job_context: JobApplicationContext,
        timestamp: Optional[str] = None,
    ) -> Path:
        timestamp = timestamp or time.strftime("%Y%m%d-%H%M%S")
        employer_slug = slugify(job_context.employer_name)
        job_slug = slugify(job_context.job_title or "application")
        path = self.output_dir / f"{employer_slug}_{job_slug}_{timestamp}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_generated_text_markdown(generated, job_context), encoding="utf-8")
        return path


def build_writing_assistant(
    provider_name: str,
    writing_profile: Optional[WritingProfile],
    output_dir: Path = Path("generated_messages"),
) -> Optional[WritingAssistant]:
    if not writing_profile:
        return None
    if provider_name == "off":
        return WritingAssistant(NoOpApplicationTextProvider(), writing_profile, output_dir)
    if provider_name == "openai":
        return WritingAssistant(OpenAIApplicationTextProvider(), writing_profile, output_dir)
    raise ValueError(f"Unsupported writing provider: {provider_name}")


def build_prompt_payload(
    job_context: JobApplicationContext,
    applicant_profile: CandidateProfile,
    writing_profile: WritingProfile,
    field_label: str,
    field_type: WritingFieldType,
    max_words: int,
) -> Dict[str, object]:
    return {
        "task": {
            "field_label": field_label,
            "field_type": field_type,
            "target_max_words": max_words,
            "instructions": [
                "Draft one final answer only.",
                "For recruiter_message, write a concise recruiter note, not a ceremonial cover letter.",
                "Use two internal candidate drafts mentally, then revise into the strongest final version.",
                "Set requires_manual_review to true.",
            ],
            "output_schema": {
                "field_label": "string",
                "field_type": field_type,
                "text": "string",
                "confidence": "number 0-1",
                "source_basis": "profile|writing_profile|job_description|mixed",
                "requires_manual_review": True,
                "grounding_notes": ["supplied facts used"],
                "warnings": ["quality or evidence warnings"],
                "notes": "optional string",
            },
        },
        "job_context": job_context.model_dump(),
        "candidate_profile": applicant_profile.model_dump(),
        "writing_profile": writing_profile.model_dump(),
    }


def parse_generated_application_text(content: str, max_words: int) -> GeneratedApplicationText:
    try:
        parsed = json.loads(content)
        generated = GeneratedApplicationText.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"Writing provider response did not match GeneratedApplicationText schema: {exc}") from exc
    words = count_words(generated.text)
    if words > max_words:
        raise ValueError(f"Generated text has {words} words, above configured max_words={max_words}")
    return generated


def lint_generated_application_text(
    generated: GeneratedApplicationText,
    writing_profile: WritingProfile,
    job_context: JobApplicationContext,
    max_words: int,
) -> GeneratedApplicationText:
    words = count_words(generated.text)
    if words > max_words:
        raise ValueError(f"Generated text has {words} words, above configured max_words={max_words}")

    lowered = generated.text.lower()
    banned = [phrase for phrase in writing_profile.writing_voice.avoid_phrases if phrase.lower() in lowered]
    if banned:
        raise ValueError(f"Generated text contains banned phrases: {', '.join(banned)}")

    warnings = list(generated.warnings)
    role_terms = role_specific_terms(job_context)
    if role_terms and not any(term in lowered for term in role_terms):
        warnings.append("Generated text may be too generic: no role or employer terms were found.")
    if generated.requires_manual_review is not True:
        warnings.append("Provider did not mark manual review as required; corrected locally.")
        generated.requires_manual_review = True
    generated.warnings = warnings
    return generated


def role_specific_terms(job_context: JobApplicationContext) -> List[str]:
    raw_terms = [job_context.employer_name, job_context.job_title or ""]
    terms: List[str] = []
    for raw in raw_terms:
        for term in re.split(r"[^A-Za-z0-9]+", raw.lower()):
            if len(term) >= 5:
                terms.append(term)
    return terms


def render_generated_text_markdown(generated: GeneratedApplicationText, job_context: JobApplicationContext) -> str:
    warnings = "\n".join(f"- {warning}" for warning in generated.warnings) or "- None"
    grounding = "\n".join(f"- {note}" for note in generated.grounding_notes) or "- Not provided"
    return (
        f"# Generated Application Text\n\n"
        f"Employer: {job_context.employer_name}\n\n"
        f"Job title: {job_context.job_title or 'Unknown'}\n\n"
        f"Job URL: {job_context.job_url}\n\n"
        f"Field: {generated.field_label}\n\n"
        f"Type: {generated.field_type}\n\n"
        f"Requires manual review: {generated.requires_manual_review}\n\n"
        f"Confidence: {generated.confidence}\n\n"
        f"## Draft\n\n{generated.text}\n\n"
        f"## Grounding Notes\n\n{grounding}\n\n"
        f"## Warnings\n\n{warnings}\n"
    )


def load_system_prompt() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "application_text_system.md"
    return prompt_path.read_text(encoding="utf-8")


def count_words(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text))


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "application"
