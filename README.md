# EchoHire 🎙️

**An AI-powered HR voice agent that conducts first-round screening interviews — built entirely on free tiers.**

Candidates upload their resume and details on a web page, receive an interview link, and talk to an AI interviewer in their browser. The agent asks about their experience, current/last company, CTC expectations, notice period, and role-specific questions — then produces a transcript and a structured evaluation for the recruiter. No human HR time spent on round one.

> 🚧 **Status: in active development.** This README grows as the project does — see [`docs/`](docs/) for the build log and architecture decisions.

## Why

First-round HR screening is repetitive: the same 10–15 questions asked to every candidate. Automating it frees the HR team to focus on candidates who actually clear the bar.

## The zero-cost constraint

Similar projects (e.g. LiveKit + SIP trunk setups) cost ~₹2–3/minute because they dial real phone numbers over PSTN. EchoHire avoids telephony entirely: interviews happen **in the browser over WebRTC**, so the only costs are API free tiers.

## Stack (planned)

| Layer | Choice | Why free |
|---|---|---|
| Voice infrastructure | LiveKit Cloud | Free tier (generous monthly minutes) |
| Speech-to-text | Deepgram / Groq Whisper | $200 free credits / free tier |
| LLM (the interviewer brain) | Groq (Llama 3.3 70B) or Gemini Flash | Free tiers |
| Text-to-speech | Edge-TTS or Deepgram Aura | Free / included in credits |
| Backend + resume parsing | Python (FastAPI) | Open source |
| Frontend | Simple web app with LiveKit client SDK | Open source |

## Project structure

```
EchoHire/
├── agent/        # LiveKit voice agent (the AI interviewer)
├── backend/      # FastAPI: resume upload, parsing, interview sessions, results
├── frontend/     # Candidate-facing web UI
├── docs/         # Build log, architecture, decisions
└── README.md
```

## Development

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Branching: `main` is stable; work happens on `feat/*` / `fix/*` / `docs/*` branches and merges via PR.

## License

MIT
