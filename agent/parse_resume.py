"""CLI: parse a resume PDF into a candidate profile the agent can interview.

Usage:
    python agent/parse_resume.py path/to/resume.pdf
    python agent/parse_resume.py path/to/resume.pdf --role "Backend Developer"

The parsed profile is saved to data/candidates/<name>.json; the interview
agent automatically picks up the most recently parsed candidate.
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

# Windows consoles default to cp1252, which can't print many characters
# LLMs emit (e.g. non-breaking hyphens in parsed resumes).
sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from resume import (  # noqa: E402 — needs .env loaded first
    extract_pdf_text,
    profile_to_prompt_block,
    save_profile,
    structure_resume,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a resume PDF for EchoHire")
    parser.add_argument("pdf", type=Path, help="path to the resume PDF")
    parser.add_argument("--role", default=None, help="role the candidate applied for")
    args = parser.parse_args()

    if not args.pdf.exists():
        raise SystemExit(f"File not found: {args.pdf}")

    print(f"Extracting text from {args.pdf.name}...")
    text = extract_pdf_text(args.pdf)
    print(f"  {len(text)} characters extracted")

    print("Structuring with LLM...")
    profile = structure_resume(text)
    print(f"  parsed by {profile.get('_parsed_by')}")

    path = save_profile(profile, role_applied=args.role)
    print(f"\nSaved: {path}\n")
    print("The agent will interview this candidate:\n")
    print(profile_to_prompt_block(profile, role_applied=args.role))


if __name__ == "__main__":
    main()
