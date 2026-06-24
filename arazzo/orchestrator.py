"""
Arazzo-based orchestrator for the weather briefing journey.

Implements Option B (workflow-per-turn split): each conversational turn is a
separate Arazzo workflow invocation. This module owns the pause/resume logic,
persisted between-turn state, and turn-stitching — the machinery Arazzo cannot
provide natively because it has no suspend/resume mechanism.

The two Arazzo workflows are:
  - geocodeLocation  (Turn 1): geocode a place name, return candidates array.
  - generateBriefing (Turn 2): given confirmed lat/lon + date, return briefing data.

Between turns the state is serialised to a plain dict so it survives a process
restart — a hard requirement from the brief.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from arazzo_runner import ArazzoRunner, WorkflowExecutionStatus

ARAZZO_FILE = Path(__file__).parent / "briefing.arazzo.yaml"


@dataclass
class BriefingState:
    """Serialisable snapshot of between-turn state."""
    location: str
    date: str
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "BriefingState":
        return cls(**json.loads(data))


@dataclass
class Turn1Result:
    """What the orchestrator returns after geocoding."""
    needs_disambiguation: bool
    candidates: list[dict[str, Any]]
    state: BriefingState


@dataclass
class BriefingResult:
    """Final structured output from the briefing journey."""
    location_name: str
    date: str
    sunrise: str
    sunset: str
    max_temp: float
    min_temp: float
    precip: float

    def format(self) -> str:
        return (
            f"Briefing for {self.location_name} on {self.date}\n"
            f"  Sunrise : {self.sunrise}\n"
            f"  Sunset  : {self.sunset}\n"
            f"  Max temp: {self.max_temp} °C\n"
            f"  Min temp: {self.min_temp} °C\n"
            f"  Precip  : {self.precip} mm"
        )


def _runner() -> ArazzoRunner:
    return ArazzoRunner.from_arazzo_path(str(ARAZZO_FILE))


def geocode_location(location: str, date: str) -> Turn1Result:
    """
    Turn 1: geocode location and return candidates.

    The outer caller inspects needs_disambiguation:
    - False → single result; call generate_briefing(state, 0) immediately.
    - True  → surface candidates to user, wait for choice, then call
              generate_briefing(state, chosen_index).

    Arazzo cannot make this branching decision itself, and cannot pause
    execution to wait for the user — that is what this function's caller does.
    """
    runner = _runner()
    result = runner.execute_workflow("geocodeLocation", inputs={"location": location})

    if result.status != WorkflowExecutionStatus.WORKFLOW_COMPLETE:
        raise RuntimeError(f"Geocoding failed: {result.error}")

    candidates = result.outputs.get("candidates", [])
    state = BriefingState(location=location, date=date, candidates=candidates)

    return Turn1Result(
        needs_disambiguation=len(candidates) != 1,
        candidates=candidates,
        state=state,
    )


def generate_briefing(state: BriefingState, chosen_index: int) -> BriefingResult:
    """
    Turn 2: generate briefing for the chosen candidate.

    Args:
        state: Persisted state from Turn 1 (survives process restart via from_json).
        chosen_index: Index into state.candidates selected by the user.
    """
    if not state.candidates:
        raise RuntimeError("No candidates in state; was Turn 1 skipped?")
    if not (0 <= chosen_index < len(state.candidates)):
        raise IndexError(f"Index {chosen_index} out of range ({len(state.candidates)} candidates)")

    candidate = state.candidates[chosen_index]
    lat = candidate["lat"]
    lon = candidate["lon"]
    location_name = candidate["display_name"]

    runner = _runner()
    result = runner.execute_workflow(
        "generateBriefing",
        inputs={
            "lat": lat,
            "lon": lon,
            "date": state.date,
            "location_name": location_name,
        },
    )

    if result.status != WorkflowExecutionStatus.WORKFLOW_COMPLETE:
        raise RuntimeError(f"Briefing generation failed: {result.error}")

    outputs = result.outputs
    return BriefingResult(
        location_name=location_name,
        date=state.date,
        sunrise=outputs["sunrise"],
        sunset=outputs["sunset"],
        max_temp=outputs["max_temp"],
        min_temp=outputs["min_temp"],
        precip=outputs["precip"],
    )


# ── CLI test harness ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    location = input("Location: ").strip()
    date = input("Date (YYYY-MM-DD): ").strip()

    print(f"\nTurn 1: geocoding {location!r}...")
    turn1 = geocode_location(location, date)

    # Serialise between-turn state — demonstrates persistence across restarts.
    persisted = turn1.state.to_json()
    print(f"[state persisted: {len(persisted)} bytes]\n")

    if turn1.needs_disambiguation:
        print(f"Multiple locations found ({len(turn1.candidates)}):")
        for i, c in enumerate(turn1.candidates):
            print(f"  [{i}] {c['display_name']}")
        choice = int(input("\nChoose: "))
    else:
        print(f"Single result: {turn1.candidates[0]['display_name']}")
        choice = 0

    # Restore from serialised state — as if the process had restarted.
    restored = BriefingState.from_json(persisted)

    print("\nTurn 2: generating briefing...")
    briefing = generate_briefing(restored, choice)

    print("\n" + "=" * 60)
    print(briefing.format())
