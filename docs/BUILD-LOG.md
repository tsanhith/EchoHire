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

## 2026-07-12 — Rate-limited mid-interview: multi-provider LLM fallback

A test call died with a Groq 429: the free tier allows 100k tokens/day on the
70B model and one day of testing ate all of it. Two responses:

1. **Cut the burn**: preemptive generation (on by default!) speculatively calls
   the LLM before the user's turn is confirmed and throws away wrong guesses —
   roughly 2x token usage for ~0.2s latency. Disabled.
2. **Never depend on one free tier**: `build_llm()` now assembles a
   `FallbackAdapter` chain from whatever keys exist in `.env`:
   **Groq → Cerebras (gpt-oss-120b) → Gemini (3.5-flash) → OpenRouter**.
   A provider that errors or rate-limits is skipped mid-call; the interview
   continues on the next one. Combined free quota: ~1.6M tokens/day.

Verified each provider with live API calls before wiring: notable findings —
Cerebras free tier now hosts `gpt-oss-120b` (fast + solid tool calling),
`gemini-2.5-flash` is retired for new accounts (had to move to `3.5-flash`),
and OpenRouter's `:free` models are too throttled for primary use (last in chain).

Also migrated the deprecated `turn_detector` plugin to
`livekit.agents.inference.TurnDetector` (`v1-mini` — fully local, still free).

Post-merge tuning after another live run: the FallbackAdapter's defaults are
hostile to free tiers — `attempt_timeout=5.0` becomes the request deadline in
the google plugin (Gemini requires ≥10s, so Gemini 400'd on every call), and
recovery probes every 0.5s are real requests that drained OpenRouter's
8-req/min free quota single-handedly. Now: 10s timeout, 30s probe interval,
and `reasoning_effort="low"` for Cerebras' gpt-oss (thinking tokens are
wasted time in a voice call).

## 2026-07-12 — Phase 2: resume pipeline (`feat/resume-pipeline`)

The agent now interviews real candidates instead of the hardcoded sample:

- `resume.py` — `pypdf` text extraction (with a guard for scanned/image PDFs)
  → LLM structuring into a fixed JSON schema (name, experience, companies,
  skills, projects…) → rendered into the interviewer's system prompt.
- Parsing reuses the same multi-provider fallback idea as the voice chain
  (Groq → Cerebras → Gemini via their OpenAI-compatible endpoints) since it
  happens offline where latency doesn't matter.
- `parse_resume.py <pdf> --role "..."` CLI saves to `data/candidates/<name>.json`;
  the agent auto-loads the most recently parsed candidate at call start
  (override with `CANDIDATE_PROFILE=<path>`).
- Extraction prompt rules: never invent facts absent from the resume, null for
  missing data — the interviewer must not "know" things the candidate never wrote.

Tested end-to-end with a generated dummy resume: PDF → 884 chars extracted →
structured by Groq → agent system prompt contains the right candidate.
