# DDL — run this once against the legibility database before starting this server:
#
#   CREATE TABLE reports (
#       record_id     SERIAL PRIMARY KEY,
#       created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
#       location      TEXT NOT NULL,
#       latitude      DOUBLE PRECISION NOT NULL,
#       longitude     DOUBLE PRECISION NOT NULL,
#       report_date   DATE NOT NULL,
#       sunrise       TIMESTAMPTZ NOT NULL,
#       sunset        TIMESTAMPTZ NOT NULL,
#       precipitation DOUBLE PRECISION NOT NULL,
#       max_temp      DOUBLE PRECISION NOT NULL,
#       min_temp      DOUBLE PRECISION NOT NULL
#   );

from datetime import date, datetime
from typing import Any
import os

import psycopg2
import psycopg2.extras
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/legibility")

app = FastAPI(title="ReportStore")


def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


class ReportIn(BaseModel):
    location: str
    latitude: float
    longitude: float
    report_date: date
    sunrise: datetime
    sunset: datetime
    precipitation: float
    max_temp: float
    min_temp: float


# The %(name)s placeholders are psycopg2 bound parameters — the driver escapes
# and type-casts each value before sending to Postgres. No SQL injection risk.
_INSERT_SQL = """
    INSERT INTO reports
        (location, latitude, longitude, report_date,
         sunrise, sunset, precipitation, max_temp, min_temp)
    VALUES
        (%(location)s, %(latitude)s, %(longitude)s, %(report_date)s,
         %(sunrise)s, %(sunset)s, %(precipitation)s, %(max_temp)s, %(min_temp)s)
    RETURNING record_id, created_at
"""


@app.post("/report", status_code=201)
def create_report(report: ReportIn, conn=Depends(get_conn)) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(_INSERT_SQL, report.model_dump())
        record_id, created_at = cur.fetchone()
        conn.commit()
    return {"record_id": record_id, "created_at": created_at.isoformat()}


@app.get("/reports")
def list_reports(conn=Depends(get_conn)) -> JSONResponse:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM reports ORDER BY created_at DESC")
        rows = [dict(r) for r in cur.fetchall()]
    return JSONResponse(
        content=[
            {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}
            for row in rows
        ]
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, port=8001)
