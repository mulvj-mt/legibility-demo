# Legibility Demo
This demonstrates a tool that reads a serialised Finite-State-Machine for a simple workflow that requires some API calls and saves the results to a database.

This project is managed by [uv](https://docs.astral.sh/uv/). To set this up, just fork and clone the repo and run `uv sync`.

Then you need to run the following in three different shells.

To serve out the serialised workflow:
```shell
uv uv run uvicorn workflow_server:app --reload
```

For persisting the report result to Postgres:
```shell
uv run uvicorn report_server:app --port 8001 --reload
```

To run the workflow:
```shell
uv run python fsm.py workflow_2
```