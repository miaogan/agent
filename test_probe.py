import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.llm_adapter import get_endpoint_mode
from app.agent_core import run_agent_stream
from app.schemas import MessageItem, Role


async def probe():
    mode = await get_endpoint_mode()
    print(f"Endpoint mode: {mode}")


asyncio.run(probe())
