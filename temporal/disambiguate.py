"""
Send the choose_location signal to a paused briefing workflow.

Usage:
  uv run python temporal/disambiguate.py <workflow-id> <candidate-index>

First queries the workflow for its candidates list so you can confirm the
choice before sending the signal.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from temporalio.client import Client

from workflow import BriefingWorkflow


async def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: signal.py <workflow-id> <candidate-index>")
        sys.exit(1)

    workflow_id = sys.argv[1]
    index = int(sys.argv[2])

    client = await Client.connect("localhost:7233")
    handle = client.get_workflow_handle(workflow_id)

    # Query the workflow for its candidates (demonstrates the query mechanism).
    try:
        candidates: list[dict] = await handle.query(BriefingWorkflow.get_candidates)
        if candidates:
            print(f"Candidates ({len(candidates)} total):")
            for i, c in enumerate(candidates):
                marker = "  <-- chosen" if i == index else ""
                print(f"  [{i}] {c['display_name']}{marker}")
        else:
            print("No candidates available yet (geocode may not have completed).")
    except Exception as e:
        print(f"Query failed: {e}")

    # Send the signal — resumes the waiting workflow.
    await handle.signal(BriefingWorkflow.choose_location, index)
    print(f"\nSignal sent: choose_location({index})")
    print("The workflow will now continue to fetch sunrise/sunset and weather.")


if __name__ == "__main__":
    asyncio.run(main())
