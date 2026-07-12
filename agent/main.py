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
from livekit.plugins import deepgram, groq, silero

from prompts import GREETING_INSTRUCTION, SYSTEM_PROMPT

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPT_DIR = PROJECT_ROOT / "data" / "transcripts"

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
        from livekit.plugins import openai as openai_plugin

        chain.append(
            openai_plugin.LLM.with_cerebras(
                model=os.getenv("CEREBRAS_MODEL", "gpt-oss-120b"),
                api_key=os.environ["CEREBRAS_API_KEY"],
            )
        )
    if os.getenv("GOOGLE_API_KEY"):
        from livekit.plugins import google

        chain.append(google.LLM(model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash")))
    if os.getenv("OPENROUTER_API_KEY"):
        from livekit.plugins import openai as openai_plugin

        chain.append(
            openai_plugin.LLM.with_openrouter(
                model=os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
                api_key=os.environ["OPENROUTER_API_KEY"],
            )
        )
    if len(chain) == 1:
        return chain[0]
    logger.info("LLM fallback chain: %d providers", len(chain))
    return agents_llm.FallbackAdapter(chain)

load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger("echohire")


class Interviewer(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)

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

    async def save_transcript() -> None:
        TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = TRANSCRIPT_DIR / f"{ctx.room.name}_{stamp}.json"
        path.write_text(
            json.dumps(session.history.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("transcript saved to %s", path)

    ctx.add_shutdown_callback(save_transcript)

    async def enforce_max_duration() -> None:
        await asyncio.sleep(MAX_CALL_SECONDS)
        logger.warning("max call duration reached, force-ending call")
        session.shutdown(drain=False)
        ctx.shutdown(reason="max call duration reached")

    watchdog = asyncio.create_task(enforce_max_duration())

    async def cancel_watchdog() -> None:
        watchdog.cancel()

    ctx.add_shutdown_callback(cancel_watchdog)

    await session.start(agent=Interviewer(), room=ctx.room)
    await session.generate_reply(instructions=GREETING_INSTRUCTION)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
