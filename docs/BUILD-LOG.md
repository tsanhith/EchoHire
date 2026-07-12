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

## 2026-07-12 — First real interview test + fixes (`feat/transcripts-and-hangup`)

Ran a full ~4 minute mock interview in console mode. It worked end-to-end: all
7 stages, resume-grounded questions, correct deflection of company questions,
~2.6s end-to-end voice latency. Three problems surfaced from the session log:

1. **The call never ended** — after "Goodbye" the session sat open until Ctrl+C.
   Fixed by giving the agent an `end_interview` function tool (LLM tool-calling):
   the closing-stage prompt tells it to call the tool after saying goodbye, the
   tool waits for the farewell audio to finish playing, then shuts the session down.
2. **The transcript was lost** — the entire point of a screening call is the
   answers (CTC, notice period). Added a shutdown callback that serializes
   `session.history` to `data/transcripts/<room>_<timestamp>.json`.
3. **STT misheard a number** — "twelve LPA" transcribed as "full LPA", and the
   LLM silently guessed. Numbers are the highest-stakes data in the call, so the
   compensation stage now requires repeating CTC/notice figures back to the
   candidate for confirmation.

Also: `load_dotenv` now resolves `.env` relative to the project root, so the
agent can be launched from any working directory.

## 2026-07-12 — Second test: the call still wouldn't die (`fix/call-ending`)

Second live test showed two failure modes: (1) the LLM often never called
`end_interview`, and (2) with no stages left it started hallucinating filler
conversation. Lesson learned: **an LLM instruction buried in stage 7 is a
suggestion, not a guarantee** — call termination needs defense in depth:

1. **Prompt**: a dedicated "Ending the call (critical)" section — goodbye and
   the tool call must happen in the same turn; explicit rules for "candidate
   has no questions", "candidate says bye early", and reschedule requests;
   "running past the end is a failure".
2. **Code**: `session.shutdown()` alone only closes the session — the job (and
   console process) keeps running. `end_interview` now also calls
   `get_job_context().shutdown()`, which terminates the job and fires the
   transcript-save callback.
3. **Watchdog**: a hard 15-minute `asyncio` timer force-ends any call as a
   final backstop — no runaway calls, no drained free-tier quota.
