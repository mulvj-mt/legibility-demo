# Temporal Spike

An evaluation of [Temporal](https://temporal.io) as a durable-execution engine for the weather/sunset briefing journey. The same journey used in the Arazzo spike (geocode → optional disambiguation → sunrise/sunset + weather → briefing) is rebuilt here as a Temporal workflow to answer the question: **does Temporal's durable execution model handle the mandatory mid-flow pause/resume requirement — and what does it cost to use it?**

The spike deliberately exercises the one thing a naive implementation would miss: killing the worker mid-flow and observing that the workflow resumes from its exact position without re-running already-completed work.

Full findings are in [FINDINGS.md](FINDINGS.md).

---

## Prerequisites

- Python ≥ 3.14 (managed by `uv` at the project root)
- `temporalio` ≥ 1.29.0 — installed automatically via `uv` from `pyproject.toml`
- **Temporal CLI** — install once:
  ```bash
  curl -sSf https://temporal.download/cli.sh | sh
  # Then add to PATH:
  export PATH="$PATH:$HOME/.temporalio/bin"
  ```
- No API keys required (Nominatim, Sunrise-Sunset, Open-Meteo are all free and keyless)

---

## Directory structure

```
temporal/
├── activities.py     # Three @activity.defn sync functions + input/output dataclasses
├── workflow.py       # BriefingWorkflow: @workflow.run, @workflow.signal, @workflow.query
├── worker.py         # Temporal worker process — run and leave running
├── start.py          # Start a workflow and wait for its result
├── disambiguate.py   # Query candidates and send the choose_location signal
├── FINDINGS.md       # Full spike write-up and reflection
└── README.md         # This file
```

---

## How it works

### The workflow/activity split

This is the conceptual core of Temporal. Workflow code must be **deterministic** — it is replayed from event history on recovery, so it cannot contain I/O, network calls, `datetime.now()`, or randomness. All side effects live in **activities**.

**`activities.py`** — three sync functions decorated with `@activity.defn`:

| Activity | Input | API called | Output |
|----------|-------|-----------|--------|
| `geocode_location` | `location: str` | Nominatim `/search` | `list[dict]` of candidates |
| `get_sunrise_sunset` | `LocationDateInput` | Sunrise-Sunset `/json` | `SunriseSunsetResult` |
| `get_weather` | `LocationDateInput` | Open-Meteo `/v1/forecast` | `WeatherResult` |

Activities use `requests.get` directly. The worker runs them in a `ThreadPoolExecutor(max_workers=10)`, keeping the asyncio event loop unblocked and enabling true parallel execution when two activities are gathered concurrently.

**`workflow.py`** — `BriefingWorkflow` class containing no I/O:

1. `await workflow.execute_activity(geocode_location, ...)` — schedules the geocode activity, awaits its result
2. Stores candidates in `self._candidates` (workflow instance state)
3. If multiple candidates: `await workflow.wait_condition(lambda: self._chosen_index is not None)` — suspends the coroutine until the `choose_location` signal arrives
4. `asyncio.gather(get_sunrise_sunset(...), get_weather(...))` — schedules both activities concurrently once lat/lon is known
5. Returns the formatted briefing string

**Signal — `choose_location(index: int)`**: sent by `disambiguate.py` to resume a waiting workflow. The `@workflow.signal` handler sets `self._chosen_index`, unblocking the `wait_condition`.

**Query — `get_candidates() -> list[dict]`**: read-only access to `self._candidates` from any external client while the workflow is waiting. Decorated with `@workflow.query`. No workflow task is created; it reads in-memory state directly.

### Durable execution

When the worker is killed while a workflow is suspended at `wait_condition`, Temporal preserves the entire workflow state in its event history (persisted in the Temporal service, not the worker process). On restart, the worker replays the workflow code from history: when it reaches `workflow.execute_activity(geocode_location, ...)`, Temporal detects that activity already completed and returns the cached result — **no new API call is made**. Execution continues from where it left off.

---

## Running the spike

All commands run from the **project root**. You need three terminals.

### Terminal 1 — Start the Temporal dev server

```bash
temporal server start-dev
```

This starts the Temporal service on `localhost:7233` and the Web UI on `http://localhost:8233`. Leave it running throughout.

### Terminal 2 — Start the worker

```bash
uv run python temporal/worker.py
```

The worker connects to `localhost:7233`, registers `BriefingWorkflow` and the three activities on task queue `briefing-task-queue`, and polls for work. Leave it running.

### Terminal 3 — Start a workflow

**Unambiguous location (single geocode result, no signal needed):**

```bash
uv run python temporal/start.py "Moel Famau" "2026-06-21"
```

Expected output:

```
Workflow started: briefing-moel-famau-2026-06-21
  Location: 'Moel Famau', Date: 2026-06-21
  Web UI: http://localhost:8233/namespaces/default/workflows/briefing-moel-famau-2026-06-21

Waiting for result...
============================================================
Briefing for Moel Famau, St Asaph, Denbighshire, Cymru / Wales, United Kingdom on 2026-06-21
  Sunrise : 2026-06-21T03:43:33+00:00
  Sunset  : 2026-06-21T20:46:08+00:00
  Max temp: 20.4 °C
  Min temp: 6.7 °C
  Precip  : 0.0 mm
```

**Ambiguous location (multiple geocode results, signal required):**

```bash
uv run python temporal/start.py "Richmond" "2026-06-21"
```

This prints the workflow ID and then blocks waiting for a signal. The workflow is now suspended at `wait_condition`.

### Terminal 3 (second tab) — Send the disambiguation signal

While `start.py` is blocked, open another tab and run:

```bash
uv run python temporal/disambiguate.py briefing-richmond-2026-06-21 3
```

This first queries the workflow for its candidates list, then sends `choose_location(3)`:

```
Candidates (6 total):
  [0] Richmond, Lichfield Gardens, North Sheen, London Borough of Richmond upon Thames ...
  [1] London Borough of Richmond upon Thames, Greater London ...
  [2] Richmond, Greater London, England, TW9 1DY ...
  [3] Richmond, North Yorkshire, York and North Yorkshire ...  <-- chosen
  [4] Richmondshire, North Yorkshire ...
  [5] Richmond, Sheffield, South Yorkshire ...

Signal sent: choose_location(3)
```

`start.py` then unblocks and prints the result.

---

## The kill/resume demo (Phase 4 — the point of the spike)

This is the sequence that demonstrates durable execution. Use "Richmond" because it requires disambiguation, giving a natural pause point.

**Step 1** — Start a workflow with a unique date so the workflow ID doesn't collide:

```bash
# Terminal 3
uv run python temporal/start.py "Richmond" "2026-07-01"
```

Wait until `start.py` prints the workflow ID and blocks. This means geocode has completed and the workflow is waiting for a signal. Confirm with:

```bash
temporal workflow describe --workflow-id briefing-richmond-2026-07-01
# Look for: HistoryLength 10, Pending Activities: 0
```

**Step 2** — Kill the worker (Terminal 2: `Ctrl-C`).

**Step 3** — Verify the workflow is unaffected:

```bash
temporal workflow list
# Shows: Running  briefing-richmond-2026-07-01
```

The workflow is still "Running". Its state lives in the Temporal service, not the worker process.

**Step 4** — Restart the worker (Terminal 2):

```bash
uv run python temporal/worker.py
```

On reconnect, the worker replays the workflow from history. The geocode activity result is read from history — no new HTTP call to Nominatim.

**Step 5** — Send the signal (Terminal 3, new tab):

```bash
uv run python temporal/disambiguate.py briefing-richmond-2026-07-01 3
```

**Step 6** — Observe the completion in Terminal 3 and verify the history:

```bash
temporal workflow show --workflow-id briefing-richmond-2026-07-01
```

You will see 27 events. The geocode activity appears exactly once (events 5–7), regardless of how many times the worker was restarted. The two downstream activities (events 15–16) are scheduled at the same timestamp, confirming concurrent execution via `asyncio.gather`.

---

## Viewing the Web UI

Open `http://localhost:8233` while the dev server is running. Select a workflow to see:

- Its current status and input/output values
- The full event history (each activity execution, signal received, workflow task completions)
- Two `ActivityTaskScheduled` events at the same timestamp when `asyncio.gather` fires both downstream activities

The Web UI is the clearest way to observe the concurrent activity scheduling and to watch the workflow transition from "Running" (waiting for signal) to "Completed".

---

## Key findings

See [FINDINGS.md](FINDINGS.md) for the full write-up. The headline points:

- **Workflow/activity split**: The boundary is strictly enforced by the sandbox. Placing a `requests.get` call directly in workflow code raises a non-determinism error on the second workflow task — this is instructive rather than surprising once understood.
- **Signals and queries**: Signals resume a suspended workflow durably (stored in event history, delivered even if the signal arrives while the worker is down). Queries read in-memory workflow state without creating a new workflow task.
- **Kill/resume**: Verified — geocode activity appears exactly once in a 27-event history across a worker kill/restart cycle. Completed activity results are read from history on replay, not re-executed.
- **vs the FSM**: Temporal provides durable pause/resume as a first-class feature with no application-level serialisation code. The FSM provides the protocol but not the durability — a process restart loses an in-flight journey. The trade-off is the determinism constraint on workflow code, which the FSM does not impose.
