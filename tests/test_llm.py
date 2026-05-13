import pytest

from autoapplyer.llm import NoOpAnswerGenerator, parse_generated_answer


def test_noop_answer_generator_returns_none(profile):
    generator = NoOpAnswerGenerator()

    assert generator.generate("Why this role?", profile) is None


def test_parse_generated_answer_validates_mocked_json_response():
    answer = parse_generated_answer(
        """
        {
          "question_text": "Why this role?",
          "answer": "The role aligns with my automation and product engineering experience.",
          "confidence": 0.82,
          "source_basis": "profile",
          "requires_manual_review": true
        }
        """,
        max_chars=120,
    )

    assert answer.confidence == 0.82
    assert answer.requires_manual_review is True


def test_parse_generated_answer_rejects_invalid_json_response():
    with pytest.raises(ValueError):
        parse_generated_answer('{"question_text": "Why?", "answer": "", "confidence": 0.7, "source_basis": "profile", "requires_manual_review": true}')

