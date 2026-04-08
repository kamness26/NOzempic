"""
weekly_run.py
─────────────
Main orchestrator. This is what GitHub Actions runs every Thursday.

Flow:
  1. Load config + participant data
  2. Pull activity data from Oura (Kam) and WHOOP (Marcus)
  3. Load this week's Renpho scans from data/weekly/
  4. Load last week's Renpho scans for delta calculation
  5. Compute weekly scores and rankings
  6. Generate Segment 1 (group dispatch) via Claude
  7. Generate Segment 2 (personal brief) per participant via Claude
  8. Send individual emails via SendGrid
  9. Save this week's data for next week's delta comparison
  10. Increment week counter in config.json

Usage:
    python weekly_run.py                    # uses current week
    WEEK_OVERRIDE=2026-03-24 python weekly_run.py  # override start date
"""

import os
import json
import glob
import shutil
from datetime import date, timedelta, datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Project root ──────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR   = ROOT / "data" / "weekly"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(config: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def get_week_range(override: str = None) -> tuple[date, date]:
    """Return (monday, sunday) for the week to process."""
    if override:
        monday = datetime.strptime(override, "%Y-%m-%d").date()
    else:
        today  = date.today()
        monday = today - timedelta(days=today.weekday() + 7)
    return monday, monday + timedelta(days=6)


def find_renpho_pdf(participant_id: str, week_start: date) -> str | None:
    """Look for a Renpho PDF for this participant in data/weekly/."""
    patterns = [
        DATA_DIR / f"{participant_id}_{week_start}.pdf",
        DATA_DIR / f"{participant_id}_*.pdf",
        DATA_DIR / "*.pdf",
    ]
    for pattern in patterns:
        matches = sorted(glob.glob(str(pattern)), reverse=True)
        if matches:
            return matches[0]
    return None


def load_previous_renpho(participant_id: str, week_start: date) -> dict | None:
    """Load last week's parsed Renpho data from JSON cache."""
    prev_week = week_start - timedelta(days=7)
    cache_path = DATA_DIR / f"{participant_id}_{prev_week}_renpho.json"
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    return None


def save_renpho_cache(participant_id: str, week_start: date, data: dict):
    """Cache parsed Renpho data as JSON for next week's delta."""
    cache_path = DATA_DIR / f"{participant_id}_{week_start}_renpho.json"
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  💾 Cached Renpho data → {cache_path.name}")


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    print("\n🏁 NOzempic Weekly Run Starting...\n")

    config     = load_config()
    week_num   = config.get("week_number", 1)
    participants = config["participants"]

    # Week date range
    override   = os.getenv("WEEK_OVERRIDE")
    monday, sunday = get_week_range(override)
    print(f"📅 Processing week {week_num}: {monday} → {sunday}\n")

    # ── Step 1: Pull activity data ────────────────────────────────────────────
    print("📡 Fetching activity data...")
    all_activity = {}

    for p in participants:
        pid    = p["id"]
        device = p["device"]
        print(f"  → {p['name']} ({device.upper()})")

        try:
            if device == "oura":
                from connectors.oura import fetch_weekly_data
                all_activity[pid] = fetch_weekly_data(str(monday), str(sunday))

            elif device == "whoop":
                from connectors.whoop import fetch_weekly_data
                all_activity[pid] = fetch_weekly_data(monday, sunday)

            print(f"     ✅ composite score: {all_activity[pid]['weekly_averages'].get('composite_activity_score')}")

        except Exception as e:
            print(f"     ❌ Failed: {e}")
            # Use neutral fallback so scoring can still proceed
            all_activity[pid] = {
                "device": device,
                "weekly_averages": {"composite_activity_score": 50},
                "daily": [],
            }

    # ── Step 2: Load Renpho scans ─────────────────────────────────────────────
    print("\n📊 Loading Renpho scans...")
    all_renpho_current  = {}
    all_renpho_previous = {}

    for p in participants:
        pid = p["id"]

        # Current week
        pdf_path = find_renpho_pdf(pid, monday)
        if pdf_path:
            from connectors.renpho import parse_pdf
            all_renpho_current[pid] = parse_pdf(pdf_path, participant_id=pid)
            save_renpho_cache(pid, monday, all_renpho_current[pid])
            print(f"  ✅ {p['name']}: {Path(pdf_path).name}")
        else:
            print(f"  ⚠️  No Renpho PDF found for {p['name']} — using empty scan")
            all_renpho_current[pid] = {"participant_id": pid, "body_sore_score": 50}

        # Previous week (for deltas)
        all_renpho_previous[pid] = load_previous_renpho(pid, monday)
        if all_renpho_previous[pid]:
            print(f"     📈 Previous scan loaded for delta calculation")

    # ── Step 3: Score and rank ────────────────────────────────────────────────
    print("\n🏆 Computing scores...")
    from engine.scoring import compute_weekly_score, rank_participants

    scores = []
    for p in participants:
        pid = p["id"]
        score = compute_weekly_score(
            participant_id  = pid,
            activity_data   = all_activity[pid],
            renpho_current  = all_renpho_current[pid],
            renpho_previous = all_renpho_previous.get(pid),
        )
        scores.append(score)
        print(f"  {p['name']}: {score['weekly_score']} pts")

    ranked = rank_participants(scores)
    winner = next(r for r in ranked if r["is_winner"])
    print(f"\n  🥇 Winner: {winner['participant_id'].title()} ({winner['weekly_score']} pts)")

    # ── Step 4: Generate email content via Claude ─────────────────────────────
    print("\n✍️  Generating email content...")
    from engine.generator import generate_segment1, generate_segment2

    segment1 = generate_segment1(ranked, all_activity, all_renpho_current, week_num)
    print("  ✅ Segment 1 (group dispatch) generated")

    participant_emails = {}
    for p in participants:
        pid = p["id"]
        score_data = next(r for r in ranked if r["participant_id"] == pid)

        seg2 = generate_segment2(
            participant     = p,
            activity        = all_activity[pid],
            renpho_current  = all_renpho_current[pid],
            renpho_previous = all_renpho_previous.get(pid),
            score_data      = score_data,
        )
        print(f"  ✅ Segment 2 generated for {p['name']}")

        # Combine into full HTML email
        full_html = _build_full_email(segment1, seg2, ranked, all_activity, p,
                                      all_renpho_current[pid], week_num, monday)
        participant_emails[pid] = full_html

    # ── Step 5: Send emails ───────────────────────────────────────────────────
    from mailer.sender import send_all
    results = send_all(participants, participant_emails, week_num)

    # ── Step 6: Update week counter ───────────────────────────────────────────
    if results["sent"]:
        config["week_number"] = week_num + 1
        save_config(config)
        print(f"\n  📌 Week counter updated to {week_num + 1}")

    print(f"\n✅ NOzempic Week {week_num} complete!\n")
    return results


def _build_full_email(seg1: str, seg2: str, ranked: list, all_activity: dict,
                      participant: dict, renpho: dict, week_num: int, week_start: date) -> str:
    """Wrap both segments in the styled HTML shell."""
    name    = participant["name"]
    winner  = next(r for r in ranked if r["is_winner"])
    pid     = participant["id"]
    score   = next(r for r in ranked if r["participant_id"] == pid)

    # Convert plain text segments to simple HTML paragraphs
    def to_html(text: str) -> str:
        lines = text.strip().split("\n")
        html_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                html_lines.append("<br>")
            elif line.startswith("🏆") or line.startswith("💡") or line.startswith("YOUR FOCUS"):
                html_lines.append(f'<p style="font-weight:700;color:#e63946;margin:16px 0 6px">{line}</p>')
            elif line[0].isdigit() and line[1] == ".":
                html_lines.append(f'<p style="margin:6px 0 6px 16px">• {line[2:].strip()}</p>')
            else:
                html_lines.append(f'<p style="margin:6px 0">{line}</p>')
        return "\n".join(html_lines)

    seg1_html = to_html(seg1)
    seg2_html = to_html(seg2)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif; background:#0a0a0a; margin:0; padding:0; }}
  .wrap {{ max-width:640px; margin:0 auto; }}
  .hdr {{ background:linear-gradient(135deg,#0a0a0a,#1a1a2e); border-bottom:3px solid #e63946; padding:32px 40px; text-align:center; }}
  .hdr-logo {{ font-size:11px; letter-spacing:4px; color:#e63946; font-weight:700; margin-bottom:8px; }}
  .hdr-title {{ font-size:42px; font-weight:900; letter-spacing:6px; color:#fff; }}
  .hdr-sub {{ font-size:11px; color:#666; letter-spacing:2px; margin-top:8px; }}
  .week-badge {{ display:inline-block; background:#e63946; color:#fff; font-size:10px; font-weight:700; letter-spacing:2px; padding:4px 12px; border-radius:20px; margin-top:10px; }}
  .seg1 {{ background:#0f0f0f; padding:32px 40px; color:#ccc; font-size:14px; line-height:1.8; }}
  .seg1 p {{ color:#ccc; }}
  .seg1 p[style*="font-weight"] {{ color:#e63946 !important; }}
  .divider {{ background:#1a1a1a; border-top:1px solid #2a2a2a; border-bottom:1px solid #2a2a2a; padding:18px 40px; text-align:center; }}
  .divider span {{ font-size:10px; letter-spacing:3px; color:#555; font-weight:700; text-transform:uppercase; }}
  .divider small {{ display:block; font-size:11px; color:#e63946; letter-spacing:2px; margin-top:6px; font-weight:700; }}
  .seg2 {{ background:#fafafa; padding:32px 40px; color:#333; font-size:14px; line-height:1.8; }}
  .score-bar {{ display:flex; gap:12px; margin:16px 0; flex-wrap:wrap; }}
  .score-pill {{ background:#fff; border:1px solid #e0e0e0; border-radius:20px; padding:8px 16px; font-size:12px; }}
  .score-pill strong {{ color:#e63946; font-size:18px; }}
  .footer {{ background:#0a0a0a; padding:20px 40px; text-align:center; border-top:1px solid #1a1a1a; }}
  .footer p {{ font-size:11px; color:#444; margin:4px 0; }}
  .footer .motto {{ color:#e63946; font-weight:700; letter-spacing:2px; font-size:12px; }}
</style>
</head>
<body>
<div class="wrap">

  <div class="hdr">
    <div class="hdr-logo">⚡ NOzempic</div>
    <div class="hdr-title">WEEKLY DISPATCH</div>
    <div class="hdr-sub">Your body. Your data. No excuses.</div>
    <div class="week-badge">Week {week_num} · {week_start.strftime('%b %d, %Y')}</div>
  </div>

  <div style="background:#e63946;color:#fff;font-size:10px;font-weight:700;letter-spacing:3px;padding:8px 40px;text-transform:uppercase;">
    📢 Segment 1 — The Dispatch
  </div>

  <div class="seg1">
    {seg1_html}
  </div>

  <div class="divider">
    <span>Your Personal Brief</span>
    <small>🔒 For Your Eyes Only</small>
  </div>

  <div style="background:#1a1a1a;color:#fff;font-size:10px;font-weight:700;letter-spacing:3px;padding:8px 40px;text-transform:uppercase;">
    📋 Segment 2 — Personal Brief · {name}
  </div>

  <div class="seg2">
    <div class="score-bar">
      <div class="score-pill">Weekly Score <strong>{score['weekly_score']}</strong> pts</div>
      <div class="score-pill">Rank <strong>#{score['rank']}</strong></div>
      <div class="score-pill">Body Score <strong>{score['body_sore_score']}</strong>/100</div>
      <div class="score-pill">Activity <strong>{score['activity_score']}</strong>/100</div>
    </div>
    {seg2_html}
  </div>

  <div class="footer">
    <p class="motto">No Shortcuts. No Ozempic. Just Work.</p>
    <p>NOzempic Week {week_num} · {week_start.strftime('%B %d, %Y')}</p>
    <p>Your personal data is never shared. This brief is for you alone.</p>
  </div>

</div>
</body>
</html>"""


if __name__ == "__main__":
    run()
