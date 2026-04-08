"""
connectors/oura.py
──────────────────
Pulls weekly activity, readiness, sleep, and HRV data from the Oura API v2.
Auth: Personal Access Token (PAT) — set OURA_PERSONAL_ACCESS_TOKEN in .env

Oura API docs: https://cloud.ouraring.com/v2/docs
"""

import os
from datetime import date, timedelta
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.ouraring.com/v2/usercollection"
HEADERS = {"Authorization": f"Bearer {os.getenv('OURA_PERSONAL_ACCESS_TOKEN')}"}


def _get(endpoint: str, start: str, end: str) -> list[dict]:
    """Generic GET for any Oura daily collection endpoint."""
    resp = requests.get(
        f"{BASE_URL}/{endpoint}",
        headers=HEADERS,
        params={"start_date": start, "end_date": end},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_week_dates() -> tuple[str, str]:
    """Return (monday, sunday) for the most recently completed week."""
    today = date.today()
    monday = today - timedelta(days=today.weekday() + 7)
    sunday = monday + timedelta(days=6)
    return str(monday), str(sunday)


def fetch_weekly_data(start: str = None, end: str = None) -> dict:
    """
    Fetch a full week of Oura data and return a normalized dict.

    Returns:
        {
            "device": "oura",
            "period": {"start": ..., "end": ...},
            "daily": [
                {
                    "date": "2026-03-28",
                    "activity_score": 72,
                    "readiness_score": 68,
                    "sleep_score": 75,
                    "steps": 8420,
                    "active_calories": 480,
                    "hrv_avg_ms": 38
                },
                ...
            ],
            "weekly_averages": {
                "activity_score": 72.1,
                "readiness_score": 70.4,
                "sleep_score": 73.0,
                "steps": 7800,
                "active_calories": 450,
                "hrv_avg_ms": 38.2,
                "composite_activity_score": 71.2   # used in leaderboard
            }
        }
    """
    if not start or not end:
        start, end = get_week_dates()

    activity  = _get("daily_activity", start, end)
    readiness = _get("daily_readiness", start, end)
    sleep     = _get("daily_sleep", start, end)

    # Index by date for easy merging
    by_date: dict[str, dict] = {}

    for item in activity:
        d = item["day"]
        by_date.setdefault(d, {"date": d})
        by_date[d]["activity_score"]   = item.get("score")
        by_date[d]["steps"]            = item.get("steps", 0)
        by_date[d]["active_calories"]  = item.get("active_calories", 0)

    for item in readiness:
        d = item["day"]
        by_date.setdefault(d, {"date": d})
        by_date[d]["readiness_score"] = item.get("score")
        by_date[d]["hrv_avg_ms"]      = item.get("contributors", {}).get(
            "hrv_balance", None
        )

    for item in sleep:
        d = item["day"]
        by_date.setdefault(d, {"date": d})
        by_date[d]["sleep_score"] = item.get("score")

    daily = sorted(by_date.values(), key=lambda x: x["date"])

    # Fill any missing scores with None
    for row in daily:
        row.setdefault("activity_score", None)
        row.setdefault("readiness_score", None)
        row.setdefault("sleep_score", None)
        row.setdefault("steps", 0)
        row.setdefault("active_calories", 0)
        row.setdefault("hrv_avg_ms", None)

    # Weekly averages (skip None values)
    def avg(key):
        vals = [d[key] for d in daily if d.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    activity_avg  = avg("activity_score")
    readiness_avg = avg("readiness_score")

    # Composite: equally weight activity + readiness (both 0-100)
    composite = None
    if activity_avg and readiness_avg:
        composite = round((activity_avg + readiness_avg) / 2, 1)

    return {
        "device": "oura",
        "period": {"start": start, "end": end},
        "daily": daily,
        "weekly_averages": {
            "activity_score":          activity_avg,
            "readiness_score":         readiness_avg,
            "sleep_score":             avg("sleep_score"),
            "steps":                   avg("steps"),
            "active_calories":         avg("active_calories"),
            "hrv_avg_ms":              avg("hrv_avg_ms"),
            "composite_activity_score": composite,
        },
    }


if __name__ == "__main__":
    import json
    data = fetch_weekly_data()
    print(json.dumps(data, indent=2))
