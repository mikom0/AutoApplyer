# AutoApplyer

AutoApplyer is a local, profile-driven job application assistant. It uses headed Playwright automation to help fill Workday-style application forms from a validated local YAML profile, upload your CV, optionally draft open-ended answers with an LLM, and then stop before final submission for manual review.

It is intentionally not a mass-submission bot. It does not bypass CAPTCHAs, evade bot detection, rotate proxies, or click final submit buttons.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
python -m playwright install
cp .env.example .env
cp profile.example.yml profile.private.yml
cp writing_profile.example.yml writing_profile.private.yml
```

Edit `profile.private.yml` with your real details. Private profiles, `.env`, browser state, traces, screenshots, and run outputs are gitignored.

## Validate Config

```bash
autoapplyer validate-profile --profile profile.private.yml
autoapplyer validate-employer --employer employers/example_workday.yml
autoapplyer validate-writing-profile --writing-profile writing_profile.private.yml
```

## Run Against The Mock Fixture

```bash
autoapplyer run \
  --profile profile.private.yml \
  --employer employers/example_workday.yml \
  --dry-run
```

Remove `--dry-run` to fill fields in the headed browser. The runner still stops at the review gate and never clicks final submit.

## Run Against A Real Employer

1. Copy `employers/example_workday.yml` to a new employer file.
2. Set `application_url` to the real application URL.
3. Add label aliases or explicit field mappings for employer-specific wording.
4. Run headed mode:

```bash
autoapplyer run --profile profile.private.yml --employer employers/acme_workday.yml
```

## Optional OpenAI Answers

LLM use is off by default. To enable suggestions, set:

```bash
OPENAI_API_KEY=...
AUTOAPPLYER_OPENAI_MODEL=gpt-4.1-mini
```

Then choose an LLM mode:

```bash
autoapplyer run \
  --profile profile.private.yml \
  --employer employers/acme_workday.yml \
  --llm-mode suggest_only
```

Supported modes:

- `off`: never call an LLM.
- `suggest_only`: generate answers and log them, but do not fill them.
- `autofill_with_review`: fill validated generated answers where allowed by config, while still stopping before submit.

## Recording The First Real Workday Flow

Use Playwright codegen for the first employer:

```bash
python -m playwright codegen "https://example.wd1.myworkdayjobs.com/example/job/..."
```

During recording:

- Apply normally until the final review page.
- Do not submit.
- Save the generated script in `docs/recordings/` or a scratch file outside version control if it contains private data.

Refactor the recording this way:

- Move reusable interactions, such as clicking `Next`, filling by label, selecting radios, and uploading files, into the generic Workday flow.
- Move employer-specific labels, selectors, and prompt wording into `employers/<name>.yml`.
- Add a custom Python hook only when config cannot express a genuinely unique step.

## Tests

```bash
pytest
```

The unit tests cover profile/config validation, field normalization and mapping, and validated LLM response parsing. The local HTML fixture in `tests/fixtures/mock_workday.html` is available for safe Playwright smoke runs.

## Dedicated Writing Assistant

Free-text application boxes should use the writing assistant rather than static profile strings. This is designed for fields such as `Message`, `Cover Letter`, `Why are you interested in this role?`, `Why this company?`, `Supporting statement`, and `Additional information`.

Edit `writing_profile.private.yml` with your real writing context. Keep it grounded: only add evidence and claims you are comfortable reusing in applications.

Example employer config:

```yaml
free_text_fields:
  - label_match:
      - "Message"
    type: "recruiter_message"
    generation:
      enabled: true
      max_words: 220
      insert_into_form: true
```

Run with the writing profile and a provider:

```bash
AUTOAPPLYER_WRITING_PROVIDER=openai \
autoapplyer run \
  --profile profile.private.yml \
  --writing-profile writing_profile.private.yml \
  --employer employers/acme_workday.yml
```

In `--dry-run`, generated text is not created or inserted. The run only logs that the field would require generated text.

Generated drafts are saved under ignored `generated_messages/` for review. Even when text is inserted into a form, AutoApplyer still stops before final submission.

To generate a draft without opening a browser:

```bash
autoapplyer generate-text \
  --profile profile.private.yml \
  --writing-profile writing_profile.private.yml \
  --writing-provider openai \
  --employer employers/walker_hamill_graduate_investment_associate.yml
```
