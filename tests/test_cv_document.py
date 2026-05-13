from pathlib import Path

import pytest

from autoapplyer.config import load_cv_document
from autoapplyer.cv_document import render_cv_html, render_cv_pdf
from autoapplyer.models import CVDocument


ROOT = Path(__file__).resolve().parents[1]


def test_cv_example_validates():
    cv = load_cv_document(ROOT / "cv.example.yml")
    assert isinstance(cv, CVDocument)
    assert cv.contact.full_name
    assert cv.experience
    assert cv.education


def test_iter_bullet_locations_yields_stable_ids():
    cv = load_cv_document(ROOT / "cv.example.yml")
    locations = cv.iter_bullet_locations()
    ids = [loc.bullet_id for loc in locations]
    assert len(ids) == len(set(ids))
    for loc in locations:
        assert "-" in loc.bullet_id
        assert loc.original_text


def test_render_cv_html_contains_all_bullets():
    cv = load_cv_document(ROOT / "cv.example.yml")
    html = render_cv_html(cv)
    for entry in cv.experience + cv.extracurricular:
        for bullet in entry.bullets:
            snippet = bullet[:30]
            assert snippet in html, f"missing bullet snippet in HTML: {snippet}"


def test_render_cv_html_role_company_ordering():
    cv = load_cv_document(ROOT / "cv.example.yml")
    html = render_cv_html(cv)
    experience_entry = cv.experience[0]
    assert experience_entry.location
    assert f"{experience_entry.company} {experience_entry.role}" in html

    extracurricular_entry = cv.extracurricular[0]
    assert extracurricular_entry.location is None
    assert f"{extracurricular_entry.role}" in html
    assert f">{extracurricular_entry.company}<" in html


def test_render_cv_pdf_produces_pdf_bytes(tmp_path: Path):
    cv = load_cv_document(ROOT / "cv.example.yml")
    out = tmp_path / "cv.pdf"
    render_cv_pdf(cv, out)
    data = out.read_bytes()
    assert data[:4] == b"%PDF"
    assert len(data) > 10_000


def test_empty_bullet_rejected():
    with pytest.raises(Exception):
        CVDocument.model_validate(
            {
                "contact": {
                    "full_name": "X",
                    "phone": "1",
                    "email": "x@x.x",
                    "linkedin_label": "X",
                    "linkedin_url": "https://x",
                },
                "experience": [
                    {
                        "bullet_id_prefix": "a",
                        "role": "r",
                        "company": "c",
                        "location": "l",
                        "date_range": "d",
                        "bullets": ["valid", "   "],
                    }
                ],
            }
        )
