from statemachine import State, StateChart
from statemachine.io import create_machine_class_from_definition
from typing import Any, Callable
import json
import requests


class WorkflowError(Exception):
    pass

class WorkflowData:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def __setitem__(self, key :str, value: Any) -> None:
        self.data[key] = value

    def __getitem__(self, key):
        return self.data.get(key)
    
    def as_format_map(self) -> dict[str, Any]:
        return self.data
    

class StepActions:
    
    @staticmethod
    def get_input(step: dict, context: WorkflowData) -> str:
        prompt = step.get("prompt", f"{step['name']}")
        data = input(prompt)
        context[step["name"]] = data
        return data
    
    @staticmethod
    def run_api(step: dict[str, str], context: WorkflowData) -> dict[Any, Any]:
        url: str = step["url"]
        method = step.get("method", "GET").upper()
        params = {k: v.format(**context.as_format_map()) for k, v in step.get("params", {}).items()}

        response = requests.request(method, url, params=params)
        response.raise_for_status()

        try:
            result = response.json()
        except ValueError, AttributeError:
            result = {"result": response.text}

        context[step["name"]] = json.dumps(result)

        return result
    
    @staticmethod
    def render_output(step: dict, context: WorkflowData) -> None:
        template = step.get("template")
        if template:
            rendered = template.format(**context.as_format_map())
            print(rendered)
        print(json.dumps(context.as_format_map(), indent=2, default=str))



class WorkFlowMachineFactory:
    def __init__(self, context):
        self.context = context

    def build(self, workflow: dict) -> type[StateChart]:
        steps = workflow["steps"]
        if not steps:
            raise WorkflowError("No steps")
        
        states_def: dict[str, dict] = {}

        for i, step in enumerate(steps):
            state_def: dict[str, Any] = {
                "enter": [lambda step=step: self.run_step(step)]
            }
            if i == 0:
                state_def["initial"] = True
            if i == len(steps) - 1:
                state_def["final"] = True
            else:
                state_def["transitions"] = [{"target": steps[i + 1]["name"]}]

            states_def[step["name"]] = state_def

        return create_machine_class_from_definition(
            "Workflow",
            states=states_def,
            validate_final_reachability = False
        )

    def run_step(self, step: dict) -> Any:
        match step.get("type"):
            case "input":
                return StepActions.get_input(step, self.context)
            case "api":
                return StepActions.run_api(step, self.context)
            case "output":
                return StepActions.render_output(step, self.context)
            case other:
                raise WorkflowError(f"Invalid step type: {other}")


if __name__ == '__main__':
    with open("workflow.json") as f:
        workflow = json.load(f)
    context = WorkflowData()
    factory = WorkFlowMachineFactory(context)
    machine = factory.build(workflow)
    machine()
    
    