"""
coach/session_routing.py
Einzige Stelle, die entscheidet, ob eine geplante Session an Garmin (Lauf/Rad),
an Hevy/CAIRN (Kraft) oder ausschliesslich CAIRN-intern geht. Reine Logik,
keine DB-/API-Zugriffe — direkt testbar.

Verbindliche Architektur (siehe CAIRN.md-Auftrag vom 2026-08-15, praezisiert
2026-08-15 mit der sync_target-Terminologie):
- Krafttraining darf NIEMALS an Garmin gesendet werden.
- Mobility, Core, Rest Day werden ebenfalls nie an Garmin gesendet.
- Rennrad/Cross Training nur dann als Garmin-Cycling-Push, wenn explizit als
  Rad-Einheit markiert (sport_hint="cycling") — 'Cross Training' allein ist
  mehrdeutig (Rennrad/Schwimmen/Wandern, siehe athlete_profile.cross_*), und
  nur Cycling ist über den vorhandenen Garmin-Adapter tatsächlich verifiziert
  (garmin_push.py unterstützt nur running/cycling).

sync_target ist der einzige gueltige Wortschatz fuer das Zielsystem einer
Session: "garmin" | "hevy" | "cairn_only". Diese Werte sind Teil des
oeffentlichen MCP-Tool-Vertrags (upsert_planned_workout) — nicht umbenennen,
ohne alle Aufrufer (mcp_server.py, Tests) mit anzupassen.
"""
from __future__ import annotations

from typing import Literal

# Beide Schreibweisen kommen im bestehenden Code vor: generate_plan.py nutzt
# 'Strength Training' (englisch), garmin_calendar_sync.py::map_title_to_session_type
# produziert 'Krafttraining' (deutsch) beim Rueck-Import von Garmin-Titeln.
STRENGTH_TYPES = frozenset({"Strength Training", "Krafttraining"})

# Nie an Garmin senden, unabhaengig von sport_hint.
NEVER_GARMIN_TYPES = STRENGTH_TYPES | frozenset({"Mobility", "Core", "Rest Day"})
# Alias fuer coach/garmin_batch.py — gleiche Menge, stabiler oeffentlicher Name.
GARMIN_BLOCKED_TYPES = NEVER_GARMIN_TYPES

# Laufbezogene Typen, wie in generate_plan.py (QUALITY_TYPES, ENDURANCE_RUN_TYPES)
# und training_plan.session_type in der Praxis verwendet. "Race Day" kommt aus
# generate_plan.py, "Race" wurde in einem real importierten Trainingsblock
# beobachtet (session_type='Race' fuer den 2026-09-26-Renntag selbst) — beide
# Schreibweisen muessen als Garmin-Push-faehig gelten, sonst faellt der
# eigentliche Renntag auf cairn_only und wird nie automatisch gepusht.
GARMIN_RUNNING_TYPES = frozenset({
    # Basis-Typen
    "Easy Run", "Trail Run", "Long Trail Run", "Recovery Run", "Long Run",
    "Tempo Session", "Tempo Run", "Interval Session", "Sprint Session", "Hill Session",
    "Race Day", "Race",
    # Erweiterte Typen (ChatGPT-kompatibel)
    "Interval Run", "Interval Training",
    "Threshold Run", "Trail Threshold", "Uphill Threshold",
    "Hill Technique", "Hill Run", "Hill Sprints",
    "Race Activation", "Activation Run",
    "Fartlek", "Strides", "Time Trial",
})

# Explizite Rad-Typen (garmin_sport=cycling)
GARMIN_CYCLING_TYPES = frozenset({"Cycling", "Bike", "Road Bike", "MTB", "E-Bike", "Rennrad", "Rennrad Endurance"})

# 'Cross Training' selbst ist NICHT automatisch Rad — siehe Docstring oben.
GARMIN_ELIGIBLE_CROSS_TYPES = frozenset({"Cross Training"})

SyncTarget = Literal["garmin", "hevy", "cairn_only"]
VALID_SYNC_TARGETS = frozenset({"garmin", "hevy", "cairn_only"})


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
        {"sync_target": "garmin"|"hevy"|"cairn_only", "garmin_sport": "running"|"cycling"|None,
         "garmin_push_required": bool, "source": "hevy"|None, "reason": str}
    """
    if session_type in NEVER_GARMIN_TYPES:
        is_strength = session_type in STRENGTH_TYPES
        return {
            "sync_target": "hevy" if is_strength else "cairn_only",
            "garmin_sport": None,
            "garmin_push_required": False,
            "source": "hevy" if is_strength else None,
            "reason": (
                "Krafttraining wird ausschliesslich in CAIRN gespeichert, nie an Garmin gesendet."
                if is_strength else
                f"{session_type!r} wird nie an Garmin gesendet (Mobility/Core/Rest Day)."
            ),
        }

    if session_type in GARMIN_CYCLING_TYPES:
        return {
            "sync_target": "garmin", "garmin_sport": "cycling", "garmin_push_required": True,
            "source": None, "reason": f"{session_type!r} ist eine Radeinheit — Garmin-Push (cycling).",
        }

    if session_type in GARMIN_RUNNING_TYPES:
        return {
            "sync_target": "garmin", "garmin_sport": "running", "garmin_push_required": True,
            "source": None, "reason": f"{session_type!r} ist eine Laufeinheit — Garmin-Push (running).",
        }

    if session_type in GARMIN_ELIGIBLE_CROSS_TYPES:
        if sport_hint == "cycling":
            return {
                "sync_target": "garmin", "garmin_sport": "cycling", "garmin_push_required": True,
                "source": None, "reason": "Cross Training explizit als Rennrad markiert — Garmin-Push (cycling).",
            }
        return {
            "sync_target": "cairn_only", "garmin_sport": None, "garmin_push_required": False,
            "source": None,
            "reason": (
                "Cross Training ohne sport_hint='cycling' — Sporttyp nicht sicher bestimmbar "
                "(koennte Schwimmen/Wandern sein), daher kein automatischer Garmin-Push."
            ),
        }

    # Unbekannter session_type: sicherer Default ist "cairn_only" (nicht senden),
    # niemals stillschweigend an Garmin weiterreichen.
    return {
        "sync_target": "cairn_only", "garmin_sport": None, "garmin_push_required": False,
        "source": None, "reason": f"Unbekannter session_type {session_type!r} — kein automatischer Garmin-Push.",
    }


def resolve_sync_target(session_type: str, requested_sync_target: str | None = None, sport_hint: str | None = None) -> dict:
    """
    Wie classify_for_push(), aber validiert zusaetzlich einen vom Aufrufer
    GEWUENSCHTEN sync_target-Wert gegen die tatsaechliche Klassifizierung.

    Sicherheitsregel: ein Aufrufer darf NIE sync_target="garmin" fuer eine
    Kraft-/Mobility-/Core-/Rest-Day-Session erzwingen. Das wird hart
    zurueckgewiesen (ValueError), nicht still ignoriert oder stillschweigend
    korrigiert — der Aufrufer soll den Fehler in seiner eigenen Logik sehen.
    Ein zu vorsichtiger Wunsch (z.B. sync_target="cairn_only" fuer einen
    Lauf) wird dagegen respektiert — das ist niemals unsicher.
    """
    computed = classify_for_push(session_type, sport_hint)
    if requested_sync_target is not None:
        if requested_sync_target not in VALID_SYNC_TARGETS:
            raise ValueError(f"Ungueltiger sync_target {requested_sync_target!r}. Erlaubt: {sorted(VALID_SYNC_TARGETS)}")
        if requested_sync_target == "garmin" and computed["sync_target"] != "garmin":
            raise ValueError(
                f"sync_target='garmin' fuer session_type={session_type!r} abgelehnt — "
                f"{computed['reason']} Kraft/Mobility/Core/Rest Day duerfen niemals an Garmin."
            )
        # Ein restriktiverer Wunsch als die Berechnung (z.B. cairn_only statt
        # garmin) ist immer sicher und wird uebernommen.
        if requested_sync_target != computed["sync_target"]:
            computed = {**computed, "sync_target": requested_sync_target, "garmin_push_required": False}
    return computed


def validate_no_target_policy(steps: list[dict], session_type: str = "") -> list[str]:
    """
    Prüft ob steps die No-Target-Policy einhalten.
    Gibt eine Liste von Warnungen zurück (leer = alles OK).
    Wirft keinen Fehler — nur Logging-Warnungen für Debug.

    Prüft rekursiv (auch inner steps in repeat-Blocks) und warnt wenn:
    - ein warmup/cooldown/recovery-Step ein nicht-no_target hat
    - ein Session-Typ aus NO_TARGET_SESSION_TYPES ein Target hat

    Reiner Vorab-Check auf den rohen step_def-Dicts (wie an
    coach.garmin_push.build_workout_payload() übergeben) — kein Ersatz für
    die tatsächliche Durchsetzung dort (build_workout_payload erzwingt die
    Policy hart, diese Funktion dient nur der Sichtbarkeit/dem Debugging
    davor). Import von garmin_push lokal, damit session_routing.py beim
    Import fuer reine Klassifizierung (z.B. in Tests) nicht zwingend die
    garminconnect-Abhaengigkeit mitzieht.
    """
    from coach.garmin_push import NO_TARGET_SESSION_TYPES, NO_TARGET_STEP_TYPES

    warnings: list[str] = []
    session_forces_no_target = session_type in NO_TARGET_SESSION_TYPES

    def _has_real_target(step: dict) -> bool:
        target = step.get("target")
        return isinstance(target, dict) and target.get("type") not in (None, "no_target")

    def _check(step: dict, path: str) -> None:
        step_type = step.get("type")
        if step_type in NO_TARGET_STEP_TYPES and _has_real_target(step):
            warnings.append(
                f"{path}: step_type={step_type!r} darf nie ein Target haben, "
                f"hat aber {step.get('target')!r}."
            )
        if session_forces_no_target and _has_real_target(step):
            warnings.append(
                f"{path}: session_type={session_type!r} erzwingt no_target fuer alle Steps, "
                f"Step hat aber {step.get('target')!r}."
            )
        if step_type == "repeat":
            for i, inner in enumerate(step.get("steps") or [], start=1):
                _check(inner, f"{path}.repeat[{i}]")

    for i, s in enumerate(steps or [], start=1):
        _check(s, f"step[{i}]")
    return warnings


def split_training_block(sessions: list[dict]) -> dict:
    """
    Teilt einen Trainingsblock (Liste von Session-Dicts mit mindestens
    'session_type', optional 'sport_hint') nach Zielsystem auf.

    Returns: {"garmin": [...], "hevy": [...], "cairn_only": [...]}
    Jede Session wird um ihre Klassifizierung ('_routing') ergänzt zurückgegeben.
    """
    result: dict[str, list[dict]] = {"garmin": [], "hevy": [], "cairn_only": []}
    for s in sessions:
        routing = classify_for_push(s.get("session_type", ""), s.get("sport_hint"))
        enriched = {**s, "_routing": routing}
        result[routing["sync_target"]].append(enriched)
    return result
