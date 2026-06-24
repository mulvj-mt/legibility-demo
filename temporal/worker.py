"""
Temporal worker process for the briefing journey.

Run this in a terminal and leave it running.  For the kill/resume demo (Phase 4):
  1. Start this worker.
  2. Start a workflow with a multi-result location (e.g. "Richmond").
  3. Observe the geocode activity complete and the workflow pause.
  4. Kill this process (Ctrl-C).
  5. Restart it.  The workflow resumes from history — geocode NOT re-run.
  6. Send the signal from a separate terminal.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from temporalio.client import Client
from temporalio.worker import Worker

from activities import geocode_location, get_sunrise_sunset, get_weather
from workflow import BriefingWorkflow, TASK_QUEUE


async def main() -> None:
    client = await Client.connect("localhost:7233")

    # Sync activities require an executor so they run in threads rather than
    # blocking the asyncio event loop.  This also gives true parallelism when
    # the workflow runs get_sunrise_sunset and get_weather concurrently.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[BriefingWorkflow],
        activities=[geocode_location, get_sunrise_sunset, get_weather],
        activity_executor=executor,
    )

    print(f"Worker started.  Task queue: {TASK_QUEUE}")
    print("Web UI: http://localhost:8233")
    print("Press Ctrl-C to stop (kill/restart to demo durable resume).\n")

    async with worker:
        await asyncio.Future()  # run until cancelled


if __name__ == "__main__":
    asyncio.run(main())
