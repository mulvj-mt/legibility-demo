from statemachine import StateChart
from statemachine.io import create_machine_class_from_definition
from dataclasses import dataclass
from typing import Any, Callable
import json
import string
import requests


# ── Template resolution ───────────────────────────────────────────────────────

class WorkflowFormatter(string.Formatter):
    """JSON-serialises dict/list values when no format spec is given."""

    def format_field(self, value: Any, format_spec: str) -> str:
        if isinstance(value, (dict, list)) and not format_spec:
            return json.dumps(value, default=str)
        return super().format_field(value, format_spec)


def _render(template: str, context: "WorkflowData") -> str:
    return WorkflowFormatter().format(template, **context.as_format_map())


# ── Event types ───────────────────────────────────────────────────────────────

@dataclass
class StepStarted:
    step_name: str
    step_type: str

@dataclass
class StepCompleted:
    step_name: str
    step_type: str

@dataclass
class AwaitingInput:
    step_name: str
    prompt: str
    options: list | None = None

@dataclass
class ApiCall:
    step_name: str
    url: str
    params: dict
    body: dict | None = None

@dataclass
class OutputRendered:
    step_name: str
    text: str


WorkflowEvent = StepStarted | StepCompleted | AwaitingInput | ApiCall | OutputRendered


# ── Input request ─────────────────────────────────────────────────────────────

@dataclass
class InputRequest:
    kind: str           # "input" | "disambiguate"
    step_name: str
    prompt: str
    options: list | None = None  # populated for "disambiguate" kind


@dataclass
class DisambiguateOption:
    label: str
    value: Any  # the full candidate dict; carries lat/lon so Nominatim isn't re-queried


# ── Exceptions ────────────────────────────────────────────────────────────────

class WorkflowError(Exception):
    pass


# ── Context store ─────────────────────────────────────────────────────────────

class WorkflowData:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __getitem__(self, key: str) -> Any:
        return self.data.get(key)

    def as_format_map(self) -> dict[str, Any]:
        return self.data


# ── Step actions ──────────────────────────────────────────────────────────────

def _run_api(step: dict, context: WorkflowData, notify: Callable[[WorkflowEvent], None]) -> dict:
    url: str = step["url"]
    method = step.get("method", "GET").upper()
    params = {k: _render(v, context) for k, v in step.get("params", {}).items()}
    headers = {k: _render(v, context) for k, v in step.get("headers", {}).items()}
    body = {k: _render(v, context) for k, v in step.get("body", {}).items()} or None
    notify(ApiCall(step_name=step["name"], url=url, params=params, body=body))

    response = requests.request(method, url, params=params, headers=headers, json=body)
    response.raise_for_status()

    try:
        result = response.json()
    except (ValueError, AttributeError):
        result = {"result": response.text}

    context[step["name"]] = result
    return result


def _render_output(step: dict, context: WorkflowData, notify: Callable[[WorkflowEvent], None]) -> None:
    template = step.get("template")
    text = _render(template, context) if template else json.dumps(context.as_format_map(), indent=2, default=str)
    print(text)
    notify(OutputRendered(step_name=step["name"], text=text))


# ── Machine factory ───────────────────────────────────────────────────────────

class WorkflowMachineFactory:
    def __init__(self, context: WorkflowData, runner: "WorkflowRunner") -> None:
        self._context = context
        self._runner = runner

    def build(self, workflow: dict) -> type[StateChart]:
        steps = workflow["steps"]
        if not steps:
            raise WorkflowError("No steps defined in workflow")

        states_def: dict[str, Any] = {}

        for i, step in enumerate(steps):
            step_type = step.get("type")
            state_def: dict[str, Any] = {}

            if i == 0:
                state_def["initial"] = True
            is_final = (i == len(steps) - 1) or bool(step.get("final"))
            if is_final:
                state_def["final"] = True

            # Final steps must not have outgoing transitions (python-statemachine constraint).
            next_name = steps[i + 1]["name"] if i < len(steps) - 1 and not is_final else None

            if step_type == "input":
                enter, on_actions = self._make_input_callbacks(step)
                state_def["enter"] = [enter]
                if next_name:
                    state_def["transitions"] = [{
                        "target": next_name,
                        "event": "provide_input",
                        "on": on_actions,
                    }]

            elif step_type == "disambiguate":
                enter, on_actions = self._make_disambiguate_callbacks(step)
                state_def["enter"] = [enter]
                if next_name:
                    state_def["transitions"] = [{
                        "target": next_name,
                        "event": "provide_input",
                        "on": on_actions,
                    }]

            elif step_type in ("api", "output"):
                state_def["enter"] = [self._make_auto_enter(step)]
                explicit = step.get("transitions")
                if explicit:
                    state_def["transitions"] = self._resolve_transitions(step, explicit)
                elif next_name:
                    state_def["transitions"] = [{"target": next_name}]

            else:
                raise WorkflowError(f"Unknown step type: {step_type!r}")

            states_def[step["name"]] = state_def

        return create_machine_class_from_definition(
            "Workflow",
            states=states_def,
            validate_final_reachability=False,
        )

    def _make_input_callbacks(self, step: dict) -> tuple[Callable, list[Callable]]:
        runner = self._runner
        context = self._context
        step_name = step["name"]
        prompt = step.get("prompt", step_name)

        def enter() -> None:
            runner._notify(StepStarted(step_name=step_name, step_type="input"))
            runner._pending = InputRequest(kind="input", step_name=step_name, prompt=prompt)
            runner._notify(AwaitingInput(step_name=step_name, prompt=prompt))

        def store(value: str) -> None:
            context[step_name] = value

        def complete() -> None:
            runner._notify(StepCompleted(step_name=step_name, step_type="input"))

        return enter, [store, complete]

    def _make_disambiguate_callbacks(self, step: dict) -> tuple[Callable, list[Callable]]:
        runner = self._runner
        context = self._context
        step_name = step["name"]
        source = step["source"]
        label_template = step.get("label_template", "{display_name}")
        prompt = step.get("prompt", "Please select one:")
        result_key = step.get("result_key", "location")

        # Mutable list shared between enter (writer) and on-actions (reader).
        _options: list[DisambiguateOption] = []

        def enter() -> None:
            candidates = context[source]
            if not candidates:
                runner._deferred_error = WorkflowError(
                    f"Step '{source}' returned no results — cannot proceed. "
                    f"(Zero-match handling is an open design question.)"
                )
                return
            options = [
                DisambiguateOption(label=label_template.format(**c), value=c)
                for c in candidates
            ]
            _options.clear()
            _options.extend(options)
            runner._notify(StepStarted(step_name=step_name, step_type="disambiguate"))
            runner._pending = InputRequest(
                kind="disambiguate",
                step_name=step_name,
                prompt=prompt,
                options=list(_options),
            )
            runner._notify(AwaitingInput(step_name=step_name, prompt=prompt, options=list(_options)))

        def store(value: str) -> None:
            selected = _options[int(value)].value
            context[step_name] = selected
            context[result_key] = selected

        def complete() -> None:
            runner._notify(StepCompleted(step_name=step_name, step_type="disambiguate"))

        return enter, [store, complete]

    def _resolve_transitions(self, step: dict, transitions: list) -> list:
        step_name = step["name"]
        resolved = []
        for t in transitions:
            rt: dict[str, Any] = {"target": t["target"]}
            if "cond" in t:
                rt["cond"] = self._resolve_cond(t["cond"], step_name)
            if "on" in t:
                rt["on"] = [self._resolve_on_action(t["on"], step_name)]
            resolved.append(rt)
        return resolved

    def _resolve_cond(self, cond_name: str, step_name: str) -> Callable:
        context = self._context
        if cond_name == "single_result":
            def guard() -> bool:
                result = context[step_name]
                return isinstance(result, list) and len(result) == 1
            return guard
        if cond_name.startswith("eq:"):
            _, path, expected = cond_name.split(":", 2)
            keys = path.split(".")
            def guard() -> bool:
                obj: Any = context[keys[0]]
                for key in keys[1:]:
                    if not isinstance(obj, dict):
                        return False
                    obj = obj.get(key)
                return obj == expected
            return guard
        raise WorkflowError(f"Unknown condition: {cond_name!r}")

    def _resolve_on_action(self, action_name: str, step_name: str) -> Callable:
        context = self._context
        if action_name.startswith("store_first_as:"):
            key = action_name.split(":", 1)[1]
            def action() -> None:
                context[key] = context[step_name][0]
            return action
        raise WorkflowError(f"Unknown action: {action_name!r}")

    def _make_auto_enter(self, step: dict) -> Callable:
        context = self._context
        runner = self._runner
        step_type = step["type"]
        step_name = step["name"]

        def enter() -> None:
            runner._notify(StepStarted(step_name=step_name, step_type=step_type))
            try:
                if step_type == "api":
                    _run_api(step, context, runner._notify)
                elif step_type == "output":
                    _render_output(step, context, runner._notify)
            except Exception as exc:
                runner._deferred_error = WorkflowError(f"Step '{step_name}' failed: {exc}")
                return
            runner._notify(StepCompleted(step_name=step_name, step_type=step_type))

        return enter


# ── Runner ────────────────────────────────────────────────────────────────────

class WorkflowRunner:
    def __init__(
        self,
        workflow: dict,
        observers: list[Callable[[WorkflowEvent], None]] | None = None,
    ) -> None:
        self._context = WorkflowData()
        self._pending: InputRequest | None = None
        self._deferred_error: Exception | None = None
        self.observers: list[Callable[[WorkflowEvent], None]] = observers or []

        factory = WorkflowMachineFactory(self._context, self)
        machine_class = factory.build(workflow)
        self._machine = machine_class()  # enters initial state; may set _pending

    def _notify(self, event: WorkflowEvent) -> None:
        for obs in self.observers:
            obs(event)

    def pending_request(self) -> InputRequest | None:
        return self._pending

    def provide_input(self, value: str) -> None:
        # Errors are deferred rather than raised inside statemachine transitions,
        # which would corrupt machine state. Check before send (error from a prior
        # step's enter) and after send (error from the transition just taken).
        if self._deferred_error is not None:
            err, self._deferred_error = self._deferred_error, None
            raise err
        if self._pending is None:
            raise WorkflowError("No input is currently pending")
        self._pending = None
        self._machine.send("provide_input", value=value)
        if self._deferred_error is not None:
            err, self._deferred_error = self._deferred_error, None
            raise err

    def is_finished(self) -> bool:
        return any(s.final for s in self._machine.configuration)


# ── CLI observer ──────────────────────────────────────────────────────────────

def stdout_observer(event: WorkflowEvent) -> None:
    match event:
        case StepStarted(step_name=name, step_type=kind):
            print(f"[start] {name} ({kind})")
        case StepCompleted(step_name=name, step_type=kind):
            print(f"[done]  {name} ({kind})")
        case AwaitingInput(step_name=name, prompt=prompt, options=options):
            print(f"[wait]  {name}: {prompt!r}")
            if options:
                for i, opt in enumerate(options):
                    print(f"         [{i}] {opt.label}")
        case ApiCall(step_name=name, url=url, params=params, body=body):
            print(f"[api]   {name}: {url}  params={params}")
            if body:
                print(f"         body={body}")
        case OutputRendered():
            pass  # already printed by _render_output


# ── CLI harness ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    workflow_name = sys.argv[1] if len(sys.argv) > 1 else "workflow_1"
    response = requests.get("http://127.0.0.1:8000/workflows", params={"name": workflow_name})
    response.raise_for_status()
    workflow = response.json()

    runner = WorkflowRunner(workflow, observers=[stdout_observer])

    while not runner.is_finished():
        req = runner.pending_request()
        if req is None:
            break
        # For disambiguate the observer already printed the options; just prompt for a number.
        prompt = "> " if req.kind == "disambiguate" else req.prompt
        value = input(prompt)
        runner.provide_input(value)
