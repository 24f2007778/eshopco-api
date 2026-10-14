
import os
import json
from pathlib import Path
import yaml
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
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

DEFAULTS = {
    "port": 8000,
    "workers": 1,
    "debug": False,
    "log_level": "info",
    "api_key": "default-secret-000",
}


def normalize_key(key):
    key = key.strip()
    if key.upper() == "NUM_WORKERS":
        return "workers"
    if key.upper().startswith("APP_"):
        key = key[4:]
    return key.lower()


def parse_value(key, value):
    value = str(value)

    if key in ("port", "workers"):
        return int(value)

    if key == "debug":
        return value.strip().lower() in ("true", "1", "yes", "on")

    return value

def load_config():
    config = DEFAULTS.copy()

    # Layer 2: environment-specific YAML.
    environment = os.environ.get("APP_ENV", "development")
    yaml_path = Path(f"config.{environment}.yaml")

    if yaml_path.exists():
        with yaml_path.open("r", encoding="utf-8") as file:
            yaml_config = yaml.safe_load(file) or {}

        for key, value in yaml_config.items():
            config[normalize_key(key)] = value

    # Layer 3: .env file. Read separately to preserve precedence.
    env_file = dotenv_values(".env")
    for key, value in env_file.items():
        if value is not None:
            normalized = normalize_key(key)
            config[normalized] = parse_value(normalized, value)

    # Layer 4: actual OS environment variables override .env.
    for key, value in os.environ.items():
        if key.startswith("APP_") or key.upper() == "NUM_WORKERS":
            normalized = normalize_key(key)
            config[normalized] = parse_value(normalized, value)

    return config


@app.get("/effective-config")
def effective_config(request: Request):
    config = load_config()

    # Highest precedence: repeated CLI-style query overrides.
    for item in request.query_params.getlist("set"):
        if "=" not in item:
            continue

        key, value = item.split("=", 1)
        normalized = normalize_key(key)

        config[normalized] = parse_value(normalized, value)

    # Never reveal the real API key.
    config["api_key"] = "****"

    return config

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
