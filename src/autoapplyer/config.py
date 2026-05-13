from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from autoapplyer.models import CandidateProfile, EmployerConfig, WritingProfile


def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return raw


def load_profile(path: Path) -> CandidateProfile:
    return CandidateProfile.model_validate(load_yaml(path))


def load_employer_config(path: Path) -> EmployerConfig:
    return EmployerConfig.model_validate(load_yaml(path))


def load_writing_profile(path: Path) -> WritingProfile:
    return WritingProfile.model_validate(load_yaml(path))


def resolve_local_path(base_dir: Path, configured_path: str) -> Path:
    path = Path(configured_path).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def resolve_application_url(config_path: Path, application_url: str) -> str:
    if application_url.startswith("file://") and not application_url.startswith("file:///"):
        relative = application_url.removeprefix("file://")
        absolute = (config_path.parent.parent / relative).resolve()
        return absolute.as_uri()
    return application_url
