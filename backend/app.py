"""EchoHire backend: candidate application portal + interview access + results.

Run:
    uvicorn backend.app:app --reload --port 8000     (from the project root)

Endpoints:
    POST /api/apply     candidate form + resume PDF -> parsed profile + interview link
    GET  /api/token     LiveKit access token for joining an interview room
    GET  /api/results   transcripts + AI evaluations for the recruiter view
    /                   static frontend (apply page, interview room, results)
"""

import json
import os
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "agent"))  # reuse resume parsing code

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from livekit import api  # noqa: E402

from resume import (  # noqa: E402
    CANDIDATE_DIR,
    extract_pdf_text,
    save_profile,
    structure_resume,
)

UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
TRANSCRIPT_DIR = PROJECT_ROOT / "data" / "transcripts"
EVALUATION_DIR = PROJECT_ROOT / "data" / "evaluations"

app = FastAPI(title="EchoHire", docs_url="/api/docs")


@app.post("/api/apply")
async def apply(
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    role: str = Form(...),
    resume: UploadFile = File(...),
):
    if not (resume.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF resume")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w.-]", "_", resume.filename)
    pdf_path = UPLOAD_DIR / f"{int(time.time())}_{safe_name}"
    pdf_path.write_bytes(await resume.read())

    try:
        text = extract_pdf_text(pdf_path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    try:
        profile = structure_resume(text)
    except RuntimeError as e:
        raise HTTPException(503, f"Resume parsing unavailable: {e}") from e

    # form fields fill gaps the resume didn't state
    profile.setdefault("name", name)
    profile["email"] = profile.get("email") or email
    profile["phone"] = profile.get("phone") or phone
    profile_path = save_profile(profile, role_applied=role)

    room = f"interview-{profile_path.stem}-{int(time.time())}"
    return {
        "candidate": profile.get("name"),
        "room": room,
        "interview_url": f"/interview.html?room={room}",
    }


@app.get("/api/token")
def token(room: str):
    if not re.fullmatch(r"interview-[\w-]+", room):
        raise HTTPException(400, "invalid room name")
    grant = api.VideoGrants(room_join=True, room=room)
    jwt = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity("candidate")
        .with_name("Candidate")
        .with_grants(grant)
        .to_jwt()
    )
    return {"token": jwt, "url": os.environ["LIVEKIT_URL"]}


def _interviewed_slugs() -> set[str]:
    """Candidate slugs that have at least one saved transcript."""
    slugs = set()
    if TRANSCRIPT_DIR.exists():
        for t_path in TRANSCRIPT_DIR.glob("*.json"):
            match = re.match(r"interview-(.+)-\d+_\d{8}_\d{6}$", t_path.stem)
            if match:
                slugs.add(match.group(1))
    return slugs


@app.get("/api/candidates")
def candidates():
    """All applications, newest first, with interview status."""
    if not CANDIDATE_DIR.exists():
        return []
    interviewed = _interviewed_slugs()
    out = []
    for path in sorted(
        CANDIDATE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append(
            {
                "slug": path.stem,
                "name": profile.get("name"),
                "role_applied": profile.get("role_applied"),
                "email": profile.get("email"),
                "experience_years": profile.get("total_experience_years"),
                "applied_at": int(path.stat().st_mtime),
                "interviewed": path.stem in interviewed,
            }
        )
    return out


@app.get("/api/results")
def results():
    """All interviews, newest first, each with its evaluation if one exists."""
    if not TRANSCRIPT_DIR.exists():
        return []
    out = []
    for t_path in sorted(TRANSCRIPT_DIR.glob("*.json"), reverse=True):
        entry: dict = {"id": t_path.stem}
        match = re.match(r"interview-(.+)-\d+_\d{8}_\d{6}$", t_path.stem)
        if match:
            profile_path = CANDIDATE_DIR / f"{match.group(1)}.json"
            if profile_path.exists():
                try:
                    profile = json.loads(profile_path.read_text(encoding="utf-8"))
                    entry["candidate"] = {
                        "name": profile.get("name"),
                        "role_applied": profile.get("role_applied"),
                        "email": profile.get("email"),
                    }
                except json.JSONDecodeError:
                    pass
        try:
            entry["transcript"] = json.loads(t_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            entry["transcript"] = None
        e_path = EVALUATION_DIR / t_path.name
        if e_path.exists():
            try:
                entry["evaluation"] = json.loads(e_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                entry["evaluation"] = None
        out.append(entry)
    return out


app.mount("/", StaticFiles(directory=PROJECT_ROOT / "frontend", html=True), name="frontend")
