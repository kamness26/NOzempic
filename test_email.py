"""
test_email.py
─────────────
Fires a real test email to Kam — uses live Oura data for activity
and the sample Renpho scan for body composition.

Run via GitHub Actions: workflow_dispatch on test_email.yml
"""

import os, json
from datetime import date, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent


def run():
    print("\n🧪  NOzempic Email Test\n")

    config = json.loads((ROOT / "config.json").read_text())
    kam    = next(p for p in config["participants"] if p["id"] == "kam")

    # ── 1. Pull live Oura data ────────────────────────────────────────────────
    print("📡  Fetching Oura data...")
    from connectors.oura import fetch_weekly_data, get_week_dates
    start, end = get_week_dates()
    try:
        kam_activity = fetch_weekly_data(start, end)
        print(f"  ✅  Composite score: {kam_activity['weekly_averages'].get('composite_activity_score')}")
    except Exception as e:
        print(f"  ⚠️  Oura fetch failed ({e}) — using mock data")
        kam_activity = {
            "device": "oura",
            "weekly_averages": {
                "activity_score": 72, "readiness_score": 70,
                "sleep_score": 75, "hrv_avg_ms": 38,
                "steps": 8200, "active_calories": 460,
                "composite_activity_score": 71.0,
            },
            "daily": [],
        }

    # ── 2. Load Renpho scan ───────────────────────────────────────────────────
    print("📊  Loading Renpho scan...")
    renpho_path = ROOT / "data" / "weekly" / "kam_latest.pdf"

    from connectors.renpho import parse_pdf
    if renpho_path.exists():
        kam_renpho = parse_pdf(str(renpho_path), participant_id="kam")
        print(f"  ✅  Loaded from {renpho_path.name}")
    else:
        print("  ℹ️   No PDF in data/weekly/ — using Apr 3 scan values")
        kam_renpho = {
            "participant_id":          "kam",
            "weight_lb":               253.4,
            "body_fat_mass_lb":        88.4,
            "body_fat_pct":            34.9,
            "muscle_mass_lb":          153.8,
            "skeletal_muscle_mass_lb": 95.0,
            "bone_mass_lb":            11.0,
            "visceral_fat":            17,
            "bmr_kcal":                1985,
            "metabolic_age":           46,
            "body_sore_score":         63,
            "optimal_weight_lb":       166.0,
            "bmi":                     33.6,
            "whr":                     1.01,
        }

    kam_renpho_prev = {
        "weight_lb":       253.4,
        "body_fat_pct":    34.9,
        "body_sore_score": 63,
    }

    # ── 3. Score ──────────────────────────────────────────────────────────────
    print("📈  Computing score...")
    from engine.scoring import compute_weekly_score
    score_data = compute_weekly_score("kam", kam_activity, kam_renpho, kam_renpho_prev)
    print(f"  Weekly score: {score_data['weekly_score']} pts")

    # ── 4. Generate recap via Claude ──────────────────────────────────────────
    print("\n✍️   Generating coaching recap via Claude...")
    from engine.generator import generate_weekly_recap
    recap_text = generate_weekly_recap(
        participant     = kam,
        activity        = kam_activity,
        renpho_current  = kam_renpho,
        renpho_previous = kam_renpho_prev,
        score_data      = score_data,
        week_num        = config.get("week_number", 1),
    )
    print("  ✅  Recap written")

    # ── 5. Build HTML ─────────────────────────────────────────────────────────
    from weekly_run import _build_email_html
    week_start = date.today() - timedelta(days=7)
    html = _build_email_html(recap_text, kam, score_data,
                             config.get("week_number", 1), week_start,
                             activity=kam_activity)

    # ── 6. Send ───────────────────────────────────────────────────────────────
    kam_email = kam.get("email") or os.getenv("ADMIN_EMAIL", "")
    if not kam_email:
        print("\n❌  No email address in config.json or ADMIN_EMAIL secret.")
        return

    print(f"\n📧  Sending test email to {kam_email}...")
    from mailer.sender import send_test_email
    ok = send_test_email(kam_email, "Kam", html)

    if ok:
        print("\n✅  Test email sent! Check your inbox.\n")
    else:
        print("\n❌  Send failed — check GMAIL_ADDRESS and GMAIL_APP_PASSWORD secrets.\n")


if __name__ == "__main__":
    run()
