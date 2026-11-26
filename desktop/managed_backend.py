"""Run the existing ASGI app under the desktop's ownership marker."""

import asyncio
import os
import sys
from pathlib import Path

import uvicorn


async def main(marker: Path, port: int):
    fallback = os.environ.get("AURA_DESKTOP_MODELS_FALLBACK")
    if fallback:
        from aura_edge.config import Settings

        if "models_dir" not in Settings().model_fields_set:
            os.environ["AURA_EDGE_MODELS_DIR"] = fallback
    server = uvicorn.Server(uvicorn.Config("aura_edge.main:app", host="127.0.0.1", port=port))

    async def wait_for_shutdown():
        while not marker.exists():
            await asyncio.sleep(0.2)
        server.should_exit = True

    async with asyncio.TaskGroup() as tasks:
        shutdown = tasks.create_task(wait_for_shutdown())
        try:
            await server.serve()
        finally:
            shutdown.cancel()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), int(sys.argv[2])))
