"""
Temporal activities for the weather briefing journey.

Activities contain all non-deterministic I/O: every API call lives here.
Workflow code never touches requests or any external system directly; it only
awaits activity results.  This is the workflow/activity split.

All three activities are sync functions, so the worker runs them in a
ThreadPoolExecutor (configured in worker.py). That gives true parallelism
when the workflow runs get_sunrise_sunset and get_weather concurrently.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests
from temporalio import activity


# ── Shared input/output types ─────────────────────────────────────────────────

@dataclass
class LocationDateInput:
    lat: str
    lon: str
    date: str


@dataclass
class SunriseSunsetResult:
    sunrise: str
    sunset: str


@dataclass
class WeatherResult:
    max_temp: float
    min_temp: float
    precip: float


# ── Activities ────────────────────────────────────────────────────────────────

@activity.defn
def geocode_location(location: str) -> list:
    """Geocode a UK place name via Nominatim. Returns a list of candidate dicts."""
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": f"{location}, UK", "format": "json", "limit": "10"},
        headers={"User-Agent": "legibility-demo/0.1"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


@activity.defn
def get_sunrise_sunset(args: LocationDateInput) -> SunriseSunsetResult:
    """Fetch sunrise and sunset times from the Sunrise-Sunset API."""
    resp = requests.get(
        "https://api.sunrise-sunset.org/json",
        params={"lat": args.lat, "lng": args.lon, "date": args.date, "formatted": "0"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()["results"]
    return SunriseSunsetResult(sunrise=data["sunrise"], sunset=data["sunset"])


@activity.defn
def get_weather(args: LocationDateInput) -> WeatherResult:
    """Fetch daily weather forecast from Open-Meteo."""
    resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": args.lat,
            "longitude": args.lon,
            "start_date": args.date,
            "end_date": args.date,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
            "timezone": "auto",
        },
        timeout=15,
    )
    resp.raise_for_status()
    daily = resp.json()["daily"]
    return WeatherResult(
        max_temp=daily["temperature_2m_max"][0],
        min_temp=daily["temperature_2m_min"][0],
        precip=daily["precipitation_sum"][0],
    )
