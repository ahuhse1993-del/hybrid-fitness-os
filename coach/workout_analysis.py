"""
CAIRN Workout Analysis
Generates workout analysis using:
- Knowledge base (markdown docs from cairn-coach-standard)
- Activity data + splits from DB
- Anthropic Claude
"""

import os
import json
import anthropic
from knowledge.loader import load_workout_analysis_knowledge


def compute_trail_segments(splits: list) -> str:
    UPHILL_THRESHOLD = 30
    DOWNHILL_THRESHOLD = -30
    if not splits:
        return ""
    classified = []
    for s in splits:
        elev = float(s[4]) if s[4] else 0.0
        dist = float(s[1]) if s[1] else 1.0
        gradient = elev / dist if dist > 0 else 0
        terrain = "uphill" if gradient > UPHILL_THRESHOLD else ("downhill" if gradient < DOWNHILL_THRESHOLD else "flat")
        classified.append({"km": s[0], "distance_km": dist, "pace_s": s[2], "hr": s[3] or 0, "elevation_m": elev, "terrain": terrain})
    segments = []
    current = None
    for point in classified:
        if current is None or point["terrain"] != current["terrain"]:
            if current:
                segments.append(current)
            current = {"terrain": point["terrain"], "start_km": point["km"] - 1, "end_km": point["km"], "km_count": 1, "total_elevation_m": point["elevation_m"], "total_distance_km": point["distance_km"], "pace_seconds": [point["pace_s"]] if point["pace_s"] else [], "hr_values": [point["hr"]] if point["hr"] else []}
        else:
            current["end_km"] = point["km"]
            current["km_count"] += 1
            current["total_elevation_m"] += point["elevation_m"]
            current["total_distance_km"] += point["distance_km"]
            if point["pace_s"]: current["pace_seconds"].append(point["pace_s"])
            if point["hr"]: current["hr_values"].append(point["hr"])
    if current:
        segments.append(current)
    merged = []
    for seg in segments:
        if merged and seg["km_count"] == 1:
            prev = merged[-1]
            prev["end_km"] = seg["end_km"]
            prev["km_count"] += seg["km_count"]
            prev["total_elevation_m"] += seg["total_elevation_m"]
            prev["total_distance_km"] += seg["total_distance_km"]
            prev["pace_seconds"].extend(seg["pace_seconds"])
            prev["hr_values"].extend(seg["hr_values"])
        else:
            merged.append(seg)
    LABELS = {"uphill": "↑ Aufstieg", "downhill": "↓ Abstieg", "flat": "→ Flach"}
    lines = ["## Trail-Segmente (Geländephasen)"]
    for i, seg in enumerate(merged, 1):
        elev = seg["total_elevation_m"]
        elev_str = f"+{elev:.0f}m" if elev > 0 else f"{elev:.0f}m"
        avg_pace_s = int(sum(seg["pace_seconds"]) / len(seg["pace_seconds"])) if seg["pace_seconds"] else None
        pace_str = f"{avg_pace_s//60}:{str(avg_pace_s%60).zfill(2)}/km" if avg_pace_s else "—"
        avg_hr = int(sum(seg["hr_values"]) / len(seg["hr_values"])) if seg["hr_values"] else None
        hr_str = f"{avg_hr} bpm" if avg_hr else "—"
        gradient_pct = abs(elev / (seg["total_distance_km"] * 1000) * 100) if seg["total_distance_km"] > 0 else 0
        lines.append(f"  {i}. {LABELS[seg['terrain']]} | km {seg['start_km']}–{seg['end_km']} ({seg['total_distance_km']:.1f} km) | {elev_str} | ~{gradient_pct:.0f}% | ⌀ {pace_str} | ⌀ HF {hr_str}")
    return "\n".join(lines)


def generate_workout_analysis(training_id: int) -> dict:
    import psycopg2
    database_url = os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL")
    conn = psycopg2.connect(database_url)
    cur = conn.cursor()

    # ── Activity ──
    cur.execute("""
        SELECT id, date, type, notes, duration_minutes, distance_km, heart_rate_avg
        FROM trainings WHERE id = %s
    """, (training_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None

    # ── Splits ──
    cur.execute("""
        SELECT split_number, distance_km, pace_seconds, heart_rate_avg, elevation_gain
        FROM splits WHERE training_id = %s ORDER BY split_number
    """, (training_id,))
    splits = cur.fetchall()

    # ── Recent trainings for context ──
    cur.execute("""
        SELECT date, type, notes, duration_minutes, distance_km, heart_rate_avg
        FROM trainings
        WHERE date < %s
        ORDER BY date DESC
        LIMIT 5
    """, (row[1],))
    recent = cur.fetchall()

    conn.close()

    # ── Format activity ──
    name = row[3] or row[2]
    distance = float(row[5]) if row[5] else 0
    duration = row[4] or 0
    avg_hr = row[6] or 0
    date_str = str(row[1])
    activity_type = row[2]

    # ── Format splits — elevation prominent ──
    total_elevation_up = 0
    total_elevation_down = 0
    splits_lines = []

    for s in splits:
        split_num = s[0]
        pace_str = f"{s[2]//60}:{str(s[2]%60).zfill(2)}/km" if s[2] else "—"
        hr = s[3] or "—"
        elev = float(s[4]) if s[4] else 0

        if elev > 0:
            total_elevation_up += elev
            elev_str = f"+{elev:.0f}m ↑"
        elif elev < 0:
            total_elevation_down += abs(elev)
            elev_str = f"{elev:.0f}m ↓"
        else:
            elev_str = "flat"

        splits_lines.append(
            f"  Km {split_num:2d}: {pace_str} | HF {hr} bpm | Elevation {elev_str}"
        )

    splits_text = "\n".join(splits_lines)
    trail_segments_text = compute_trail_segments(splits)

    # ── Elevation summary ──
    elevation_summary = f"Total ascent: +{total_elevation_up:.0f}m | Total descent: -{total_elevation_down:.0f}m"

    # ── Recent trainings ──
    recent_text = ""
    for r in recent:
        parts = [str(r[0]), r[1]]
        if r[4]: parts.append(f"{float(r[4]):.1f} km")
        if r[3]: parts.append(f"{r[3]} min")
        if r[5]: parts.append(f"HF {r[5]} bpm")
        recent_text += "  " + " | ".join(str(p) for p in parts) + "\n"

    # ── Load knowledge ──
    knowledge = load_workout_analysis_knowledge()

    # ── System prompt ──
    system_prompt = f"""You are CAIRN — a professional endurance coach.

Your entire coaching philosophy, communication style and decision framework is defined in the knowledge documents below.

Read them carefully. Every response must comply with them.

{knowledge}

---

IMPORTANT CONTEXT RULE:
The splits data includes elevation per kilometer.
When pace is slow, always check elevation before drawing conclusions.
A slow kilometer on a steep climb is very different from a slow kilometer on flat terrain.
The coach should always explain pace in the context of the elevation profile.

LANGUAGE RULE:
Always respond in German (Deutsch). The athlete is German-speaking.
Write naturally. Like a coach standing next to the athlete after training.

OUTPUT RULE:
Return ONLY a JSON object. No markdown fences. No preamble.
Structure:
{{
  "summary": "2-3 sentences — coach opening + what happened. React like a real coach.",
  "observations": ["observation 1", "observation 2", "observation 3"],
  "meaning": "1-2 sentences — what this means for upcoming training",
  "recommendation": "one clear sentence — what the coach recommends next",
  "next_session": "concrete description of the next recommended session",
  "closing": "one grounded closing thought",
  "tags": [{{"label": "short label max 20 chars", "type": "good or warn"}}]
}}"""

    user_prompt = f"""Analyse this workout for Alexander:

## Activity
Name: {name}
Date: {date_str}
Type: {activity_type}
Distance: {distance:.1f} km
Duration: {duration} min
Average HR: {avg_hr} bpm
{elevation_summary}

## Splits (pace | heart rate | elevation per km)
{splits_text if splits_text else "No split data available."}

Note: Elevation shows meters gained (+) or lost (-) per kilometer.
Use this to explain pace variations — a slow km on a steep climb is expected and correct.

{trail_segments_text}

## Recent Training Context
{recent_text if recent_text else "No recent training data."}

Write the full workout analysis."""

    # ── Call Anthropic ──
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt,
    )

    raw = message.content[0].text.strip()
    raw = raw.replace('```json', '').replace('```', '').strip()

    try:
        result = json.loads(raw)
    except Exception:
        result = {
            "summary": raw,
            "observations": [],
            "meaning": "",
            "recommendation": "",
            "next_session": "",
            "closing": "",
            "tags": [],
        }

    return result