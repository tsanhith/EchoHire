# Build Log

Chronological notes on what was built, what broke, and what was learned.

---

## 2026-07-12 — Phase 0: Setup

- Repo initialized with `main` / `dev` / feature-branch workflow, conventional commits.
- Python 3.13 venv, `.env` + committed `.env.example` pattern for secrets.
- Studied the reference project (LiveKit + Deepgram + Groq + Vobiz SIP). Key insight:
  its ₹2–3/min cost is entirely the PSTN/SIP leg. Replacing the phone call with a
  browser WebRTC call through LiveKit Cloud makes the whole pipeline free-tier.
- Wrote [PLAN.md](PLAN.md) and the [company questionnaire](QUESTIONS-FOR-COMPANY.md).

## 2026-07-12 — Phase 1: Voice agent core (`feat/voice-agent`)

Goal: a working AI interviewer you can talk to, before any web UI exists.

**Stack wiring** (`agent/main.py`):
- `AgentSession` from `livekit-agents` orchestrates the loop:
  Silero VAD (detects speech) → Deepgram Nova-3 STT → Groq Llama 3.3 70B → Deepgram Aura TTS.
- A local turn-detection model decides when the candidate has *finished* a thought —
  much more natural than a fixed silence timeout, and it runs locally (free).
- Groq chosen for the LLM because voice UX lives or dies on latency; Groq's
  inference is the fastest available on a free tier.

**Prompt design** (`agent/prompts.py`):
- Voice-specific rules matter more than expected: short replies, one question at a
  time, no markdown/symbols (TTS reads them aloud), numbers in words.
- The interview is a fixed 7-stage structure (greeting → experience → current
  situation → CTC → logistics → candidate questions → close) so mandatory screening
  data (CTC, notice period) is never skipped.
- Candidate profile is hardcoded for now; Phase 2 injects it from the parsed resume.

**Testing**: `python agent/main.py console` runs the full pipeline against your
microphone in the terminal — no frontend needed.
