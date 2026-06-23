"""
weekly_run.py
─────────────
Main orchestrator. Runs every Thursday via GitHub Actions.

Flow:
  1. Load config + participant data
  2. Pull activity data from Oura Ring
  3. Load this week's Renpho scans from data/weekly/
  4. Load last week's Renpho scans for delta calculation
  5. Compute weekly score
  6. Generate personal coaching recap via Claude
  7. Send email via SendGrid
  8. Save this week's data for next week's delta comparison
  9. Increment week counter in config.json

Usage:
    python weekly_run.py
    WEEK_OVERRIDE=2026-03-24 python weekly_run.py
"""

import os
import json
import glob
import math
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


# ── Chart builders ────────────────────────────────────────────────────────────

def _score_rings_html(averages: dict) -> str:
    """Three SVG circular rings for Activity, Readiness, Sleep weekly averages."""
    def ring(score, label, color):
        v    = score or 0
        r    = 38
        circ = 2 * math.pi * r
        dash = circ * (v / 100)
        gap  = circ - dash
        return f"""<td align="center" style="padding:0 16px;">
          <svg width="110" height="110" viewBox="0 0 110 110" xmlns="http://www.w3.org/2000/svg">
            <circle cx="55" cy="55" r="{r}" fill="none" stroke="#2a2a2a" stroke-width="10"/>
            <circle cx="55" cy="55" r="{r}" fill="none" stroke="{color}" stroke-width="10"
              stroke-dasharray="{dash:.1f} {gap:.1f}" stroke-linecap="round"
              transform="rotate(-90 55 55)"/>
            <text x="55" y="50" text-anchor="middle" fill="#fff"
              font-size="22" font-weight="700" font-family="Arial,sans-serif">{int(v)}</text>
            <text x="55" y="66" text-anchor="middle" fill="#666"
              font-size="9" font-family="Arial,sans-serif" letter-spacing="1">{label}</text>
          </svg>
        </td>"""

    act  = averages.get("activity_score")  or 0
    read = averages.get("readiness_score") or 0
    slp  = averages.get("sleep_score")     or 0
    return f"""<table cellpadding="0" cellspacing="0" border="0" width="100%">
      <tr>
        {ring(act,  "ACTIVITY",  "#60a5fa")}
        {ring(read, "READINESS", "#34d399")}
        {ring(slp,  "SLEEP",     "#f59e0b")}
      </tr>
    </table>"""


def _stat_cards_html(averages: dict) -> str:
    """Inline metric cards for steps, calories, and HRV."""
    steps    = averages.get("steps")          or 0
    cals     = averages.get("active_calories") or 0
    hrv      = averages.get("hrv_avg_ms")     or 0

    steps_color = "#60a5fa" if steps >= 10000 else "#e63946"
    hrv_color   = "#a78bfa"

    def card(icon, value, unit, label, color):
        return f"""<td align="center" style="padding:0 6px;">
          <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:14px 10px;text-align:center;min-width:90px;">
            <div style="font-size:20px;margin-bottom:4px;">{icon}</div>
            <div style="color:{color};font-size:20px;font-weight:800;font-family:Arial,sans-serif;line-height:1;">{value}</div>
            <div style="color:#555;font-size:9px;letter-spacing:1px;margin-top:3px;font-family:Arial,sans-serif;">{unit}</div>
            <div style="color:#444;font-size:9px;margin-top:2px;font-family:Arial,sans-serif;">{label}</div>
          </div>
        </td>"""

    steps_fmt = f"{int(steps):,}"
    cals_fmt  = f"{int(cals):,}"
    hrv_fmt   = f"{int(hrv)}" if hrv else "—"

    return f"""<table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:4px;">
      <tr>
        {card("👟", steps_fmt, "STEPS / DAY", "avg daily steps", steps_color)}
        {card("🔥", cals_fmt,  "CAL / DAY",   "active calories",  "#f59e0b")}
        {card("💜", hrv_fmt,   "ms HRV",      "avg HRV balance",  hrv_color)}
      </tr>
    </table>"""


def _scores_bar_chart_svg(daily: list) -> str:
    """Grouped bar chart: Activity, Readiness, Sleep per day."""
    W, H = 540, 140
    n    = len(daily)
    if not n:
        return ""

    PAD_L, PAD_R, PAD_T, PAD_B = 4, 4, 18, 26
    cw = W - PAD_L - PAD_R
    ch = H - PAD_T - PAD_B

    metrics = [
        ("activity_score",  "#60a5fa"),
        ("readiness_score", "#34d399"),
        ("sleep_score",     "#f59e0b"),
    ]
    n_m    = len(metrics)
    gw     = cw / n
    bar_w  = gw * 0.22
    gap    = gw * 0.04

    # Gridlines
    grid = ""
    for pct in [0.25, 0.5, 0.75, 1.0]:
        y   = PAD_T + ch * (1 - pct)
        lbl = int(pct * 100)
        grid += f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W-PAD_R}" y2="{y:.1f}" stroke="#222" stroke-width="1"/>'
        grid += f'<text x="{PAD_L+2}" y="{y-2:.1f}" fill="#444" font-size="7" font-family="Arial,sans-serif">{lbl}</text>'

    # Bars
    bars = ""
    for di, day in enumerate(daily):
        xg  = PAD_L + di * gw
        lbl = day["date"][-5:]  # MM-DD
        bars += f'<text x="{xg+gw/2:.1f}" y="{H-4}" text-anchor="middle" fill="#555" font-size="8" font-family="Arial,sans-serif">{lbl}</text>'
        for mi, (key, color) in enumerate(metrics):
            val  = day.get(key) or 0
            bh   = ch * (val / 100)
            x    = xg + gap + mi * (bar_w + gap)
            y    = PAD_T + ch - bh
            bars += f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{color}" rx="2" opacity="0.9"/>'

    # Legend
    legend = ""
    labels = [("ACTIVITY", "#60a5fa"), ("READINESS", "#34d399"), ("SLEEP", "#f59e0b")]
    for i, (lbl, col) in enumerate(labels):
        lx = PAD_L + i * 120
        legend += f'<rect x="{lx}" y="4" width="10" height="6" fill="{col}" rx="1"/>'
        legend += f'<text x="{lx+14}" y="11" fill="#777" font-size="8" font-family="Arial,sans-serif">{lbl}</text>'

    return f"""<svg width="100%" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="display:block;">
      {grid}{bars}{legend}
    </svg>"""


def _steps_chart_svg(daily: list, target: int = 10000) -> str:
    """Bar chart for daily steps with 10k target line."""
    W, H = 540, 110
    n    = len(daily)
    if not n:
        return ""

    PAD_L, PAD_R, PAD_T, PAD_B = 4, 4, 12, 26
    cw = W - PAD_L - PAD_R
    ch = H - PAD_T - PAD_B

    max_steps = max((d.get("steps") or 0 for d in daily), default=target)
    max_val   = max(max_steps * 1.15, target * 1.15)

    bar_w = cw / n * 0.65
    slot  = cw / n

    bars = ""
    for di, day in enumerate(daily):
        steps = day.get("steps") or 0
        pct   = min(steps / max_val, 1.0)
        bh    = ch * pct
        x     = PAD_L + di * slot + (slot - bar_w) / 2
        y     = PAD_T + ch - bh
        color = "#60a5fa" if steps >= target else "#e63946"
        lbl   = day["date"][-5:]
        s_lbl = f"{steps//1000}k" if steps >= 1000 else str(steps)
        bars += f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{color}" rx="3" opacity="0.9"/>'
        if steps > 0:
            bars += f'<text x="{x+bar_w/2:.1f}" y="{y-2:.1f}" text-anchor="middle" fill="#aaa" font-size="7" font-family="Arial,sans-serif">{s_lbl}</text>'
        bars += f'<text x="{x+bar_w/2:.1f}" y="{H-4}" text-anchor="middle" fill="#555" font-size="8" font-family="Arial,sans-serif">{lbl}</text>'

    target_y   = PAD_T + ch * (1 - min(target / max_val, 1.0))
    target_svg = (
        f'<line x1="{PAD_L}" y1="{target_y:.1f}" x2="{W-PAD_R}" y2="{target_y:.1f}" '
        f'stroke="#34d399" stroke-width="1.5" stroke-dasharray="4 3"/>'
        f'<text x="{W-PAD_R-2}" y="{target_y-3:.1f}" text-anchor="end" fill="#34d399" '
        f'font-size="8" font-family="Arial,sans-serif">10k goal</text>'
    )

    return f"""<svg width="100%" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="display:block;">
      {target_svg}{bars}
    </svg>"""


def _hrv_chart_svg(daily: list) -> str:
    """Area line chart for daily HRV trend."""
    W, H = 540, 90
    n    = len(daily)
    valid = [(i, d.get("hrv_avg_ms")) for i, d in enumerate(daily) if d.get("hrv_avg_ms") is not None]
    if len(valid) < 2:
        return ""

    PAD_L, PAD_R, PAD_T, PAD_B = 4, 4, 10, 22
    cw = W - PAD_L - PAD_R
    ch = H - PAD_T - PAD_B

    vals    = [v for _, v in valid]
    min_v   = min(vals) * 0.85
    max_v   = max(vals) * 1.15
    rng     = max_v - min_v or 1

    def pt(i, v):
        x = PAD_L + (i / (n - 1)) * cw
        y = PAD_T + ch * (1 - (v - min_v) / rng)
        return x, y

    pts = [pt(i, v) for i, v in valid]

    area = (
        f"M {pts[0][0]:.1f},{PAD_T+ch} "
        + " ".join(f"L {x:.1f},{y:.1f}" for x, y in pts)
        + f" L {pts[-1][0]:.1f},{PAD_T+ch} Z"
    )
    line = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)

    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#a78bfa"/>'
        for x, y in pts
    )

    # Value labels on dots
    dot_labels = "".join(
        f'<text x="{x:.1f}" y="{y-5:.1f}" text-anchor="middle" fill="#a78bfa" font-size="7" font-family="Arial,sans-serif">{int(v)}</text>'
        for (_, v), (x, y) in zip(valid, pts)
    )

    day_labels = "".join(
        f'<text x="{PAD_L + (i/(n-1))*cw:.1f}" y="{H-4}" text-anchor="middle" fill="#555" font-size="8" font-family="Arial,sans-serif">{d["date"][-5:]}</text>'
        for i, d in enumerate(daily)
    )

    return f"""<svg width="100%" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="display:block;">
      <defs>
        <linearGradient id="hg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#a78bfa" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="#a78bfa" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <path d="{area}" fill="url(#hg)"/>
      <path d="{line}" fill="none" stroke="#a78bfa" stroke-width="2" stroke-linejoin="round"/>
      {dots}{dot_labels}{day_labels}
    </svg>"""


# ── Email builder ─────────────────────────────────────────────────────────────

def _section(title: str, content: str, bg: str = "#111") -> str:
    return f"""
    <div style="background:{bg};padding:24px 32px 20px;border-bottom:1px solid #1e1e1e;">
      <div style="font-size:9px;letter-spacing:3px;color:#555;font-weight:700;
                  font-family:Arial,sans-serif;text-transform:uppercase;margin-bottom:14px;">
        {title}
      </div>
      {content}
    </div>"""


def _to_html(text: str) -> str:
    """Convert Claude's plain text recap into styled HTML paragraphs."""
    lines      = text.strip().split("\n")
    html_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            html_lines.append("<br>")
        elif any(line.startswith(h) for h in ("YOUR NUMBERS", "WHAT THE DATA", "YOUR FOCUS", "💡")):
            html_lines.append(
                f'<p style="font-weight:700;color:#e63946;margin:20px 0 6px;'
                f'font-family:Arial,sans-serif;font-size:11px;letter-spacing:2px;">{line}</p>'
            )
        elif len(line) > 1 and line[0].isdigit() and line[1] == ".":
            html_lines.append(
                f'<p style="margin:6px 0 6px 16px;font-family:Arial,sans-serif;'
                f'font-size:14px;color:#333;line-height:1.7;">• {line[2:].strip()}</p>'
            )
        else:
            html_lines.append(
                f'<p style="margin:6px 0;font-family:Arial,sans-serif;'
                f'font-size:14px;color:#333;line-height:1.7;">{line}</p>'
            )
    return "\n".join(html_lines)


def _build_email_html(recap_text: str, participant: dict, score_data: dict,
                      week_num: int, week_start: date, activity: dict = None) -> str:
    name      = participant["name"]
    averages  = (activity or {}).get("weekly_averages", {})
    daily     = (activity or {}).get("daily", [])

    rings_html  = _score_rings_html(averages)    if averages else ""
    stats_html  = _stat_cards_html(averages)     if averages else ""
    scores_svg  = _scores_bar_chart_svg(daily)   if daily    else ""
    steps_svg   = _steps_chart_svg(daily)        if daily    else ""
    hrv_svg     = _hrv_chart_svg(daily)          if daily    else ""
    recap_html  = _to_html(recap_text)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0a0a0a;">
<div style="max-width:640px;margin:0 auto;background:#0a0a0a;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#0a0a0a,#1a1a2e);border-bottom:3px solid #e63946;
              padding:32px 40px;text-align:center;">
    <div style="font-size:11px;letter-spacing:4px;color:#e63946;font-weight:700;
                font-family:Arial,sans-serif;margin-bottom:8px;">⚡ NOzempic</div>
    <div style="font-size:40px;font-weight:900;letter-spacing:5px;color:#fff;
                font-family:Arial,sans-serif;">WEEKLY RECAP</div>
    <div style="font-size:11px;color:#555;letter-spacing:2px;margin-top:8px;
                font-family:Arial,sans-serif;">Your body. Your data. No excuses.</div>
    <div style="display:inline-block;background:#e63946;color:#fff;font-size:10px;
                font-weight:700;letter-spacing:2px;padding:4px 14px;border-radius:20px;
                margin-top:12px;font-family:Arial,sans-serif;">
      Week {week_num} · {week_start.strftime('%b %d, %Y')}
    </div>
  </div>

  <!-- Weekly Score Rings -->
  {_section("Weekly Averages — Oura Ring", rings_html)}

  <!-- Stat Cards -->
  {_section("Activity Metrics", stats_html)}

  <!-- Daily Scores Chart -->
  {_section("Daily Scores — Activity · Readiness · Sleep",
    f'<div style="background:#0d0d0d;border-radius:8px;padding:12px 4px 4px;">{scores_svg}</div>')}

  <!-- Steps Chart -->
  {_section("Daily Steps",
    f'<div style="background:#0d0d0d;border-radius:8px;padding:12px 4px 4px;">{steps_svg}</div>'
    if steps_svg else '<p style="color:#555;font-size:12px;font-family:Arial,sans-serif;">No step data this week.</p>')}

  <!-- HRV Chart -->
  {_section("HRV Trend (ms)",
    f'<div style="background:#0d0d0d;border-radius:8px;padding:12px 4px 4px;">{hrv_svg}</div>'
    if hrv_svg else '<p style="color:#555;font-size:12px;font-family:Arial,sans-serif;">No HRV data this week.</p>')}

  <!-- Body Composition -->
  <div style="background:#111;padding:24px 32px 20px;border-bottom:1px solid #1e1e1e;">
    <div style="font-size:9px;letter-spacing:3px;color:#555;font-weight:700;
                font-family:Arial,sans-serif;text-transform:uppercase;margin-bottom:14px;">
      Composite Scores
    </div>
    <table cellpadding="0" cellspacing="0" border="0" width="100%">
      <tr>
        <td style="padding:0 6px 0 0;">
          <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:10px;
                      padding:12px 16px;text-align:center;">
            <div style="color:#e63946;font-size:24px;font-weight:800;font-family:Arial,sans-serif;">
              {score_data['weekly_score']}
            </div>
            <div style="color:#555;font-size:9px;letter-spacing:1px;font-family:Arial,sans-serif;">
              WEEKLY SCORE
            </div>
          </div>
        </td>
        <td style="padding:0 6px;">
          <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:10px;
                      padding:12px 16px;text-align:center;">
            <div style="color:#60a5fa;font-size:24px;font-weight:800;font-family:Arial,sans-serif;">
              {score_data['activity_score']}
            </div>
            <div style="color:#555;font-size:9px;letter-spacing:1px;font-family:Arial,sans-serif;">
              ACTIVITY
            </div>
          </div>
        </td>
        <td style="padding:0 0 0 6px;">
          <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:10px;
                      padding:12px 16px;text-align:center;">
            <div style="color:#34d399;font-size:24px;font-weight:800;font-family:Arial,sans-serif;">
              {score_data['improvement_score']}
            </div>
            <div style="color:#555;font-size:9px;letter-spacing:1px;font-family:Arial,sans-serif;">
              IMPROVEMENT
            </div>
          </div>
        </td>
      </tr>
    </table>
  </div>

  <!-- Coach's Recap -->
  <div style="background:#fafafa;padding:32px 32px 24px;">
    <div style="font-size:9px;letter-spacing:3px;color:#bbb;font-weight:700;
                font-family:Arial,sans-serif;text-transform:uppercase;margin-bottom:18px;
                background:#e63946;padding:6px 12px;border-radius:4px;display:inline-block;">
      Coach's Recap · {name}
    </div>
    {recap_html}
  </div>

  <!-- Footer -->
  <div style="background:#0a0a0a;padding:20px 32px;text-align:center;border-top:1px solid #1a1a1a;">
    <p style="font-size:12px;color:#e63946;font-weight:700;letter-spacing:2px;
              font-family:Arial,sans-serif;margin:0 0 6px;">
      No Shortcuts. No Ozempic. Just Work.
    </p>
    <p style="font-size:11px;color:#333;font-family:Arial,sans-serif;margin:0;">
      NOzempic Week {week_num} · {week_start.strftime('%B %d, %Y')}
    </p>
  </div>

</div>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

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
                    f"NOzempic: Oura fetch failed for {p['name']}",
                    f"<p>Data fetch failed for <strong>{p['name']}</strong>:</p><pre>{e}</pre>",
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

    # ── Step 3: Score ─────────────────────────────────────────────────────────
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

    # ── Step 4: Generate + send ───────────────────────────────────────────────
    print("\n✍️  Generating coaching recaps...")
    from engine.generator import generate_weekly_recap
    from mailer.sender import send_weekly_email

    sent_count = 0
    for p in participants:
        pid  = p["id"]
        name = p["name"]

        recap_text = generate_weekly_recap(
            participant     = p,
            activity        = all_activity[pid],
            renpho_current  = all_renpho_current[pid],
            renpho_previous = all_renpho_previous.get(pid),
            score_data      = scores[pid],
            week_num        = week_num,
        )
        print(f"  ✅ Recap generated for {name}")

        html = _build_email_html(
            recap_text, p, scores[pid], week_num, week_start,
            activity=all_activity[pid],
        )

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


if __name__ == "__main__":
    run()
