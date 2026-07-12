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
)
from livekit.plugins import deepgram, groq, silero
from livekit.plugins.turn_detector.english import EnglishModel

from prompts import GREETING_INSTRUCTION, SYSTEM_PROMPT

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPT_DIR = PROJECT_ROOT / "data" / "transcripts"

# Hard ceiling so no call (and no free-tier quota) can run away.
MAX_CALL_SECONDS = 15 * 60

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
        llm=groq.LLM(model="llama-3.3-70b-versatile"),
        tts=deepgram.TTS(),
        turn_detection=EnglishModel(),
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
