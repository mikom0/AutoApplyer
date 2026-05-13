from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Optional

from dotenv import load_dotenv
from pydantic import ValidationError

from autoapplyer.models import CandidateProfile, GeneratedAnswer


class AnswerGenerator(ABC):
    @abstractmethod
    def generate(
        self,
        question_text: str,
        profile: CandidateProfile,
        job_description: Optional[str] = None,
        max_chars: Optional[int] = None,
    ) -> Optional[GeneratedAnswer]:
        raise NotImplementedError


class NoOpAnswerGenerator(AnswerGenerator):
    def generate(
        self,
        question_text: str,
        profile: CandidateProfile,
        job_description: Optional[str] = None,
        max_chars: Optional[int] = None,
    ) -> Optional[GeneratedAnswer]:
        return None


class OpenAIAnswerGenerator(AnswerGenerator):
    def __init__(self, model: Optional[str] = None):
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required when LLM mode is enabled")
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model or os.getenv("AUTOAPPLYER_OPENAI_MODEL", "gpt-4.1-mini")

    def generate(
        self,
        question_text: str,
        profile: CandidateProfile,
        job_description: Optional[str] = None,
        max_chars: Optional[int] = None,
    ) -> Optional[GeneratedAnswer]:
        profile_json = profile.model_dump_json(indent=2)
        max_chars_instruction = f"Keep the answer under {max_chars} characters." if max_chars else "Keep the answer concise."
        messages = [
            {
                "role": "system",
                "content": (
                    "You draft concise job application form answers. "
                    "Do not fabricate experience, credentials, employment authorization, salary expectations, or qualifications. "
                    "Use only the provided profile, optional resume/context, and job description. "
                    "Return only JSON matching this schema: "
                    '{"question_text": str, "answer": str, "confidence": number, '
                    '"source_basis": "profile|resume|job_description|mixed", "requires_manual_review": bool}.'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question: {question_text}\n\n"
                    f"{max_chars_instruction}\n\n"
                    f"Candidate profile JSON:\n{profile_json}\n\n"
                    f"Job description:\n{job_description or 'Not provided'}"
                ),
            },
        ]
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = response.choices[0].message.content or "{}"
        return parse_generated_answer(content, max_chars=max_chars)


def build_answer_generator(mode: str) -> AnswerGenerator:
    if mode == "off":
        return NoOpAnswerGenerator()
    return OpenAIAnswerGenerator()


def parse_generated_answer(content: str, max_chars: Optional[int] = None) -> GeneratedAnswer:
    try:
        parsed = json.loads(content)
        answer = GeneratedAnswer.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"OpenAI response did not match GeneratedAnswer schema: {exc}") from exc
    if max_chars and len(answer.answer) > max_chars:
        answer.answer = answer.answer[:max_chars].rstrip()
    return answer
