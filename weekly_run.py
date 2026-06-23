"""
weekly_run.py
─────────────
Main orchestrator. This is what GitHub Actions runs every Thursday.

Flow:
  1. Load config + participant data
  2. Pull activity data from Oura Ring
  3. Load this week's Renpho scans from data/weekly/
  4. Load last week's Renpho scans for delta calculation
  5. Compute weekly scores per participant
  6. Generate personal coaching recap per participant via Claude
  7. Send individual emails via SendGrid
  8. Save this week's data for next week's delta comparison
  9. Increment week counter in config.json

Usage:
    python weekly_run.py                    # uses current week
    WEEK_OVERRIDE=2026-03-24 python weekly_run.py  # override start date
"""

import os
import json
import glob
from datetime import date, timedelta, datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT        = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR    = ROOT / "data" / "weekly"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(config: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def get_week_range(override: str = None) -> tuple[date, date]:
    """Return (thursday, wednesday) — the 7-day window ending yesterday."""
    if override:
        week_start = datetime.strptime(override, "%Y-%m-%d").date()
    else:
        today      = date.today()
        week_start = today - timedelta(days=7)
    return week_start, week_start + timedelta(days=6)


def find_renpho_pdf(participant_id: str, week_start: date) -> str | None:
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


def find_renpho_json(participant_id: str) -> dict | None:
    matches = sorted(
        glob.glob(str(DATA_DIR / f"{participant_id}_*_renpho.json")),
        reverse=True,
    )
    if matches:
        with open(matches[0]) as f:
            return json.load(f)
    return None


def load_previous_renpho(participant_id: str, week_start: date) -> dict | None:
    prev_week  = week_start - timedelta(days=7)
    cache_path = DATA_DIR / f"{participant_id}_{prev_week}_renpho.json"
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    return None


def save_renpho_cache(participant_id: str, week_start: date, data: dict):
    cache_path = DATA_DIR / f"{participant_id}_{week_start}_renpho.json"
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  💾 Cached Renpho data → {cache_path.name}")


def run():
    print("\n🏁 NOzempic Weekly Run Starting...\n")

    config       = load_config()
    week_num     = config.get("week_number", 1)
    participants = config["participants"]

    override = os.getenv("WEEK_OVERRIDE")
    week_start, week_end = get_week_range(override)
    print(f"📅 Processing week {week_num}: {week_start} → {week_end}\n")

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
                all_activity[pid] = fetch_weekly_data(str(week_start), str(week_end))
            else:
                raise ValueError(f"Unsupported device: {device}")

            print(f"     ✅ composite score: {all_activity[pid]['weekly_averages'].get('composite_activity_score')}")

        except Exception as e:
            print(f"     ❌ Failed: {e}")
            admin_email = os.getenv("ADMIN_EMAIL")
            if admin_email:
                from mailer.sender import send_weekly_email
                send_weekly_email(
                    admin_email, "Admin",
                    f"NOzempic: {device.upper()} fetch failed for {p['name']}",
                    f"<p>Data fetch failed for <strong>{p['name']}</strong> ({device.upper()}):</p><pre>{e}</pre>",
                    week_num,
                )
            raise

    # ── Step 2: Load Renpho scans ─────────────────────────────────────────────
    print("\n📊 Loading Renpho scans...")
    all_renpho_current  = {}
    all_renpho_previous = {}

    for p in participants:
        pid = p["id"]

        pdf_path = find_renpho_pdf(pid, week_start)
        if pdf_path:
            from connectors.renpho import parse_pdf
            all_renpho_current[pid] = parse_pdf(pdf_path, participant_id=pid)
            save_renpho_cache(pid, week_start, all_renpho_current[pid])
            print(f"  ✅ {p['name']}: {Path(pdf_path).name}")
        else:
            cached = find_renpho_json(pid)
            if cached:
                all_renpho_current[pid] = cached
                print(f"  📂 {p['name']}: no PDF — using cached scan from {cached.get('scan_date', 'unknown date')}")
            else:
                print(f"  ⚠️  No Renpho data for {p['name']} — using neutral defaults")
                all_renpho_current[pid] = {"participant_id": pid, "body_sore_score": 50}

        all_renpho_previous[pid] = load_previous_renpho(pid, week_start)
        if all_renpho_previous[pid]:
            print(f"     📈 Previous scan loaded for delta calculation")

    # ── Step 3: Score each participant ────────────────────────────────────────
    print("\n📈 Computing scores...")
    from engine.scoring import compute_weekly_score

    scores = {}
    for p in participants:
        pid = p["id"]
        scores[pid] = compute_weekly_score(
            participant_id  = pid,
            activity_data   = all_activity[pid],
            renpho_current  = all_renpho_current[pid],
            renpho_previous = all_renpho_previous.get(pid),
        )
        print(f"  {p['name']}: {scores[pid]['weekly_score']} pts")

    # ── Step 4: Generate + send individual recaps ─────────────────────────────
    print("\n✍️  Generating coaching recaps...")
    from engine.generator import generate_weekly_recap
    from mailer.sender import send_weekly_email

    sent_count = 0
    for p in participants:
        pid  = p["id"]
        name = p["name"]

        recap_text = generate_weekly_recap(
            participant    = p,
            activity       = all_activity[pid],
            renpho_current = all_renpho_current[pid],
            renpho_previous= all_renpho_previous.get(pid),
            score_data     = scores[pid],
            week_num       = week_num,
        )
        print(f"  ✅ Recap generated for {name}")

        html = _build_email_html(recap_text, p, scores[pid], week_num, week_start)

        send_weekly_email(
            to_email = p["email"],
            to_name  = name,
            subject  = f"NOzempic Week {week_num} — Your Recap",
            html     = html,
            week_num = week_num,
        )
        print(f"  📧 Email sent to {name}")
        sent_count += 1

    # ── Step 5: Update week counter ───────────────────────────────────────────
    if sent_count:
        config["week_number"] = week_num + 1
        save_config(config)
        print(f"\n  📌 Week counter updated to {week_num + 1}")

    print(f"\n✅ NOzempic Week {week_num} complete!\n")


def _build_email_html(recap_text: str, participant: dict, score_data: dict,
                      week_num: int, week_start: date) -> str:
    name = participant["name"]

    def to_html(text: str) -> str:
        lines = text.strip().split("\n")
        html_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                html_lines.append("<br>")
            elif any(line.startswith(h) for h in ("YOUR NUMBERS", "WHAT THE DATA", "YOUR FOCUS", "💡")):
                html_lines.append(f'<p style="font-weight:700;color:#e63946;margin:20px 0 6px">{line}</p>')
            elif len(line) > 1 and line[0].isdigit() and line[1] == ".":
                html_lines.append(f'<p style="margin:6px 0 6px 16px">• {line[2:].strip()}</p>')
            else:
                html_lines.append(f'<p style="margin:6px 0">{line}</p>')
        return "\n".join(html_lines)

    recap_html = to_html(recap_text)

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
  .body {{ background:#fafafa; padding:32px 40px; color:#333; font-size:14px; line-height:1.8; }}
  .score-bar {{ display:flex; gap:12px; margin:0 0 24px; flex-wrap:wrap; }}
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
    <div class="hdr-title">WEEKLY RECAP</div>
    <div class="hdr-sub">Your body. Your data. No excuses.</div>
    <div class="week-badge">Week {week_num} · {week_start.strftime('%b %d, %Y')}</div>
  </div>

  <div class="body">
    <div class="score-bar">
      <div class="score-pill">Weekly Score <strong>{score_data['weekly_score']}</strong> pts</div>
      <div class="score-pill">Body Comp <strong>{score_data['body_sore_score']}</strong>/100</div>
      <div class="score-pill">Activity <strong>{score_data['activity_score']}</strong>/100</div>
      <div class="score-pill">Improvement <strong>{score_data['improvement_score']}</strong>/100</div>
    </div>
    {recap_html}
  </div>

  <div class="footer">
    <p class="motto">No Shortcuts. No Ozempic. Just Work.</p>
    <p>NOzempic Week {week_num} · {week_start.strftime('%B %d, %Y')}</p>
    <p>This recap is private and for {name} only.</p>
  </div>

</div>
</body>
</html>"""


if __name__ == "__main__":
    run()
