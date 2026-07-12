"""Resume parsing: PDF -> raw text -> structured candidate profile (via LLM).

This is the offline half of Phase 2. The profile JSON it produces is what the
interview agent receives instead of the hardcoded sample candidate.
"""

import json
import os
import re
from pathlib import Path

from openai import OpenAI
from pypdf import PdfReader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CANDIDATE_DIR = PROJECT_ROOT / "data" / "candidates"

EXTRACTION_PROMPT = """You extract structured data from resumes for an HR screening system.
Given the raw text of a resume, return ONLY a JSON object with these fields:

{
  "name": "full name",
  "email": "email or null",
  "phone": "phone or null",
  "total_experience_years": number (estimate from work history, 0 for freshers),
  "current_company": "most recent employer or null",
  "current_role": "most recent job title or null",
  "companies": [{"name": "...", "role": "...", "duration": "..."}],
  "skills": ["skill1", "skill2", ...],
  "education": "highest qualification, institution, year",
  "notable_projects": ["one-line description of up to 3 significant projects"],
  "summary": "2-3 sentence overview of this candidate"
}

Rules: use null for missing data, never invent facts not present in the resume,
keep skills to the 10-15 most significant."""


def extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(pdf_path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if len(text.strip()) < 100:
        raise ValueError(
            f"Extracted only {len(text.strip())} characters from {pdf_path.name} — "
            "the PDF is probably scanned/image-based. Export a text-based PDF."
        )
    return text


def _llm_providers() -> list[dict]:
    """Provider configs for offline parsing, in preference order."""
    providers = []
    if os.getenv("GROQ_API_KEY"):
        providers.append(
            {
                "name": "groq",
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": os.environ["GROQ_API_KEY"],
                "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            }
        )
    if os.getenv("CEREBRAS_API_KEY"):
        providers.append(
            {
                "name": "cerebras",
                "base_url": "https://api.cerebras.ai/v1",
                "api_key": os.environ["CEREBRAS_API_KEY"],
                "model": os.getenv("CEREBRAS_MODEL", "gpt-oss-120b"),
            }
        )
    if os.getenv("GOOGLE_API_KEY"):
        providers.append(
            {
                "name": "gemini",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                "api_key": os.environ["GOOGLE_API_KEY"],
                "model": os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
            }
        )
    if not providers:
        raise RuntimeError("No LLM API key found in .env (GROQ/CEREBRAS/GOOGLE)")
    return providers


def structure_resume(resume_text: str) -> dict:
    """Ask an LLM to turn raw resume text into the profile schema."""
    last_error: Exception | None = None
    for provider in _llm_providers():
        try:
            client = OpenAI(base_url=provider["base_url"], api_key=provider["api_key"])
            response = client.chat.completions.create(
                model=provider["model"],
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": EXTRACTION_PROMPT},
                    {"role": "user", "content": resume_text[:20000]},
                ],
            )
            profile = json.loads(response.choices[0].message.content)
            profile["_parsed_by"] = f"{provider['name']}/{provider['model']}"
            return profile
        except Exception as e:  # noqa: BLE001 — try the next provider
            print(f"  {provider['name']} failed ({e}), trying next provider...")
            last_error = e
    raise RuntimeError(f"All LLM providers failed to parse the resume: {last_error}")


def profile_to_prompt_block(profile: dict, role_applied: str | None = None) -> str:
    """Render the profile as the candidate block for the interviewer prompt."""
    companies = "; ".join(
        f"{c.get('name')} ({c.get('role')}, {c.get('duration')})"
        for c in profile.get("companies") or []
    )
    lines = [
        f"Name: {profile.get('name')}",
        f"Applied for: {role_applied or profile.get('current_role') or 'the open position'}",
        f"Total experience: {profile.get('total_experience_years')} years",
        f"Current/last: {profile.get('current_role')} at {profile.get('current_company')}",
        f"Work history: {companies or 'not stated'}",
        f"Skills: {', '.join(profile.get('skills') or [])}",
        f"Education: {profile.get('education')}",
        f"Notable projects: {'; '.join(profile.get('notable_projects') or [])}",
        f"Summary: {profile.get('summary')}",
    ]
    return "\n".join(lines)


def save_profile(profile: dict, role_applied: str | None = None) -> Path:
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (profile.get("name") or "candidate").lower()).strip("-")
    path = CANDIDATE_DIR / f"{slug}.json"
    profile["role_applied"] = role_applied
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_latest_profile() -> dict | None:
    """The most recently saved candidate profile, or None if none exist.

    Explicit override: set CANDIDATE_PROFILE=<path to json> in the environment.
    """
    override = os.getenv("CANDIDATE_PROFILE")
    if override:
        return json.loads(Path(override).read_text(encoding="utf-8"))
    if not CANDIDATE_DIR.exists():
        return None
    candidates = sorted(
        CANDIDATE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not candidates:
        return None
    return json.loads(candidates[0].read_text(encoding="utf-8"))
