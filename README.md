# Legibility Demo
This demonstrates a tool that reads a serialised Finite-State-Machine for a simple workflow that requires some API calls and saves the results to a database.

This project is managed by [uv](https://docs.astral.sh/uv/). To set this up, just fork and clone the repo and run `uv sync`.

Then you need to run the following in three different shells.

To serve out the serialised workflow:
```shell
uv run uvicorn workflow_server:app --reload
```

For persisting the report result to Postgres:
```shell
uv run uvicorn report_server:app --port 8001 --reload
```

To run the workflow from the command line:
```shell
uv run python fsm.py workflow_2
```

## Chat interface

The project also includes a Gradio chat interface that wraps the FSM in an agentic loop. You will need an Anthropic API key:

```shell
export ANTHROPIC_API_KEY=sk-ant-...
```

Then start the Gradio app (keeping the two servers running):
```shell
uv run python app.py
```

The UI opens at http://localhost:7860. The left panel is the chat; the right panel shows a real-time trace of every FSM step ("Show Your Working"). Try prompts like:

- *What workflows are available?*
- *Weather briefing for Keswick on 21 June*
- *Briefing for 51.5074, -0.1278 on 2026-06-25*