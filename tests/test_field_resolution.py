from pathlib import Path

from autoapplyer.config import load_employer_config, load_profile
from autoapplyer.field_resolution import FieldResolver, normalize_label


ROOT = Path(__file__).resolve().parents[1]


def test_normalize_label_removes_required_marker_and_punctuation():
    assert normalize_label("Email Address * (Required)") == "email address"


def test_explicit_mapping_resolves_from_profile():
    profile = load_profile(ROOT / "profile.example.yml")
    employer = load_employer_config(ROOT / "employers" / "example_workday.yml")
    resolver = FieldResolver(profile, employer)

    resolved = resolver.resolve_mapping(employer.field_mappings[0])

    assert resolved is not None
    assert resolved.value == "Miko Matheron"
    assert resolved.source == "profile"


def test_alias_mapping_resolves_work_authorization_boolean_to_yes():
    profile = load_profile(ROOT / "profile.example.yml")
    employer = load_employer_config(ROOT / "employers" / "example_workday.yml")
    resolver = FieldResolver(profile, employer)

    resolved = resolver.resolve_label("Are you authorized to work in this country?")

    assert resolved is not None
    assert resolved.value == "Yes"
    assert resolved.profile_path == "work_authorization.authorized_to_work"

