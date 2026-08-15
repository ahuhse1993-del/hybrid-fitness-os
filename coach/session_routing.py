"""
coach/session_routing.py
Einzige Stelle, die entscheidet, ob eine geplante Session an Garmin (Lauf/Rad),
an Hevy/CAIRN (Kraft) oder an keins von beiden geht. Reine Logik, keine DB-/API-
Zugriffe — direkt testbar.

Verbindliche Architektur (siehe CAIRN.md-Auftrag vom 2026-08-15):
- Krafttraining darf NIEMALS an Garmin gesendet werden.
- Mobility, Core, Rest Day werden ebenfalls nie an Garmin gesendet.
- Rennrad/Cross Training nur dann als Garmin-Cycling-Push, wenn explizit als
  Rad-Einheit markiert (sport_hint="cycling") — 'Cross Training' allein ist
  mehrdeutig (Rennrad/Schwimmen/Wandern, siehe athlete_profile.cross_*), und
  nur Cycling ist über den vorhandenen Garmin-Adapter tatsächlich verifiziert
  (garmin_push.py unterstützt nur running/cycling).
"""
from __future__ import annotations

from typing import Literal

# Beide Schreibweisen kommen im bestehenden Code vor: generate_plan.py nutzt
# 'Strength Training' (englisch), garmin_calendar_sync.py::map_title_to_session_type
# produziert 'Krafttraining' (deutsch) beim Rueck-Import von Garmin-Titeln.
STRENGTH_TYPES = frozenset({"Strength Training", "Krafttraining"})

# Nie an Garmin senden, unabhaengig von sport_hint.
NEVER_GARMIN_TYPES = STRENGTH_TYPES | frozenset({"Mobility", "Core", "Rest Day"})

# Laufbezogene Typen, wie in generate_plan.py (QUALITY_TYPES, ENDURANCE_RUN_TYPES)
# und training_plan.session_type in der Praxis verwendet.
GARMIN_RUNNING_TYPES = frozenset({
    "Easy Run", "Trail Run", "Recovery Run", "Long Run",
    "Tempo Session", "Interval Session", "Sprint Session", "Hill Session",
    "Race Day",
})

# 'Cross Training' selbst ist NICHT automatisch Rad — siehe Docstring oben.
GARMIN_ELIGIBLE_CROSS_TYPES = frozenset({"Cross Training"})

RoutingTarget = Literal["garmin", "hevy", "none"]


def classify_for_push(session_type: str, sport_hint: str | None = None) -> dict:
    """
    Klassifiziert eine einzelne Session für den Push/die Speicherung.

    Args:
        session_type: z.B. "Easy Run", "Strength Training", "Cross Training".
        sport_hint:   nur für 'Cross Training' relevant — "cycling" wenn die
                      Einheit explizit als Rad markiert ist (z.B. aus
                      athlete_profile.cross_rennrad oder Trainingsplan-Metadaten).
                      Ohne sport_hint="cycling" wird Cross Training NICHT an
                      Garmin geschickt (Sicherheitsstandard: im Zweifel nicht
                      pushen, statt einen falschen Sporttyp zu raten).

    Returns:
        {"target": "garmin"|"hevy"|"none", "garmin_sport": "running"|"cycling"|None,
         "garmin_push_required": bool, "source": "hevy"|None, "reason": str}
    """
    if session_type in NEVER_GARMIN_TYPES:
        is_strength = session_type in STRENGTH_TYPES
        return {
            "target": "hevy" if is_strength else "none",
            "garmin_sport": None,
            "garmin_push_required": False,
            "source": "hevy" if is_strength else None,
            "reason": (
                "Krafttraining wird ausschliesslich in CAIRN gespeichert, nie an Garmin gesendet."
                if is_strength else
                f"{session_type!r} wird nie an Garmin gesendet (Mobility/Core/Rest Day)."
            ),
        }

    if session_type in GARMIN_RUNNING_TYPES:
        return {
            "target": "garmin", "garmin_sport": "running", "garmin_push_required": True,
            "source": None, "reason": f"{session_type!r} ist eine Laufeinheit — Garmin-Push (running).",
        }

    if session_type in GARMIN_ELIGIBLE_CROSS_TYPES:
        if sport_hint == "cycling":
            return {
                "target": "garmin", "garmin_sport": "cycling", "garmin_push_required": True,
                "source": None, "reason": "Cross Training explizit als Rennrad markiert — Garmin-Push (cycling).",
            }
        return {
            "target": "none", "garmin_sport": None, "garmin_push_required": False,
            "source": None,
            "reason": (
                "Cross Training ohne sport_hint='cycling' — Sporttyp nicht sicher bestimmbar "
                "(koennte Schwimmen/Wandern sein), daher kein automatischer Garmin-Push."
            ),
        }

    # Unbekannter session_type: sicherer Default ist "none" (nicht senden),
    # niemals stillschweigend an Garmin weiterreichen.
    return {
        "target": "none", "garmin_sport": None, "garmin_push_required": False,
        "source": None, "reason": f"Unbekannter session_type {session_type!r} — kein automatischer Garmin-Push.",
    }


def split_training_block(sessions: list[dict]) -> dict:
    """
    Teilt einen Trainingsblock (Liste von Session-Dicts mit mindestens
    'session_type', optional 'sport_hint') nach Zielsystem auf.

    Returns: {"garmin": [...], "hevy": [...], "none": [...]}
    Jede Session wird um ihre Klassifizierung ('_routing') ergänzt zurückgegeben.
    """
    result: dict[str, list[dict]] = {"garmin": [], "hevy": [], "none": []}
    for s in sessions:
        routing = classify_for_push(s.get("session_type", ""), s.get("sport_hint"))
        enriched = {**s, "_routing": routing}
        result[routing["target"]].append(enriched)
    return result
