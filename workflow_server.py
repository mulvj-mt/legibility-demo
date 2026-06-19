from pathlib import Path
from typing import Any
import re
import json

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse

WORKFLOWS_DIR = Path(__file__).parent / "workflows"
VALID_NAME_REGEX = re.compile(r"^[a-z0-9_]+$")

_COUNTRIES: list[dict] = json.loads(
    (Path(__file__).parent / "countries.json").read_text(encoding="utf-8")
)

app = FastAPI(title="WorkflowProvider")

@app.get("/healthcheck")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/countries/search")
def search_countries(q: str = Query(..., min_length=1, description="Country name query")) -> list[dict]:
    q_lower = q.lower()
    return [c for c in _COUNTRIES if q_lower in c["name"].lower()]


@app.get("/workflows/list")
def list_workflows() -> list[dict]:
    results = []
    for path in sorted(WORKFLOWS_DIR.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                definition = json.load(f)
        except json.JSONDecodeError:
            continue
        results.append({
            "name": path.stem,
            "description": definition.get("description", ""),
            "inputs": definition.get("inputs", {}),
        })
    return results


@app.get("/workflows")
def get_workflow(name: str = Query(..., min_length=1, description="Workflow name")) -> Any:
    if not VALID_NAME_REGEX.match(name):
        raise HTTPException(
            status_code=400,
            detail="Invalid workflow name - use only lowercase letters, numbers and underscores."
        )
    
    path = WORKFLOWS_DIR/f"{name}.json"

    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"No workflow found called '{name}'")
    
    try:
        with path.open("r", encoding="utf-8") as f:
            definition = json.load(f)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail=f"The file for {name} contains invalid JSON"
        )
    
    return JSONResponse(content=definition)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app)
