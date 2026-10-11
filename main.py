
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)

DATA_FILE = Path(__file__).with_name("telemetry.json")


class MetricsRequest(BaseModel):
    regions: list[str]
    threshold_ms: float = Field(default=180)


def percentile95(values):
    values = sorted(values)
    if len(values) == 1:
        return values[0]

    position = 0.95 * (len(values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower

    return values[lower] + fraction * (
        values[upper] - values[lower]
    )


@app.get("/")
def home():
    return {"message": "eShopCo telemetry API is running"}


@app.post("/api/metrics")
def get_metrics(request: MetricsRequest):
    try:
        with DATA_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to read telemetry.json: {exc}",
        )

    if isinstance(data, dict):
        data = data.get(
            "records",
            data.get("telemetry", data.get("data", [])),
        )

    if not isinstance(data, list):
        raise HTTPException(
            status_code=500,
            detail="Telemetry data must be a JSON array",
        )

    results = {}

    for region in request.regions:
        records = [
            record for record in data
            if str(record.get("region", "")).lower() == region.lower()
        ]

        if not records:
            results[region] = {
                "avg_latency": None,
                "p95_latency": None,
                "avg_uptime": None,
                "breaches": 0,
            }
            continue

        latencies = [float(r["latency_ms"]) for r in records]
        uptimes = [float(r["uptime_pct"]) for r in records]

        results[region] = {
            "avg_latency": sum(latencies) / len(latencies),
            "p95_latency": percentile95(latencies),
            "avg_uptime": sum(uptimes) / len(uptimes),
            "breaches": sum(
                latency > request.threshold_ms
                for latency in latencies
            ),
        }

    return results
