from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from autoapplyer.browser_runner import WorkdayLikeRunner
from autoapplyer.config import load_employer_config, load_profile, load_writing_profile
from autoapplyer.llm import build_answer_generator
from autoapplyer.writing_assistant import build_writing_assistant


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autoapplyer")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_profile = subparsers.add_parser("validate-profile", help="Validate a candidate profile YAML file.")
    validate_profile.add_argument("--profile", required=True, type=Path)

    validate_employer = subparsers.add_parser("validate-employer", help="Validate an employer config YAML file.")
    validate_employer.add_argument("--employer", required=True, type=Path)

    validate_writing = subparsers.add_parser("validate-writing-profile", help="Validate a private writing profile YAML file.")
    validate_writing.add_argument("--writing-profile", required=True, type=Path)

    generate_text = subparsers.add_parser("generate-text", help="Generate configured application free-text drafts without opening a browser.")
    generate_text.add_argument("--profile", required=True, type=Path)
    generate_text.add_argument("--employer", required=True, type=Path)
    generate_text.add_argument("--writing-profile", required=True, type=Path)
    generate_text.add_argument(
        "--writing-provider",
        choices=["off", "openai"],
        default=None,
        help="Provider for dedicated application writing. Defaults to AUTOAPPLYER_WRITING_PROVIDER or off.",
    )
    generate_text.add_argument("--output-dir", type=Path, default=Path("generated_messages"))

    run = subparsers.add_parser("run", help="Run a headed application assistant flow.")
    run.add_argument("--profile", required=True, type=Path)
    run.add_argument("--employer", required=True, type=Path)
    run.add_argument("--llm-mode", choices=["off", "suggest_only", "autofill_with_review"], default=None)
    run.add_argument("--writing-profile", type=Path, default=None, help="Optional private writing profile for generated free-text fields.")
    run.add_argument(
        "--writing-provider",
        choices=["off", "openai"],
        default=None,
        help="Provider for dedicated application writing. Defaults to AUTOAPPLYER_WRITING_PROVIDER or off.",
    )
    run.add_argument("--dry-run", action="store_true", help="Navigate and log intended actions without modifying fields.")
    run.add_argument("--headless", action="store_true", help="Run browser headless. Headed is the default.")
    run.add_argument("--close-browser", action="store_true", help="Close the browser at the review gate instead of pausing.")
    run.add_argument("--storage-state", type=Path, default=None, help="Optional Playwright storage state JSON.")
    run.add_argument("--runs-dir", type=Path, default=Path("runs"))
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    try:
        if args.command == "validate-profile":
            load_profile(args.profile)
            print(f"Profile OK: {args.profile}")
            return 0

        if args.command == "validate-employer":
            load_employer_config(args.employer)
            print(f"Employer config OK: {args.employer}")
            return 0

        if args.command == "validate-writing-profile":
            load_writing_profile(args.writing_profile)
            print(f"Writing profile OK: {args.writing_profile}")
            return 0

        if args.command == "generate-text":
            profile = load_profile(args.profile)
            employer = load_employer_config(args.employer)
            writing_profile = load_writing_profile(args.writing_profile)
            writing_provider = args.writing_provider or os.getenv("AUTOAPPLYER_WRITING_PROVIDER", "off")
            writing_assistant = build_writing_assistant(writing_provider, writing_profile, output_dir=args.output_dir)
            if not writing_assistant:
                raise RuntimeError("A writing profile and provider are required for generate-text")

            from autoapplyer.models import JobApplicationContext

            job_context = JobApplicationContext(
                employer_name=employer.employer_name,
                job_title=employer.job_title,
                job_url=employer.application_url,
                job_description_text=employer.job_description_text,
            )
            generated_count = 0
            for field in employer.free_text_fields:
                if not field.generation.enabled:
                    continue
                label = field.label_match[0]
                generated = writing_assistant.generate_application_text(
                    job_context=job_context,
                    applicant_profile=profile,
                    field_label=label,
                    field_type=field.type,
                    max_words=field.generation.max_words,
                )
                saved_path = writing_assistant.save_generated_text(generated, job_context)
                generated_count += 1
                print(f"Generated {field.type} for {label}: {saved_path}")
                if generated.warnings:
                    print("Warnings:")
                    for warning in generated.warnings:
                        print(f"- {warning}")
            if generated_count == 0:
                print("No enabled free-text fields found in employer config.")
            return 0

        if args.command == "run":
            profile = load_profile(args.profile)
            employer = load_employer_config(args.employer)
            llm_mode = args.llm_mode or employer.llm_mode
            answer_generator = build_answer_generator(llm_mode)
            writing_profile = load_writing_profile(args.writing_profile) if args.writing_profile else None
            writing_provider = args.writing_provider or os.getenv("AUTOAPPLYER_WRITING_PROVIDER", "off")
            writing_assistant = build_writing_assistant(writing_provider, writing_profile)
            runner = WorkdayLikeRunner(
                profile=profile,
                employer_config=employer,
                employer_config_path=args.employer,
                answer_generator=answer_generator,
                writing_assistant=writing_assistant,
                llm_mode=llm_mode,
                dry_run=args.dry_run,
                headed=not args.headless,
                storage_state=args.storage_state,
                keep_open=not args.close_browser,
                artifacts_dir=args.runs_dir,
            )
            context = runner.run()
            print(f"Run saved: {context.artifact_paths.get('run_json')}")
            print_application_checklist(context)
            print("Stopped before final submission. Manual review is required.")
            return 0

        parser.error("Unknown command")
        return 2
    except Exception as exc:
        logging.getLogger(__name__).exception("Command failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def print_application_checklist(context) -> None:
    events = context.filled_fields
    uploaded = any(event.label == "resume" and event.status == "filled" for event in events)
    resume_detected = any(event.label == "resume" for event in events)
    generated = bool(context.generated_application_texts)
    generated_needed = any(event.source == "llm" and event.status == "skipped" for event in events)
    unresolved = [event for event in events if event.status == "unresolved"]
    deterministic = [
        event.label
        for event in events
        if event.status == "filled" and event.source in {"profile", "employer_config", "alias"} and event.label != "resume"
    ]
    deterministic_detected = [
        event.label
        for event in events
        if event.status == "skipped" and event.source in {"profile", "employer_config", "alias"} and event.label != "resume"
    ]
    print("Checklist:")
    if uploaded:
        print("- CV uploaded: yes")
    elif resume_detected and context.dry_run:
        print("- CV upload: detected, dry-run skipped")
    else:
        print("- CV uploaded: no")
    if generated:
        print("- Generated text: yes")
    elif generated_needed and context.dry_run:
        print("- Generated text: needed, dry-run skipped")
    else:
        print("- Generated text: no")
    if deterministic:
        print(f"- Profile fields filled: {', '.join(deterministic)}")
    elif deterministic_detected and context.dry_run:
        print(f"- Profile fields detected: {', '.join(deterministic_detected)}")
    else:
        print("- Profile fields filled: none")
    print("- Terms checkbox: left for manual review")
    print("- Final submit: not clicked")
    if unresolved:
        print("- Unresolved:")
        for event in unresolved:
            print(f"  - {event.label}: {event.message or 'unresolved'}")


if __name__ == "__main__":
    raise SystemExit(main())
