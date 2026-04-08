"""
engine/scoring.py
─────────────────
Normalizes data from Oura, WHOOP, and Renpho into a single weekly
score per participant, then ranks the group.

Scoring formula (configurable in config.json):
    Body Sore Score  → 40%  (Renpho's own 0-100 composite)
    Activity Score   → 30%  (Oura or WHOOP normalized composite)
    Weekly Improvement→ 30% (body fat % delta + weight delta)
"""

import json
from pathlib import Path
from typing import Optional


CONFIG_PATH = Path(__file__).parent.parent / "config.json"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ── Improvement scoring ───────────────────────────────────────────────────────

def score_improvement(
    weight_delta_lb: Optional[float],
    body_fat_delta_pct: Optional[float],
) -> float:
    """
    Convert weekly body composition deltas into a 0-100 improvement score.

    Benchmarks (achievable but challenging weekly targets):
        Weight loss ≥ 2.0 lb  → full weight score  (50 pts)
        BF% drop   ≥ 0.5%    → full BF score       (50 pts)
    """
    weight_score = 0.0
    bf_score     = 0.0

    if weight_delta_lb is not None:
        # Negative delta = lost weight = good. Cap at ±3 lb.
        clamped = max(-3.0, min(3.0, weight_delta_lb))
        # Map [-3, +3] → [50, -50], then shift to [0, 100]
        weight_score = max(0.0, min(50.0, (-clamped / 3.0) * 50.0 + 25.0))

    if body_fat_delta_pct is not None:
        # Negative delta = dropped BF% = good. Cap at ±1.0%.
        clamped = max(-1.0, min(1.0, body_fat_delta_pct))
        bf_score = max(0.0, min(50.0, (-clamped / 1.0) * 50.0 + 25.0))

    # If only one metric available, double its weight
    if weight_delta_lb is None:
        return round(bf_score * 2, 1)
    if body_fat_delta_pct is None:
        return round(weight_score * 2, 1)

    return round(weight_score + bf_score, 1)


# ── Main scoring ──────────────────────────────────────────────────────────────

def compute_weekly_score(
    participant_id:     str,
    activity_data:      dict,        # output of oura.py or whoop.py
    renpho_current:     dict,        # output of renpho.py (this week)
    renpho_previous:    Optional[dict] = None,  # last week's renpho data
) -> dict:
    """
    Compute a single participant's weekly score.

    Returns:
        {
            "participant_id": "kam",
            "body_sore_score": 69.0,
            "activity_score": 72.0,
            "improvement_score": 55.0,
            "weekly_score": 65.8,      # weighted composite
            "components": { ... }       # breakdown for transparency
        }
    """
    config  = load_config()
    weights = config["scoring_weights"]

    # 1. Body Sore Score (direct from Renpho)
    body_sore = renpho_current.get("body_sore_score") or 50.0

    # 2. Activity Score (composite from Oura or WHOOP)
    activity_score = (
        activity_data.get("weekly_averages", {}).get("composite_activity_score")
        or 50.0
    )

    # 3. Improvement Score (week-over-week body composition delta)
    weight_delta   = None
    bf_delta       = None

    if renpho_previous:
        prev_weight = renpho_previous.get("weight_lb")
        curr_weight = renpho_current.get("weight_lb")
        if prev_weight and curr_weight:
            weight_delta = round(curr_weight - prev_weight, 2)

        prev_bf = renpho_previous.get("body_fat_pct")
        curr_bf = renpho_current.get("body_fat_pct")
        if prev_bf and curr_bf:
            bf_delta = round(curr_bf - prev_bf, 2)

    improvement_score = score_improvement(weight_delta, bf_delta)

    # 4. Weighted composite
    weekly_score = round(
        body_sore        * weights["body_sore_score"]   +
        activity_score   * weights["activity_score"]    +
        improvement_score * weights["weekly_improvement"],
        1,
    )

    return {
        "participant_id":   participant_id,
        "body_sore_score":  body_sore,
        "activity_score":   activity_score,
        "improvement_score": improvement_score,
        "weekly_score":     weekly_score,
        "components": {
            "weight_delta_lb":    weight_delta,
            "body_fat_delta_pct": bf_delta,
            "device":             activity_data.get("device"),
            "hrv_avg_ms":         activity_data.get("weekly_averages", {}).get("hrv_avg_ms"),
        },
    }


def rank_participants(scores: list[dict]) -> list[dict]:
    """
    Sort participants by weekly_score descending and add rank + delta from leader.

    Returns the same list with added fields:
        "rank": 1-based position
        "gap_from_leader": points behind first place (0 for winner)
    """
    ranked = sorted(scores, key=lambda x: x["weekly_score"], reverse=True)
    leader_score = ranked[0]["weekly_score"] if ranked else 0

    for i, entry in enumerate(ranked):
        entry["rank"]             = i + 1
        entry["gap_from_leader"]  = round(leader_score - entry["weekly_score"], 1)
        entry["is_winner"]        = (i == 0)

    return ranked


if __name__ == "__main__":
    # Quick test with mock data
    import json

    mock_oura = {
        "device": "oura",
        "weekly_averages": {
            "activity_score": 72, "readiness_score": 70,
            "hrv_avg_ms": 38, "composite_activity_score": 71,
        },
    }
    mock_whoop = {
        "device": "whoop",
        "weekly_averages": {
            "recovery_score": 84, "strain_normalized": 72,
            "hrv_avg_ms": 52, "composite_activity_score": 79,
        },
    }
    mock_renpho_now  = {"body_sore_score": 69, "weight_lb": 256.2, "body_fat_pct": 32.3}
    mock_renpho_prev = {"body_sore_score": 68, "weight_lb": 258.0, "body_fat_pct": 32.5}

    scores = [
        compute_weekly_score("kam",    mock_oura,  mock_renpho_now, mock_renpho_prev),
        compute_weekly_score("marcus", mock_whoop, mock_renpho_now, None),
    ]
    ranked = rank_participants(scores)
    print(json.dumps(ranked, indent=2))
