# AutoApplyer Plan

## What Exists Today

- The repository started as an empty workspace with one local CV file: `CV Miko Matheron.pdf`.
- There was no Git repository metadata, Python project setup, application code, tests, or documentation.

## Target Architecture

AutoApplyer is structured as a local, human-in-the-loop job application assistant. It uses Playwright to help complete Workday-style application flows, but it always stops before final submission.

### Layers

1. **Data and configuration**
   - `profile.example.yml` documents the local private profile format.
   - `profile.private.yml` is the intended ignored real profile file.
   - Pydantic models validate the candidate profile, employer config, generated LLM answers, and run context.
   - Employer-specific behavior lives in YAML under `employers/`.

2. **Employer config**
   - `employers/example_workday.yml` is the initial template.
   - Config supports entry URL, ATS family, label aliases, explicit field mappings, upload strategy, step hints, and review/submit boundary detection.
   - Custom Python hooks are intentionally deferred until real employer differences require them.

3. **Field resolution**
   - Field values resolve in this order:
     1. Explicit employer mapping.
     2. Deterministic profile path mapping.
     3. Known normalized label aliases.
     4. Optional LLM answer generation for open-ended prompts.
   - Every field is logged as filled, skipped, or unresolved.

4. **Browser automation**
   - Playwright runs headed by default.
   - Locators prefer labels, roles, visible text, placeholders, and config-isolated CSS selectors.
   - A hard review gate scans for final submit controls and never clicks them.
   - Run artifacts are saved under ignored `runs/`.

5. **Optional LLM layer**
   - `NoOpAnswerGenerator` is the default.
   - `OpenAIAnswerGenerator` is available only when explicitly enabled and configured with environment variables.
   - LLM output is JSON and validated with Pydantic.
   - LLM answers are still subject to manual review before submission.

6. **Dedicated writing assistant**
   - `writing_profile.private.yml` stores richer private writing context and style preferences.
   - `autoapplyer/writing_assistant.py` builds grounded prompts for free-text fields such as recruiter messages and cover-letter-style responses.
   - Generated drafts are validated, linted for banned generic phrasing, saved under ignored `generated_messages/`, and only inserted when employer config explicitly allows it.
   - Dry runs log that generated text would be needed but do not call the provider or fill the form.

7. **Run context**
   - Each run records employer, job URL/title when known, filled fields, generated answers, unresolved fields, and artifact paths.
   - Context is saved as JSON under `runs/`.

8. **Structured CV and per-employer tailoring**
   - `cv.private.yml` stores the CV as structured content (contact, experience, education, extracurricular, skills lines).
   - `autoapplyer/cv_document.py` renders the CV via an HTML + CSS template under `cv_templates/` to a PDF using Playwright's print-to-PDF, producing a layout that matches the original static CV.
   - `autoapplyer/cv_tailor.py` runs two LLM steps behind a `CVTailorProvider` ABC: a keyword analyzer that turns the JD into a `JobKeywordProfile`, and a bullet rewriter that proposes one rewrite per ranked bullet.
   - Each rewrite passes a lint pass that rejects banned phrases, em dashes, overgrowth beyond 15 percent in length, mismatched original text, and flags new tokens that look like fabricated tools, languages, employers, or metrics by cross-referencing `writing_profile.claim_rules.never_invent`.
   - Tailored CVs and a markdown report (keyword profile, accepted rewrites, rejected rewrites, ATS readback) are saved under ignored `generated_cvs/`.
   - The browser runner uses the tailored PDF as the upload when `cv_tailoring.enabled` is true, otherwise it falls back to `profile.documents.resume_path`. The manual review gate still applies.

## MVP Scope

- Python package layout with `pyproject.toml`.
- Strict profile and employer config validation.
- Example private-profile template and Workday-style employer template.
- CLI with `validate-profile`, `validate-employer`, and `run`.
- Headed Playwright runner.
- Generic Workday-like multi-step handler.
- File upload support for the resume path.
- Hard review gate before final submission.
- Structured local run JSON and log artifacts.
- Optional LLM interface plus OpenAI implementation behind an explicit flag.
- Dedicated writing assistant for explicitly configured free-text application fields.
- Unit tests for validation, field normalization/resolution, and LLM response validation.
- Local mock Workday-style HTML fixture for safe smoke testing.
- Documentation for recording and refactoring the first real employer flow.

## Deferred

- Real employer-specific Python hooks.
- CAPTCHA handling of any kind.
- Bot-detection evasion, stealth plugins, proxies, or automated final submission.
- Full Workday account creation/sign-in orchestration.
- Multi-employer batch execution.
- Persistent answer cache beyond a simple ignored cache directory.
- End-to-end browser CI against live sites.
- Multi-page CV layouts; current renderer assumes a single A4 page.
- Alternative CV templates beyond `default`; the template loader is in place but only one template ships.
- Section-level reordering during tailoring; the rewriter only edits bullet text, not entry order or which entries appear.

## Implementation Stages

1. Scaffold the Python package, config files, docs, and ignored private artifacts.
2. Implement Pydantic models and YAML loading.
3. Implement field normalization and deterministic field resolution.
4. Implement optional LLM answer generation interface and JSON validation.
5. Implement headed Playwright runner and generic Workday-like flow.
6. Add mock fixture and tests.
7. Record the first real employer flow with Playwright codegen, then refactor the recording into:
   - Generic Workday behavior when reusable.
   - Employer YAML when selectors or labels differ.
   - A Python hook only if config cannot express the behavior cleanly.
