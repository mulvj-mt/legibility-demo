"""Strands tool definitions for the legibility-demo workflow agent."""
import json
import sys
import requests
from pathlib import Path
from strands import tool
from fsm import (
    WorkflowRunner, WorkflowError,
    StepStarted, StepCompleted, AwaitingInput, ApiCall, OutputRendered,
)

sys.path.insert(0, str(Path(__file__).parent / "arazzo"))
from orchestrator import geocode_location, generate_briefing, BriefingState  # noqa: E402

WORKFLOW_SERVER = "http://127.0.0.1:8000"


def _make_observer(session: dict):
    def obs(event) -> None:
        match event:
            case StepStarted(step_name=name, step_type=kind):
                session["event_log"].append(f"[start] {name} ({kind})")
            case StepCompleted(step_name=name, step_type=kind):
                session["event_log"].append(f"[done]  {name} ({kind})")
            case AwaitingInput(step_name=name, prompt=prompt, options=options):
                session["event_log"].append(f"[wait]  {name}: {prompt!r}")
                if options:
                    for i, opt in enumerate(options):
                        session["event_log"].append(f"  [{i}] {opt.label}")
            case ApiCall(step_name=name, url=url, params=params, body=body):
                msg = f"[api]   {name}: {url}"
                if params:
                    msg += f"  params={params}"
                session["event_log"].append(msg)
                if body:
                    session["event_log"].append(f"        body={body}")
            case OutputRendered(step_name=name, text=text):
                session["event_log"].append(f"[output] {name}:\n{text}")
                prior = session.get("last_output")
                session["last_output"] = f"{prior}\n\n{text}" if prior else text
    return obs


def _current_status(runner: WorkflowRunner, session: dict) -> dict:
    if runner.is_finished():
        result: dict = {"status": "complete"}
        if "last_output" in session:
            result["output"] = session["last_output"]
        return result
    req = runner.pending_request()
    if req is None:
        return {"status": "error", "error_message": "Runner stalled with no pending request"}
    if req.kind == "disambiguate":
        return {
            "status": "needs_disambiguation",
            "pending_step": req.step_name,
            "pending_prompt": req.prompt,
            "options": [{"index": i, "label": opt.label} for i, opt in enumerate(req.options or [])],
        }
    return {
        "status": "needs_input",
        "pending_step": req.step_name,
        "pending_prompt": req.prompt,
    }


def make_tools(session: dict) -> list:
    """Return a list of Strands tool objects closed over the given Gradio session dict."""
    observer = _make_observer(session)

    def list_workflows() -> str:
        """List available workflows with their names, descriptions, and required inputs.

        Call this first to discover which workflows are available before calling
        start_workflow. Returns a JSON array; each element has 'name', 'description',
        and 'inputs' (a dict mapping input-step names to their descriptions).
        """
        resp = requests.get(f"{WORKFLOW_SERVER}/workflows/list")
        resp.raise_for_status()
        return json.dumps(resp.json())

    def start_workflow(workflow_name: str, inputs: dict) -> str:
        """Start a named workflow, pre-filling any inputs already known from the user's message.

        Args:
            workflow_name: Exact name of the workflow as returned by list_workflows.
            inputs: Dict mapping input step names to their values extracted from the
                    user's message (e.g. {"place_name": "Birkenhead", "date": "2026-06-21"}).
                    Pass an empty dict if nothing was extracted.

        Returns a JSON status object with one of these shapes:
        - {"status": "complete", "output": "...briefing text..."}
        - {"status": "needs_input", "pending_step": "...", "pending_prompt": "..."}
        - {"status": "needs_disambiguation", "pending_step": "...", "pending_prompt": "...", "options": [{"index": 0, "label": "..."}, ...]}
        - {"status": "error", "error_message": "..."}
        """
        try:
            resp = requests.get(f"{WORKFLOW_SERVER}/workflows", params={"name": workflow_name})
            resp.raise_for_status()
            workflow = resp.json()
        except Exception as e:
            return json.dumps({"status": "error", "error_message": f"Could not fetch workflow '{workflow_name}': {e}"})

        session.pop("last_output", None)

        try:
            runner = WorkflowRunner(workflow, observers=[observer])
        except WorkflowError as e:
            return json.dumps({"status": "error", "error_message": str(e)})

        session["runner"] = runner

        # Drive through any input steps whose values were extracted from the user's message.
        while not runner.is_finished():
            req = runner.pending_request()
            if req is None or req.kind != "input" or req.step_name not in inputs:
                break
            try:
                runner.provide_input(str(inputs[req.step_name]))
            except WorkflowError as e:
                return json.dumps({"status": "error", "error_message": str(e)})

        return json.dumps(_current_status(runner, session))

    def resume_workflow(value: str) -> str:
        """Provide the next input value to the currently paused workflow.

        For needs_input status: pass the user-supplied string value.
        For needs_disambiguation status: pass the numeric index of the chosen option
        as a string (e.g. "2").

        Returns the same status object shape as start_workflow.
        """
        runner: WorkflowRunner | None = session.get("runner")
        if runner is None:
            return json.dumps({
                "status": "error",
                "error_message": "No workflow is currently running. Call start_workflow first.",
            })
        if runner.is_finished():
            return json.dumps({
                "status": "error",
                "error_message": "Workflow has already completed. Start a new one.",
            })
        try:
            runner.provide_input(value)
        except WorkflowError as e:
            return json.dumps({"status": "error", "error_message": str(e)})
        return json.dumps(_current_status(runner, session))

    # ── Arazzo-based briefing tools (Option B: workflow-per-turn split) ──────

    def arazzo_start_briefing(location: str, date: str) -> str:
        """Start the Arazzo-based weather briefing journey for a UK location and date.

        Geocodes the location via the Arazzo geocodeLocation workflow. If the result
        is unambiguous, proceeds directly to the full briefing. If multiple candidates
        are found, returns them for the user to choose from; call
        arazzo_resume_briefing with the returned state and the user's choice to continue.

        Args:
            location: UK place name (e.g. "Moel Famau", "Richmond").
            date:     Date in YYYY-MM-DD format (e.g. "2026-06-21").

        Returns a JSON object with one of these shapes:
        - {"status": "complete", "output": "...briefing text..."}
        - {"status": "needs_disambiguation", "options": [{"index": 0, "label": "..."}, ...],
           "state": "<opaque JSON string — pass unchanged to arazzo_resume_briefing>"}
        - {"status": "error", "error_message": "..."}
        """
        try:
            turn1 = geocode_location(location, date)
        except Exception as e:
            return json.dumps({"status": "error", "error_message": str(e)})

        if not turn1.candidates:
            return json.dumps({
                "status": "error",
                "error_message": f"No locations found for {location!r}",
            })

        if not turn1.needs_disambiguation:
            try:
                briefing = generate_briefing(turn1.state, 0)
            except Exception as e:
                return json.dumps({"status": "error", "error_message": str(e)})
            return json.dumps({"status": "complete", "output": briefing.format()})

        return json.dumps({
            "status": "needs_disambiguation",
            "options": [
                {"index": i, "label": c["display_name"]}
                for i, c in enumerate(turn1.candidates)
            ],
            "state": turn1.state.to_json(),
        })

    def arazzo_resume_briefing(state: str, chosen_index: int) -> str:
        """Complete the Arazzo briefing after the user has chosen a location candidate.

        Args:
            state:        The opaque state string returned by arazzo_start_briefing.
            chosen_index: The numeric index of the candidate chosen by the user.

        Returns a JSON object:
        - {"status": "complete", "output": "...briefing text..."}
        - {"status": "error", "error_message": "..."}
        """
        try:
            restored = BriefingState.from_json(state)
            briefing = generate_briefing(restored, int(chosen_index))
        except Exception as e:
            return json.dumps({"status": "error", "error_message": str(e)})
        return json.dumps({"status": "complete", "output": briefing.format()})

    return [
        tool(list_workflows), tool(start_workflow), tool(resume_workflow),
        tool(arazzo_start_briefing), tool(arazzo_resume_briefing),
    ]
