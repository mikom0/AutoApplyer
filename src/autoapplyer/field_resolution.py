from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from autoapplyer.models import CandidateProfile, EmployerConfig, FieldMapping


KNOWN_LABEL_PATHS: Dict[str, str] = {
    "full name": "personal.full_name",
    "legal name": "personal.full_name",
    "name": "personal.full_name",
    "preferred name": "personal.preferred_name",
    "email": "personal.email",
    "email address": "personal.email",
    "phone": "personal.phone",
    "phone number": "personal.phone",
    "mobile phone": "personal.phone",
    "city": "personal.city",
    "region": "personal.region",
    "country": "personal.country",
    "linkedin": "personal.linkedin",
    "linkedin profile": "personal.linkedin",
    "portfolio": "personal.portfolio",
    "github": "personal.github",
    "notice period": "application_defaults.notice_period",
    "available start date": "application_defaults.available_start_date",
    "desired salary": "application_defaults.desired_salary",
    "salary": "application_defaults.desired_salary",
    "remote preference": "application_defaults.remote_preference",
    "willing to relocate": "application_defaults.willing_to_relocate",
    "authorized to work": "work_authorization.authorized_to_work",
    "are you authorized to work in this country": "work_authorization.authorized_to_work",
    "require sponsorship": "work_authorization.requires_sponsorship",
    "will you now or in the future require sponsorship": "work_authorization.requires_sponsorship",
    "why are you interested in this role": "standard_answers.why_interested_generic",
}


@dataclass(frozen=True)
class ResolvedField:
    label: str
    value: Any
    source: str
    profile_path: Optional[str] = None
    max_chars: Optional[int] = None
    llm_allowed: bool = False


def normalize_label(label: str) -> str:
    lowered = label.lower()
    lowered = lowered.replace("*", " ")
    lowered = re.sub(r"\(required\)|required", " ", lowered)
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def get_profile_value(profile: CandidateProfile, profile_path: str) -> Any:
    value: Any = profile.model_dump()
    for part in profile_path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(profile_path)
        value = value[part]
    return value


def bool_to_form_value(value: bool) -> str:
    return "Yes" if value else "No"


def display_value(value: Any) -> str:
    if isinstance(value, bool):
        return bool_to_form_value(value)
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


class FieldResolver:
    def __init__(self, profile: CandidateProfile, employer_config: EmployerConfig):
        self.profile = profile
        self.employer_config = employer_config
        self.alias_map = self._build_alias_map()

    def resolve_mapping(self, mapping: FieldMapping) -> Optional[ResolvedField]:
        label = mapping.labels[0] if mapping.labels else mapping.profile_path or "configured field"
        if mapping.value is not None:
            return ResolvedField(
                label=label,
                value=mapping.value,
                source="employer_config",
                max_chars=mapping.max_chars,
                llm_allowed=mapping.llm_allowed,
            )
        if mapping.profile_path:
            try:
                value = get_profile_value(self.profile, mapping.profile_path)
            except KeyError:
                return None
            if value is None or value == "":
                return None
            return ResolvedField(
                label=label,
                value=display_value(value),
                source="profile",
                profile_path=mapping.profile_path,
                max_chars=mapping.max_chars,
                llm_allowed=mapping.llm_allowed,
            )
        return None

    def resolve_label(self, label: str) -> Optional[ResolvedField]:
        normalized = normalize_label(label)
        profile_path = self.alias_map.get(normalized) or KNOWN_LABEL_PATHS.get(normalized)
        if not profile_path:
            return None
        try:
            value = get_profile_value(self.profile, profile_path)
        except KeyError:
            return None
        if value is None or value == "":
            return None
        return ResolvedField(
            label=label,
            value=display_value(value),
            source="alias",
            profile_path=profile_path,
        )

    def _build_alias_map(self) -> Dict[str, str]:
        alias_map: Dict[str, str] = {}
        for profile_path, aliases in self.employer_config.label_aliases.items():
            for alias in aliases:
                alias_map[normalize_label(alias)] = profile_path
        for mapping in self.employer_config.field_mappings:
            if mapping.profile_path:
                for label in mapping.labels:
                    alias_map[normalize_label(label)] = mapping.profile_path
        return alias_map

