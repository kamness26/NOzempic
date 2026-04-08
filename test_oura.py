"""
test_oura.py
────────────
Quick connectivity test for the Oura API.
Pulls the last 7 days of data and prints a clean summary to the console.
Run this from GitHub Actions or locally with your token set in .env.
"""

import os, json
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

from connectors.oura import fetch_weekly_data

def run():
    token = os.getenv("OURA_PERSONAL_ACCESS_TOKEN", "")
    if not token:
        print("❌  OURA_PERSONAL_ACCESS_TOKEN is not set.")
        return

    print("🔵  Oura API — Connection Test")
    print(f"    Token: {token[:8]}{'*' * (len(token) - 8)}")
    print()

    today  = date.today()
    monday = today - timedelta(days=today.weekday())   # this week's Monday
    sunday = monday + timedelta(days=6)

    print(f"📅  Fetching: {monday} → {sunday}\n")

    try:
        data = fetch_weekly_data(str(monday), str(sunday))
    except Exception as e:
        print(f"❌  API call failed: {e}")
        return

    avgs = data.get("weekly_averages", {})

    print("=" * 50)
    print("  WEEKLY AVERAGES")
    print("=" * 50)
    print(f"  Activity Score    : {avgs.get('activity_score', 'N/A')}")
    print(f"  Readiness Score   : {avgs.get('readiness_score', 'N/A')}")
    print(f"  Sleep Score       : {avgs.get('sleep_score', 'N/A')}")
    print(f"  HRV Avg (ms)      : {avgs.get('hrv_avg_ms', 'N/A')}")
    print(f"  Steps / day       : {avgs.get('steps', 'N/A')}")
    print(f"  Active Cal / day  : {avgs.get('active_calories', 'N/A')}")
    print(f"  Composite Score   : {avgs.get('composite_activity_score', 'N/A')}")
    print("=" * 50)
    print()
    print("📆  DAILY BREAKDOWN")
    print("-" * 50)

    for day in data.get("daily", []):
        d         = day.get("date", "?")
        activity  = day.get("activity_score", "--")
        readiness = day.get("readiness_score", "--")
        sleep     = day.get("sleep_score", "--")
        hrv       = day.get("hrv_avg_ms", "--")
        steps     = day.get("steps", "--")
        print(f"  {d}  |  Act:{activity:>3}  Read:{readiness:>3}  Sleep:{sleep:>3}  HRV:{hrv:>4}ms  Steps:{steps}")

    print("-" * 50)
    print(f"\n✅  Oura connection successful — {len(data['daily'])} days returned.\n")

if __name__ == "__main__":
    run()
