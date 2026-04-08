"""
test_email.py
─────────────
Fires a real test email to Kam only — uses live Oura data for activity
and the sample Renpho scan for body composition. Nili's data is mocked.

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

    config       = json.loads((ROOT / "config.json").read_text())
    participants = config["participants"]
    kam          = next(p for p in participants if p["id"] == "kam")

    # ── 1. Pull Kam's real Oura data ─────────────────────────────────────────
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

    # ── 2. Load sample Renpho scan ────────────────────────────────────────────
    print("📊  Loading Renpho scan...")
    renpho_path = ROOT / "data" / "weekly" / "kam_latest.pdf"
    sample_path = ROOT / "nozempic_sample_email.html"  # fallback indicator

    from connectors.renpho import parse_pdf
    if renpho_path.exists():
        kam_renpho = parse_pdf(str(renpho_path), participant_id="kam")
        print(f"  ✅  Loaded from {renpho_path.name}")
    else:
        # Use hardcoded values from Kam's Apr 3 scan as baseline
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

    # ── 3. Mock Nili's data (placeholder until first real scan) ───────────────
    nili_activity = {
        "device": "whoop",
        "weekly_averages": {
            "recovery_score": 84, "strain_normalized": 72,
            "hrv_avg_ms": 52, "resting_hr": 57,
            "composite_activity_score": 79.0,
        },
        "daily": [],
    }
    nili_renpho = {
        "participant_id":          "nili",
        "weight_lb":               219.8,
        "body_fat_mass_lb":        70.4,
        "body_fat_pct":            32.0,
        "muscle_mass_lb":          139.4,
        "skeletal_muscle_mass_lb": 85.4,
        "bone_mass_lb":            10.0,
        "visceral_fat":            13,
        "bmr_kcal":                1834,
        "metabolic_age":           49,
        "body_sore_score":         70,
        "optimal_weight_lb":       145.0,
        "bmi":                     33.3,
        "whr":                     0.92,
    }
    nili = next((p for p in participants if p["id"] == "nili"), {
        "id": "nili", "name": "Nili", "device": "whoop",
        "goals": ["body_fat_reduction", "endurance"],
        "target_weight_lb": 175.0, "starting_weight_lb": 200.0,
    })

    # ── 4. Score and rank ─────────────────────────────────────────────────────
    print("🏆  Computing scores...")
    from engine.scoring import compute_weekly_score, rank_participants

    scores = [
        compute_weekly_score("kam",  kam_activity,  kam_renpho,  kam_renpho_prev),
        compute_weekly_score("nili", nili_activity, nili_renpho, None),
    ]
    ranked = rank_participants(scores)
    for r in ranked:
        print(f"  #{r['rank']} {r['participant_id'].title()}: {r['weekly_score']} pts")

    # ── 5. Generate email content via Claude ──────────────────────────────────
    print("\n✍️   Generating email content via Claude...")
    from engine.generator import generate_segment1, generate_segment2

    all_activity = {"kam": kam_activity, "nili": nili_activity}
    all_renpho   = {"kam": kam_renpho,   "nili": nili_renpho}

    seg1 = generate_segment1(ranked, all_activity, all_renpho, week_num=1)
    print("  ✅  Segment 1 written")

    kam_score = next(r for r in ranked if r["participant_id"] == "kam")
    seg2 = generate_segment2(
        participant     = kam,
        activity        = kam_activity,
        renpho_current  = kam_renpho,
        renpho_previous = kam_renpho_prev,
        score_data      = kam_score,
    )
    print("  ✅  Segment 2 written")

    # ── 6. Build HTML ─────────────────────────────────────────────────────────
    from weekly_run import _build_full_email
    from datetime import datetime
    html = _build_full_email(
        seg1, seg2, ranked, all_activity,
        kam, kam_renpho, week_num=1,
        week_start=date.today() - timedelta(days=date.today().weekday() + 7)
    )

    # ── 7. Send to Kam only ───────────────────────────────────────────────────
    kam_email = kam.get("email") or os.getenv("ADMIN_EMAIL", "")
    if not kam_email:
        print("\n❌  No email address for Kam in config.json or ADMIN_EMAIL secret.")
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
