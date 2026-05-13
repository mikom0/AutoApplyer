from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


StrictModelConfig = ConfigDict(extra="forbid", validate_assignment=True)


class PersonalProfile(BaseModel):
    model_config = StrictModelConfig

    full_name: str
    preferred_name: Optional[str] = None
    email: str
    phone: str
    address: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    country: str
    linkedin: str
    portfolio: Optional[str] = None
    github: Optional[str] = None


class WorkAuthorization(BaseModel):
    model_config = StrictModelConfig

    country: str
    requires_sponsorship: bool
    authorized_to_work: bool
    visa_status: Optional[str] = None


class ApplicationDefaults(BaseModel):
    model_config = StrictModelConfig

    notice_period: str
    available_start_date: Optional[str] = None
    desired_salary: Optional[str] = None
    salary_currency: Optional[str] = None
    willing_to_relocate: bool
    remote_preference: str
    employment_type_preferences: Optional[List[str]] = None


class Documents(BaseModel):
    model_config = StrictModelConfig

    resume_path: str
    cover_letter_path: Optional[str] = None

    @field_validator("resume_path")
    @classmethod
    def resume_path_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resume_path must not be empty")
        return value


class StandardAnswers(BaseModel):
    model_config = StrictModelConfig

    why_interested_generic: Optional[str] = None
    work_authorization_answer: Optional[str] = None
    sponsorship_answer: Optional[str] = None
    salary_answer: Optional[str] = None
    relocation_answer: Optional[str] = None


class SensitiveOptionalAnswers(BaseModel):
    model_config = StrictModelConfig

    gender: Optional[str] = None
    ethnicity: Optional[str] = None
    disability_status: Optional[str] = None
    veteran_status: Optional[str] = None
    pronouns: Optional[str] = None


class CandidateProfile(BaseModel):
    model_config = StrictModelConfig

    personal: PersonalProfile
    work_authorization: WorkAuthorization
    application_defaults: ApplicationDefaults
    documents: Documents
    standard_answers: StandardAnswers = Field(default_factory=StandardAnswers)
    sensitive_optional_answers: SensitiveOptionalAnswers = Field(default_factory=SensitiveOptionalAnswers)


class ResumeUploadStrategy(BaseModel):
    model_config = StrictModelConfig

    label_aliases: List[str] = Field(default_factory=lambda: ["Resume", "CV"])
    selectors: List[str] = Field(default_factory=lambda: ["input[type='file']"])


class FieldMapping(BaseModel):
    model_config = StrictModelConfig

    labels: List[str] = Field(default_factory=list)
    profile_path: Optional[str] = None
    value: Optional[Any] = None
    answer_key: Optional[str] = None
    llm_allowed: bool = False
    max_chars: Optional[int] = None
    required: bool = False
    input_type: Optional[Literal["text", "textarea", "select", "radio", "checkbox", "file"]] = None

    @model_validator(mode="after")
    def _require_a_source(self) -> "FieldMapping":
        if self.profile_path is None and self.value is None and self.answer_key is None and not self.llm_allowed:
            label = self.labels[0] if self.labels else "<no label>"
            raise ValueError(
                f"FieldMapping for '{label}' must set at least one of profile_path, value, "
                "answer_key, or llm_allowed=true"
            )
        return self


class StepConfig(BaseModel):
    model_config = StrictModelConfig

    name: str
    next_button_text: List[str] = Field(default_factory=lambda: ["Next", "Continue"])
    stop_texts: List[str] = Field(default_factory=list)
    field_mappings: List[FieldMapping] = Field(default_factory=list)


class ReviewGateConfig(BaseModel):
    model_config = StrictModelConfig

    stop_texts: List[str] = Field(default_factory=lambda: ["Review", "Submit", "Submit Application"])
    submit_texts: List[str] = Field(default_factory=lambda: ["Submit", "Submit Application"])
    require_manual_review: bool = True


LLMMode = Literal["off", "suggest_only", "autofill_with_review"]
WritingFieldType = Literal[
    "recruiter_message",
    "short_cover_letter",
    "cover_letter",
    "why_interested",
    "why_company",
    "supporting_statement",
    "additional_information",
]


class FreeTextGenerationConfig(BaseModel):
    model_config = StrictModelConfig

    enabled: bool = False
    max_words: int = Field(default=180, ge=20, le=800)
    insert_into_form: bool = False
    provider: Optional[str] = None


class FreeTextFieldConfig(BaseModel):
    model_config = StrictModelConfig

    label_match: List[str]
    type: WritingFieldType
    generation: FreeTextGenerationConfig = Field(default_factory=FreeTextGenerationConfig)
    required: bool = False


class EmployerConfig(BaseModel):
    model_config = StrictModelConfig

    employer_name: str
    ats_family: Literal["workday_like", "generic_form"]
    application_url: str
    job_title: Optional[str] = None
    job_description_text: Optional[str] = None
    job_description_url: Optional[str] = None
    llm_mode: LLMMode = "off"
    start_button_texts: List[str] = Field(default_factory=list)
    label_aliases: Dict[str, List[str]] = Field(default_factory=dict)
    field_mappings: List[FieldMapping] = Field(default_factory=list)
    free_text_fields: List[FreeTextFieldConfig] = Field(default_factory=list)
    resume_upload: ResumeUploadStrategy = Field(default_factory=ResumeUploadStrategy)
    steps: List[StepConfig] = Field(default_factory=list)
    review_gate: ReviewGateConfig = Field(default_factory=ReviewGateConfig)
    selectors: Dict[str, str] = Field(default_factory=dict)


SourceBasis = Literal["profile", "resume", "job_description", "mixed"]
WritingSourceBasis = Literal["profile", "writing_profile", "job_description", "mixed"]


class GeneratedAnswer(BaseModel):
    model_config = StrictModelConfig

    question_text: str
    answer: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_basis: SourceBasis
    requires_manual_review: bool = True

    @field_validator("answer")
    @classmethod
    def answer_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("answer must not be empty")
        return value


class WritingProfile(BaseModel):
    model_config = StrictModelConfig

    applicant_summary: str
    academic_background: "WritingSafePointSection"
    relevant_experience: "WritingExperienceSection"
    sector_interests: List[str] = Field(default_factory=list)
    role_interests: List[str] = Field(default_factory=list)
    writing_voice: "WritingVoice"
    claim_rules: "WritingClaimRules"
    application_preferences: "WritingApplicationPreferences"


class WritingSafePointSection(BaseModel):
    model_config = StrictModelConfig

    safe_points: List[str] = Field(default_factory=list)
    do_not_claim_unless_job_context_confirms: List[str] = Field(default_factory=list)


class WritingEvidencePoint(BaseModel):
    model_config = StrictModelConfig

    claim: str
    basis: str
    allowed_contexts: List[str] = Field(default_factory=list)


class WritingExperienceSection(BaseModel):
    model_config = StrictModelConfig

    safe_points: List[str] = Field(default_factory=list)
    evidence_points: List[WritingEvidencePoint] = Field(default_factory=list)


class WritingVoice(BaseModel):
    model_config = StrictModelConfig

    preferred_style: List[str] = Field(default_factory=lambda: ["concise", "warm", "specific", "direct"])
    avoid_phrases: List[str] = Field(
        default_factory=lambda: [
            "thrilled to apply",
            "excited to apply",
            "esteemed firm",
            "dynamic environment",
            "passionate about innovation",
            "leveraging my diverse skill set",
            "my background aligns well",
        ]
    )
    cadence_rules: List[str] = Field(
        default_factory=lambda: [
            "Vary sentence length.",
            "Avoid rule-of-three rhythm.",
            "Avoid em dashes.",
        ]
    )


class WritingClaimRules(BaseModel):
    model_config = StrictModelConfig

    safe_to_make_without_job_confirmation: List[str] = Field(default_factory=list)
    never_invent: List[str] = Field(
        default_factory=lambda: [
            "employers",
            "grades",
            "deal experience",
            "financial modelling experience",
            "metrics",
            "languages",
            "technical tools",
            "visa status",
        ]
    )


class WritingApplicationPreferences(BaseModel):
    model_config = StrictModelConfig

    recruiter_message_words: List[int] = Field(default_factory=lambda: [120, 220])
    default_tone: str = "polished but not formal"
    prefer_concrete_fit_over_enthusiasm: bool = True
    include_manual_review_note: bool = True


class JobApplicationContext(BaseModel):
    model_config = StrictModelConfig

    employer_name: str
    job_title: Optional[str] = None
    job_url: str
    job_description_text: Optional[str] = None


class GeneratedApplicationText(BaseModel):
    model_config = StrictModelConfig

    field_label: str
    field_type: WritingFieldType
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_basis: WritingSourceBasis
    requires_manual_review: bool = True
    grounding_notes: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @field_validator("text")
    @classmethod
    def text_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be empty")
        return value


FieldStatus = Literal["filled", "skipped", "unresolved", "generated", "review_gate", "clicked"]
FieldSource = Literal["employer_config", "profile", "alias", "llm", "system"]


class FieldEvent(BaseModel):
    model_config = StrictModelConfig

    label: str
    status: FieldStatus
    source: FieldSource
    value_preview: Optional[str] = None
    message: Optional[str] = None


class ApplicationRunContext(BaseModel):
    model_config = StrictModelConfig

    run_id: str
    employer: str
    job_url: str
    job_title: Optional[str] = None
    run_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    dry_run: bool = False
    filled_fields: List[FieldEvent] = Field(default_factory=list)
    generated_answers: List[GeneratedAnswer] = Field(default_factory=list)
    generated_application_texts: List[GeneratedApplicationText] = Field(default_factory=list)
    unresolved_fields: List[str] = Field(default_factory=list)
    artifact_paths: Dict[str, str] = Field(default_factory=dict)

    def add_event(self, event: FieldEvent) -> None:
        self.filled_fields.append(event)
        if event.status == "unresolved":
            self.unresolved_fields.append(event.label)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
