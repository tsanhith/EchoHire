"""EchoHire interview agent — LiveKit worker entrypoint.

Run modes:
    python agent/main.py download-files   # one-time: fetch local VAD/turn models
    python agent/main.py console          # talk to the agent in your terminal (mic)
    python agent/main.py dev              # connect to LiveKit Cloud (hot reload)
    python agent/main.py start            # production mode

Pipeline: Deepgram STT -> Groq Llama 3.3 70B -> Deepgram Aura TTS,
with Silero VAD + a local turn-detection model for natural interruptions.

Transcripts are saved to data/transcripts/ (gitignored) when the call ends.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
    get_job_context,
    llm as agents_llm,
)
from livekit.agents.inference import TurnDetector

# Plugins must be imported at module level: LiveKit registers them on the
# main thread, and build_llm() runs on a job thread.
from livekit.plugins import deepgram, google, groq, silero
from livekit.plugins import openai as openai_plugin

from evaluation import evaluate_transcript
from prompts import GREETING_INSTRUCTION, build_system_prompt
from resume import CANDIDATE_DIR, load_latest_profile, profile_to_prompt_block

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPT_DIR = PROJECT_ROOT / "data" / "transcripts"
EVALUATION_DIR = PROJECT_ROOT / "data" / "evaluations"

# Hard ceiling so no call (and no free-tier quota) can run away.
MAX_CALL_SECONDS = 15 * 60


def build_llm() -> agents_llm.LLM:
    """Groq is the primary LLM (fastest inference = best voice latency).

    Free tiers have daily token caps (Groq: 100k/day on the 70B model), so
    every provider with a key in .env joins an automatic fallback chain:
    Groq -> Cerebras -> Gemini -> OpenRouter. A rate-limited provider is
    skipped mid-call without dropping the interview.
    """
    chain: list[agents_llm.LLM] = [
        groq.LLM(model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))
    ]
    if os.getenv("CEREBRAS_API_KEY"):
        chain.append(
            openai_plugin.LLM.with_cerebras(
                model=os.getenv("CEREBRAS_MODEL", "gpt-oss-120b"),
                api_key=os.environ["CEREBRAS_API_KEY"],
                # gpt-oss is a reasoning model; keep thinking minimal for voice
                reasoning_effort="low",
            )
        )
    if os.getenv("GOOGLE_API_KEY"):
        chain.append(google.LLM(model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash")))
    if os.getenv("OPENROUTER_API_KEY"):
        chain.append(
            openai_plugin.LLM.with_openrouter(
                model=os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
                api_key=os.environ["OPENROUTER_API_KEY"],
            )
        )
    if len(chain) == 1:
        return chain[0]
    logger.info("LLM fallback chain: %d providers", len(chain))
    return agents_llm.FallbackAdapter(
        chain,
        # Gemini rejects request deadlines under 10s (the google plugin turns
        # attempt_timeout into the deadline; the 5s default 400s every call).
        attempt_timeout=10.0,
        # Recovery probes are real requests: at the 0.5s default they drain
        # per-minute quotas (OpenRouter free = 8 req/min) all by themselves.
        retry_interval=30.0,
    )

load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger("echohire")


def load_candidate_block(room_name: str) -> str | None:
    """Candidate profile for this interview.

    Web-portal rooms are named "interview-<candidate-slug>-<timestamp>", which
    maps to data/candidates/<candidate-slug>.json. Any other room (console
    testing, playground) gets the most recently parsed candidate; if none
    exists, the sample candidate.
    """
    profile = None
    match = re.fullmatch(r"interview-(.+)-\d+", room_name)
    if match:
        path = CANDIDATE_DIR / f"{match.group(1)}.json"
        if path.exists():
            profile = json.loads(path.read_text(encoding="utf-8"))
        else:
            logger.warning("no profile for room %s at %s", room_name, path)
    if profile is None:
        profile = load_latest_profile()
    if profile is None:
        logger.warning("no parsed candidate found, interviewing the sample candidate")
        return None
    logger.info("interviewing candidate: %s", profile.get("name"))
    return profile_to_prompt_block(profile, role_applied=profile.get("role_applied"))


class Interviewer(Agent):
    def __init__(self, candidate_block: str | None) -> None:
        super().__init__(instructions=build_system_prompt(candidate_block))

    @function_tool
    async def end_interview(self, context: RunContext) -> None:
        """End the call. Use ONLY in the closing stage, after you have thanked
        the candidate and said goodbye."""
        speech = context.session.current_speech
        if speech:
            await speech.wait_for_playout()
        logger.info("interview ended by agent")
        context.session.shutdown()
        # session.shutdown() only closes the session; the job (and console
        # process) keeps running without this.
        get_job_context().shutdown(reason="interview completed")


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    logger.info("candidate joined room %s", ctx.room.name)

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(model="nova-3", language="en"),
        llm=build_llm(),
        tts=deepgram.TTS(),
        turn_handling={
            # v1-mini runs fully in-process (no cloud inference) — free forever
            "turn_detection": TurnDetector(version="v1-mini"),
            # Preemptive generation speculatively calls the LLM before the
            # user's turn is confirmed and discards wrong guesses — roughly
            # doubles token burn for ~0.2s latency. Not worth it on free tiers.
            "preemptive_generation": {"enabled": False},
        },
    )

    async def save_transcript_and_evaluate() -> None:
        history = session.history.to_dict()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{ctx.room.name}_{stamp}.json"

        TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        transcript_path = TRANSCRIPT_DIR / base_name
        transcript_path.write_text(
            json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("transcript saved to %s", transcript_path)

        try:
            # blocking HTTP client — keep it off the event loop
            report = await asyncio.to_thread(evaluate_transcript, history)
            EVALUATION_DIR.mkdir(parents=True, exist_ok=True)
            evaluation_path = EVALUATION_DIR / base_name
            evaluation_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.info(
                "evaluation saved to %s (recommendation: %s)",
                evaluation_path,
                report.get("recommendation"),
            )
        except Exception:
            logger.exception("evaluation failed — transcript is still saved")

    ctx.add_shutdown_callback(save_transcript_and_evaluate)

    async def enforce_max_duration() -> None:
        await asyncio.sleep(MAX_CALL_SECONDS)
        logger.warning("max call duration reached, force-ending call")
        session.shutdown(drain=False)
        ctx.shutdown(reason="max call duration reached")

    watchdog = asyncio.create_task(enforce_max_duration())

    async def cancel_watchdog() -> None:
        watchdog.cancel()

    ctx.add_shutdown_callback(cancel_watchdog)

    await session.start(
        agent=Interviewer(load_candidate_block(ctx.room.name)), room=ctx.room
    )
    await session.generate_reply(instructions=GREETING_INSTRUCTION)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
