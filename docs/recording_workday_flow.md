# Recording And Refactoring A Workday Flow

## Record

```bash
python -m playwright codegen "https://example.wd1.myworkdayjobs.com/example/job/..."
```

Use the browser normally until the final review page. Stop there. Do not submit.

If the flow requires sign-in, save browser storage state manually only to an ignored path such as `.auth/workday-storage-state.json`.

## Refactor

Review the generated code and split it into three categories:

1. **Reusable Workday behavior**
   - Fill by label.
   - Select radio/checkbox by visible text.
   - Upload resume into file inputs.
   - Click `Next`, `Continue`, or `Review`.
   - Detect final submit controls and stop.

2. **Employer config**
   - Application URL.
   - Employer name and job title.
   - Label aliases.
   - Field mappings.
   - Resume upload selector strategy.
   - Step button text and review boundary text.

3. **Custom hook**
   - Use only when the page has behavior that cannot be cleanly represented in YAML.

## Safety Boundary

AutoApplyer must never click final submit controls. The generic flow treats submit-like button text as a review gate and returns control to the user.

