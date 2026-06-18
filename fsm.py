from statemachine import StateChart
from statemachine.io import create_machine_class_from_definition
from dataclasses import dataclass
from typing import Any, Callable
import json
import requests


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

@dataclass
class ApiCall:
    step_name: str
    url: str
    params: dict


WorkflowEvent = StepStarted | StepCompleted | AwaitingInput | ApiCall


# ── Input request ─────────────────────────────────────────────────────────────

@dataclass
class InputRequest:
    kind: str           # "input" | "disambiguate"
    step_name: str
    prompt: str
    options: list | None = None  # populated for "disambiguate" kind


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


# ── Step actions (stateless) ──────────────────────────────────────────────────

class StepActions:

    @staticmethod
    def run_api(step: dict, context: WorkflowData, notify: Callable[[WorkflowEvent], None]) -> dict:
        url: str = step["url"]
        method = step.get("method", "GET").upper()
        params = {
            k: v.format(**context.as_format_map())
            for k, v in step.get("params", {}).items()
        }
        notify(ApiCall(step_name=step["name"], url=url, params=params))

        response = requests.request(method, url, params=params)
        response.raise_for_status()

        try:
            result = response.json()
        except (ValueError, AttributeError):
            result = {"result": response.text}

        context[step["name"]] = json.dumps(result)
        return result

    @staticmethod
    def render_output(step: dict, context: WorkflowData) -> None:
        template = step.get("template")
        if template:
            print(template.format(**context.as_format_map()))
        print(json.dumps(context.as_format_map(), indent=2, default=str))


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
            if i == len(steps) - 1:
                state_def["final"] = True

            next_name = steps[i + 1]["name"] if i < len(steps) - 1 else None

            if step_type == "input":
                state_def["enter"] = [self._make_input_enter(step)]
                if next_name:
                    state_def["transitions"] = [{
                        "target": next_name,
                        "event": "provide_input",
                        "on": self._make_input_on(step),
                    }]

            elif step_type in ("api", "output"):
                state_def["enter"] = [self._make_auto_enter(step)]
                if next_name:
                    state_def["transitions"] = [{"target": next_name}]

            else:
                raise WorkflowError(f"Unknown step type: {step_type!r}")

            states_def[step["name"]] = state_def

        return create_machine_class_from_definition(
            "Workflow",
            states=states_def,
            validate_final_reachability=False,
        )

    def _make_input_enter(self, step: dict) -> Callable:
        runner = self._runner
        step_name = step["name"]
        prompt = step.get("prompt", step_name)

        def enter() -> None:
            runner._notify(StepStarted(step_name=step_name, step_type="input"))
            runner._pending = InputRequest(kind="input", step_name=step_name, prompt=prompt)
            runner._notify(AwaitingInput(step_name=step_name, prompt=prompt))

        return enter

    def _make_input_on(self, step: dict) -> list[Callable]:
        context = self._context
        runner = self._runner
        step_name = step["name"]

        def store(value: str) -> None:
            context[step_name] = value

        def complete() -> None:
            runner._notify(StepCompleted(step_name=step_name, step_type="input"))

        return [store, complete]

    def _make_auto_enter(self, step: dict) -> Callable:
        context = self._context
        runner = self._runner
        step_type = step.get("type")
        step_name = step["name"]

        def enter() -> None:
            runner._notify(StepStarted(step_name=step_name, step_type=step_type))
            if step_type == "api":
                StepActions.run_api(step, context, runner._notify)
            elif step_type == "output":
                StepActions.render_output(step, context)
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
        if self._pending is None:
            raise WorkflowError("No input is currently pending")
        self._pending = None
        self._machine.send("provide_input", value=value)

    def is_finished(self) -> bool:
        return self._machine.current_state.final


# ── CLI observer ──────────────────────────────────────────────────────────────

def stdout_observer(event: WorkflowEvent) -> None:
    match event:
        case StepStarted(step_name=name, step_type=kind):
            print(f"[start] {name} ({kind})")
        case StepCompleted(step_name=name, step_type=kind):
            print(f"[done]  {name} ({kind})")
        case AwaitingInput(step_name=name, prompt=prompt):
            print(f"[wait]  {name}: {prompt!r}")
        case ApiCall(step_name=name, url=url, params=params):
            print(f"[api]   {name}: {url}  params={params}")


# ── CLI harness ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    response = requests.get("http://127.0.0.1:8000/workflows", params={"name": "workflow_1"})
    workflow = response.json()

    runner = WorkflowRunner(workflow, observers=[stdout_observer])

    while not runner.is_finished():
        req = runner.pending_request()
        if req is None:
            break
        value = input(req.prompt)
        runner.provide_input(value)
