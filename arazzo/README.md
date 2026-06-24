# Arazzo Spike

An evaluation of the [OpenAPI Arazzo specification](https://spec.openapis.org/arazzo/latest.html) as a replacement for (or complement to) the hand-rolled FSM engine used in this project.

The existing FSM engine runs a weather/sunset briefing journey: geocode a UK location, optionally disambiguate between multiple results, input a date, then fetch sunrise/sunset and weather and render a briefing. This spike rebuilds that same journey in Arazzo and answers one central question: **can Arazzo own mid-flow pause/resume for human input, or does it require an outer orchestration layer?**

Full findings are in [FINDINGS.md](FINDINGS.md). Short answer: **Option B** — Arazzo has no native suspend mechanism; an outer Python layer must own the pause.

---

## Prerequisites

- Python ≥ 3.14 (managed by `uv` at the project root)
- `arazzo-runner` ≥ 0.9.6 — installed automatically via `uv` from `pyproject.toml`
- No API keys required (Nominatim, Sunrise-Sunset, Open-Meteo are all free and keyless)

---

## Directory structure

```
arazzo/
├── briefing.arazzo.yaml        # Arazzo workflow document (two workflows)
├── orchestrator.py             # Outer orchestration layer: pause/resume/state/stitching
├── openapi/
│   ├── nominatim.yaml          # Minimal OpenAPI 3.1 fragment — Nominatim /search
│   ├── sunrise_sunset.yaml     # Minimal OpenAPI 3.1 fragment — Sunrise-Sunset /json
│   └── open_meteo_forecast.yaml # Minimal OpenAPI 3.1 fragment — Open-Meteo /v1/forecast
├── FINDINGS.md                 # Full spike write-up and verdict
└── README.md                   # This file
```

---

## How it works

### The two Arazzo workflows

`briefing.arazzo.yaml` defines two workflows that each map to one conversational turn:

**`geocodeLocation` (Turn 1)**

Input: `{ location: string }`

Calls Nominatim `searchLocation` and returns the full array of matching candidates as `candidates`. The Arazzo runtime expression `$response.body` captures the whole response body (a JSON array). The workflow output `$steps.geocode.outputs.candidates` threads it to the caller.

**`generateBriefing` (Turn 2)**

Inputs: `{ lat, lon, date, location_name }`

Calls Sunrise-Sunset `getSunriseSunset` and Open-Meteo `getForecast` sequentially, threading `$inputs.lat` / `$inputs.lon` / `$inputs.date` into each call. Extracts nested fields with JSON Pointer syntax:

```yaml
sunrise: $response.body#/results/sunrise
max_temp: $response.body#/daily/temperature_2m_max/0
```

Each workflow runs to completion in a single `ArazzoRunner.execute_workflow()` call. There is no way for a workflow to pause mid-execution and wait for user input — this is the key limitation that mandates the outer layer.

### The outer orchestration layer (`orchestrator.py`)

Because Arazzo cannot suspend, the pause/resume logic lives here. The layer:

1. Calls `geocodeLocation` → inspects `needs_disambiguation` (`len(candidates) != 1`)
2. If disambiguation is needed: **returns to the caller** with the candidates list and serialised state — the pause happens at the Python call boundary, not inside Arazzo
3. Persists between-turn state as plain JSON (`BriefingState.to_json()`) — process-restart safe
4. On resume: restores state (`BriefingState.from_json()`), calls `generateBriefing` with the chosen candidate's `lat`/`lon`

This is the structure the brief calls **Option B**: Arazzo handles API choreography; an outer layer handles everything the FSM was providing (pause, persisted state, turn-stitching).

### OpenAPI fragments

Each API is described by a minimal OpenAPI 3.1 fragment covering only the single operation the journey uses. Arazzo requires OpenAPI descriptions; it references operations by `operationId`, not by URL. The operationIds used:

| Fragment | `operationId` | Endpoint |
|----------|---------------|----------|
| `nominatim.yaml` | `searchLocation` | `GET /search` |
| `sunrise_sunset.yaml` | `getSunriseSunset` | `GET /json` |
| `open_meteo_forecast.yaml` | `getForecast` | `GET /v1/forecast` |

---

## Running the spike

All commands run from the **project root**.

### 1. Inspect the Arazzo document

List the defined workflows:

```bash
uv run arazzo-runner list-workflows arazzo/briefing.arazzo.yaml
```

Describe a specific workflow:

```bash
uv run arazzo-runner describe-workflow arazzo/briefing.arazzo.yaml --workflow-id geocodeLocation
uv run arazzo-runner describe-workflow arazzo/briefing.arazzo.yaml --workflow-id generateBriefing
```

### 2. Run Turn 1 headless (geocode only)

Single result — no disambiguation needed:

```bash
uv run arazzo-runner execute-workflow arazzo/briefing.arazzo.yaml \
  --workflow-id geocodeLocation \
  --inputs '{"location": "Moel Famau"}'
```

Multiple results — candidates returned for disambiguation:

```bash
uv run arazzo-runner execute-workflow arazzo/briefing.arazzo.yaml \
  --workflow-id geocodeLocation \
  --inputs '{"location": "Richmond"}'
```

### 3. Run Turn 2 headless (briefing only, given known lat/lon)

```bash
uv run arazzo-runner execute-workflow arazzo/briefing.arazzo.yaml \
  --workflow-id generateBriefing \
  --inputs '{"lat": "53.1541445", "lon": "-3.2555194", "date": "2026-06-21", "location_name": "Moel Famau"}'
```

### 4. Run the full interactive journey via the orchestrator

This exercises both turns and the between-turn state serialisation/deserialisation:

```bash
uv run python arazzo/orchestrator.py
```

You will be prompted for a location and date. If the location is ambiguous (e.g. "Richmond"), a numbered list of candidates is shown and you choose by index. The script explicitly serialises state to JSON and restores it before Turn 2, demonstrating that the state is process-restart safe.

Example session with Moel Famau (single result):

```
Location: Moel Famau
Date (YYYY-MM-DD): 2026-06-21

Turn 1: geocoding 'Moel Famau'...
[state persisted: 552 bytes]

Single result: Moel Famau, St Asaph, Denbighshire, Cymru / Wales, United Kingdom

Turn 2: generating briefing...

============================================================
Briefing for Moel Famau, St Asaph, Denbighshire, Cymru / Wales, United Kingdom on 2026-06-21
  Sunrise : 2026-06-21T03:43:33+00:00
  Sunset  : 2026-06-21T20:46:08+00:00
  Max temp: 20.4 °C
  Min temp: 6.7 °C
  Precip  : 0.0 mm
```

Example session with Richmond (disambiguation):

```
Location: Richmond
Date (YYYY-MM-DD): 2026-06-21

Turn 1: geocoding 'Richmond'...
[state persisted: 3194 bytes]

Multiple locations found (6):
  [0] Richmond, Lichfield Gardens, North Sheen, London Borough of Richmond upon Thames ...
  [1] London Borough of Richmond upon Thames, Greater London ...
  [2] Richmond, Greater London, England, TW9 1DY ...
  [3] Richmond, North Yorkshire, York and North Yorkshire ...
  [4] Richmondshire, North Yorkshire ...
  [5] Richmond, Sheffield, South Yorkshire ...

Choose: 3

Turn 2: generating briefing...

============================================================
Briefing for Richmond, North Yorkshire, York and North Yorkshire, England on 2026-06-21
  Sunrise : 2026-06-21T03:29:12+00:00
  Sunset  : 2026-06-21T20:48:21+00:00
  Max temp: 20.8 °C
  Min temp: 10.6 °C
  Precip  : 0.0 mm
```

---

## Key findings

See [FINDINGS.md](FINDINGS.md) for the full write-up. The headline points:

- **OpenAPI overhead**: Low — three minimal fragments took ~20 minutes to write and forced useful discipline (you verify real field paths before writing runtime expressions).
- **Runtime expressions**: Comparable to the FSM's template syntax but more explicit — `$response.body#/results/sunrise` vs `{get_sunset[results][sunrise]}`. JSON Pointer syntax handles array indexing (`/daily/temperature_2m_max/0`) cleanly.
- **The decisive verdict (Option B)**: Arazzo v1.1.0 has no native pause/resume. `arazzo-runner` v0.9.6 confirms: `execute_workflow()` runs atomically; no suspend/resume API exists. The outer orchestration layer in `orchestrator.py` is functionally a mini-FSM. For the mandatory mid-flow-input requirement, Arazzo sits beneath a pause/resume layer rather than replacing it.
- **Is it better than the FSM?** Marginal improvement for API choreography specifically (standardised definitions, off-shelf executor, OpenAPI discipline). Not a net improvement overall — two moving parts instead of one, and the outer layer re-implements the same pause/resume responsibility the FSM already owns.
