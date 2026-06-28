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
  7. Send email
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
import re
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


# ── Recap parser ──────────────────────────────────────────────────────────────

def _parse_recap(text: str) -> dict:
    """
    Parse the structured Claude output into named sections.
    Returns dict with keys: analysis, focuses (list of {head, body}), fact, closing.
    """
    sections = {
        "analysis": "",
        "focuses": [],
        "fact": "",
        "closing": "",
    }

    lines = [l.strip() for l in text.strip().splitlines()]
    i = 0
    while i < len(lines):
        line = lines[i]

        if line == "WHAT_THE_DATA_SAYS":
            i += 1
            body = []
            while i < len(lines) and not lines[i].startswith("FOCUS_") and lines[i] not in ("FACT", "CLOSING"):
                if lines[i]:
                    body.append(lines[i])
                i += 1
            sections["analysis"] = " ".join(body)

        elif line.startswith("FOCUS_"):
            i += 1
            head = lines[i] if i < len(lines) else ""
            i += 1
            body_parts = []
            while i < len(lines) and not lines[i].startswith("FOCUS_") and lines[i] not in ("FACT", "CLOSING", "WHAT_THE_DATA_SAYS"):
                if lines[i]:
                    body_parts.append(lines[i])
                i += 1
            sections["focuses"].append({"head": head, "body": " ".join(body_parts)})

        elif line == "FACT":
            i += 1
            parts = []
            while i < len(lines) and lines[i] not in ("CLOSING",):
                if lines[i]:
                    parts.append(lines[i])
                i += 1
            sections["fact"] = " ".join(parts)

        elif line == "CLOSING":
            i += 1
            parts = []
            while i < len(lines):
                if lines[i]:
                    parts.append(lines[i])
                i += 1
            sections["closing"] = " ".join(parts)

        else:
            i += 1

    return sections


# ── SVG chart builders ────────────────────────────────────────────────────────

def _ring_svg(score: float, color: str, label: str) -> str:
    v    = round(score or 0)
    r    = 36
    circ = 2 * math.pi * r
    dash = circ * (v / 100)
    gap  = circ - dash
    return f"""<td align="center" style="padding:0 10px;">
      <svg width="96" height="96" viewBox="0 0 96 96" xmlns="http://www.w3.org/2000/svg">
        <circle cx="48" cy="48" r="{r}" fill="none" stroke="#1e1e1e" stroke-width="9"/>
        <circle cx="48" cy="48" r="{r}" fill="none" stroke="{color}" stroke-width="9"
          stroke-dasharray="{dash:.1f} {gap:.1f}" stroke-linecap="round"
          transform="rotate(-90 48 48)"/>
        <text x="48" y="44" text-anchor="middle" fill="#ffffff"
          font-size="20" font-weight="700" font-family="Arial,sans-serif">{v}</text>
        <text x="48" y="58" text-anchor="middle" fill="#555"
          font-size="8" font-family="Arial,sans-serif" letter-spacing="1">{label}</text>
      </svg>
    </td>"""


def _scores_chart_svg(daily: list) -> str:
    W, H = 520, 110
    n = len(daily)
    if not n:
        return ""
    PL, PR, PT, PB = 4, 4, 16, 24
    cw, ch = W - PL - PR, H - PT - PB
    metrics = [("activity_score", "#60a5fa"), ("readiness_score", "#34d399"), ("sleep_score", "#f59e0b")]
    n_m = len(metrics)
    gw  = cw / n
    bw  = gw * 0.21
    gap = gw * 0.04

    grid = ""
    for pct in [0.25, 0.5, 0.75, 1.0]:
        y = PT + ch * (1 - pct)
        grid += f'<line x1="{PL}" y1="{y:.1f}" x2="{W-PR}" y2="{y:.1f}" stroke="#1a1a1a" stroke-width="1"/>'
        grid += f'<text x="{PL+2}" y="{y-2:.1f}" fill="#333" font-size="7" font-family="Arial,sans-serif">{int(pct*100)}</text>'

    bars = ""
    for di, day in enumerate(daily):
        xg  = PL + di * gw
        lbl = day["date"][-5:]
        bars += f'<text x="{xg+gw/2:.1f}" y="{H-4}" text-anchor="middle" fill="#444" font-size="8" font-family="Arial,sans-serif">{lbl}</text>'
        for mi, (key, color) in enumerate(metrics):
            val = day.get(key) or 0
            bh  = ch * (val / 100)
            x   = xg + gap + mi * (bw + gap)
            y   = PT + ch - bh
            bars += f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{color}" rx="2" opacity="0.9"/>'

    legend = ""
    for i, (lbl, col) in enumerate([("ACTIVITY", "#60a5fa"), ("READINESS", "#34d399"), ("SLEEP", "#f59e0b")]):
        lx = PL + i * 120
        legend += f'<rect x="{lx}" y="3" width="8" height="6" fill="{col}" rx="1"/>'
        legend += f'<text x="{lx+12}" y="9" fill="#666" font-size="7" font-family="Arial,sans-serif">{lbl}</text>'

    return f'<svg width="100%" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="display:block;">{grid}{bars}{legend}</svg>'


def _steps_chart_svg(daily: list, target: int = 10000) -> str:
    W, H = 520, 100
    n    = len(daily)
    if not n:
        return ""
    PL, PR, PT, PB = 4, 4, 12, 22
    cw, ch = W - PL - PR, H - PT - PB

    max_steps = max((d.get("steps") or 0 for d in daily), default=target)
    max_val   = max(max_steps * 1.15, target * 1.15)
    slot      = cw / n
    bw        = slot * 0.65

    bars = ""
    for di, day in enumerate(daily):
        steps = day.get("steps") or 0
        pct   = min(steps / max_val, 1.0)
        bh    = ch * pct
        x     = PL + di * slot + (slot - bw) / 2
        y     = PT + ch - bh
        color = "#60a5fa" if steps >= target else "#e63946"
        lbl   = day["date"][-5:]
        s_lbl = f"{steps//1000}k" if steps >= 1000 else str(steps)
        bars += f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{color}" rx="3" opacity="0.9"/>'
        if steps > 0:
            bars += f'<text x="{x+bw/2:.1f}" y="{y-2:.1f}" text-anchor="middle" fill="#aaa" font-size="7" font-family="Arial,sans-serif">{s_lbl}</text>'
        bars += f'<text x="{x+bw/2:.1f}" y="{H-4}" text-anchor="middle" fill="#444" font-size="8" font-family="Arial,sans-serif">{lbl}</text>'

    ty = PT + ch * (1 - min(target / max_val, 1.0))
    target_svg = (
        f'<line x1="{PL}" y1="{ty:.1f}" x2="{W-PR}" y2="{ty:.1f}" stroke="#34d399" stroke-width="1.5" stroke-dasharray="5 3"/>'
        f'<text x="{W-PR-2}" y="{ty-3:.1f}" text-anchor="end" fill="#34d399" font-size="7" font-family="Arial,sans-serif">10k goal</text>'
    )
    return f'<svg width="100%" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="display:block;">{target_svg}{bars}</svg>'


def _hrv_chart_svg(daily: list) -> str:
    W, H = 520, 80
    n    = len(daily)
    valid = [(i, d.get("hrv_avg_ms")) for i, d in enumerate(daily) if d.get("hrv_avg_ms") is not None]
    if len(valid) < 2:
        return ""
    PL, PR, PT, PB = 4, 4, 10, 20
    cw, ch = W - PL - PR, H - PT - PB

    vals  = [v for _, v in valid]
    min_v = min(vals) * 0.85
    max_v = max(vals) * 1.15
    rng   = max_v - min_v or 1

    def pt(i, v):
        x = PL + (i / (n - 1)) * cw
        y = PT + ch * (1 - (v - min_v) / rng)
        return x, y

    pts  = [pt(i, v) for i, v in valid]
    area = (f"M {pts[0][0]:.1f},{PT+ch} " +
            " ".join(f"L {x:.1f},{y:.1f}" for x, y in pts) +
            f" L {pts[-1][0]:.1f},{PT+ch} Z")
    line = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#a78bfa"/>' for x, y in pts)
    vlabels = "".join(
        f'<text x="{x:.1f}" y="{y-5:.1f}" text-anchor="middle" fill="#a78bfa" font-size="7" font-family="Arial,sans-serif">{int(v)}</text>'
        for (_, v), (x, y) in zip(valid, pts)
    )
    dlabels = "".join(
        f'<text x="{PL+(i/(n-1))*cw:.1f}" y="{H-2}" text-anchor="middle" fill="#444" font-size="8" font-family="Arial,sans-serif">{d["date"][-5:]}</text>'
        for i, d in enumerate(daily)
    )
    return f"""<svg width="100%" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="display:block;">
      <defs><linearGradient id="hg" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#a78bfa" stop-opacity="0.25"/>
        <stop offset="100%" stop-color="#a78bfa" stop-opacity="0"/>
      </linearGradient></defs>
      <path d="{area}" fill="url(#hg)"/>
      <path d="{line}" fill="none" stroke="#a78bfa" stroke-width="2" stroke-linejoin="round"/>
      {dots}{vlabels}{dlabels}
    </svg>"""


# ── Stat card helpers ─────────────────────────────────────────────────────────

def _stat_card(value: str, unit: str, delta: str, delta_color: str) -> str:
    return f"""<div style="background:#141414;border:1px solid #222;border-radius:8px;padding:14px 10px;text-align:center;">
      <div style="font-size:22px;font-weight:800;color:#fff;line-height:1;font-family:Arial,sans-serif;">{value}</div>
      <div style="font-size:9px;letter-spacing:1px;color:#555;margin-top:3px;font-family:Arial,sans-serif;">{unit}</div>
      <div style="font-size:9px;color:{delta_color};margin-top:4px;font-family:Arial,sans-serif;">{delta}</div>
    </div>"""


def _chart_section(title: str, svg: str, bg: str = "#0a0a0a") -> str:
    return f"""<div style="background:#111;padding:20px 24px;border-bottom:1px solid #1c1c1c;">
      <div style="font-size:9px;letter-spacing:2.5px;color:#555;font-weight:700;font-family:Arial,sans-serif;text-transform:uppercase;margin-bottom:10px;">{title}</div>
      <div style="background:{bg};border-radius:6px;padding:14px 6px 6px;">{svg}</div>
    </div>"""


# ── Main email builder ────────────────────────────────────────────────────────

def _build_email_html(recap_text: str, participant: dict, score_data: dict,
                      week_num: int, week_start: date, activity: dict = None,
                      renpho: dict = None) -> str:

    name     = participant["name"]
    averages = (activity or {}).get("weekly_averages", {})
    daily    = (activity or {}).get("daily", [])

    # ── Score rings ───────────────────────────────────────────────────────────
    act_score  = averages.get("activity_score")  or 0
    read_score = averages.get("readiness_score") or 0
    slp_score  = averages.get("sleep_score")     or 0
    hrv_val    = averages.get("hrv_avg_ms")      or 0

    rings_html = f"""<table cellpadding="0" cellspacing="0" border="0" width="100%">
      <tr>
        {_ring_svg(act_score,  "#60a5fa", "ACTIVITY")}
        {_ring_svg(read_score, "#34d399", "READINESS")}
        {_ring_svg(slp_score,  "#f59e0b", "SLEEP")}
        {_ring_svg(min(hrv_val / 80 * 100, 100), "#a78bfa", "HRV")}
      </tr>
    </table>"""

    # ── Progress bar ──────────────────────────────────────────────────────────
    start_w   = participant.get("starting_weight_lb")
    target_w  = participant.get("target_weight_lb")
    curr_w    = (renpho or {}).get("weight_lb") or start_w
    prog_pct  = 0
    lbs_left  = ""
    if start_w and target_w and curr_w:
        total    = start_w - target_w
        lost     = start_w - curr_w
        prog_pct = max(0, min(100, round(lost / total * 100, 1))) if total > 0 else 0
        lbs_left = f"{round(curr_w - target_w, 1)} lb to milestone"

    prog_bar = f"""<div style="margin-top:16px;">
      <div style="display:flex;justify-content:space-between;font-size:9px;color:#555;font-family:Arial,sans-serif;margin-bottom:5px;">
        <span>Weight goal progress ({int(curr_w or start_w or 0)} → {int(target_w or 0)} lb)</span>
        <span style="color:#e63946;font-weight:700;">{prog_pct}%</span>
      </div>
      <div style="height:6px;background:#1e1e1e;border-radius:3px;overflow:hidden;">
        <div style="height:100%;width:{prog_pct}%;background:#e63946;border-radius:3px;"></div>
      </div>
      <div style="font-size:9px;color:#333;margin-top:4px;font-family:Arial,sans-serif;">Week {week_num} · {lbs_left}</div>
    </div>"""

    # ── Stat cards ────────────────────────────────────────────────────────────
    steps_avg = averages.get("steps") or 0
    cals_avg  = averages.get("active_calories") or 0
    visc_fat  = (renpho or {}).get("visceral_fat")
    muscle_lb = (renpho or {}).get("muscle_mass_lb")

    steps_delta_color = "#60a5fa" if steps_avg >= 10000 else "#e63946"
    steps_delta       = f"↑ goal hit" if steps_avg >= 10000 else f"↓ {int(10000 - steps_avg):,} to goal"
    visc_color        = "#e63946" if visc_fat and visc_fat > 10 else "#34d399"
    visc_delta        = "elevated" if visc_fat and visc_fat > 10 else "normal"

    stat_row = f"""<table cellpadding="0" cellspacing="0" border="0" width="100%">
      <tr>
        <td style="padding:0 5px 0 0;">{_stat_card(f"{int(steps_avg):,}", "STEPS / DAY", steps_delta, steps_delta_color)}</td>
        <td style="padding:0 5px;">{_stat_card(f"{int(cals_avg):,}", "CAL / DAY", "active burn", "#f59e0b")}</td>
        <td style="padding:0 5px;">{_stat_card(f"{int(hrv_val)}", "HRV (ms)", "avg balance", "#a78bfa")}</td>
        <td style="padding:0 0 0 5px;">{_stat_card(str(visc_fat) if visc_fat else "—", "VISCERAL FAT", visc_delta, visc_color)}</td>
      </tr>
    </table>"""

    # ── Charts ────────────────────────────────────────────────────────────────
    scores_svg = _scores_chart_svg(daily)
    steps_svg  = _steps_chart_svg(daily)
    hrv_svg    = _hrv_chart_svg(daily)

    # ── Parse Claude's structured recap ──────────────────────────────────────
    recap = _parse_recap(recap_text)

    focuses_html = ""
    for i, f in enumerate(recap["focuses"][:3], 1):
        focuses_html += f"""<div style="display:flex;gap:12px;align-items:flex-start;margin-bottom:14px;">
          <div style="min-width:24px;height:24px;border-radius:50%;background:#e63946;color:#fff;
                      font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;
                      font-family:Arial,sans-serif;flex-shrink:0;margin-top:1px;">{i}</div>
          <div>
            <div style="font-size:13px;font-weight:700;color:#111;font-family:Arial,sans-serif;margin-bottom:3px;">{f['head']}</div>
            <div style="font-size:13px;color:#444;font-family:Arial,sans-serif;line-height:1.55;">{f['body']}</div>
          </div>
        </div>"""

    fact_html = ""
    if recap["fact"]:
        fact_html = f"""<div style="background:#f0f0f0;border-left:3px solid #e63946;padding:12px 16px;
                            border-radius:0 6px 6px 0;margin-top:18px;">
          <div style="font-size:9px;letter-spacing:1.5px;color:#e63946;font-weight:700;
                      font-family:Arial,sans-serif;margin-bottom:5px;">FACT OF THE WEEK</div>
          <div style="font-size:13px;color:#333;font-family:Arial,sans-serif;line-height:1.55;">{recap['fact']}</div>
        </div>"""

    closing_html = ""
    if recap["closing"]:
        closing_html = f'<div style="font-size:12px;color:#999;margin-top:16px;font-style:italic;font-family:Arial,sans-serif;">{recap["closing"]} — Coach NOzempic</div>'

    analysis_html = ""
    if recap["analysis"]:
        analysis_html = f'<div style="font-size:13px;color:#555;line-height:1.6;font-family:Arial,sans-serif;margin-bottom:20px;padding-bottom:18px;border-bottom:1px solid #e8e8e8;">{recap["analysis"]}</div>'

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0a0a0a;">
<div style="max-width:620px;margin:0 auto;background:#0a0a0a;">

  <!-- Header -->
  <div style="background:#0a0a0a;padding:28px 28px 22px;text-align:center;border-bottom:3px solid #e63946;">
    <div style="font-size:10px;letter-spacing:4px;color:#e63946;font-weight:700;font-family:Arial,sans-serif;margin-bottom:6px;">⚡ NOZEMPIC</div>
    <div style="font-size:34px;font-weight:900;letter-spacing:4px;color:#fff;font-family:Arial,sans-serif;line-height:1;">WEEK {week_num}</div>
    <div style="font-size:10px;color:#444;letter-spacing:2px;margin-top:6px;font-family:Arial,sans-serif;">{week_start.strftime('%b %d').upper()} – {(week_start + timedelta(days=6)).strftime('%b %d, %Y').upper()} · YOUR RECAP</div>
  </div>

  <!-- Score rings + progress -->
  <div style="background:#0f0f0f;padding:22px 24px 18px;border-bottom:1px solid #1c1c1c;">
    <div style="font-size:9px;letter-spacing:2.5px;color:#555;font-weight:700;font-family:Arial,sans-serif;text-transform:uppercase;margin-bottom:14px;">Weekly averages — Oura Ring</div>
    {rings_html}
    {prog_bar}
  </div>

  <!-- Stat cards -->
  <div style="background:#111;padding:20px 24px;border-bottom:1px solid #1c1c1c;">
    <div style="font-size:9px;letter-spacing:2.5px;color:#555;font-weight:700;font-family:Arial,sans-serif;text-transform:uppercase;margin-bottom:12px;">Activity metrics</div>
    {stat_row}
  </div>

  <!-- Daily scores chart -->
  {_chart_section("Daily scores — Activity · Readiness · Sleep", scores_svg) if scores_svg else ""}

  <!-- Steps chart -->
  {_chart_section("Daily steps vs 10k goal", steps_svg) if steps_svg else ""}

  <!-- HRV chart -->
  {_chart_section("HRV trend (ms)", hrv_svg) if hrv_svg else ""}

  <!-- Coach's recap -->
  <div style="background:#f9f9f9;padding:26px 28px 24px;">
    <div style="font-size:9px;letter-spacing:2px;color:#fff;font-weight:700;font-family:Arial,sans-serif;
                background:#e63946;padding:5px 12px;border-radius:4px;display:inline-block;margin-bottom:18px;">
      COACH'S TAKE · {name.upper()}
    </div>
    {analysis_html}
    {focuses_html}
    {fact_html}
    {closing_html}
  </div>

  <!-- Footer -->
  <div style="background:#0a0a0a;padding:16px 28px;text-align:center;border-top:3px solid #1c1c1c;">
    <div style="font-size:10px;color:#e63946;font-weight:700;letter-spacing:2px;font-family:Arial,sans-serif;">NO SHORTCUTS. NO OZEMPIC. JUST WORK.</div>
    <div style="font-size:10px;color:#333;margin-top:4px;font-family:Arial,sans-serif;">NOzempic · Week {week_num} · {week_start.strftime('%B %d, %Y')}</div>
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

    # ── Pull activity data ────────────────────────────────────────────────────
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
            raise

    # ── Load Renpho scans ─────────────────────────────────────────────────────
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

    # ── Score ─────────────────────────────────────────────────────────────────
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

    # ── Generate + send ───────────────────────────────────────────────────────
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
            activity = all_activity[pid],
            renpho   = all_renpho_current[pid],
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

    if sent_count:
        config["week_number"] = week_num + 1
        save_config(config)
        print(f"\n  📌 Week counter updated to {week_num + 1}")

    print(f"\n✅ NOzempic Week {week_num} complete!\n")


if __name__ == "__main__":
    run()
