"""
engine/generator.py
───────────────────
Uses Claude to generate each participant's weekly coaching recap email.
Brutally honest, data-driven, genuinely invested — no leaderboards, no comparisons.
"""

import os
import json
from datetime import date
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def _recap_prompt(participant: dict, activity: dict, renpho_current: dict,
                  renpho_previous: dict | None, score_data: dict, week_num: int) -> str:
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

    return f"""You are the NOzempic coach writing {name}'s weekly recap. Week {week_num}. Date: {date.today().strftime('%B %d, %Y')}.

You're in {name}'s corner — direct, honest, genuinely helpful. Still firm. Still no nonsense. Same energy as a great trainer who tells you the truth because they actually give a damn.

RULES:
- Use REAL absolute numbers (weight, body fat %, visceral fat, metabolic age, BMR, muscle mass) — this is their private email
- Give 2-3 SPECIFIC, actionable tips derived from their actual data — not generic advice
- Reference their stated goals: {goals}
- If metabolic age > actual age, call it out and explain what drives it
- If visceral fat is elevated, make it the top priority
- Protect muscle mass if it's above standard — don't let them crash diet
- WEIGHT GOAL FRAMING: The target of {target} lb is a realistic FIRST MILESTONE (~6 months at a healthy pace). Frame it as a meaningful checkpoint, not the finish line. The Renpho "optimal" figure of {ultimate} lb is a long-term benchmark — mention as context only. Sustainable loss is 1–1.5 lb/week; anything faster risks muscle loss and rebound. Coach accordingly.
- End with one encouraging but honest sentence about their trajectory
- Tone: locker room energy adapted for a 1:1. Sharp. Direct. Mean but loving.
- Length: ~300 words

{name.upper()}'S DATA — WEEK {week_num}:

Goals: {goals}
Starting weight: {start_weight} lb
Current weight: {current_weight} lb
First milestone target: {target} lb (~6-month goal at 1–1.5 lb/week)
Long-term Renpho benchmark: {ultimate} lb (multi-year horizon)
Progress to first milestone: {progress_pct}% complete

Body Composition (Renpho):
{json.dumps(renpho_current, indent=2)}

Week-over-week changes:
- Weight delta: {score_data.get("components", {}).get("weight_delta_lb")} lb
- Body fat delta: {score_data.get("components", {}).get("body_fat_delta_pct")}%

Activity ({activity.get("device", "unknown").upper()}):
{json.dumps(activity.get("weekly_averages", {}), indent=2)}

Weekly scores:
- Body Composition Score: {score_data.get("body_sore_score")}
- Activity Score: {score_data.get("activity_score")}
- Improvement Score: {score_data.get("improvement_score")}
- WEEKLY TOTAL: {score_data.get("weekly_score")}

The email already shows charts for all scores, steps, and HRV — do NOT describe those numbers in prose. The visual data is handled. Your job is the coaching layer on top.

Write ONLY the following, with no extra text, headers, or formatting outside this structure:

WHAT_THE_DATA_SAYS
[2-3 sentences MAX. Honest, specific analysis of the week — what the numbers actually mean for {name}'s goals. Reference visceral fat, recovery, or trend if relevant. No generic advice.]

FOCUS_1
[Headline: 4-6 words, punchy]
[Body: 1-2 sentences. Specific, data-tied action. No fluff.]

FOCUS_2
[Headline: 4-6 words, punchy]
[Body: 1-2 sentences. Specific, data-tied action. No fluff.]

FOCUS_3
[Headline: 4-6 words, punchy]
[Body: 1-2 sentences. Specific, data-tied action. No fluff.]

FACT
[One sentence. A genuinely interesting health fact tied directly to something in this week's data — HRV, visceral fat, sleep, steps. Not generic.]

CLOSING
[One sentence. Honest trajectory read. Direct. No cheerleading.]"""


def generate_weekly_recap(participant: dict, activity: dict, renpho_current: dict,
                          renpho_previous: dict | None, score_data: dict, week_num: int) -> str:
    """Generate the weekly coaching recap for one participant via Claude."""
    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": _recap_prompt(
            participant, activity, renpho_current, renpho_previous, score_data, week_num
        )}]
    )
    return msg.content[0].text
