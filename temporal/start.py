"""
Start a briefing workflow and wait for its result.

Usage:
  uv run python temporal/start.py "Richmond" "2026-06-21"
  uv run python temporal/start.py "Moel Famau" "2026-06-21"

If the location returns multiple candidates the workflow will pause here
(waiting for a choose_location signal).  Open a second terminal and run:
  uv run python temporal/signal.py <workflow-id> <index>
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from temporalio.client import Client

from workflow import BriefingWorkflow, TASK_QUEUE


async def main() -> None:
    location = sys.argv[1] if len(sys.argv) > 1 else "Richmond"
    date = sys.argv[2] if len(sys.argv) > 2 else "2026-06-21"

    client = await Client.connect("localhost:7233")

    workflow_id = f"briefing-{location.lower().replace(' ', '-')}-{date}"

    handle = await client.start_workflow(
        BriefingWorkflow.run,
        args=[location, date],
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )

    print(f"Workflow started: {handle.id}")
    print(f"  Location: {location!r}, Date: {date}")
    print(f"  Web UI:   http://localhost:8233/namespaces/default/workflows/{handle.id}")
    print()
    print("Waiting for result (if disambiguation needed, run signal.py in another terminal)...")
    print(f"  uv run python temporal/disambiguate.py {handle.id} <index>")
    print()

    result = await handle.result()
    print("=" * 60)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
