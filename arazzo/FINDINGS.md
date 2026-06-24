# Arazzo Spike — Findings

Spike date: 2026-06-23. Arazzo spec: v1.1.0. arazzo-runner: v0.9.6 (jentic/arazzo-engine).  
Journey rebuilt: weather/sunset briefing (Nominatim → sunrise-sunset → Open-Meteo).

---

## 1. OpenAPI-describing the third-party APIs — overhead

**Verdict: low overhead, one-time cost, clear value.**

All three APIs needed minimal OpenAPI fragments — only the single operation each that the journey calls, plus the specific response fields the Arazzo expressions reference.

| API | Approach | Effort |
|-----|----------|--------|
| Nominatim | Hand-written minimal fragment (~40 lines) | ~5 min |
| Sunrise-Sunset | Hand-written minimal fragment (~45 lines) | ~5 min |
| Open-Meteo | Official OpenAPI spec exists (`openapi/forecast.yml` in their repo) but is large; wrote a minimal fragment (~55 lines) instead | ~10 min |

The Open-Meteo official spec is usable but describing only the fields you actually need is more practical — it keeps the Arazzo file readable and avoids importing a 3,000-line spec for three parameters.

Writing these fragments surfaced an early discipline win: it forces you to check the real field paths before writing runtime expressions (`lat`/`lon` on Nominatim, `results.sunrise` on sunrise-sunset, `daily.temperature_2m_max` as an array on Open-Meteo). The FSM avoided this by letting you discover errors at runtime.

**Overhead is proportional to API count, not journey complexity.** For a journey touching three APIs, it was ~20 minutes total. For a large system touching 20 APIs, it would be a meaningful but amortised cost if the descriptions are reused across journeys.

---

## 2. Runtime expressions vs FSM templating — expressiveness

**Verdict: comparable expressiveness, more formal, slightly more verbose.**

| Operation | FSM templating | Arazzo runtime expression |
|-----------|----------------|--------------------------|
| Insert geocode result into param | `{location[lat]}` | `$inputs.lat` (lat passed as explicit input) |
| Extract nested response field | `{get_sunset[results][sunrise]}` | `$response.body#/results/sunrise` |
| Extract array element | `{get_weather[daily][temperature_2m_max][0]}` | `$response.body#/daily/temperature_2m_max/0` |
| Thread step output to next step | Implicit (shared `WorkflowData` context) | `$steps.getSunset.outputs.sunrise` (explicit) |
| Thread step output to workflow output | Implicit | `$steps.getWeather.outputs.max_temp` (explicit) |

All three real API calls executed correctly on the first attempt. The JSON Pointer syntax (`#/path/0`) is readable and precise. The FSM's dict-subscript syntax (`[key]`) is terser but fragile against non-string keys (arrays work only because Python's `format()` subscript does implicit coercion).

The explicitness of Arazzo's step outputs is a genuine improvement: every value a step exposes to downstream steps is declared and named. In the FSM, any step can read any key from the shared context — it's an implicit global.

The main loss: Arazzo expressions cannot do inline string interpolation on a value before passing it (`"{$inputs.location}, UK"` is supported for the `q` parameter, but this edge case required the `{…}` interpolation syntax rather than the `$…` expression syntax — a minor inconsistency in the spec that arazzo-runner handles correctly).

---

## 3. The decisive question: which option?

### Confirmed: **Option B** — workflow-per-turn split

**The evidence:**

**Arazzo spec v1.1.0**: No native pause/resume mechanism. The spec covers step-level success/failure actions (`continue`, `goto`, `retry`, `end`) and workflow-to-workflow calls via `workflowId` in a step. There is no construct for suspending execution and handing control back to a caller to wait for human input. AsyncAPI support (v1.1 addition) handles asynchronous messaging channels — it is not a human-input/pause mechanism.

**arazzo-runner v0.9.6**: Confirmed by reading the source. `execute_workflow()` runs a workflow to completion in a single call. `start_workflow()` + `execute_next_step()` allow step-by-step driving, but these are fine-grained execution hooks for the runner's own loop — not a suspend/resume mechanism. There is no checkpoint, callback for human input, or "wait-for-signal" step type. Flow control is `continue | goto | retry | end`. The runner has no concept of a workflow instance that can be serialised, stored, and resumed.

**What was built:** Two Arazzo workflows in a single `.arazzo.yaml` file:
- `geocodeLocation` (Turn 1): geocode → return candidates array
- `generateBriefing` (Turn 2): sunrise-sunset + weather → return briefing data

A Python outer orchestration layer (`orchestrator.py`, ~80 lines) owns everything Arazzo cannot:
- Inspects Turn 1 output to decide if disambiguation is needed
- Pauses (returns to the caller with the candidates list and serialised state)
- Persists between-turn state as plain JSON (process-restart safe — verified)
- On user reply, restores state, calls Turn 2 with chosen lat/lon
- Stitches Turn 1 output (`lat`, `lon`, `display_name`) into Turn 2 inputs

**The orchestrator layer is small, but it is functionally a mini-FSM.** It has implicit states (geocoded-waiting-for-choice, complete), conditional branching (single result vs multiple), and serialisable execution context. That it is only 80 lines reflects the simplicity of a two-turn journey with one pause point. A journey with three pause points would require more state, more conditional branches, and more stitching.

---

## 4. Is Option B better than keeping the FSM?

**Reasoned verdict: marginal improvement for isolated API choreography; not a clear win overall.**

### What Arazzo adds

- **Standardised, validatable workflow definitions**: `.arazzo.yaml` is machine-readable and tool-able. The FSM's JSON workflow files are bespoke.
- **OpenAPI-description discipline**: Forces explicit API contracts. The FSM resolves URLs and parameters ad-hoc from JSON templates.
- **Off-shelf executor**: arazzo-runner handles HTTP, auth, parameter mapping, success-criteria evaluation. The FSM's `_run_api()` is ~20 lines of requests boilerplate.
- **Reusability**: The `geocodeLocation` and `generateBriefing` workflows could be composed into other journeys without rewriting Python.

### What Arazzo does not replace

The FSM engine's pause/resume/disambiguation/context machinery is entirely absent from Arazzo. The outer orchestration layer re-implements:

| FSM capability | Equivalent in Option B |
|----------------|----------------------|
| `WorkflowRunner.pending_request()` | `Turn1Result.needs_disambiguation` |
| `WorkflowRunner.provide_input()` | `generate_briefing(restored_state, chosen_index)` |
| `WorkflowData` shared context | `BriefingState` serialised JSON |
| Conditional transitions (`single_result` guard) | `if not turn1.needs_disambiguation:` |
| `disambiguate` step type | `orchestrator.py` loop + caller surfaces options |

The outer layer is not simpler than the FSM — it is the FSM for the pause/resume concern, plus the complexity of the turn-boundary stitching.

**Two moving parts vs one.** Every turn boundary requires explicit state serialisation, explicit input-output stitching, and a new `ArazzoRunner.from_arazzo_path()` initialisation (which re-parses all OpenAPI specs). The FSM's runner is a single persistent object. This has a real overhead cost per turn.

**Net assessment**: If the team were building a new system from scratch that touched many different third-party APIs, Arazzo's OpenAPI-grounded approach would pay off. For this existing project with a working FSM engine and a small set of journeys, the migration cost is not justified by the gains. The sweet spot for Arazzo is the API-choreography layer (the part that already works well in arazzo-runner) — not as a replacement for the pause/resume/state-management FSM.

---

## 5. Does the outer layer reinvent durable execution?

**Yes, partially — and this is the most important flag from the spike.**

The outer orchestration layer re-implements, in simplified form, exactly the properties that durable-execution engines (Temporal, Durable Task Framework, Inngest) are designed to provide:

- Serialisable workflow state that survives process restarts
- Explicit pause at a human-action step
- Resume from persisted state with a supplied value
- Turn-boundary stitching

For a two-turn journey, the 80-line `orchestrator.py` is manageable. For a production journey with five pause points (geocode → disambiguate → confirm date → preview report → confirm save), the outer layer would need to serialise five state snapshots, track which step was interrupted, and resume correctly from any of them. That is the problem durable-execution engines solve with infrastructure.

**The real decision tree, if this requirement is taken seriously:**

1. **FSM (current)**: Custom, in-repo, proven for this use case. Pause/resume is native. Maintenance cost = whoever owns the repo.
2. **Arazzo + thin outer layer**: Standardised API choreography, but the pause/resume layer is bespoke Python and will grow with journey complexity.
3. **Durable execution engine (e.g. Temporal) + Arazzo for API choreography**: Arazzo handles the HTTP steps; Temporal owns the durable workflow state and human-in-the-loop signals. Clean separation, production-grade. High setup cost.

The spike does not evaluate option 3, but the outer layer it was forced to sketch is clearly reinventing it at small scale.

---

## File inventory

```
arazzo/
  openapi/
    nominatim.yaml           # minimal OpenAPI fragment (Nominatim /search)
    sunrise_sunset.yaml      # minimal OpenAPI fragment (sunrise-sunset /json)
    open_meteo_forecast.yaml # minimal OpenAPI fragment (Open-Meteo /v1/forecast)
  briefing.arazzo.yaml       # Arazzo document: geocodeLocation + generateBriefing workflows
  orchestrator.py            # outer orchestration layer (pause/resume/state/stitching)
  FINDINGS.md                # this document
```

Agent tools added to `tools.py`:
- `arazzo_start_briefing(location, date)` — Turn 1 + optional auto-complete if unambiguous
- `arazzo_resume_briefing(state, chosen_index)` — Turn 2 after disambiguation

The agent holds the opaque `state` JSON between the two calls but does not interpret it; all logic lives in the orchestrator.
