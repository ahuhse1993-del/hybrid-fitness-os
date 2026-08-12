"""
CAIRN – Plan-Generierung als GitHub Action Job.

Liest die Fragebogen-Daten eines Jobs aus plan_jobs, baut den kompletten
Trainingsplan (Athleten-Analyse, CAIRN-Routinen, Web-Search-Recherche,
Wochen-Generierung, Workout-Vorschläge) und schreibt ihn in die DB.

Ausführen: python data/generate_plan.py <job_id>
"""
import os
import sys
import json
import re
import traceback
from datetime import date, timedelta, datetime

import psycopg2
from psycopg2.extras import Json
import anthropic
from dotenv import load_dotenv

load_dotenv()

HEVY_CATEGORIES = {
    'Upper Body CAIRN': 'oberkörper',
    'Lower Body + Arms CAIRN': 'unterkörper',
    'Full Body Light CAIRN': 'full_body_light',
}


def get_db():
    database_url = os.getenv("DATABASE_URL") or os.getenv("RAILWAY_DATABASE_URL")
    return psycopg2.connect(database_url)


def get_today():
    try:
        import pytz
        zurich = pytz.timezone('Europe/Zurich')
        return datetime.now(zurich).date()
    except Exception:
        return (datetime.utcnow() + timedelta(hours=2)).date()


def update_job_status(job_id, status, error=None):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE plan_jobs SET status = %s, error = %s, updated_at = NOW() WHERE id = %s",
            (status, error, job_id)
        )
        conn.commit()
    finally:
        conn.close()


def build_hevy_context():
    """CAIRN-Routinen aus der cairn_routines Tabelle (per hevy_routines_sync.py aus Hevy synchronisiert)."""
    hevy_context = ""
    routine_by_category = {}
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT title, exercises FROM cairn_routines")
        rows = cur.fetchall()
        print(f"DEBUG: cairn_routines query returned {len(rows)} rows")

        cairn_routines = {}
        for title, exercises in rows:
            title = (title or '').strip()
            if not title or title in cairn_routines:
                continue  # Duplikat ignorieren
            cairn_routines[title] = exercises or []

        if cairn_routines:
            hevy_lines = ["STRENGTH TRAINING: Verwende NUR diese Workout-Namen (exakt so wie hier geschrieben):"]
            for title, exercises in cairn_routines.items():
                category = HEVY_CATEGORIES.get(title, 'Ganzkörper')
                routine_by_category.setdefault(category, title)
                hevy_lines.append(f"- {title} [{category}]: {', '.join(exercises[:4])}")
            hevy_lines.append("Jeder andere Strength Training Name ist VERBOTEN.")

            if routine_by_category.get('oberkörper') and routine_by_category.get('unterkörper'):
                hevy_lines.append(f"Normale Wochen: {routine_by_category['oberkörper']} und {routine_by_category['unterkörper']} abwechselnd")
            if routine_by_category.get('full_body_light'):
                hevy_lines.append(f"Deload/Taper: {routine_by_category['full_body_light']}")

            hevy_context = "\n" + "\n".join(hevy_lines) + "\n"
    except Exception as e:
        print(f"Hevy Routinen Fehler: {e}")
    finally:
        conn.close()
    return hevy_context, routine_by_category


def build_athlete_analysis(today):
    """Athletenprofil + letzte 4 Wochen Aktivitäten + HRV/Schlaf/Befinden aus der DB."""
    athlete_analysis_context = ""
    conn = get_db()
    try:
        cur = conn.cursor()

        # Letzte 4 Wochen Aktivitäten
        cur.execute("""
            SELECT type, distance_km, heart_rate_avg
            FROM trainings
            WHERE date >= %s
        """, (today - timedelta(days=28),))
        training_rows = cur.fetchall()
        print(f"DEBUG: trainings query returned {len(training_rows)} rows")

        # HRV / Schlaf / Befinden
        cur.execute("""
            SELECT hrv_last_night, sleep_duration_h, feel
            FROM daily_logs
            WHERE date >= %s
        """, (today - timedelta(days=30),))
        log_rows = cur.fetchall()
        print(f"DEBUG: daily_logs query returned {len(log_rows)} rows")

        # Athletenprofil
        cur.execute("""
            SELECT long_term_goals,
                   hr_z1_min, hr_z1_max, hr_z2_min, hr_z2_max, hr_z3_min, hr_z3_max,
                   hr_z4_min, hr_z4_max, hr_z5_min, hr_z5_max,
                   cross_rennrad, cross_schwimmen, cross_wandern, cross_ski
            FROM athlete_profile ORDER BY id LIMIT 1
        """)
        profile_row = cur.fetchone()
        print(f"DEBUG: athlete_profile query returned {'1 row' if profile_row else 'no row'}")

        run_rows = [r for r in training_rows if r[0] in ('Run', 'TrailRun')]
        run_kms = [float(r[1]) for r in run_rows if r[1]]
        avg_weekly_km = round(sum(run_kms) / 4.0, 1) if run_kms else 0
        total_runs = len(run_rows)
        max_km = round(max(run_kms), 1) if run_kms else 0

        hr_values = [float(r[2]) for r in training_rows if r[2]]
        avg_hr = round(sum(hr_values) / len(hr_values)) if hr_values else None

        type_counts = {}
        for r in training_rows:
            t = r[0] or 'Unbekannt'
            type_counts[t] = type_counts.get(t, 0) + 1
        top_types = sorted(type_counts.items(), key=lambda x: -x[1])[:3]
        top_types_str = ', '.join(f"{t} ({c}x)" for t, c in top_types) if top_types else 'keine Daten'

        hrv_values = [float(r[0]) for r in log_rows if r[0] is not None]
        avg_hrv = round(sum(hrv_values) / len(hrv_values), 1) if hrv_values else None

        sleep_values = [float(r[1]) for r in log_rows if r[1] is not None]
        avg_sleep = round(sum(sleep_values) / len(sleep_values), 1) if sleep_values else None

        feel_values = []
        for r in log_rows:
            try:
                if r[2] is not None:
                    feel_values.append(float(r[2]))
            except (TypeError, ValueError):
                pass
        avg_feel = round(sum(feel_values) / len(feel_values), 1) if feel_values else None

        long_term_goals = (profile_row[0] if profile_row else '') or 'keine angegeben'

        hr_zone_parts = []
        if profile_row:
            for i, label in enumerate(['Z1', 'Z2', 'Z3', 'Z4', 'Z5']):
                lo, hi = profile_row[1 + i * 2], profile_row[2 + i * 2]
                if lo is not None and hi is not None:
                    hr_zone_parts.append(f"{label} {lo}-{hi}")
        hr_zones_str = ', '.join(hr_zone_parts) if hr_zone_parts else 'keine hinterlegt'

        cross_prefs = []
        if profile_row:
            if profile_row[11]: cross_prefs.append('Rennrad')
            if profile_row[12]: cross_prefs.append('Schwimmen')
            if profile_row[13]: cross_prefs.append('Wandern')
            if profile_row[14]: cross_prefs.append('Ski')
        cross_prefs_str = ', '.join(cross_prefs) if cross_prefs else 'keine Präferenz hinterlegt'
        print("DEBUG: athlete analysis calculations complete")

        athlete_analysis_context = f"""
ATHLETEN-ANALYSE (echte Daten — der gesamte Plan muss darauf basieren):
Aktuelles Laufniveau: Ø {avg_weekly_km} km/Woche über die letzten 4 Wochen ({total_runs} Einheiten)
Längste Einheit: {max_km} km
Häufigste Session-Typen: {top_types_str}
Ø Herzfrequenz: {avg_hr if avg_hr is not None else 'keine Daten'} bpm
Ø HRV: {avg_hrv if avg_hrv is not None else 'keine Daten'} ms | Ø Schlaf: {avg_sleep if avg_sleep is not None else 'keine Daten'}h | Ø Befinden: {avg_feel if avg_feel is not None else 'keine Daten'}/10
HF-Zonen: {hr_zones_str}
Langzeitziele: {long_term_goals}
Cross Training: {cross_prefs_str}

Der gesamte Trainingsplan — jede Woche, jede Phase, jede Progression — muss auf diesem Athleten-Niveau aufbauen. Nicht zu hoch starten, nicht zu tief. Realistisch progressiv aufbauen basierend auf dem was der Athlet aktuell wirklich leistet. Ein Athlet der Ø 30km/Woche läuft startet anders als einer der Ø 80km/Woche läuft.
"""
        print("DEBUG: athlete_analysis_context built")
    except Exception as e:
        print(f"Athleten-Analyse Fehler: {e}")
        traceback.print_exc()
        athlete_analysis_context = ""
    finally:
        conn.close()
    return athlete_analysis_context


def build_training_science_context(client, terrain='', race_elevation_m=0):
    """Einmaliger Web-Search-Pre-Call für Trainingswissenschaft (statt pro Wochen-Batch)."""
    training_science_context = ""
    if terrain == 'trail':
        query = f"trail ultra running training elevation hill sessions periodization {race_elevation_m}m gain taper week race week training reduction"
    elif terrain == 'road':
        query = "road running training plan interval tempo quality sessions periodization taper week race week training reduction"
    else:
        query = "Key principles for hybrid athlete training plan (running + strength): optimal sequence, quality session placement, interference effect avoidance, taper week race week training reduction"
    try:
        science_message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system="Answer in max 200 words.",
            messages=[{"role": "user", "content": query}]
        )
        science_text = "".join(
            block.text for block in science_message.content
            if hasattr(block, 'text') and getattr(block, 'type', None) == 'text'
        ).strip()
        if science_text:
            training_science_context = "\nTRAININGSWISSENSCHAFT (Recherche):\n" + science_text + "\n"
        print("DEBUG: training science pre-call done")
    except Exception as e:
        print(f"Training Science Pre-Call Fehler: {e}")
        training_science_context = ""
    return training_science_context


def apply_post_processing(plan_json):
    """Trainingsregeln durchsetzen: kein Quality nach Unterkörper-Kraft, kein Quality direkt vor Long Run."""
    print(f"DEBUG: starting post-processing, {len(plan_json.get('weeks', []))} weeks total")
    quality_types = {'Tempo Session', 'Interval Session', 'Sprint Session', 'Hill Session'}
    swap_fields = (
        'session_type', 'notes', 'distance_km', 'duration_min', 'session_zone',
        'warmup_km', 'warmup_min', 'main_sets', 'main_distance_m', 'main_pace',
        'recovery_m', 'cooldown_km', 'cooldown_min',
    )

    for week in plan_json.get('weeks', []):
        sessions = week.get('sessions', [])
        by_day = {s.get('day_of_week'): s for s in sessions if s.get('day_of_week') is not None}

        for day in sorted(by_day.keys()):
            s = by_day[day]
            next_s = by_day.get(day + 1)
            if not next_s:
                continue

            notes_lower = (s.get('notes') or '').lower()
            is_lower = (s.get('session_type') == 'Strength Training' and
                       ('lower' in notes_lower or 'unterk' in notes_lower or
                        'bein' in notes_lower or 'leg' in notes_lower or
                        'squat' in notes_lower or 'deadlift' in notes_lower or
                        'kreuzheben' in notes_lower or 'bulgar' in notes_lower))
            is_quality_before_long = (s.get('session_type') in quality_types and
                                     next_s.get('session_type') == 'Long Run')

            if (is_lower and next_s.get('session_type') in quality_types) or is_quality_before_long:
                swap_target = None
                for other_day in sorted(by_day.keys()):
                    if other_day in (day, day + 1):
                        continue
                    if by_day[other_day].get('session_type') in {'Easy Run', 'Trail Run', 'Recovery Run'}:
                        swap_target = by_day[other_day]
                        break
                if swap_target:
                    for field in swap_fields:
                        next_s[field], swap_target[field] = swap_target.get(field), next_s.get(field)

    print("DEBUG: post-processing complete")
    return plan_json


QUALITY_TYPES = {'Tempo Session', 'Interval Session', 'Sprint Session', 'Hill Session'}


def _is_lower_body_strength(session):
    notes_lower = (session.get('notes') or '').lower()
    return (session.get('session_type') == 'Strength Training' and
            ('lower' in notes_lower or 'unterk' in notes_lower or
             'bein' in notes_lower or 'leg' in notes_lower or
             'squat' in notes_lower or 'deadlift' in notes_lower or
             'kreuzheben' in notes_lower or 'bulgar' in notes_lower))


def _is_hard_session(session):
    """Harte Einheit = Quality Session, Long Run oder Unterkörper-Kraft."""
    return (session.get('session_type') in QUALITY_TYPES or
            session.get('session_type') == 'Long Run' or
            _is_lower_body_strength(session))


def _is_leg_loading(session):
    """Beinbelastend = harte Einheit oder Trail Run."""
    return _is_hard_session(session) or session.get('session_type') == 'Trail Run'


def validate_and_fix_plan(plan_json, race_date, start_monday):
    """Prüft den generierten Plan gegen die Belastungsregeln. Für jeden Verstoss wird versucht,
    die betroffene Session mit einem kompatiblen Easy Run oder einem freien (Rest Day) Kalendertag
    in derselben Woche zu tauschen. Ist kein kompatibler Tausch möglich, bleibt die Session stehen."""
    print("DEBUG: starting plan validation")

    try:
        race_date_obj = date.fromisoformat(race_date) if race_date else None
    except Exception:
        race_date_obj = None

    swap_fields = (
        'session_type', 'notes', 'distance_km', 'duration_min', 'session_zone',
        'warmup_km', 'warmup_min', 'main_sets', 'main_distance_m', 'main_pace',
        'recovery_m', 'cooldown_km', 'cooldown_min', 'elevation_gain_m',
    )

    def find_swap_target(by_day, exclude_days):
        for day in sorted(by_day.keys()):
            if day in exclude_days:
                continue
            if by_day[day].get('session_type') == 'Easy Run':
                return day, False  # bestehende Easy-Run-Session, Feldertausch
        for day in range(1, 8):
            if day in exclude_days or day in by_day:
                continue
            return day, True  # freier Kalendertag (Rest Day), Session wird verschoben
        return None, None

    def swap_or_move(by_day, offending_day, offending_session, exclude_days):
        target_day, is_move = find_swap_target(by_day, exclude_days | {offending_day})
        if target_day is None:
            print(f"KEIN TAUSCH MÖGLICH: {offending_session}")
            return
        if is_move:
            offending_session['day_of_week'] = target_day
            by_day[target_day] = offending_session
            del by_day[offending_day]
        else:
            target_session = by_day[target_day]
            for field in swap_fields:
                offending_session[field], target_session[field] = target_session.get(field), offending_session.get(field)

    def convert_to_easy_run(session):
        # Ein Tausch mit einem anderen Tag INNERHALB der Rennwoche würde die verbotene
        # Session nur verschieben, nicht entfernen — die ganze Woche unterliegt derselben
        # Beschränkung. Deshalb wird direkt umgewandelt statt getauscht.
        session['session_type'] = 'Easy Run'
        session['distance_km'] = min(session.get('distance_km') or 6, 6)
        session['session_zone'] = 'Z1-Z2'
        session['notes'] = 'Sehr leicht, Rennwoche.'
        for field in ('warmup_km', 'warmup_min', 'main_sets', 'main_distance_m',
                      'main_pace', 'recovery_m', 'cooldown_km', 'cooldown_min'):
            session[field] = None

    for week in plan_json.get('weeks', []):
        week_num = week.get('week_number', 1)
        sessions = week.get('sessions', [])
        by_day = {s.get('day_of_week'): s for s in sessions if s.get('day_of_week') is not None}

        week_monday = start_monday + timedelta(weeks=week_num - 1)
        is_race_week = bool(race_date_obj) and week_monday <= race_date_obj <= week_monday + timedelta(days=6)

        # Race Day liegt nicht exakt auf race_date
        if is_race_week:
            expected_dow = race_date_obj.isoweekday()
            if by_day.get(expected_dow, {}).get('session_type') != 'Race Day':
                print(f"VALIDATOR FEHLER: Race Day liegt nicht exakt auf {race_date} (Woche {week_num})")
                race_days = [d for d, s in by_day.items() if s.get('session_type') == 'Race Day']
                if race_days:
                    wrong_day = race_days[0]
                    race_session = by_day[wrong_day]
                    if expected_dow in by_day:
                        target_session = by_day[expected_dow]
                        for field in swap_fields:
                            race_session[field], target_session[field] = target_session.get(field), race_session.get(field)
                    else:
                        race_session['day_of_week'] = expected_dow
                        by_day[expected_dow] = race_session
                        del by_day[wrong_day]
                else:
                    print(f"KEIN TAUSCH MÖGLICH: kein Race Day in Rennwoche {week_num} gefunden")

        # Quality, Long Run oder Unterkörper-Kraft in der Rennwoche
        if is_race_week:
            for day in sorted(by_day.keys()):
                s = by_day[day]
                if s.get('session_type') == 'Race Day':
                    continue
                if (s.get('session_type') in QUALITY_TYPES or
                        s.get('session_type') == 'Long Run' or
                        _is_lower_body_strength(s)):
                    print(f"VALIDATOR FEHLER: {s.get('session_type')} in der Rennwoche (Woche {week_num}, Tag {day})")
                    convert_to_easy_run(s)

        # Tages-Checks laufen zweimal: ein Tausch kann an anderer Stelle eine neue
        # Verletzung erzeugen (z.B. Unterkörper-Kraft landet neu vor einer Quality Session),
        # der zweite Durchlauf fängt diese kaskadierten Fälle ab.
        for _pass in range(2):
            # Quality Session am Tag nach Long Run
            for day in sorted(by_day.keys()):
                s = by_day[day]
                next_s = by_day.get(day + 1)
                if s.get('session_type') == 'Long Run' and next_s and next_s.get('session_type') in QUALITY_TYPES:
                    print(f"VALIDATOR FEHLER: Quality Session am Tag nach Long Run (Woche {week_num}, Tag {day + 1})")
                    swap_or_move(by_day, day + 1, next_s, {day, day + 1})

            # Unterkörper-Kraft am Tag vor Quality Session
            for day in sorted(by_day.keys()):
                s = by_day[day]
                next_s = by_day.get(day + 1)
                if _is_lower_body_strength(s) and next_s and next_s.get('session_type') in QUALITY_TYPES:
                    print(f"VALIDATOR FEHLER: Unterkörper-Kraft am Tag vor Quality Session (Woche {week_num}, Tag {day})")
                    swap_or_move(by_day, day, s, {day, day + 1})

            # Harte Einheit am Tag vor Long Run
            for day in sorted(by_day.keys()):
                s = by_day[day]
                next_s = by_day.get(day + 1)
                if next_s and next_s.get('session_type') == 'Long Run' and _is_hard_session(s):
                    print(f"VALIDATOR FEHLER: Harte Einheit am Tag vor Long Run (Woche {week_num}, Tag {day})")
                    swap_or_move(by_day, day, s, {day, day + 1})

            # Drei beinbelastende Tage hintereinander
            for day in sorted(by_day.keys()):
                d1, d2, d3 = by_day.get(day), by_day.get(day + 1), by_day.get(day + 2)
                if d1 and d2 and d3 and _is_leg_loading(d1) and _is_leg_loading(d2) and _is_leg_loading(d3):
                    print(f"VALIDATOR FEHLER: Drei beinbelastende Tage hintereinander (Woche {week_num}, Tag {day}-{day + 2})")
                    swap_or_move(by_day, day + 2, d3, {day, day + 1, day + 2})

    print("DEBUG: plan validation complete")
    return plan_json


def generate_plan(job_id, data):
    goal_type = data.get('goal_type', 'race')
    race_type = data.get('race_type', '')
    race_name = data.get('race_name', '')
    race_date = data.get('race_date', '')
    race_distance_km = data.get('race_distance_km', 0)
    terrain = data.get('terrain', '')
    race_elevation_m = data.get('race_elevation_m', 0)
    gpx_data = data.get('gpx_data', None)
    days_per_week = data.get('days_per_week', 5)
    long_run_day = data.get('long_run_day', 6)
    quality_sessions = data.get('quality_sessions', 1)
    strength_sessions = data.get('strength_sessions', 2)
    strength_days = data.get('strength_days', [])
    total_weeks = data.get('total_weeks', 16)
    phases = data.get('phases', [])
    start_date = data.get('start_date', None)
    cross_training = data.get('cross_training', False)
    cross_training_types = data.get('cross_training_types', [])
    cross_training_days = data.get('cross_training_days', 0)

    today = get_today()
    if start_date:
        try:
            start_day = date.fromisoformat(start_date)
            start_monday = start_day - timedelta(days=start_day.weekday())
            actual_start_day = start_day.isoweekday()  # 1=Mo, 7=So
        except Exception:
            start_monday = today
            actual_start_day = 1
    else:
        start_monday = today
        actual_start_day = 1
    day_names = ['', 'Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
    gain_per_km = (race_elevation_m / race_distance_km) if race_distance_km else 0
    try:
        race_dow = date.fromisoformat(race_date).isoweekday() if race_date else None  # 1=Mo, 7=So
    except Exception:
        race_dow = None

    half = total_weeks // 2
    week_ranges = [(1, half), (half + 1, total_weeks)] if total_weeks > 10 else [(1, total_weeks)]

    all_weeks = []

    hevy_context, routine_by_category = build_hevy_context()

    cross_training_context = ""
    if cross_training:
        cross_training_context = f"""
CROSS TRAINING: {cross_training_days}x pro Woche — Typen: {', '.join(cross_training_types) if cross_training_types else 'flexibel'}. Nutze session_type='Cross Training' mit notes=Typ (z.B. 'Rennrad 60 min').
"""

    terrain_rules = ""
    if terrain in ['trail', 'mixed']:
        terrain_rules = """
TERRAIN TRAIL/BERGE:
32. Long Runs finden bevorzugt auf hügeligem, bergigem oder technischem Terrain statt.
33. Hill Sessions und Trail Runs sind die primären Quality-Formen. Flache Tempo- oder Intervall-Sessions werden ergänzend eingesetzt.
34. Die geplanten Höhenmeter werden progressiv aufgebaut und dürfen nicht gleichzeitig mit Laufdistanz und Intensität stark gesteigert werden.
35. Technische Trail Runs werden nicht allein anhand von Kilometern bewertet. Dauer, Höhenmeter, Untergrund und RPE müssen bei der Belastungsberechnung berücksichtigt werden.
"""

    athlete_analysis_context = build_athlete_analysis(today)

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    print("DEBUG: anthropic client created")

    training_science_context = build_training_science_context(client, terrain, race_elevation_m)

    for (week_from, week_to) in week_ranges:
        phase_context = []
        for ph in phases:
            phase_context.append(f"{ph.get('name','').upper()}: {ph.get('weeks',0)} Wochen")

        gpx_context = ""
        if gpx_data:
            gpx_context = f"""
STRECKENPROFIL (GPX-Analyse):
- Distanz: {gpx_data.get('distance_km')} km
- Höhenmeter aufwärts: {gpx_data.get('elevation_gain_m')} m
- Höhenmeter pro km: {gpx_data.get('gain_per_km')} m/km
- Profil: {gpx_data.get('profile_de')}
- Max. Steigung: {gpx_data.get('max_grade_pct')} %
"""

        prompt = f"""Du bist CAIRN Coach. Erstelle Woche {week_from} bis {week_to} eines {total_weeks}-Wochen Trainingsplans.

ATHLETENPROFIL:
- Ziel: {goal_type}
- Rennen: {race_name} ({race_type}) · {race_distance_km if race_distance_km else '?'} km
- Renndatum: {race_date}
- Terrain: {terrain} · Distanz: {race_distance_km}km · Höhenmeter: {race_elevation_m}m · D+ pro km: {gain_per_km:.0f}m/km
- Gesamtplan: {total_weeks} Wochen · Phasen: {', '.join(phase_context)}

RACE DAY FIXPUNKT: {race_date} ist der Renntag. Dieser Tag ist ABSOLUT unveränderlich.
Der Race Day muss exakt auf dieses Datum fallen – unabhängig vom üblichen Long Run Tag.
Berechne alle Phasen rückwärts von diesem Datum.

RENNWOCHE REGELN (Woche die {race_date} enthält) — ABSOLUT UNVERÄNDERLICH:
- KEIN Long Run
- KEIN Quality Session (Tempo/Interval/Hill/Sprint)
- KEIN Unterkörper-Kraft (Lower Body)
- Erlaubt: Easy Run (max 6km), Upper Body Kraft, Mobility, Rest Day, Race Day
- Race Day = {race_date} (day_of_week={race_dow}) mit session_type='Race Day'
- Alle anderen Tage der Rennwoche: sehr leicht oder Ruhe
{athlete_analysis_context}
{training_science_context}
Plane basierend auf diesen wissenschaftlichen Erkenntnissen UND den Athletendaten.

WOCHENSTRUKTUR — GENAU {days_per_week} Sessions pro Woche:
- {strength_sessions}x Strength Training — NUR an: {', '.join([['','Mo','Di','Mi','Do','Fr','Sa','So'][d] for d in strength_days]) if strength_days else 'flexibel'}
- 1x Long Run — IMMER an {day_names[long_run_day]} (Tag {long_run_day})
- {quality_sessions}x Quality (Tempo Session / Interval Session / Sprint Session / Hill Session)
- {days_per_week - strength_sessions - 1 - quality_sessions}x Easy Run oder Trail Run
- {7 - days_per_week}x Rest Day — diese Tage komplett leer lassen, KEIN Eintrag
{gpx_context}
{hevy_context}
{cross_training_context}
CAIRN PLAN-GENERATOR – VERBINDLICHE REGELN

REGELPRIORITÄT:
Bei Konflikten gilt folgende Reihenfolge:
1. Rennwoche und Race Day
2. Verletzungs-, Erholungs- und Belastungsregeln
3. Taper
4. Deload
5. Peak
6. Volumenprogression
7. Bevorzugte Wochenstruktur

DEFINITIONEN:
- Harte Sessions: Quality Session, Long Run, Unterkörper-Kraft und Race.
- Quality Sessions: Tempo, Intervalle, Sprints, Hill Session und intensiver Trail Run.
- Easy/Recovery Runs sind keine harten Sessions.
- Wochenvolumen bezeichnet die gesamte geplante Laufdistanz in Kilometern.
- Abstandsregeln beziehen sich auf Kalendertage.
- Das Rennen zählt nicht als Long Run oder Quality Session, sondern als Race.

BELASTUNGSREGELN:
1. Der reguläre Long-Run-Tag ist {day_names[long_run_day]} und wird außerhalb der Rennwoche nicht verschoben.
2. Am Kalendertag vor und nach dem Long Run sind ausschließlich erlaubt: Easy Run, Recovery Run, Rest Day, Mobility, Oberkörper-Kraft.

BELASTUNGSVERTEILUNG KRAFT UND QUALITY:
3. Unterkörper-Kraft und Quality Session dürfen nicht am selben Tag stattfinden.
4. Unterkörper-Kraft am Tag vor einer Quality Session ist zu vermeiden.
5. Wenn zwei Krafttage verfügbar sind: Oberkörper bevorzugt auf den Tag unmittelbar vor der Quality Session, Unterkörper bevorzugt auf den Tag danach.
6. Unterkörper-Kraft darf am Tag nach einer Quality Session stattfinden. In diesem Fall: reduziertes Beinvolumen, RPE 6-7, 2-3 Wiederholungen im Tank, kein Muskelversagen, keine neue Übung.
7. Oberkörper-Kraft gilt nicht als harte Beineinheit und darf vor oder nach Quality Sessions und Longruns eingeplant werden.
8. Nie drei beinbelastende Tage hintereinander. Beinbelastend = Quality Session, Long Run, Unterkörper-Kraft, anspruchsvoller Trail Run.
9. Tag nach Long Run: bevorzugt Easy Run, Recovery Run, lockeres Cross Training, Oberkörper-Kraft oder Rest. Kein hartes Unterkörpertraining, keine Quality Session.
10. Tag vor Long Run: nur Easy Run, Recovery Run, lockeres Cross Training, Oberkörper-Kraft oder Rest.
11. Priorität bei Konflikten: Race Day > Long Run > Quality Session > Unterkörper-Kraft > Easy Run/Cross Training.
12. Wenn Trainingstage keine ideale Verteilung ermöglichen: zuerst Umfang und Intensität Unterkörper reduzieren, nicht Quality oder Long Run verschieben.

13. Harte Sessions dürfen niemals an zwei aufeinanderfolgenden Kalendertagen liegen.
14. Zwischen zwei laufintensiven harten Sessions muss mindestens ein vollständiger leichter oder trainingsfreier Tag liegen.
15. Bei Konflikten wird zuerst die Quality Session reduziert oder entfernt. Der Long Run bleibt auf seinem festgelegten Tag.

PROGRESSION:
16. Das Laufvolumen darf gegenüber der vorherigen regulären Belastungswoche um maximal 10% steigen.
17. Eine Deload- oder Taperwoche bildet keine neue Basis für die 10%-Progression. Nach einer Reduktionswoche darf maximal zum Umfang der letzten regulären Belastungswoche zurückgekehrt werden.
18. Jede vierte Trainingswoche ist grundsätzlich eine Deload-Woche: Laufvolumen etwa 20% unter der vorherigen Belastungswoche, keine Quality Session, Long Run entsprechend verkürzen, Intensität ausschließlich Easy/Recovery.
19. Liegt eine planmäßige Deload-Woche innerhalb von Peak, Taper oder Rennwoche, gelten stattdessen die Regeln der jeweiligen Rennphase.
20. Die Peak-Woche liegt zwei bis drei Wochen vor dem Rennen und enthält das höchste sinnvolle Wochenvolumen des Trainingsblocks.
21. Der Taper beginnt 14 Tage vor dem Race Day: erste Taperwoche Volumen etwa 30% unter Peak, Rennwoche Volumen etwa 40-60% unter Peak (Race nicht eingerechnet), Intensität darf in kurzen kontrollierten Abschnitten erhalten bleiben.

RENNWOCHE ({race_date}):
22. Race Day liegt exakt am {race_date} und hat day_of_week={race_dow}. Er darf niemals verschoben werden.
23. In der Rennwoche gibt es keinen Long Run, keine reguläre Quality Session und kein Unterkörper-Krafttraining.
24. An den Tagen vor dem Race Day sind ausschließlich erlaubt: Easy Run mit maximal 6 km, kurzer Recovery Run, lockeres Oberkörper-Krafttraining, Mobility, Rest Day.
25. Spätestens am Tag vor dem Race Day: maximal 20-30 Minuten sehr lockerer Shake-out Run oder Rest, keine zusätzliche Ermüdung erzeugen.
26. Training nach dem Race Day wird ausschließlich als Recovery geplant.

SESSION-SPEZIFIK:
27. Tempo-, Intervall- und Sprint-Sessions werden pace-basiert geplant und enthalten konkrete Zielbereiche in min/km.
28. Pace-basierte Quality Sessions müssen enthalten: Warm-up, Hauptteil mit Distanz oder Dauer, Zielpace als Bereich, Trab- oder Stehpausen, Cooldown.
29. Hill Sessions und Trail Runs werden über RPE und Belastungsdauer gesteuert. Es werden keine verbindlichen Pace-Ziele angegeben.
30. Long Runs auf Trail-, Berg- oder technisch anspruchsvollem Terrain werden über RPE gesteuert. Pace dient dort nicht als Belastungsziel.
31. Jede Laufeinheit enthält: Distanz, geschätzte Dauer, Intensitätssteuerung, Terrain, elevation_gain_m.
{terrain_rules}

ERLAUBTE SESSION-TYPEN — NUR diese 14, exakt so geschrieben (kein anderer Wert erlaubt):
Easy Run, Recovery Run, Long Run, Tempo Session, Interval Session, Sprint Session, Hill Session, Trail Run, Cross Training, Strength Training, Mobility, Rest Day, Time Trial, Race Day

Strength Training hat KEINE eigenen session_type-Unterkategorien. Oberkörper A/B, Unterkörper A/B oder Full Body gehören ausschließlich ins notes-Feld, z.B. session_type: "Strength Training", notes: "Oberkörper A".

STRUKTURIERTE FELDER — NUR für Quality Sessions (Tempo/Interval/Sprint/Hill Session) ausfüllen, für alle anderen Session-Typen weglassen/null:
- warmup_km, warmup_min: Einlaufen
- main_sets, main_distance_m, main_pace: Hauptteil (Sätze × Distanz in Metern bei Zielpace)
- recovery_m: Trabpause zwischen den Sätzen in Metern
- cooldown_km, cooldown_min: Auslaufen
notes fasst das in einem lesbaren Satz zusammen, z.B. "2km Einlaufen · 8×400m bei 4:00/km · 200m Trabpause · 2km Auslaufen".

elevation_gain_m (INTEGER) — für JEDE Lauf-Session (Easy Run, Long Run, Trail Run, Hill Session etc.) die geschätzten Höhenmeter dieser Einheit, passend zum Terrain und D+ pro km des Rennens. Bei Terrain "road" meist 0 oder gering, bei "trail"/"mixed" realistisch nach Streckenprofil.

WICHTIG: Antworte NUR mit JSON. Kein Text davor oder danach. Kein plan_meta. Beginne direkt mit {{
{{"weeks": [{{"week_number": {week_from}, "phase": "base", "total_km": 40, "sessions": [{{"day_of_week": 1, "session_type": "Interval Session", "distance_km": 10, "duration_min": 55, "session_zone": "Z4-Z5", "warmup_km": 2, "warmup_min": 12, "main_sets": 8, "main_distance_m": 400, "main_pace": "4:00/km", "recovery_m": 200, "cooldown_km": 2, "cooldown_min": 10, "elevation_gain_m": 50, "notes": "2km Einlaufen · 8×400m bei 4:00/km · 200m Trabpause · 2km Auslaufen"}}]}}]}}

Wochen {week_from} bis {week_to}. day_of_week: 1=Mo bis 7=So. Rest Days nicht eintragen. Genau {days_per_week} Sessions pro Woche."""

        print(f"DEBUG: calling anthropic for weeks {week_from}-{week_to}")
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=32000,
            messages=[{"role": "user", "content": prompt}]
        ) as stream:
            message = stream.get_final_message()
        print(f"DEBUG: anthropic call for weeks {week_from}-{week_to} returned, stop_reason={message.stop_reason}, blocks={len(message.content)}")

        raw = ""
        for block in message.content:
            if hasattr(block, 'text') and getattr(block, 'type', None) == 'text':
                raw += block.text
        raw = raw.replace('```json', '').replace('```', '').strip()
        if not raw.startswith('{'):
            json_match = re.search(r'\{[\s\S]*"weeks"[\s\S]*\}', raw)
            if json_match:
                raw = json_match.group(0)
        try:
            part_json = json.loads(raw)
            all_weeks.extend(part_json.get('weeks', []))
            print(f"OK weeks {week_from}-{week_to}: {len(part_json.get('weeks', []))} weeks")
        except Exception as parse_err:
            print(f"JSON parse error for weeks {week_from}-{week_to}: {parse_err}")
            print(f"Raw length: {len(raw)}")
            print(f"Raw start: {raw[:500]}")
            print(f"Stop reason: {message.stop_reason}")
            continue

    plan_json = {"weeks": all_weeks}
    plan_json = apply_post_processing(plan_json)
    plan_json = validate_and_fix_plan(plan_json, race_date, start_monday)

    # ─── In DB speichern ───
    print("DEBUG: starting DB save")
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO plans (name, goal_type, race_name, race_date, race_distance_km,
                total_weeks, days_per_week, long_run_day, quality_sessions, strength_sessions, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
            RETURNING id
        """, (
            race_name or f"{goal_type} Plan {total_weeks}W",
            goal_type, race_name,
            race_date if race_date else None,
            race_distance_km or 0,
            total_weeks, days_per_week, long_run_day,
            quality_sessions, strength_sessions
        ))
        plan_id = cur.fetchone()[0]
        print(f"DEBUG: plan metadata inserted, plan_id={plan_id}")

        cur.execute("UPDATE plans SET status='archived' WHERE status='active' AND id != %s", (plan_id,))
        print("DEBUG: old plans archived")

        cur.execute("DELETE FROM training_plan WHERE plan_id = %s OR plan_id IS NULL", (plan_id,))
        print("DEBUG: old training_plan rows deleted")

        sessions_inserted = 0
        for week in plan_json.get('weeks', []):
            week_num = week.get('week_number', 1)
            phase = week.get('phase', 'base')
            week_monday = start_monday + timedelta(weeks=week_num - 1)

            for session in week.get('sessions', []):
                day_of_week = session.get('day_of_week', 1)
                if week_num == 1 and day_of_week < actual_start_day:
                    continue
                cur.execute("""
                    INSERT INTO training_plan
                    (week_date, day_of_week, session_type, session_zone,
                     duration_min, distance_km, notes, phase, plan_id, plan_week,
                     warmup_km, warmup_min, main_sets, main_distance_m, main_pace,
                     recovery_m, cooldown_km, cooldown_min, elevation_gain_m)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    week_monday,
                    day_of_week,
                    session.get('session_type', 'Easy Run'),
                    session.get('session_zone', ''),
                    session.get('duration_min', 0),
                    session.get('distance_km', 0),
                    session.get('notes', ''),
                    phase,
                    plan_id,
                    week_num,
                    session.get('warmup_km'),
                    session.get('warmup_min'),
                    session.get('main_sets'),
                    session.get('main_distance_m'),
                    session.get('main_pace'),
                    session.get('recovery_m'),
                    session.get('cooldown_km'),
                    session.get('cooldown_min'),
                    session.get('elevation_gain_m'),
                ))
                sessions_inserted += 1
        print(f"DEBUG: sessions insert loop complete, sessions_inserted={sessions_inserted}")

        conn.commit()
        print("DEBUG: DB transaction committed")
    finally:
        conn.close()

    # ─── Workout-Vorschläge: Coach vergleicht geplante Strength-Sessions mit den CAIRN-Routinen ───
    print("DEBUG: starting workout suggestions")
    try:
        strength_notes = []
        for week in plan_json.get('weeks', []):
            for session in week.get('sessions', []):
                if session.get('session_type') == 'Strength Training':
                    note = session.get('notes', '') or 'Strength Training'
                    if note not in strength_notes:
                        strength_notes.append(note)
        print(f"DEBUG: found {len(strength_notes)} distinct strength session notes")

        if strength_notes and hevy_context:
            profile_conn = get_db()
            try:
                profile_cur = profile_conn.cursor()
                profile_cur.execute("""
                    SELECT long_term_goals, cross_rennrad, cross_schwimmen, cross_wandern, cross_ski
                    FROM athlete_profile ORDER BY id LIMIT 1
                """)
                profile_row = profile_cur.fetchone()
            finally:
                profile_conn.close()
            print("DEBUG: athlete_profile (workout suggestions) query complete")

            long_term_goals = (profile_row[0] if profile_row else '') or 'keine angegeben'
            cross_prefs = []
            if profile_row:
                if profile_row[1]: cross_prefs.append('Rennrad')
                if profile_row[2]: cross_prefs.append('Schwimmen')
                if profile_row[3]: cross_prefs.append('Wandern')
                if profile_row[4]: cross_prefs.append('Ski')

            athlete_profile_context = f"ATHLETENPROFIL LANGZEITZIELE: {long_term_goals}"
            if cross_prefs:
                athlete_profile_context += f"\nCROSS TRAINING PRÄFERENZEN: {', '.join(cross_prefs)}"

            suggestion_prompt = f"""Du bist CAIRN Coach. Du hast gerade einen neuen Trainingsplan erstellt.

{athlete_profile_context}

GEPLANTE STRENGTH-TRAINING-EINHEITEN IN DIESEM PLAN:
{chr(10).join('- ' + n for n in strength_notes)}

{hevy_context}

AUFGABE:
Vergleiche die geplanten Einheiten mit den offiziellen CAIRN-Routinen und deren Übungsauswahl.
Wo sinnvoll: schlage konkrete Anpassungen vor — Übungen ergänzen, streichen oder anpassen.
Nur wenn es wirklich etwas zu verbessern gibt, nicht erzwingen. Maximal 4 Vorschläge. Wenn nichts zu verbessern ist: leere Liste.
Berücksichtige die Langzeitziele des Athleten. Wenn Optik oder ästhetische Ziele genannt sind, haben diese Vorrang vor reinem Functional Training. Schlage nur Änderungen vor die WIRKLICH fehlen — prüfe die Übungsliste sorgfältig bevor du etwas vorschlägst.

Für jeden Vorschlag:
- workout_name: exakt einer der oben genannten geplanten Einheiten-Namen
- change: was konkret ändern (1 Satz, mit konkreten Übungsnamen)
- reason: warum (1-2 Sätze, CAIRN-Ton — ruhig, direkt, wie ein erfahrener Bergführer, nie wie Software)

Antworte NUR mit JSON:
{{"suggestions": [{{"workout_name": "...", "change": "...", "reason": "..."}}]}}"""

            sugg_message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1200,
                messages=[{"role": "user", "content": suggestion_prompt}]
            )
            print(f"DEBUG: workout suggestions anthropic call complete, stop_reason={sugg_message.stop_reason}")
            sugg_raw = ""
            for block in sugg_message.content:
                if hasattr(block, 'text'):
                    sugg_raw += block.text
            sugg_raw = sugg_raw.replace('```json', '').replace('```', '').strip()
            if not sugg_raw.startswith('{'):
                m = re.search(r'\{[\s\S]*"suggestions"[\s\S]*\}', sugg_raw)
                if m:
                    sugg_raw = m.group(0)

            raw_suggestions = json.loads(sugg_raw).get('suggestions', [])
            print(f"DEBUG: parsed {len(raw_suggestions)} workout suggestions")
            now_iso = datetime.utcnow().isoformat()
            workout_suggestions = [
                {
                    "id": f"{plan_id}-{i + 1}",
                    "workout_name": s.get('workout_name', ''),
                    "change": s.get('change', ''),
                    "reason": s.get('reason', ''),
                    "status": "pending",
                    "comment": None,
                    "created_at": now_iso,
                    "responded_at": None,
                }
                for i, s in enumerate(raw_suggestions)
            ]

            if workout_suggestions:
                sugg_conn = get_db()
                try:
                    sugg_cur = sugg_conn.cursor()
                    sugg_cur.execute(
                        "UPDATE plans SET workout_suggestions = %s WHERE id = %s",
                        (Json(workout_suggestions), plan_id)
                    )
                    sugg_conn.commit()
                    print("DEBUG: workout_suggestions saved to DB")
                finally:
                    sugg_conn.close()
    except Exception as sugg_err:
        print(f"Workout-Vorschläge Fehler: {sugg_err}")

    print(f"generate_plan completed, sessions={sessions_inserted}")
    return sessions_inserted


def main():
    if len(sys.argv) < 2:
        print("Usage: python data/generate_plan.py <job_id>")
        sys.exit(1)
    job_id = sys.argv[1]
    print(f"generate_plan started (job_id={job_id})")

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT data FROM plan_jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        print(f"Job {job_id} nicht gefunden")
        sys.exit(1)

    data = row[0]
    update_job_status(job_id, 'running')

    try:
        sessions_inserted = generate_plan(job_id, data)
        update_job_status(job_id, 'done')
        print(f"Job {job_id} abgeschlossen, {sessions_inserted} Sessions gespeichert.")
    except Exception as e:
        trace = traceback.format_exc()
        print(f"Job {job_id} fehlgeschlagen: {e}\n{trace}")
        update_job_status(job_id, 'error', error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
