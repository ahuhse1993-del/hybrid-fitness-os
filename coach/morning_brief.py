"""
CAIRN Morning Brief
Generates the daily morning brief using:
- Knowledge base (markdown docs from cairn-coach-standard)
- Athlete context (live data from DB)
- Anthropic Claude
"""

import os
import json
import anthropic
from coach.context_builder import build_morning_brief_context, format_context_for_prompt
from knowledge.loader import load_morning_brief_knowledge


def generate_morning_brief(athlete_feedback: dict = None) -> dict:
    """
    Generates the morning brief.
    Returns dict with brief text + coaching metadata.
    """

    # ── 1. Load knowledge (coaching philosophy) ──
    knowledge = load_morning_brief_knowledge()

    # ── 2. Build athlete context ──
    context = build_morning_brief_context()

    # Inject fresh athlete feedback from check-in if available
    if athlete_feedback:
        if athlete_feedback.get('feel') and context.get('today_health'):
            context['today_health']['feel'] = athlete_feedback.get('feel')
            context['today_health']['notes'] = athlete_feedback.get('notes', [])
            context['today_health']['athlete_text'] = athlete_feedback.get('text', '')
        elif athlete_feedback.get('feel'):
            context['today_health'] = {
                'feel': athlete_feedback.get('feel'),
                'notes': athlete_feedback.get('notes', []),
                'athlete_text': athlete_feedback.get('text', ''),
            }

    context_text = format_context_for_prompt(context)

    # ── 3. Build prompt ──
    system_prompt = f"""You are CAIRN — a professional endurance coach.

Your entire coaching philosophy, communication style and decision framework is defined in the knowledge documents below.

Read them carefully. Every response must comply with them.
This includes any length constraints defined for specific output formats.

{knowledge}

---

LANGUAGE RULE:
Always respond in German (Deutsch). The athlete is German-speaking.
Write naturally. Like a coach. Not like a translation.

OUTPUT RULE:
Return ONLY a JSON object. No markdown fences. No preamble.
Structure:
{{
  "brief": "The morning brief as plain text, following the length and structure rules defined in the knowledge documents above",
  "suggestion": "One-line suggestion if plan change is recommended, else empty string",
  "session_type": "Recommended session type if change, else empty string",
  "session_zone": "Zone if relevant, else empty string",
  "primary_target": "pace or hr or feel or none",
  "secondary_target": "pace or hr or feel or none",
  "replan_needed": true or false
}}

The "brief" field is plain text. No headers, no bullet points, no markdown formatting inside it."""

    user_prompt = f"""Here is today's athlete data:

{context_text}

Write today's Morning Brief for Alexander."""

    # ── 4. Call Anthropic ──
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt,
    )

    raw = message.content[0].text.strip()
    raw = raw.replace('```json', '').replace('```', '').strip()

    try:
        result = json.loads(raw)
    except Exception:
        result = {
            "brief": raw,
            "suggestion": "",
            "session_type": "",
            "session_zone": "",
            "primary_target": "none",
            "secondary_target": "none",
            "replan_needed": False,
        }

    return result