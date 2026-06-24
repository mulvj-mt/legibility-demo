# Temporal Spike — Findings

Spike date: 2026-06-24. SDK: `temporalio` 1.29.0. Server: Temporal CLI 1.7.2 (Server 1.31.1).

---

## 1. The workflow/activity split as implemented

**Activities** (`activities.py`) — all three API calls:

| Activity | Input | Output |
|----------|-------|--------|
| `geocode_location` | `location: str` | `list[dict]` (Nominatim candidates) |
| `get_sunrise_sunset` | `LocationDateInput` | `SunriseSunsetResult` |
| `get_weather` | `LocationDateInput` | `WeatherResult` |

Activities are sync functions. They use `requests.get` directly. The worker runs them in a `ThreadPoolExecutor(max_workers=10)`, which both keeps the asyncio event loop unblocked and gives true parallelism when two activities are gathered concurrently.

**Workflow** (`workflow.py`) — contains no I/O:
- Calls `workflow.execute_activity(geocode_location, ...)` — schedules the activity, awaits result
- Sets `self._candidates = candidates` — stores geocode result in workflow state
- If multiple candidates: `await workflow.wait_condition(lambda: self._chosen_index is not None)` — suspends
- On resume: calls `get_sunrise_sunset` and `get_weather` concurrently via `asyncio.gather`
- Returns the formatted briefing string

**Non-determinism error hit during development:** The first draft put the `requests.get` call for geocoding inside the workflow run method to test what would happen. Temporal raised a non-determinism error immediately on the second workflow task execution — the sandbox detected that `requests` was being called from inside workflow code. The fix was to move it to an activity. This is exactly the instructive error the brief flagged: it makes the boundary viscerally clear. Once the requests call is in an activity, the error disappears and the workflow replays cleanly.

The `with workflow.unsafe.imports_passed_through():` block in `workflow.py` is required when importing dataclass types defined in `activities.py`. Without it, the workflow sandbox intercepts the import (because `activities.py` imports `requests` at the top level) and raises an import error. Wrapping the import in `imports_passed_through()` tells the sandbox to let those imports through unchanged.

---

## 2. Signals and queries

**Signal: `choose_location(index: int)`**

Defined with `@workflow.signal` on the workflow class. The signal handler sets `self._chosen_index = index`, which unblocks the `wait_condition` predicate (`lambda: self._chosen_index is not None`).

The workflow stays in "Running" state in the Web UI indefinitely while waiting — it is suspended with no CPU usage, no polling, no timeout (unless one is configured). The signal is durable: if the signal is sent while the worker is down, Temporal stores it in the event history and delivers it when a worker reconnects.

**Query: `get_candidates() -> list[dict]`**

Defined with `@workflow.query`. A non-mutating read of `self._candidates`. The query executes against the current in-memory workflow state — no new workflow task is created. Key property: queries can be issued while the workflow is waiting for a signal, and they return the state at the moment the workflow is suspended (the geocode results, ready to display to the user for disambiguation).

Demo output from `disambiguate.py`:
```
Candidates (6 total):
  [0] Richmond, Lichfield Gardens, North Sheen, ...
  [1] London Borough of Richmond upon Thames, ...
  [2] Richmond, Greater London, England, TW9 1DY ...
  [3] Richmond, North Yorkshire, ...   <-- chosen
  ...
Signal sent: choose_location(3)
```

---

## 3. The kill/resume observation (Phase 4 — the required core)

This is the central finding of the spike.

### What happened

**Workflow ID:** `briefing-richmond-2026-06-25`

| Time (UTC) | Event |
|-----------|-------|
| 12:19:03 | Workflow started (geocode activity scheduled, started, completed) |
| 12:19:03 | Workflow suspended at `wait_condition` (HistoryLength: 10) |
| ~12:19:30 | **Worker killed** (`kill <pid>`) |
| ~12:20:00 | Worker restarted |
| 12:21:33 | Signal `choose_location(3)` sent from `disambiguate.py` |
| 12:21:35 | `WorkflowExecutionCompleted` (HistoryLength: 27) |

### The event history tells the story

```
 1  WorkflowExecutionStarted
 2  WorkflowTaskScheduled
 3  WorkflowTaskStarted
 4  WorkflowTaskCompleted
 5  ActivityTaskScheduled       ← geocode activity scheduled
 6  ActivityTaskStarted
 7  ActivityTaskCompleted        ← geocode result stored in history
 8  WorkflowTaskScheduled
 9  WorkflowTaskStarted
10  WorkflowTaskCompleted        ← workflow set _candidates, now waiting for signal
                                 ↑ WORKER KILLED HERE — events 11+ delayed ~2.5 min ↑
11  WorkflowExecutionSignaled    ← choose_location(3) signal delivered
12  WorkflowTaskScheduled
13  WorkflowTaskStarted
14  WorkflowTaskCompleted        ← wait_condition unblocked
15  ActivityTaskScheduled        ← get_sunrise_sunset scheduled  ┐ concurrent:
16  ActivityTaskScheduled        ← get_weather scheduled         ┘ same timestamp
17  ActivityTaskStarted
18  ActivityTaskCompleted
19  WorkflowTaskScheduled
20  WorkflowTaskStarted
21  WorkflowTaskCompleted
22  ActivityTaskStarted
23  ActivityTaskCompleted
24  WorkflowTaskScheduled
25  WorkflowTaskStarted
26  WorkflowTaskCompleted
27  WorkflowExecutionCompleted
```

### Key observations

**1. The workflow was unaffected by the worker kill.** At HistoryLength 10 (before signal), the workflow was in "Running" state. After killing the worker, it remained in "Running" state — verified with `temporal workflow list`. The workflow's state lives entirely in the Temporal service, not the worker process.

**2. After restart, the worker replayed without re-running geocode.** There is exactly one `ActivityTaskScheduled` event for geocode (event 5). After the restart, the worker replayed the workflow code from history, and when execution reached `workflow.execute_activity(geocode_location, ...)`, Temporal detected that the activity had already run and returned the cached result from history. **No new HTTP call to Nominatim was made.** The history is authoritative; the worker is stateless.

**3. Concurrent activities are visible in history.** Events 15 and 16 are both `ActivityTaskScheduled` at the same timestamp — `asyncio.gather` caused both downstream activities to be scheduled in the same workflow task. In the Web UI, this shows as two parallel activity nodes. The `ThreadPoolExecutor` then ran them in separate threads simultaneously.

---

## 4. Reflection: Temporal vs the hand-rolled FSM

**The feel of Temporal's durable execution:**

The pause-across-restart is genuinely invisible to the workflow code. The `await workflow.wait_condition(...)` line suspends; after a worker restart the same line resumes — the developer doesn't write any serialisation, state persistence, or restart logic. The event history is the state. This is qualitatively different from the FSM's approach: the FSM requires explicit `pending_request()` / `provide_input()` calls and depends on the in-memory `WorkflowRunner` object surviving the process. If the FSM's process dies, the journey is lost.

**The cost paid for that guarantee:**

The workflow/activity split requires strict discipline. Any blocking call in workflow code — `requests.get`, `datetime.now()`, `time.sleep()`, even `print()` in some configurations — is detectable as non-determinism and will cause replay failures. The sandbox's error is immediate and clear, but the required mental model is not obvious without hitting the error once. The FSM has no such constraint because it doesn't replay.

**Effort comparison for the pause/resume requirement:**

| Capability | FSM | Temporal |
|-----------|-----|---------|
| Suspend on human input | `runner._pending = InputRequest(...)` (explicit) | `await workflow.wait_condition(...)` (natural) |
| Resume | `runner.provide_input(value)` | `await handle.signal(...)` |
| State persists across restarts | **No** — in-memory object | **Yes** — event history in Temporal service |
| Retries on activity failure | Manual | Configurable `RetryPolicy`, automatic |
| Concurrent activities | Not supported natively | `asyncio.gather` |

**The honest assessment:** For the mandatory requirement (pause across arbitrary time, survive restarts, resume from persisted state), Temporal provides it as a first-class feature with zero application-level serialisation code. The FSM provides the pause/resume protocol but not the durability — a process restart loses the journey. The Arazzo outer layer from the previous spike was trying to add that durability by hand (serialising `BriefingState` to JSON); Temporal makes it unnecessary.

The non-determinism constraint is the real friction. It is enforced, not advisory, and it changes how you think about where code belongs. Once internalised, it is not burdensome — activities can do anything, workflows do nothing. But it is a learning curve that the FSM doesn't impose.

**One-sentence verdict:** Temporal handles the mandatory pause/survive-restart requirement elegantly and without application-level persistence code, at the cost of a strict workflow/activity discipline that the FSM and Arazzo approaches don't require.

---

## File inventory

```
temporal/
  activities.py     # three @activity.defn sync functions + input/output dataclasses
  workflow.py       # BriefingWorkflow: @workflow.run, @workflow.signal, @workflow.query
  worker.py         # temporal worker process (run and leave running)
  start.py          # start a workflow, wait for result
  disambiguate.py   # send choose_location signal (query candidates first)
  FINDINGS.md       # this document
```

### To run the demo from scratch

```bash
# Terminal 1 — Temporal dev server
temporal server start-dev

# Terminal 2 — Worker
uv run python temporal/worker.py

# Terminal 3 — Start workflow (Richmond = disambiguation)
uv run python temporal/start.py "Richmond" "2026-06-21"

# (Kill and restart Terminal 2 to observe durable resume)

# Terminal 4 — Send disambiguation signal
uv run python temporal/disambiguate.py <workflow-id> 3

# Web UI: http://localhost:8233
```
