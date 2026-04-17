"""
connectors/whoop.py
───────────────────
Pulls weekly recovery, strain, sleep, and HRV data from the WHOOP API v1.
Auth: OAuth2 — run onboarding/whoop_auth.py once to generate tokens.
Tokens are refreshed automatically when expired.

WHOOP API docs: https://developer.whoop.com/docs/developing
"""

import os
from datetime import date, timedelta, datetime, timezone
import requests
from dotenv import load_dotenv, set_key

load_dotenv()

BASE_URL      = "https://api.prod.whoop.com/developer/v1"
TOKEN_URL     = "https://api.prod.whoop.com/oauth/oauth2/token"
CLIENT_ID     = os.getenv("WHOOP_CLIENT_ID")
CLIENT_SECRET = os.getenv("WHOOP_CLIENT_SECRET")
ENV_FILE      = os.path.join(os.path.dirname(__file__), "../.env")


def _get_headers() -> dict:
    return {"Authorization": f"Bearer {os.getenv('WHOOP_ACCESS_TOKEN')}"}


def _refresh_tokens():
    """Exchange refresh token for a new access token.

    Uses HTTP Basic Auth (matching the initial token exchange) — WHOOP
    rejects client credentials sent as form fields on the token endpoint.

    After a successful refresh, attempts to persist the new tokens back to
    GitHub Secrets via gh CLI so future runs don't re-hit 401.
    Requires GH_PAT secret with repo secrets:write permission.
    """
    import base64
    import subprocess

    refresh = os.getenv("WHOOP_REFRESH_TOKEN")
    if not refresh:
        raise RuntimeError("No WHOOP_REFRESH_TOKEN found. Run onboarding/whoop_auth.py first.")

    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh,
        },
        headers={
            "Content-Type":  "application/x-www-form-urlencoded",
            "Authorization": f"Basic {credentials}",
        },
        timeout=10,
    )
    resp.raise_for_status()
    tokens = resp.json()

    new_access  = tokens["access_token"]
    new_refresh = tokens["refresh_token"]

    # Update current process so this run succeeds immediately
    os.environ["WHOOP_ACCESS_TOKEN"]  = new_access
    os.environ["WHOOP_REFRESH_TOKEN"] = new_refresh

    # Persist to GitHub Secrets so next week's run starts with valid tokens
    gh_pat = os.getenv("GH_PAT")
    if gh_pat:
        env = {**os.environ, "GH_TOKEN": gh_pat}
        for name, value in [("WHOOP_ACCESS_TOKEN", new_access),
                             ("WHOOP_REFRESH_TOKEN", new_refresh)]:
            result = subprocess.run(
                ["gh", "secret", "set", name, "--body", value,
                 "--repo", "kamness26/NOzempic"],
                env=env, capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"  🔑 {name} refreshed and saved to GitHub Secrets")
            else:
                raise RuntimeError(
                    f"Failed to persist {name} to GitHub Secrets: {result.stderr.strip()}"
                )
    else:
        raise RuntimeError(
            "GH_PAT not set — cannot persist refreshed WHOOP tokens. "
            "Add GH_PAT secret with repo:secrets:write permission to the repository."
        )

    # Also write to local .env if present (for local dev)
    set_key(ENV_FILE, "WHOOP_ACCESS_TOKEN",  new_access)
    set_key(ENV_FILE, "WHOOP_REFRESH_TOKEN", new_refresh)


def _get(endpoint: str, params: dict = None) -> dict:
    """GET with automatic token refresh on 401."""
    resp = requests.get(
        f"{BASE_URL}/{endpoint}",
        headers=_get_headers(),
        params=params or {},
        timeout=10,
    )
    if resp.status_code == 401:
        _refresh_tokens()
        resp = requests.get(
            f"{BASE_URL}/{endpoint}",
            headers=_get_headers(),
            params=params or {},
            timeout=10,
        )
    resp.raise_for_status()
    return resp.json()


def _paginate(endpoint: str, start_iso: str, end_iso: str) -> list[dict]:
    """Collect all pages for a date-ranged WHOOP endpoint."""
    results = []
    params = {"start": start_iso, "end": end_iso, "limit": 25}
    while True:
        data = _get(endpoint, params)
        results.extend(data.get("records", []))
        token = data.get("next_token")
        if not token:
            break
        params["nextToken"] = token
    return results


def _to_iso(d: date) -> str:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).isoformat()


def get_week_dates() -> tuple[date, date]:
    today = date.today()
    start = today - timedelta(days=7)
    return start, start + timedelta(days=6)


def fetch_weekly_data(start: date = None, end: date = None) -> dict:
    """
    Fetch a full week of WHOOP data and return a normalized dict.

    Returns the same shape as connectors/oura.py for easy scoring.

        {
            "device": "whoop",
            "period": {"start": ..., "end": ...},
            "daily": [
                {
                    "date": "2026-03-28",
                    "recovery_score": 71,      # 0-100 (≈ Oura readiness)
                    "strain_score": 14.2,       # 0-21
                    "strain_normalized": 67.6,  # 0-100 for fair comparison
                    "sleep_performance_pct": 78,
                    "hrv_avg_ms": 52,
                    "resting_hr": 58
                },
                ...
            ],
            "weekly_averages": {
                "recovery_score": 76.0,
                "strain_score": 13.8,
                "strain_normalized": 65.7,
                "sleep_performance_pct": 80.0,
                "hrv_avg_ms": 52.1,
                "resting_hr": 57.0,
                "composite_activity_score": 70.9   # used in leaderboard
            }
        }
    """
    if not start or not end:
        start, end = get_week_dates()

    start_iso = _to_iso(start)
    end_iso   = _to_iso(end + timedelta(days=1))  # WHOOP end is exclusive

    recoveries = _paginate("recovery", start_iso, end_iso)
    cycles     = _paginate("cycle",    start_iso, end_iso)  # strain lives here
    sleeps     = _paginate("sleep",    start_iso, end_iso)

    # Index by date
    by_date: dict[str, dict] = {}

    for r in recoveries:
        d = r.get("created_at", "")[:10]
        by_date.setdefault(d, {"date": d})
        score = r.get("score", {})
        by_date[d]["recovery_score"] = score.get("recovery_score")
        by_date[d]["hrv_avg_ms"]     = score.get("hrv_rmssd_milli")
        by_date[d]["resting_hr"]     = score.get("resting_heart_rate")

    for c in cycles:
        d = c.get("created_at", "")[:10]
        by_date.setdefault(d, {"date": d})
        score = c.get("score", {})
        raw_strain = score.get("strain")
        by_date[d]["strain_score"]      = raw_strain
        by_date[d]["strain_normalized"] = round(raw_strain / 21 * 100, 1) if raw_strain else None

    for s in sleeps:
        if not s.get("nap"):  # skip naps
            d = s.get("created_at", "")[:10]
            by_date.setdefault(d, {"date": d})
            score = s.get("score", {})
            by_date[d]["sleep_performance_pct"] = score.get("sleep_performance_percentage")

    daily = sorted(by_date.values(), key=lambda x: x["date"])

    # Fill defaults
    for row in daily:
        row.setdefault("recovery_score", None)
        row.setdefault("strain_score", None)
        row.setdefault("strain_normalized", None)
        row.setdefault("sleep_performance_pct", None)
        row.setdefault("hrv_avg_ms", None)
        row.setdefault("resting_hr", None)

    def avg(key):
        vals = [d[key] for d in daily if d.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    recovery_avg = avg("recovery_score")
    strain_norm  = avg("strain_normalized")

    # Composite: recovery (effort sustainability) + strain (effort exerted)
    composite = None
    if recovery_avg and strain_norm:
        composite = round((recovery_avg * 0.55 + strain_norm * 0.45), 1)

    return {
        "device": "whoop",
        "period": {"start": str(start), "end": str(end)},
        "daily": daily,
        "weekly_averages": {
            "recovery_score":          recovery_avg,
            "strain_score":            avg("strain_score"),
            "strain_normalized":       strain_norm,
            "sleep_performance_pct":   avg("sleep_performance_pct"),
            "hrv_avg_ms":              avg("hrv_avg_ms"),
            "resting_hr":              avg("resting_hr"),
            "composite_activity_score": composite,
        },
    }


if __name__ == "__main__":
    import json
    data = fetch_weekly_data()
    print(json.dumps(data, indent=2))
