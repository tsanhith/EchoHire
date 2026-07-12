# EchoHire — Project Plan

*Written 2026-07-12, at project start. This is a living document.*

## What we're building

An AI HR agent that conducts **first-round screening interviews over a voice call**. Candidates apply on a web portal (resume + details), get an interview call, and the AI asks about their experience, last company, CTC expectations, notice period, and role-specific questions. The recruiter gets a transcript + structured evaluation instead of spending an hour per candidate.

## Decisions made (and why)

| Decision | Choice | Reasoning |
|---|---|---|
| Voice infra | **LiveKit Cloud** (free tier) | Industry standard for realtime voice agents; agent code is transport-agnostic |
| Call medium | **Browser call now, SIP phone later** | Real phone calls cost ₹1–3/min in carrier charges — physically cannot be free. Browser WebRTC calls are free. LiveKit lets the *same agent* serve both, so the phone layer is a pluggable upgrade (Twilio/Telnyx trial credits for demos, company-paid trunk for production). |
| STT | **Deepgram** (free $200 credits) | Best latency/accuracy tradeoff; same as reference implementations |
| LLM | **Groq — Llama 3.3 70B** (free tier) | Fastest inference available; latency is the #1 UX factor in voice agents |
| TTS | **Deepgram Aura / Edge-TTS** | Covered by Deepgram credits; Edge-TTS as a zero-cost fallback |
| Interview language | **English only** (v1) | Most reliable STT/TTS quality; standard for tech hiring |
| Backend | **FastAPI (Python)** | Same language as the LiveKit agent SDK; one venv, one repo |
| Frontend | **Simple web app + LiveKit client SDK** | Candidate upload page, interview room, recruiter results view |

## Why this is free (vs the ₹2–3/min alternative)

The reference project ([LIvekitAIVoice](https://github.com/toprmrproducer/LIvekitAIVoice)) dials real phone numbers through a Vobiz SIP trunk — that per-minute carrier charge is the entire cost. EchoHire replaces the phone leg with a browser link:

```
Reference:  Candidate's phone ←(PSTN, ₹2–3/min)← SIP trunk ← LiveKit ← Agent
EchoHire:   Candidate's browser ←(WebRTC, free)← LiveKit Cloud ← Agent
```

Everything else (STT, LLM, TTS) runs on free tiers/credits.

## Architecture

```
┌─────────────┐     upload resume + details      ┌──────────────┐
│  Candidate   │ ───────────────────────────────▶ │   Backend     │
│  (browser)   │ ◀─── interview link ──────────── │   (FastAPI)   │
└──────┬──────┘                                   └──────┬───────┘
       │ joins LiveKit room (WebRTC)                     │ parses resume,
       ▼                                                 │ builds interview context
┌─────────────┐                                          ▼
│ LiveKit Cloud│ ◀── agent joins room ──── ┌────────────────────┐
└─────────────┘                            │  Interview Agent    │
                                           │  STT → LLM → TTS    │
                                           └─────────┬──────────┘
                                                     │ transcript + evaluation
                                                     ▼
                                           ┌────────────────────┐
                                           │ Recruiter dashboard │
                                           └────────────────────┘
```

## Interview flow

1. **Apply** — candidate uploads resume (PDF) + form (name, phone, email, role, current CTC optional).
2. **Parse** — backend extracts text from the PDF, LLM structures it (skills, companies, years of experience).
3. **Interview** — candidate opens their link, agent greets them by name and runs the interview:
   - Warm-up / intro
   - Walk through recent experience (grounded in *their actual resume* — the agent references specific companies/projects from it)
   - Last company, reason for leaving, notice period
   - Current & expected CTC
   - Role-specific screening questions (from the company's question bank)
   - Candidate's questions, close
4. **Evaluate** — post-call, LLM scores the transcript against a rubric → summary, red flags, recommendation (proceed / reject / borderline).
5. **Review** — recruiter sees transcript, audio, scores in the dashboard.

## Build phases

- **Phase 0 — Setup** ✅ repo, venv, branching, docs
- **Phase 1 — Voice agent core**: LiveKit agent with STT/LLM/TTS pipeline, hardcoded resume context, test call in browser playground
- **Phase 2 — Resume pipeline**: PDF upload endpoint, parsing, structured candidate profile fed into agent's system prompt
- **Phase 3 — Portal**: candidate upload page + interview room page (LiveKit web SDK)
- **Phase 4 — Evaluation & recruiter view**: post-call scoring, transcript storage, results page
- **Phase 5 (optional, paid)** — SIP trunk for real phone calls

## Free-tier budget

| Service | Free allowance | Rough capacity |
|---|---|---|
| LiveKit Cloud | free plan minutes/month | dozens of interviews/month |
| Deepgram | $200 signup credits | ~700+ hours of STT |
| Groq | free tier rate limits | fine for 1 concurrent interview |
| Hosting (dev) | run locally | ₹0 |

> Rate-limit note: free tiers comfortably handle **one interview at a time**. Concurrent interviews at scale would need paid tiers — out of scope for this internal project.

## Open questions (pending from company/senior)

See [QUESTIONS-FOR-COMPANY.md](QUESTIONS-FOR-COMPANY.md).

## Git workflow

- `main` — stable, working states only
- `dev` — integration branch
- `feat/*`, `fix/*`, `docs/*` — work branches, merged into `dev` via PR
- Conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`)
