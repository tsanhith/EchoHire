"""EchoHire interview agent — LiveKit worker entrypoint.

Run modes:
    python agent/main.py download-files   # one-time: fetch local VAD/turn models
    python agent/main.py console          # talk to the agent in your terminal (mic)
    python agent/main.py dev              # connect to LiveKit Cloud (hot reload)
    python agent/main.py start            # production mode

Pipeline: Deepgram STT -> Groq Llama 3.3 70B -> Deepgram Aura TTS,
with Silero VAD + a local turn-detection model for natural interruptions.
"""

import logging

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import deepgram, groq, silero
from livekit.plugins.turn_detector.english import EnglishModel

from prompts import GREETING_INSTRUCTION, SYSTEM_PROMPT

load_dotenv()

logger = logging.getLogger("echohire")


class Interviewer(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)


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

    await session.start(agent=Interviewer(), room=ctx.room)
    await session.generate_reply(instructions=GREETING_INSTRUCTION)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
