"""
Temporal workflow for the weather briefing journey.

DETERMINISM RULE: this file contains no I/O, no network calls, no
datetime.now(), no randomness.  Any violation causes a non-determinism error
on replay — the error is instructive and shows exactly where the boundary is.
All side effects are delegated to activities (activities.py).

Signal:  choose_location(index)  — sent by the client after geocoding returns
                                    multiple candidates
Query:   get_candidates()        — read the pending candidates from a waiting
                                    workflow without interrupting it
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

# workflow.unsafe.imports_passed_through() lets the sandbox import modules
# that themselves import non-deterministic libraries (like requests), since
# we only need the dataclass types here, not any I/O.
with workflow.unsafe.imports_passed_through():
    from activities import (
        LocationDateInput,
        SunriseSunsetResult,
        WeatherResult,
        geocode_location,
        get_sunrise_sunset,
        get_weather,
    )

TASK_QUEUE = "briefing-task-queue"

_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
)


@workflow.defn
class BriefingWorkflow:
    def __init__(self) -> None:
        self._candidates: list[dict] = []
        self._chosen_index: int | None = None

    # ── Signal ───────────────────────────────────────────────────────────────

    @workflow.signal
    def choose_location(self, index: int) -> None:
        """Signal sent by the client carrying the user's chosen candidate index."""
        self._chosen_index = index

    # ── Query ────────────────────────────────────────────────────────────────

    @workflow.query
    def get_candidates(self) -> list[dict]:
        """Read the geocoded candidates from the waiting workflow (non-mutating)."""
        return self._candidates

    # ── Workflow run ─────────────────────────────────────────────────────────

    @workflow.run
    async def run(self, location: str, date: str) -> str:
        # ── Activity 1: geocode ──────────────────────────────────────────────
        # This is the only activity that runs before the possible human pause.
        # After a worker kill/restart, Temporal replays from event history and
        # does NOT re-execute this call — the result is read from history.
        candidates: list[dict] = await workflow.execute_activity(
            geocode_location,
            location,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_RETRY,
        )

        if not candidates:
            raise ApplicationError(f"No locations found for {location!r}")

        # Store candidates in workflow state so get_candidates() query can read them.
        self._candidates = candidates

        # ── Human pause (if needed) ──────────────────────────────────────────
        # wait_condition suspends the coroutine until the predicate is true.
        # The workflow sits in "Running" state in the UI, consuming no CPU,
        # until the choose_location signal arrives.
        if len(candidates) > 1:
            await workflow.wait_condition(lambda: self._chosen_index is not None)
            chosen = candidates[self._chosen_index]  # type: ignore[index]
        else:
            chosen = candidates[0]

        loc_date = LocationDateInput(lat=chosen["lat"], lon=chosen["lon"], date=date)
        location_name = chosen["display_name"]

        # ── Activities 2 & 3: concurrent ────────────────────────────────────
        # get_sunrise_sunset and get_weather are independent once lat/lon is
        # known.  asyncio.gather schedules both; the worker runs them in the
        # ThreadPoolExecutor simultaneously (visible as two parallel activity
        # entries in the Web UI history).
        sunrise_result, weather_result = await asyncio.gather(
            workflow.execute_activity(
                get_sunrise_sunset,
                loc_date,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            ),
            workflow.execute_activity(
                get_weather,
                loc_date,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            ),
        )

        # Cast from Any (asyncio.gather loses type info)
        sr: SunriseSunsetResult = sunrise_result
        wr: WeatherResult = weather_result

        return (
            f"Briefing for {location_name} on {date}\n"
            f"  Sunrise : {sr.sunrise}\n"
            f"  Sunset  : {sr.sunset}\n"
            f"  Max temp: {wr.max_temp} °C\n"
            f"  Min temp: {wr.min_temp} °C\n"
            f"  Precip  : {wr.precip} mm"
        )


# ApplicationError must be imported inside the workflow after imports_passed_through
# — define it here as a thin alias to avoid a sandbox-import issue.
from temporalio.exceptions import ApplicationError  # noqa: E402
