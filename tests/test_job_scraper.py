from pathlib import Path

import pytest

from autoapplyer.job_scraper import _infer_employer, scrape_job_posting


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "mock_workday_jd.html"


def test_scrape_local_mock_workday_jd():
    result = scrape_job_posting(FIXTURE.as_uri(), headed=False, settle_ms=200)
    assert "Total Return team" in result.job_description_text
    assert "financial modelling" in result.job_description_text.lower()
    assert result.job_title == "Junior Investment Analyst"
    assert result.extraction_selector == "[data-automation-id='jobPostingDescription']"
    assert "Mock Capital Partners is an equal opportunity employer" not in result.job_description_text


def test_infer_employer_workday_subdomain():
    employer = _infer_employer(
        "https://acme.wd1.myworkdayjobs.com/external/job/123",
        page_title="Junior Analyst | Acme",
        job_title="Junior Analyst",
    )
    assert employer.lower() == "acme"


def test_infer_employer_falls_back_to_host():
    employer = _infer_employer(
        "https://careers.example-firm.com/jobs/123",
        page_title="",
        job_title=None,
    )
    assert "example" in employer.lower()


def test_infer_employer_uses_page_title_split():
    employer = _infer_employer(
        "https://random.host/jobs/abc",
        page_title="Analyst Programme | Capital Co",
        job_title="Analyst Programme",
    )
    assert employer == "Capital Co"
