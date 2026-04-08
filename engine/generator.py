"""
engine/generator.py
───────────────────
Uses Claude to generate both email segments from the week's data:
  - Segment 1: The group dispatch (roast + leaderboard)
  - Segment 2: The personal brief (per-participant, private)

Returns rendered HTML strings ready for SendGrid.
"""

import os
import json
from datetime import date
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


# ── Prompt builders ───────────────────────────────────────────────────────────

def _segment1_prompt(ranked: list[dict], all_activity: dict, all_renpho: dict, week_num: int) -> str:
    return f"""You are the NOzempic coach — brutally honest, wickedly funny, but genuinely invested in these people getting healthier. Think Bill Parcells crossed with a sports statistician who reads too much.

You're writing SEGMENT 1 of the weekly NOzempic email: the GROUP DISPATCH. This goes to everyone.

RULES:
- Never reveal absolute weight or body fat numbers — only deltas (changes)
- Use the data to craft specific, earned roasts — not generic insults
- The winner gets genuine, brief praise. The loser gets the roast.
- End with ONE fun health fact directly tied to something in this week's data
- Tone: locker room energy. Sharp. Funny. Mean but loving.
- Length: ~300 words max

THIS WEEK'S DATA:
Week number: {week_num}
Date: {date.today().strftime('%B %d, %Y')}

RANKINGS:
{json.dumps(ranked, indent=2)}

ACTIVITY DATA:
{json.dumps(all_activity, indent=2)}

BODY COMPOSITION DELTAS:
{json.dumps({pid: {
    "weight_delta_lb": data.get("components", {}).get("weight_delta_lb"),
    "body_fat_delta_pct": data.get("components", {}).get("body_fat_delta_pct"),
    "body_sore_score": data.get("body_sore_score"),
    "activity_score": data.get("activity_score"),
    "device": data.get("components", {}).get("device"),
    "hrv_avg_ms": data.get("components", {}).get("hrv_avg_ms"),
} for pid, data in {r["participant_id"]: r for r in ranked}.items()}, indent=2)}

Write ONLY the body text of the email — no subject line, no HTML tags, just the raw content in this structure:

🏆 WINNER: [Name] — [one punchy sentence about why they won]

LEADERBOARD
[simple text leaderboard: rank, name, score, gap]

THIS WEEK'S MOVEMENT
[2-3 sentences on the key deltas — who moved the needle, who didn't]

THE ROAST
[2-3 paragraphs of specific, data-backed commentary on the loser(s). Funny and true.]

💡 FACT OF THE WEEK
[One fascinating health fact tied directly to something in this week's data]

See you next Thursday.
— Coach NOzempic"""


def _segment2_prompt(participant: dict, activity: dict, renpho_current: dict,
                     renpho_previous: dict | None, score_data: dict) -> str:
    name = participant["name"]
    goals = ", ".join(participant.get("goals", []))
    target = participant.get("target_weight_lb")
    current_weight = renpho_current.get("weight_lb")
    start_weight = participant.get("starting_weight_lb")

    ultimate = participant.get("ultimate_weight_lb")

    progress_pct = None
    if current_weight and start_weight and target:
        total_to_lose = start_weight - target
        lost_so_far = start_weight - current_weight
        progress_pct = round((lost_so_far / total_to_lose) * 100, 1) if total_to_lose > 0 else 0

    return f"""You are the NOzempic coach writing SEGMENT 2 of the weekly email: the PERSONAL BRIEF for {name}.

This section is PRIVATE — only {name} sees it. Drop the locker room act. Same coach, different register: direct, honest, genuinely helpful. Still firm. Still no nonsense. But you're in their corner here.

RULES:
- Use REAL absolute numbers here (weight, body fat %, visceral fat, etc.) — this is private
- Give 2-3 SPECIFIC, actionable tips derived from their actual data — not generic advice
- Reference their stated goals: {goals}
- If metabolic age > actual age, call it out and explain what drives it
- If visceral fat is elevated, make it the top priority
- Protect muscle mass if it's above standard — don't let them crash diet
- WEIGHT GOAL FRAMING: The target of {target} lb is a realistic FIRST MILESTONE (roughly 6 months out at a healthy pace). Frame it as a meaningful first checkpoint, not the finish line. The Renpho "optimal" figure of {ultimate} lb is a long-term benchmark — mention it as context but do NOT present it as an imminent goal. Sustainable weight loss is 1–1.5 lb/week; anything faster risks muscle loss and rebound. Coach accordingly.
- End with one encouraging but honest sentence about their trajectory
- Length: ~250 words

{name.upper()}'S DATA THIS WEEK:

Goals: {goals}
Starting weight: {start_weight} lb
Current weight: {current_weight} lb
First milestone target: {target} lb (realistic ~6-month goal at 1–1.5 lb/week)
Long-term Renpho benchmark: {ultimate} lb (multi-year horizon — not a near-term coaching target)
Progress to first milestone: {progress_pct}% complete

Body Composition (Renpho):
{json.dumps(renpho_current, indent=2)}

Week-over-week changes:
- Weight delta: {score_data.get("components", {}).get("weight_delta_lb")} lb
- Body fat delta: {score_data.get("components", {}).get("body_fat_delta_pct")}%

Activity ({activity.get("device", "unknown").upper()}):
{json.dumps(activity.get("weekly_averages", {}), indent=2)}

Weekly scores:
- Body Sore Score: {score_data.get("body_sore_score")}
- Activity Score: {score_data.get("activity_score")}
- Improvement Score: {score_data.get("improvement_score")}
- WEEKLY TOTAL: {score_data.get("weekly_score")}

Write ONLY the body text in this structure:

Hey {name} — here's your full picture.

YOUR NUMBERS THIS WEEK
[Key metrics listed cleanly — weight, BF%, visceral fat, metabolic age, BMR, muscle mass]

WHAT THE DATA SAYS
[2-3 sentences of honest analysis — what's working, what isn't, what stands out]

YOUR FOCUS THIS WEEK
1. [Specific tip 1 — tied to data]
2. [Specific tip 2 — tied to data]
3. [Specific tip 3 — tied to data]

[One closing sentence: honest trajectory assessment]

— Coach NOzempic"""


# ── Main generator ────────────────────────────────────────────────────────────

def generate_segment1(ranked: list[dict], all_activity: dict,
                      all_renpho: dict, week_num: int) -> str:
    """Generate the group dispatch copy via Claude."""
    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": _segment1_prompt(ranked, all_activity, all_renpho, week_num)}]
    )
    return msg.content[0].text


def generate_segment2(participant: dict, activity: dict, renpho_current: dict,
                      renpho_previous: dict | None, score_data: dict) -> str:
    """Generate the personal brief for one participant via Claude."""
    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": _segment2_prompt(
            participant, activity, renpho_current, renpho_previous, score_data
        )}]
    )
    return msg.content[0].text


def build_email_html(segment1_text: str, segment2_text: str,
                     ranked: list[dict], all_activity: dict,
                     participant: dict, renpho: dict, week_num: int) -> str:
    """
    Wrap Claude's generated copy inside the styled HTML email template.
    Injects the real chart data for the SVG visualizations.
    """
    from email.templates.renderer import render_email
    return render_email(
        segment1_text=segment1_text,
        segment2_text=segment2_text,
        ranked=ranked,
        all_activity=all_activity,
        participant=participant,
        renpho=renpho,
        week_num=week_num,
    )
