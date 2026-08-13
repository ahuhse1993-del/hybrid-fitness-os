"""
CAIRN – Plan-Generierung, hybrider Ansatz.

Phase 1 (deterministisch, kein LLM): Aus den Fragebogen-Daten + Athletenprofil wird ein
vollständiges "Skelett" gebaut — jede Session bekommt day_of_week, session_type,
distance_km, elevation_gain_m und duration_min bereits fest zugewiesen. Ein Validator
prüft dieses Skelett zweimal (Gesamtplan + nochmal pro Woche) BEVOR irgendein LLM-Aufruf
passiert. Bei einem ungültigen Skelett wird kein Plan erzeugt.

Phase 2 (LLM, pro Woche ein Call): Der Coach bekommt das fixe Skelett einer Woche und darf
nur "Flavor"-Felder ergänzen (notes, session_zone, warmup/main/cooldown-Struktur). Die
fixierten Felder werden danach mit den Skelett-Werten überschrieben, falls das Modell sie
trotzdem angefasst hat.

Die reine Skelett-Logik (Daten, Phasen, Zielvolumen, Höhenmeter, Tages-Layout, Validator)
ist frei von DB-/API-Zugriffen und direkt testbar — siehe data/test_generate_plan.py.

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

DAY_NAMES = ['', 'Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
DAY_ABBR = ['', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']

QUALITY_TYPES = {'Tempo Session', 'Interval Session', 'Sprint Session', 'Hill Session'}
ENDURANCE_RUN_TYPES = {'Easy Run', 'Trail Run', 'Recovery Run'}

# Kein Pace hinterlegt -> Default-Paces (min/km)
DEFAULT_PACE_MIN_PER_KM = {
    'easy': 6.5,      # 6:30/km
    'trail': 8.0,     # 8:00/km + HM-Zuschlag
    'quality': 5.5,   # 5:30/km
    'long_run': 7.0,  # 7:00/km
}

# Grobe HM/km-Schätzung falls kein reales HM-Trainingsdatum vorliegt (avg_weekly_hm nicht
# aus der DB ableitbar, da trainings-Tabelle keine Höhenmeter-Historie führt).
TERRAIN_HM_PER_KM_ESTIMATE = {'trail': 15, 'mixed': 10, 'road': 3}

FLAVOR_FIELDS = ('notes', 'session_zone', 'warmup_km', 'warmup_min', 'main_sets',
                  'main_distance_m', 'main_pace', 'recovery_m', 'cooldown_km', 'cooldown_min')
FIXED_FIELDS = ('day_of_week', 'session_type', 'distance_km', 'elevation_gain_m', 'duration_min')

# DB-Spaltenlaengen (training_plan) fuer String-Flavor-Felder, die das LLM frei befuellt. Der
# Prompt bittet um kurze Werte, aber ohne harten Cutoff crasht ein zu langer LLM-Wert den INSERT
# mit psycopg2.errors.StringDataRightTruncation (character varying(N)) erst NACH bereits erfolgtem
# plan-metadata-INSERT, mitten in der Session-Insert-Schleife.
FLAVOR_FIELD_MAXLEN = {'session_zone': 20, 'main_pace': 50}


class SkeletonError(Exception):
    """Eingabefehler oder ungültiges Skelett — es wird kein Plan erzeugt, kein LLM-Aufruf."""
    pass


# ══════════════════════════════════════════════════════════════════════════
# ═══ Reine Domänenlogik — keine DB/API-Zugriffe, direkt testbar ═══
# ══════════════════════════════════════════════════════════════════════════

def validate_inputs(strength_sessions, strength_days, days_per_week):
    """Klarstellung 2: strength_sessions muss der Anzahl eindeutiger, gültiger strength_days
    entsprechen. Klarstellung 3: belegte Trainingstage (Kraft inbegriffen) <= days_per_week."""
    for d in strength_days:
        if not isinstance(d, int) or d < 1 or d > 7:
            raise SkeletonError(f"Eingabefehler: ungültiger strength_day {d!r} (erwartet 1-7)")
    unique_days = set(strength_days)
    if len(unique_days) != strength_sessions:
        raise SkeletonError(
            f"Eingabefehler: strength_sessions={strength_sessions} entspricht nicht der Anzahl "
            f"eindeutiger strength_days={sorted(unique_days)} ({len(unique_days)})"
        )
    ausdauer_days = days_per_week - strength_sessions
    if ausdauer_days < 0:
        raise SkeletonError(
            f"Eingabefehler: strength_sessions={strength_sessions} > days_per_week={days_per_week}"
        )
    return ausdauer_days


def normalize_dates(start_date_str, race_date_str):
    """Berechnet start_monday, race_week_monday, total_weeks, actual_start_day, race_dow
    deterministisch. total_weeks wird NIE vom LLM bestimmt (Klarstellung 6)."""
    start_day = date.fromisoformat(start_date_str)
    race_day = date.fromisoformat(race_date_str)
    if race_day < start_day:
        raise SkeletonError(f"Eingabefehler: race_date {race_date_str} liegt vor start_date {start_date_str}")

    start_monday = start_day - timedelta(days=start_day.weekday())
    race_week_monday = race_day - timedelta(days=race_day.weekday())
    total_weeks = round((race_week_monday - start_monday).days / 7) + 1

    return {
        'start_day': start_day,
        'start_monday': start_monday,
        'race_day': race_day,
        'race_week_monday': race_week_monday,
        'total_weeks': total_weeks,
        'actual_start_day': start_day.isoweekday(),   # 1=Mo..7=So
        'race_dow': race_day.isoweekday(),
    }


def compute_phase_map(total_weeks):
    """Phasenzuweisung rückwärts von race_date (letzte Woche = RACE)."""
    taper_weeks = 1 if total_weeks <= 12 else 2
    phase_by_week = {}

    if total_weeks >= 1:
        phase_by_week[total_weeks] = 'RACE'
    for i in range(1, taper_weeks + 1):
        wk = total_weeks - i
        if wk >= 1:
            phase_by_week[wk] = 'TAPER'
    peak_week = total_weeks - taper_weeks - 1
    if peak_week >= 1:
        phase_by_week[peak_week] = 'PEAK'

    remaining = [w for w in range(1, total_weeks + 1) if w not in phase_by_week]
    for w in remaining:
        if w % 4 == 0:
            phase_by_week[w] = 'DELOAD'

    remaining = [w for w in range(1, total_weeks + 1) if w not in phase_by_week]
    base_count = round(len(remaining) * 0.35)
    for i, w in enumerate(remaining):
        phase_by_week[w] = 'BASE' if i < base_count else 'BUILD'

    return phase_by_week, taper_weeks, peak_week


def compute_weekly_progression(phase_by_week, total_weeks, start_value, desired_peak, max_value,
                                deload_pct=0.775, taper_pct=0.70, race_pct=0.35,
                                race_week_available_ratio=1.0):
    """Progression über die Belastungswochen (BASE/BUILD/PEAK), unabhängig für km ODER hm aufrufbar.
    Nach einem Deload wird die nächste Belastungswoche gegen die letzte Belastungswoche VOR
    dem Deload verglichen (last_load wird während DELOAD nicht aktualisiert).

    Statt naiv jede Woche maximal +10% zu wachsen (was die Zielobergrenze oft schon Wochen vor
    der eigentlichen Peak-Woche erreicht und danach mehrfach identisch plateauen lässt — siehe
    BUILD-PLATEAU-Regeln), wird die ideale GLEICHMÄSSIGE geometrische Wachstumsrate über alle
    Belastungswochen bis zur Peak-Woche berechnet und als Schrittweite verwendet (weiterhin
    gedeckelt bei max. +10%/Woche als Sicherheitsobergrenze). Ist selbst +10%/Woche zu wenig um
    das Ziel rechtzeitig zu erreichen (desired_peak zu hoch für die verfügbare Zeit), bleibt die
    bisherige Maximalprogression aktiv und die Peak-Woche erreicht das Ziel entsprechend nicht."""
    load_week_nums = [w for w in range(1, total_weeks + 1) if phase_by_week.get(w) in ('BASE', 'BUILD', 'PEAK')]
    n_load_weeks = len(load_week_nums)
    if n_load_weeks >= 2 and start_value > 0 and desired_peak > start_value:
        ideal_ratio = (desired_peak / start_value) ** (1 / (n_load_weeks - 1))
    else:
        ideal_ratio = 1.10
    step_ratio = min(1.10, ideal_ratio)

    targets = {}
    last_load = start_value
    peak_actual = None

    for week_num in range(1, total_weeks + 1):
        phase = phase_by_week.get(week_num, 'BUILD')
        if phase in ('BASE', 'BUILD', 'PEAK'):
            if week_num == 1:
                candidate = min(start_value, max_value)
            else:
                candidate = min(last_load * step_ratio, desired_peak, max_value)
            candidate = min(candidate, max_value)
            targets[week_num] = round(candidate, 1)
            last_load = candidate
            if phase == 'PEAK':
                peak_actual = candidate
        elif phase == 'DELOAD':
            targets[week_num] = round(last_load * deload_pct, 1)
        elif phase == 'TAPER':
            reference = peak_actual if peak_actual is not None else last_load
            targets[week_num] = round(reference * taper_pct, 1)
        elif phase == 'RACE':
            reference = peak_actual if peak_actual is not None else last_load
            targets[week_num] = round(reference * race_pct * race_week_available_ratio, 1)

    return targets, peak_actual


def enforce_max_plateau(targets, phase_by_week, load_phases, conflicts, label, max_run=2):
    """BUILD-PLATEAU Regel 13: maximal 2 identische aufeinanderfolgende Belastungswochen. Bei
    einer 3. (oder weiteren) identischen Woche in Folge wird leicht reduziert und dokumentiert —
    ein Sicherheitsnetz für Fälle, in denen die geometrische Glättung (compute_weekly_progression)
    wegen einer bindenden Obergrenze (max_km/race_distance_km) trotzdem ins Plateau läuft."""
    weeks_sorted = sorted(w for w in targets if phase_by_week.get(w) in load_phases)
    i = 0
    while i < len(weeks_sorted):
        j = i + 1
        while j < len(weeks_sorted) and targets[weeks_sorted[j]] == targets[weeks_sorted[i]]:
            j += 1
        run_len = j - i
        if run_len > max_run:
            for k in range(i + max_run, j):
                wk = weeks_sorted[k]
                steps = k - (i + max_run) + 1
                new_val = round(targets[wk] * (1 - 0.02 * steps), 1)
                conflicts.append(
                    f"Woche {wk}: {label} von {targets[wk]} auf {new_val} reduziert — maximal "
                    f"{max_run} identische Belastungswochen in Folge erlaubt."
                )
                targets[wk] = new_val
        i = j
    return targets


def apply_partial_week1_reduction(targets, actual_start_day):
    """Angebrochene erste Woche: target proportional zu verfügbaren Tagen reduzieren."""
    if actual_start_day > 1 and 1 in targets:
        available_days = 7 - actual_start_day + 1
        targets[1] = round(targets[1] * (available_days / 7), 1)
    return targets


def compute_desired_peak_km(avg_weekly_km, race_distance_km, max_km):
    return min(avg_weekly_km * 1.30, race_distance_km * 1.50, max_km)


def compute_desired_peak_hm(avg_weekly_hm, race_elevation_m):
    """Root-Cause-Fix: vorher min(race*0.85, avg_hm*1.30) — bei einer (mangels echter HM-Historie)
    grob geschätzten avg_weekly_hm dominierte praktisch immer der historische Wert und deckelte
    das Ziel weit unter die Renn-Anforderung, unabhängig von der verfügbaren Vorbereitungszeit
    (z.B. 1246 HM Renn-HM -> desired_peak_hm nur 702). Jetzt renn-verankert (Ziel ~105% der
    Renn-HM, innerhalb des 90-120%-Korridors für Trail 20-42km), mit avg_weekly_hm nur noch als
    Untergrenze (falls der Athlet aktuell bereits mehr HM/Woche trainiert als das Rennen verlangt).
    WIE SCHNELL realistisch dorthin gewachsen werden darf, regelt weiterhin ausschließlich die
    sichere Progression in compute_weekly_progression() (max. +10%/Woche) — dieses Ziel ist nur
    die Obergrenze, kein erzwungener Wert."""
    return max(race_elevation_m * 1.05, avg_weekly_hm * 1.05)


def estimate_avg_weekly_hm(avg_weekly_km, terrain):
    """Fallback wenn keine reale HM-Trainingshistorie vorliegt."""
    per_km = TERRAIN_HM_PER_KM_ESTIMATE.get(terrain, TERRAIN_HM_PER_KM_ESTIMATE['road'])
    return avg_weekly_km * per_km


def compute_longrun_km(target_km, phase, race_distance_km):
    """Reiner Zielanteil (35-40% normal, bis 45% Peak) — wird von compute_longrun_progression()
    zusätzlich gegen die eigene +10%/Woche-Progressionskette gedeckelt (niedrigere Distanz gilt)."""
    fraction = 0.425 if phase == 'PEAK' else 0.375
    longrun = target_km * fraction
    ceiling = min(race_distance_km * 0.80, target_km)
    return round(min(longrun, ceiling), 1)


def compute_desired_peak_longrun_km(race_distance_km, max_km):
    """Renn-verankertes Peak-Longrun-Ziel für Trailrennen 20-42km (Root-Cause-Fix): der Peak-Longrun
    war bisher NUR ein fixer Anteil (42.5%) am bereits durch race_distance_km*1.50/max_km gedeckelten
    Wochenziel (compute_desired_peak_km) — ohne eigene, rennspezifische Zielformel. Bei einem relativ
    kurzen Rennen (z.B. 31km) ergab das nur ~19-20km statt der für einen 31km-Trail sinnvollen
    23-25km. Zielkorridor: 75-82% der Renndistanz (Ziel 78%), gedeckelt durch max_km/Planobergrenze.
    Nur für Trail-Distanzen 20-42km spezifiziert (siehe Aufgabenstellung); ausserhalb dieses Bereichs
    gilt weiterhin ausschliesslich die bestehende Wochenanteils-Logik (Rückgabe None)."""
    if not (20 <= race_distance_km <= 42):
        return None
    target = round(race_distance_km * 0.78, 1)
    return min(target, round(max_km * 0.70, 1))


def compute_longrun_progression(phase_by_week, total_weeks, avg_weekly_km, km_targets, race_distance_km,
                                 max_km=None, conflicts=None):
    """Longrun als Quote von target_km, wobei die Quote selbst über die Belastungswochen linear von
    36.5% (Mittelwert 35-38%, Woche 1) auf einen Ziel-Wert für die PEAK-Woche ansteigt.

    Root-Cause-Fix (Problem 3): vorher plateaute die Quote für Nicht-PEAK-Wochen fix bei 39.5% und
    die PEAK-Woche sprang fix auf 42.5% — bei einer Peak-Woche, die (wie hier) unmittelbar nach
    einem Deload folgt, reicht der bestehende +10%/Woche-Sicherheitsdeckel (candidate = min(...,
    last_normal_longrun*1.10), gegenüber dem zuletzt TATSÄCHLICH GESPEICHERTEN Belastungswochen-Wert
    — unverändert, das ist KEIN Bug, sondern die bewusste Sicherheitsbremse) dann strukturell nicht
    aus, um von einer ohnehin schon zu niedrigen Vorwoche noch das renn-spezifische Ziel zu erreichen.
    Die Quote-Obergrenzen wurden daher angehoben: Nicht-PEAK-Wochen jetzt bis 41.8% (vorher 39.5%,
    weiterhin sicher unter der 40%-Validator-Obergrenze +2% Toleranz = 42%), PEAK-Woche renn-verankert
    über compute_desired_peak_longrun_km() (Trail 20-42km) und gedeckelt bei 46.5% (sicher unter der
    45%-Validator-Obergrenze +2% Toleranz = 47%) statt fix bei 42.5% — das gibt der Kette mehr Raum,
    um VOR der Peak-Woche schon näher an das Renn-Ziel heranzuwachsen, sodass der letzte +10%-Schritt
    tatsächlich ausreicht. Reicht die verfügbare Vorbereitungszeit selbst damit nicht aus, wird das
    Renn-Ziel weiterhin NICHT erzwungen, sondern (dokumentiert in `conflicts`, falls übergeben)
    unterschritten — die sichere Progression hat immer Vorrang vor dem Zielwert.

    Nach einem Deload vergleicht die nächste Belastungswoche weiterhin gegen den letzten NORMALEN
    Belastungs-Longrun (die Kette wird während DELOAD/TAPER nicht aktualisiert)."""
    load_week_nums = [w for w in range(1, total_weeks + 1) if phase_by_week.get(w) in ('BASE', 'BUILD', 'PEAK')]
    START_FRACTION = 0.365      # Mittelwert 35-38%
    BASE_BUILD_CEILING = 0.418  # sicher unter der 40%-Validator-Obergrenze (+2% Toleranz) für Nicht-Peak-Wochen

    peak_week_nums = [w for w in load_week_nums if phase_by_week.get(w) == 'PEAK']
    peak_week_num = peak_week_nums[0] if peak_week_nums else (load_week_nums[-1] if load_week_nums else None)
    peak_target_km = km_targets.get(peak_week_num, 0) if peak_week_num else 0
    race_anchor_peak_km = compute_desired_peak_longrun_km(race_distance_km, max_km) if max_km else None
    if race_anchor_peak_km is not None and peak_target_km > 0:
        # sicher unter der 45%-Validator-Obergrenze (+2% Toleranz = 47%) für die PEAK-Woche
        PEAK_FRACTION = min(race_anchor_peak_km / peak_target_km, 0.465)
    else:
        PEAK_FRACTION = 0.425   # Mittelwert bis 45%, Fallback ohne Renn-Anker (kein Trail 20-42km)

    # Interpolation läuft NUR über BASE/BUILD-Wochen. Die PEAK-Woche bekommt die Quote fix — sonst
    # nähert sich die Interpolation kurz vor der Peak-Woche schon der Peak-Quote an, obwohl für
    # BUILD-Wochen eine niedrigere Obergrenze gilt (Validator prüft phasenabhängig).
    non_peak_load_weeks = [w for w in load_week_nums if phase_by_week.get(w) != 'PEAK']
    n_non_peak = len(non_peak_load_weeks)

    fraction_by_week = {}
    for idx, wn in enumerate(non_peak_load_weeks):
        progress = idx / (n_non_peak - 1) if n_non_peak > 1 else 1.0
        fraction_by_week[wn] = START_FRACTION + (BASE_BUILD_CEILING - START_FRACTION) * progress
    for wn in load_week_nums:
        if phase_by_week.get(wn) == 'PEAK':
            fraction_by_week[wn] = PEAK_FRACTION

    start_value = round(avg_weekly_km * START_FRACTION, 1)
    ceiling = race_distance_km * 0.82

    longrun_by_week = {}
    last_normal_longrun = None

    for week_num in range(1, total_weeks + 1):
        phase = phase_by_week.get(week_num, 'BUILD')

        if phase in ('BASE', 'BUILD', 'PEAK'):
            target_km = km_targets.get(week_num, 0)
            fraction_based = round(target_km * fraction_by_week.get(week_num, START_FRACTION), 1)
            if last_normal_longrun is None:
                candidate = fraction_based if fraction_based > 0 else start_value
            else:
                candidate = min(fraction_based, round(last_normal_longrun * 1.10, 1))
            value = round(min(candidate, ceiling), 1)
            longrun_by_week[week_num] = value
            last_normal_longrun = value
            if phase == 'PEAK' and race_anchor_peak_km is not None and value < race_anchor_peak_km - 0.05 and conflicts is not None:
                conflicts.append(
                    f"Woche {week_num} (PEAK): renn-verankertes Longrun-Ziel {race_anchor_peak_km}km bei "
                    f"verfügbarer Vorbereitungszeit/Basis nicht sicher erreichbar, Peak-Longrun bleibt bei "
                    f"{value}km (sichere +10%/Woche-Progression greift)."
                )
        elif phase == 'DELOAD':
            reference = last_normal_longrun if last_normal_longrun is not None else start_value
            longrun_by_week[week_num] = round(min(reference * 0.775, ceiling), 1)
        elif phase == 'TAPER':
            reference = last_normal_longrun if last_normal_longrun is not None else start_value
            longrun_by_week[week_num] = round(min(reference * 0.70, ceiling), 1)
        # RACE: kein Longrun (build_week_skeleton platziert ohnehin keinen)

    return longrun_by_week


def compute_longrun_hm(target_hm):
    """Anteil des Longrun am Wochen-HM-Ziel. Root-Cause-Fix: von 0.55 auf 0.78 angehoben — bei
    0.55 ergab die Peak-Woche (target_hm == desired_peak_hm) einen Peak-Longrun von nur ~55% der
    renn-verankerten Ziel-HM statt der spezifizierten 75-90% (siehe compute_desired_peak_hm)."""
    return round(target_hm * 0.78)


def opposite_adjacent_days(day):
    """Tag davor / danach innerhalb derselben Kalenderwoche (kein Wrap über Wochengrenzen)."""
    before = day - 1 if day > 1 else None
    after = day + 1 if day < 7 else None
    return before, after


def solve_week_layout(open_days, gym_days, longrun_day, quality_target, is_race_week, is_deload, is_taper,
                       extra_forbidden_quality_days=None):
    """Löst Quality-Tage und Lower-/Upper-Body-Zuweisung GEMEINSAM (Klarstellung 5) über eine
    kleine Kombinatorik (max. wenige Gymtage). Bevorzugt: möglichst viele Quality-Slots
    gefunden, Lower Body sinnvoll platziert wenn Phase das zulässt."""
    forbidden_for_quality = set(extra_forbidden_quality_days or ())
    if longrun_day:
        b, a = opposite_adjacent_days(longrun_day)
        if b: forbidden_for_quality.add(b)
        if a: forbidden_for_quality.add(a)

    def dist_from_longrun(d):
        if not longrun_day:
            return 99
        diff = abs(d - longrun_day)
        return min(diff, 7 - diff)

    quality_pool_base = sorted(
        [d for d in open_days if d not in gym_days and d not in forbidden_for_quality],
        key=lambda d: -dist_from_longrun(d)
    )

    lower_candidates = [None]
    if not (is_race_week or is_deload):
        lower_candidates += sorted(gym_days)

    best = None
    for lower_day in lower_candidates:
        if lower_day is not None and longrun_day:
            b, a = opposite_adjacent_days(longrun_day)
            if lower_day in (b, a):
                continue  # Lower Body Tag vor/nach Longrun verboten

        local_pool = list(quality_pool_base)
        if lower_day is not None:
            _, after_lower = opposite_adjacent_days(lower_day)
            if after_lower:
                local_pool = [d for d in local_pool if d != after_lower]  # Lower Body -> Tag danach kein Quality

        chosen_quality = local_pool[:quality_target]
        score = len(chosen_quality) * 100
        if lower_day is not None and not is_taper:
            score += 10

        candidate = {
            'quality_days': sorted(chosen_quality),
            'lower_body_day': lower_day,
            'upper_body_days': sorted(d for d in gym_days if d != lower_day),
        }
        if best is None or score > best[0]:
            best = (score, candidate)

    return best[1] if best else {'quality_days': [], 'lower_body_day': None, 'upper_body_days': sorted(gym_days)}


def pick_quality_type(index, terrain, phase=None):
    """phase='TAPER': keine harte Hill Session mehr kurz vor dem Rennen (Root-Cause-Fix — vorher
    stand 'Hill Session' bei Trail/Mixed immer an erster Stelle und wurde dadurch fuer den
    einzigen Taper-Quality-Slot (index 0) IMMER gewaehlt, unabhaengig von der Phase)."""
    if phase == 'TAPER':
        types = (['Tempo Session', 'Interval Session', 'Hill Session'] if terrain in ('trail', 'mixed')
                 else ['Tempo Session', 'Interval Session', 'Sprint Session'])
    elif terrain in ('trail', 'mixed'):
        types = ['Hill Session', 'Tempo Session', 'Interval Session']
    else:
        types = ['Tempo Session', 'Interval Session', 'Sprint Session']
    return types[index % len(types)]


def clamp_flavor_field_lengths(llm_data):
    """Schneidet LLM-generierte Flavor-Felder auf die DB-Spaltenlaenge (FLAVOR_FIELD_MAXLEN), damit
    ein zu langer Wert (LLM ignoriert den Prompt-Hinweis) nicht mehr den training_plan-INSERT mit
    StringDataRightTruncation crasht. Aendert llm_data in-place und gibt es zurueck."""
    for f, maxlen in FLAVOR_FIELD_MAXLEN.items():
        v = llm_data.get(f)
        if isinstance(v, str) and len(v) > maxlen:
            llm_data[f] = v[:maxlen].rstrip()
    return llm_data


def pick_cross_training_days(candidate_days, cross_count, hard_days):
    """Waehlt die 'besten' verfuegbaren Tage fuer Cross Training statt einfach die ersten freien
    Wochentage aufsteigend zu nehmen (Root-Cause-Fix — vorher landete Cross Training dadurch
    fast immer auf dem fruehesten offenen Tag, i.d.R. Montag). Greedy: waehlt bei jedem Schritt
    den Tag mit maximalem Mindestabstand zu bereits belegten 'harten' Tagen (Longrun/Quality/Gym)
    UND zu bereits gewaehlten Cross-Tagen, damit mehrere Cross-Sessions sich ueber die Woche
    verteilen statt zu clustern."""
    if cross_count <= 0 or not candidate_days:
        return []
    hard_days = set(hard_days)
    remaining = list(candidate_days)
    chosen = []
    for _ in range(min(cross_count, len(remaining))):
        reference = hard_days | set(chosen)

        def score(d):
            if not reference:
                return (0, d)
            return (-min(abs(d - r) for r in reference), d)

        best = min(remaining, key=score)
        chosen.append(best)
        remaining.remove(best)
    return sorted(chosen)


def build_week_skeleton(week_num, phase, race_dow, is_race_week, actual_start_day, is_week1,
                         long_run_day, strength_days, quality_sessions_input, cross_training,
                         cross_training_days, ausdauer_days, terrain, conflicts,
                         week_monday=None, race_day=None, prev_week_longrun_day=None,
                         is_final_taper_week=False):
    """TERMINIERUNG Schritt 1-7. Gibt eine sortierte Liste von Session-Skeletten
    (ohne distance_km/elevation_gain_m/duration_min, die kommen erst danach) zurück.
    is_final_taper_week: die Kalenderwoche unmittelbar vor der Rennwoche (Klarstellung 3) —
    dort gilt Sonntag zwingend als Rest und Cross Training nur früh in der Woche (Regel 16)."""
    sessions = []

    available_days = set(range(1, 8))
    if is_week1 and actual_start_day > 1:
        available_days = set(range(actual_start_day, 8))
    if is_race_week:
        available_days = {d for d in available_days if d <= race_dow}  # keine Sessions nach Race Day
    if is_final_taper_week:
        available_days.discard(7)  # Sonntag = zwingend Rest (Regel 16)

    reserved = set()

    # 1. Race Day
    if is_race_week:
        sessions.append({'day_of_week': race_dow, 'session_type': 'Race Day'})
        reserved.add(race_dow)

    # 2. Longrun (ausser Rennwoche, nur wenn Datum im Planzeitraum). Regel 15: der letzte längere
    # Lauf muss mindestens 8 Tage vor Race Day liegen — in der letzten Taper-Woche ist das für
    # KEINEN Tag mehr erfüllbar (siehe Klarstellung 3), daher entfällt der Longrun dort komplett.
    longrun_placed_day = None
    longrun_too_close_to_race = False
    if week_monday is not None and race_day is not None and long_run_day in available_days:
        longrun_date = week_monday + timedelta(days=long_run_day - 1)
        days_before_race = (race_day - longrun_date).days
        longrun_too_close_to_race = days_before_race < 8
    if not is_race_week and not longrun_too_close_to_race and long_run_day in available_days:
        longrun_placed_day = long_run_day
        sessions.append({'day_of_week': long_run_day, 'session_type': 'Long Run'})
        reserved.add(long_run_day)
    elif longrun_too_close_to_race and not is_race_week:
        conflicts.append(f"Woche {week_num}: Longrun-Tag {long_run_day} liegt weniger als 8 Tage vor Race Day, "
                          f"Longrun entfällt (nur Shakeout/Easy erlaubt).")

    # 3. Gymtage reservieren (Kollision Race Day/Longrun -> Gym entfällt, wird NICHT nachgeholt)
    gym_days = sorted(d for d in strength_days if d in available_days and d not in reserved)
    if len(gym_days) < len(set(strength_days) & available_days):
        pass  # (kann strukturell nicht auftreten, gym_days ist bereits die Schnittmenge)
    dropped_gym_days = sorted((set(strength_days) & available_days) - set(gym_days))
    for d in dropped_gym_days:
        conflicts.append(f"Woche {week_num}: Gymtag {d} kollidiert mit Race Day/Longrun, entfällt.")

    # 4. Quality-Tage + Lower/Upper gemeinsam lösen
    if is_race_week or phase == 'DELOAD':
        quality_target = 0
    elif phase == 'TAPER':
        quality_target = min(1, quality_sessions_input)
    else:
        quality_target = quality_sessions_input

    open_for_layout = available_days - reserved
    # Wochenübergreifend: Sonntag-Longrun der Vorwoche verbietet Quality am Montag dieser Woche
    extra_forbidden_quality = {1} if prev_week_longrun_day == 7 and 1 in open_for_layout else None

    layout = solve_week_layout(
        open_days=open_for_layout,
        gym_days=set(gym_days),
        longrun_day=longrun_placed_day,
        quality_target=quality_target,
        is_race_week=is_race_week,
        is_deload=(phase == 'DELOAD'),
        is_taper=(phase == 'TAPER'),
        extra_forbidden_quality_days=extra_forbidden_quality,
    )
    if len(layout['quality_days']) < quality_target:
        conflicts.append(
            f"Woche {week_num}: kein sinnvoller Quality-Slot für {quality_target - len(layout['quality_days'])} "
            f"von {quality_target} geplanten Quality Sessions gefunden — entfällt."
        )

    for i, d in enumerate(layout['quality_days']):
        sessions.append({'day_of_week': d, 'session_type': pick_quality_type(i, terrain, phase)})
    reserved |= set(layout['quality_days'])

    for d in gym_days:
        if d == layout['lower_body_day']:
            day_before_lower, _ = opposite_adjacent_days(d)
            focus = 'lower_moderate' if day_before_lower in layout['quality_days'] else 'lower'
        elif phase == 'DELOAD':
            focus = 'full_light'
        elif phase == 'TAPER' or is_race_week:
            focus = 'upper_light'
        else:
            focus = 'upper'
        sessions.append({'day_of_week': d, 'session_type': 'Strength Training', '_strength_focus': focus})
    if layout['lower_body_day'] is None and gym_days and not (is_race_week or phase == 'DELOAD'):
        conflicts.append(f"Woche {week_num}: kein geeigneter Lower-Body-Slot verfügbar, alle Gymtage als Upper Body.")
    reserved |= set(gym_days)

    # 5. Cross Training platzieren (ersetzt Easy Run). Regel 10/11: Cross bleibt auch im Deload
    # bestehen (nur Dauer/Intensität werden reduziert, siehe compute_duration_min-Nachbearbeitung
    # weiter unten) — wird NICHT durch einen Easy Run ersetzt. Regel 16: in der letzten Taper-
    # Woche nur früh in der Woche (Tag 1-3), locker.
    remaining_days = sorted(available_days - reserved)
    cross_count = 0
    if cross_training and not is_race_week and cross_training_days > 0:
        # 'harte' Tage, zu denen Cross Training moeglichst Abstand halten soll (siehe
        # pick_cross_training_days) — Root-Cause-Fix fuer Punkt 1: vorher wurden einfach die
        # ersten freien Tage aufsteigend genommen, wodurch Cross fast immer auf Montag landete.
        hard_days_for_cross = set(gym_days) | set(layout['quality_days'])
        if longrun_placed_day:
            hard_days_for_cross.add(longrun_placed_day)
        if is_final_taper_week:
            # Regel 16: in der letzten Taper-Woche weiterhin frueh in der Woche bevorzugen (Tag 1-3
            # vor Tag 4+), aber INNERHALB dieser Praeferenz weiterhin den best verfuegbaren Tag waehlen.
            early = [d for d in remaining_days if d <= 3]
            late = [d for d in remaining_days if d > 3]
            chosen_cross_days = pick_cross_training_days(early, cross_training_days, hard_days_for_cross)
            if len(chosen_cross_days) < cross_training_days:
                chosen_cross_days += pick_cross_training_days(
                    late, cross_training_days - len(chosen_cross_days),
                    hard_days_for_cross | set(chosen_cross_days),
                )
        else:
            chosen_cross_days = pick_cross_training_days(remaining_days, cross_training_days, hard_days_for_cross)
        chosen_cross_days = sorted(chosen_cross_days)
        cross_count = len(chosen_cross_days)
        for d in chosen_cross_days:
            sessions.append({'day_of_week': d, 'session_type': 'Cross Training'})
        reserved |= set(chosen_cross_days)

    # 6. Easy Runs platzieren
    remaining_days = sorted(available_days - reserved)
    endurance_used = (1 if longrun_placed_day else 0) + len(layout['quality_days']) + cross_count + (1 if is_race_week else 0)
    easy_slots = max(0, ausdauer_days - endurance_used)
    easy_days = remaining_days[:easy_slots]
    for d in easy_days:
        sessions.append({'day_of_week': d, 'session_type': 'Easy Run'})

    # 7. Resttage: keine Session = Rest Day, wird nicht gespeichert

    return sorted(sessions, key=lambda s: s['day_of_week'])


def distribute_week_km(target_km, phase, sessions, race_distance_km, longrun_km_override=None,
                        conflicts=None, week_num=None, cross_replaces_run=False):
    """Verteilt Laufkilometer deterministisch auf die Sessions einer Woche. Race Day zählt NICHT
    ins Wochenvolumen. Gibt (sessions, actual_target_run_km, cross_adjusted_target_run_km) zurück.

    CROSS ALS LAUFERSATZ (Root-Cause-Fix): target_km ist das reine, unbeeinflusste
    Progressions-Laufziel (base_target_run_km, = die historische Laufbasis avg_weekly_km
    hochgerechnet — NICHT die historical_run_baseline_km selbst reduzieren, siehe
    build_full_skeleton). Ob und wie stark Cross Training die tatsächlich zu verteilenden
    Laufkilometer (actual_target_run_km) reduziert, hängt jetzt vom Flag cross_replaces_run ab:

    - cross_replaces_run=False (Default, solange das Frontend diese Entscheidung noch nicht
      abfragt — offener Punkt): Cross kommt ZUSÄTZLICH zur historischen Laufbasis. Das Laufziel
      wird NICHT reduziert (actual_target_run_km = target_km). target_cross_minutes wird an
      anderer Stelle (build_full_skeleton) weiterhin separat ausgewiesen.
    - cross_replaces_run=True: Cross ersetzt bewusst eine Laufeinheit. Reduktion anhand eines
      geschätzten ersetzten Easy-Run-Anteils (nicht mehr pauschal 20% unabhängig von der
      Trainingshistorie), weiterhin sicher gedeckelt bei max. 20% des Laufziels gesamt.

    SESSION-VERTEILUNG bei 3 Läufen/Woche (Regeln 6-9, Klarstellung 2): Longrun zuerst aus der
    sicheren Progressionskette (longrun_km_override) gesetzt — wird durch Cross NICHT reduziert.
    Quality erhält 20-25% von actual_target_run_km. Easy erhält den Rest, begrenzt auf das Minimum
    aus (Longrun-Distanz, 35% von actual_target_run_km, 14.9km — NIEMALS 15-19km). Verletzt der
    Rest diese Grenze, wird NUR actual_target_run_km über den Easy-Anteil reduziert (Longrun bleibt
    unangetastet); die tatsächlich verteilten Sessionkilometer entsprechen danach exakt dem
    reduzierten actual_target_run_km. Diese (und alle weiteren, z.B. Taper-Regel-16-) Reduktionen
    werden NICHT mehr hier als Konflikt geloggt (Root-Cause-Fix Warning-Konsistenz) — build_full_
    skeleton loggt nach ALLEN Anpassungen einer Woche eine einzige, garantiert finale Meldung."""
    conflicts = conflicts if conflicts is not None else []

    longrun = [s for s in sessions if s['session_type'] == 'Long Run']
    quality = [s for s in sessions if s['session_type'] in QUALITY_TYPES]
    easy = [s for s in sessions if s['session_type'] in ENDURANCE_RUN_TYPES]
    cross_count = len([s for s in sessions if s['session_type'] == 'Cross Training'])

    if cross_replaces_run and cross_count:
        estimated_replaced_easy_km = min(target_km * 0.15, 8.0)
        cross_reduction_km = min(estimated_replaced_easy_km * cross_count, target_km * 0.20)
        actual_target_run_km = round(target_km - cross_reduction_km, 1)
    else:
        actual_target_run_km = target_km
    cross_adjusted_target_run_km = actual_target_run_km

    # 1. Longrun zuerst, aus der sicheren Progressionskette.
    longrun_km = 0
    for s in longrun:
        if not easy and not quality:
            # Longrun ist die einzige Laufsession der Woche (z.B. sehr wenige Ausdauertage) ->
            # trägt das gesamte Wochenvolumen statt nur des üblichen 35-40%-Anteils.
            km = round(min(actual_target_run_km, race_distance_km * 0.80), 1)
        elif longrun_km_override is not None:
            km = longrun_km_override
        else:
            km = compute_longrun_km(actual_target_run_km, phase, race_distance_km)
        s['distance_km'] = km
        longrun_km = km

    # 2. Quality: 20-25% von actual_target_run_km.
    quality_total = round(min(actual_target_run_km * 0.225, max(actual_target_run_km - longrun_km, 0)), 1)
    if quality:
        per_quality = round(quality_total / len(quality), 1)
        for s in quality:
            s['distance_km'] = per_quality
        quality_total = round(per_quality * len(quality), 1)
    else:
        quality_total = 0

    # 3. Easy: Rest, mit Obergrenzen (Regel 9).
    remaining = round(actual_target_run_km - longrun_km - quality_total, 1)
    if easy:
        n = len(easy)
        per_easy = remaining / n
        max_easy = min(
            longrun_km if longrun_km > 0 else remaining,
            actual_target_run_km * 0.35,
            14.9,  # NIEMALS 15-19km aufblasen (Regel 9)
        )
        # Root-Cause-Fix: max_easy haengt selbst von actual_target_run_km ab (ueber die 35%-
        # Grenze). Kappt eine Korrektur den Easy-Anteil, sinkt dadurch actual_target_run_km
        # (longrun_km/quality_total bleiben absolut gleich) — wodurch die 35%-Grenze selbst
        # kleiner wird. Eine einmalige Korrektur konnte den finalen Easy-Anteil dadurch knapp
        # ÜBER 35% DES NEUEN (kleineren) actual_target_run_km landen lassen. Fixpunkt-Iteration
        # (konvergiert schnell — jede Runde reduziert nur noch geringfuegig weiter) statt
        # einmaliger Korrektur, damit die Obergrenze garantiert gegen den tatsächlich finalen
        # Wert gilt.
        for _ in range(5):
            if per_easy <= max_easy + 0.05:
                break
            remaining = round(max_easy * n, 1)
            actual_target_run_km = round(longrun_km + quality_total + remaining, 1)
            max_easy = min(
                longrun_km if longrun_km > 0 else remaining,
                actual_target_run_km * 0.35,
                14.9,
            )
            per_easy = remaining / n
        for s in easy:
            s['distance_km'] = round(max(per_easy, 0), 1)
        assigned = sum(s['distance_km'] for s in sessions
                        if s['session_type'] in ENDURANCE_RUN_TYPES or s['session_type'] == 'Long Run'
                        or s['session_type'] in QUALITY_TYPES)
        diff = round(actual_target_run_km - assigned, 1)
        # Rundungsdifferenz nur verbuchen wenn dadurch die Easy-Obergrenze nicht verletzt wird
        if easy[-1]['distance_km'] + diff <= max_easy + 0.05:
            easy[-1]['distance_km'] = round(max(easy[-1]['distance_km'] + diff, 0), 1)
        else:
            actual_target_run_km = round(actual_target_run_km - diff, 1)
    elif quality:
        # keine Easy Runs diese Woche -> Rundungsdifferenz auf die letzte Quality Session buchen
        assigned = sum(s['distance_km'] for s in sessions
                        if s['session_type'] == 'Long Run' or s['session_type'] in QUALITY_TYPES)
        diff = round(actual_target_run_km - assigned, 1)
        quality[-1]['distance_km'] = round(max(quality[-1]['distance_km'] + diff, 0), 1)

    for s in sessions:
        if s['session_type'] in ('Cross Training', 'Strength Training', 'Mobility'):
            s['distance_km'] = 0
        if s['session_type'] == 'Race Day':
            s['distance_km'] = race_distance_km

    return sessions, actual_target_run_km, cross_adjusted_target_run_km


def distribute_week_hm(target_hm, sessions, terrain, race_elevation_m, phase=None):
    """Analog zu distribute_week_km, für Höhenmeter. `phase` steuert eine reduzierte Quality-HM-
    Zuteilung in TAPER (Root-Cause-Fix — vorher bekam die (in TAPER einzige) Quality Session
    unabhängig von der Phase pauschal 55%/15% der verbleibenden Wochen-HM, was z.B. bei einer
    Taper-Woche ohne Longrun zu einer einzelnen, deutlich zu intensiven Hill Session kurz vor dem
    Rennen führte; siehe auch pick_quality_type für die begleitende Typ-Präferenz)."""
    longrun = [s for s in sessions if s['session_type'] == 'Long Run']
    quality = [s for s in sessions if s['session_type'] in QUALITY_TYPES]
    easy = [s for s in sessions if s['session_type'] in ENDURANCE_RUN_TYPES]

    remaining = target_hm

    for s in longrun:
        if not easy and not quality:
            hm = round(target_hm)
        else:
            hm = compute_longrun_hm(target_hm)
        s['elevation_gain_m'] = hm
        remaining -= hm

    if phase == 'TAPER':
        quality_fraction = 0.25 if terrain in ('trail', 'mixed') else 0.08
    else:
        quality_fraction = 0.55 if terrain in ('trail', 'mixed') else 0.15
    quality_total = round(max(remaining, 0) * quality_fraction)
    if quality:
        per_quality = round(quality_total / len(quality))
        for s in quality:
            s['elevation_gain_m'] = per_quality
        remaining -= per_quality * len(quality)

    if easy:
        per_easy = remaining / len(easy)
        for s in easy:
            s['elevation_gain_m'] = max(0, round(per_easy))
        assigned = sum(s['elevation_gain_m'] for s in sessions
                        if s['session_type'] in ENDURANCE_RUN_TYPES or s['session_type'] == 'Long Run'
                        or s['session_type'] in QUALITY_TYPES)
        diff = round(target_hm - assigned)
        easy[-1]['elevation_gain_m'] = max(0, easy[-1]['elevation_gain_m'] + diff)
    elif quality:
        assigned = sum(s['elevation_gain_m'] for s in sessions
                        if s['session_type'] == 'Long Run' or s['session_type'] in QUALITY_TYPES)
        diff = round(target_hm - assigned)
        quality[-1]['elevation_gain_m'] = max(0, quality[-1]['elevation_gain_m'] + diff)

    for s in sessions:
        if s['session_type'] in ('Cross Training', 'Strength Training', 'Mobility'):
            s['elevation_gain_m'] = 0
        if s['session_type'] == 'Race Day':
            s['elevation_gain_m'] = race_elevation_m

    return sessions


def enforce_final_taper_week_constraints(sessions, actual_target_run_km, week_num, conflicts):
    """Regel 16 (letzte Woche vor Race Day): kein einzelner Easy Run > 8km, Freitag+Samstag
    zusammen maximal 12-14km, Sonntag = Rest (bereits durch build_week_skeleton sichergestellt).
    Regel 17: das Wochenziel darf dabei unterschritten werden statt unvernünftige Sessions zu
    erzwingen — die Reduktion wird dokumentiert, kein harter Fehler."""
    changed = False

    for s in sessions:
        if s['session_type'] in ENDURANCE_RUN_TYPES and s.get('distance_km', 0) > 8.0:
            conflicts.append(
                f"Woche {week_num} (letzte Taper-Woche): Easy Run von {s['distance_km']}km auf "
                f"8.0km reduziert (Regel 16, Klarstellung 17: Prozentziel darf unterschritten werden)."
            )
            s['distance_km'] = 8.0
            changed = True

    fri = next((s for s in sessions if s['day_of_week'] == 5), None)
    sat = next((s for s in sessions if s['day_of_week'] == 6), None)
    fri_km = fri.get('distance_km', 0) if fri else 0
    sat_km = sat.get('distance_km', 0) if sat else 0
    combined = round(fri_km + sat_km, 1)
    if combined > 14.0 and combined > 0:
        scale = 13.0 / combined
        if fri:
            fri['distance_km'] = round(fri_km * scale, 1)
        if sat:
            sat['distance_km'] = round(sat_km * scale, 1)
        conflicts.append(
            f"Woche {week_num} (letzte Taper-Woche): Fr+Sa von {combined}km auf ~13.0km reduziert "
            f"(Regel 16: max. 12-14km zusammen)."
        )
        changed = True

    if changed:
        actual_target_run_km = round(sum(
            s.get('distance_km', 0) for s in sessions if s['session_type'] != 'Race Day'
        ), 1)

    return actual_target_run_km


def parse_pace_to_min_per_km(pace_str):
    """'5:30' -> 5.5"""
    if not pace_str:
        return None
    try:
        m, s = str(pace_str).split(':')
        return int(m) + int(s) / 60
    except Exception:
        return None


def compute_duration_min(session_type, distance_km, elevation_gain_m, terrain, athlete_paces=None):
    """duration_min ist deterministisch — das LLM darf diesen Wert nicht anfassen."""
    athlete_paces = athlete_paces or {}
    distance_km = distance_km or 0
    elevation_gain_m = elevation_gain_m or 0

    if session_type == 'Strength Training':
        return 45
    if session_type == 'Mobility':
        return 20
    if session_type == 'Cross Training':
        return 60
    if session_type == 'Race Day':
        pace = athlete_paces.get('long_run') or DEFAULT_PACE_MIN_PER_KM['long_run']
        return round(distance_km * pace + elevation_gain_m / 100)
    if session_type == 'Trail Run':
        pace = athlete_paces.get('trail') or DEFAULT_PACE_MIN_PER_KM['trail']
        return round(distance_km * pace + elevation_gain_m / 100)
    if session_type == 'Long Run':
        pace = athlete_paces.get('long_run') or DEFAULT_PACE_MIN_PER_KM['long_run']
        surcharge = elevation_gain_m / 100 * 0.7 if terrain in ('trail', 'mixed') else 0
        return round(distance_km * pace + surcharge)
    if session_type in QUALITY_TYPES:
        pace = athlete_paces.get('quality') or DEFAULT_PACE_MIN_PER_KM['quality']
        return round(distance_km * pace)
    # Easy Run / Recovery Run
    pace = athlete_paces.get('easy') or DEFAULT_PACE_MIN_PER_KM['easy']
    surcharge = elevation_gain_m / 100 * 0.7 if terrain in ('trail', 'mixed') else 0
    return round(distance_km * pace + surcharge)


def build_full_skeleton(inputs):
    """Baut das komplette, deterministische Plan-Skelett. `inputs` ist ein dict mit:
    start_date, race_date, race_distance_km, race_elevation_m, terrain, long_run_day,
    strength_days, strength_sessions, quality_sessions, days_per_week, cross_training,
    cross_training_days, cross_replaces_run (bool, optional, Default False — siehe
    distribute_week_km()), avg_weekly_km, max_km, athlete_paces (dict, optional)."""
    ausdauer_days = validate_inputs(
        inputs['strength_sessions'], inputs['strength_days'], inputs['days_per_week']
    )
    dates = normalize_dates(inputs['start_date'], inputs['race_date'])
    total_weeks = dates['total_weeks']
    phase_by_week, taper_weeks, peak_week = compute_phase_map(total_weeks)

    avg_weekly_km = inputs['avg_weekly_km']
    max_km = inputs['max_km'] or avg_weekly_km * 2  # kein Limit hinterlegt -> grosszügiger Fallback
    race_distance_km = inputs['race_distance_km']
    race_elevation_m = inputs['race_elevation_m']
    terrain = inputs['terrain']

    desired_peak_km = compute_desired_peak_km(avg_weekly_km, race_distance_km, max_km)
    avg_weekly_hm = inputs.get('avg_weekly_hm') or estimate_avg_weekly_hm(avg_weekly_km, terrain)
    desired_peak_hm = compute_desired_peak_hm(avg_weekly_hm, race_elevation_m)

    available_days_before_race = dates['race_dow'] - 1
    race_week_ratio = available_days_before_race / 6

    conflicts = []

    km_targets, peak_km_actual = compute_weekly_progression(
        phase_by_week, total_weeks, avg_weekly_km, desired_peak_km, max_km,
        race_week_available_ratio=race_week_ratio,
    )
    km_targets = apply_partial_week1_reduction(km_targets, dates['actual_start_day'])
    load_phases = ('BASE', 'BUILD', 'PEAK')
    km_targets = enforce_max_plateau(km_targets, phase_by_week, load_phases, conflicts, 'target_km', max_run=2)

    hm_targets, peak_hm_actual = compute_weekly_progression(
        phase_by_week, total_weeks, avg_weekly_hm, desired_peak_hm, desired_peak_hm * 1.5,
        race_week_available_ratio=race_week_ratio,
    )
    hm_targets = apply_partial_week1_reduction(hm_targets, dates['actual_start_day'])

    longrun_targets = compute_longrun_progression(phase_by_week, total_weeks, avg_weekly_km, km_targets,
                                                    race_distance_km, max_km=max_km, conflicts=conflicts)
    longrun_targets = apply_partial_week1_reduction(longrun_targets, dates['actual_start_day'])

    # cross_replaces_run: Entscheidung, ob Cross Training eine Laufeinheit ersetzt (Laufziel wird
    # reduziert) oder zusaetzlich zur historischen Laufbasis kommt (Laufziel unveraendert). Das
    # Frontend fragt das (Stand jetzt) noch nicht ab — offener Punkt, siehe TODO in generate_plan().
    # Default False: die historische Laufbasis (avg_weekly_km/historical_run_baseline_km) gilt als
    # stabil und wird durch eine geplante Cross-Session NICHT automatisch gekappt.
    cross_replaces_run = bool(inputs.get('cross_replaces_run', False))

    # Identische aufeinanderfolgende Belastungswochen (max. 2 erlaubt, siehe enforce_max_plateau
    # oben) sind zulässig, müssen aber dokumentiert werden statt stillschweigend zu passieren.
    for week_num in range(2, total_weeks + 1):
        phase = phase_by_week.get(week_num)
        prev_phase = phase_by_week.get(week_num - 1)
        if phase in load_phases and prev_phase in load_phases:
            if km_targets.get(week_num) == km_targets.get(week_num - 1):
                conflicts.append(
                    f"Woche {week_num}: target_km identisch zu Woche {week_num - 1} "
                    f"({km_targets.get(week_num)} km) — Zielobergrenze (desired_peak_km={desired_peak_km}) "
                    f"bereits vor der eigentlichen Peak-Woche erreicht, weiteres Wachstum durch "
                    f"absolute_plan_cap_km/race_distance_km-Formel begrenzt."
                )

    weeks = []
    prev_week_longrun_day = None
    for week_num in range(1, total_weeks + 1):
        phase = phase_by_week.get(week_num, 'BUILD')
        is_race_week = (phase == 'RACE')
        is_week1 = (week_num == 1)
        week_monday = dates['start_monday'] + timedelta(weeks=week_num - 1)

        is_final_taper_week = (week_num == total_weeks - 1) and phase == 'TAPER'

        sessions = build_week_skeleton(
            week_num=week_num, phase=phase, race_dow=dates['race_dow'],
            is_race_week=is_race_week, actual_start_day=dates['actual_start_day'], is_week1=is_week1,
            long_run_day=inputs['long_run_day'], strength_days=inputs['strength_days'],
            quality_sessions_input=inputs['quality_sessions'], cross_training=inputs['cross_training'],
            cross_training_days=inputs['cross_training_days'], ausdauer_days=ausdauer_days,
            terrain=terrain, conflicts=conflicts,
            week_monday=week_monday, race_day=dates['race_day'],
            prev_week_longrun_day=prev_week_longrun_day,
            is_final_taper_week=is_final_taper_week,
        )
        this_longrun = next((s['day_of_week'] for s in sessions if s['session_type'] == 'Long Run'), None)
        prev_week_longrun_day = this_longrun
        sessions, actual_target_run_km, cross_adjusted_km = distribute_week_km(
            km_targets.get(week_num, 0), phase, sessions, race_distance_km,
            longrun_km_override=longrun_targets.get(week_num),
            conflicts=conflicts, week_num=week_num, cross_replaces_run=cross_replaces_run,
        )
        sessions = distribute_week_hm(hm_targets.get(week_num, 0), sessions, terrain, race_elevation_m, phase)

        if is_final_taper_week:
            # Regel 16: letzte Woche vor Race Day — Fr+Sa zusammen max 12-14km, kein Easy > 8km.
            actual_target_run_km = enforce_final_taper_week_constraints(sessions, actual_target_run_km, week_num, conflicts)

        # Root-Cause-Fix Warning-Konsistenz: EINE einzige, garantiert finale Meldung pro Woche,
        # NACHDEM alle Anpassungen (Cross-Reduktion, Easy-Obergrenze, ggf. Taper-Regel-16) bereits
        # angewendet sind — verhindert veraltete Zwischenwerte in fruehen Teil-Meldungen (vorher
        # z.B. "26.0 auf 24.1" geloggt, tatsaechlich gespeicherte Sessions ergaben aber 18.9).
        if abs(actual_target_run_km - cross_adjusted_km) > 0.15:
            conflicts.append(
                f"Woche {week_num}: Laufziel von {cross_adjusted_km}km auf {actual_target_run_km}km reduziert "
                f"(Easy-Run-/Taper-Obergrenzen) — finaler, tatsächlich gespeicherter Wert."
            )

        for s in sessions:
            s['duration_min'] = compute_duration_min(
                s['session_type'], s.get('distance_km', 0), s.get('elevation_gain_m', 0),
                terrain, inputs.get('athlete_paces'),
            )

        # Regel 10: Cross Training bleibt im Deload bestehen, aber Dauer -20-30% und Z1-Z2.
        if phase == 'DELOAD':
            for s in sessions:
                if s['session_type'] == 'Cross Training':
                    s['duration_min'] = round(s['duration_min'] * 0.75)  # Mittelwert von -20% bis -30%
                    s['_deload_cross_zone'] = 'Z1-Z2'

        # Cross-Training-Minuten getrennt von target_run_km ausweisen — Laufkm bleiben unberührt.
        target_cross_minutes = sum(s.get('duration_min', 0) for s in sessions if s['session_type'] == 'Cross Training')

        weeks.append({
            'week_number': week_num,
            'week_date': week_monday,
            'phase': phase,
            'target_km': km_targets.get(week_num, 0),
            'base_target_run_km': km_targets.get(week_num, 0),  # Alias, siehe Problem-1-Spezifikation:
                                                                  # geplantes Laufziel VOR Cross-Substitution
            'actual_target_run_km': actual_target_run_km,
            'target_hm': hm_targets.get(week_num, 0),
            'target_longrun_km': longrun_targets.get(week_num),
            'target_cross_minutes': target_cross_minutes,
            'sessions': sessions,
        })

    return {
        'weeks': weeks,
        'dates': dates,
        'total_weeks': total_weeks,
        'taper_weeks': taper_weeks,
        'peak_week': peak_week,
        'avg_weekly_km': avg_weekly_km,
        'historical_run_baseline_km': avg_weekly_km,  # Alias: robuste, tatsaechlich gelaufene
                                                        # Wochenbasis aus der Aktivitaetshistorie
        'cross_replaces_run': cross_replaces_run,
        'desired_peak_km': desired_peak_km,
        'peak_km_actual': peak_km_actual,
        'desired_peak_hm': desired_peak_hm,
        'peak_hm_actual': peak_hm_actual,
        'conflicts': conflicts,
    }


def _is_lower_body(session):
    return session.get('session_type') == 'Strength Training' and session.get('_strength_focus', '').startswith('lower')


def validate_skeleton(skeleton, max_km, only_week_num=None):
    """Prüft das Skelett gegen alle geforderten Regeln. Gibt (is_valid, errors) zurück.
    only_week_num: falls gesetzt, werden nur Fehler INNERHALB dieser Woche gemeldet (Nachbarwochen
    dienen weiterhin als Kontext für wochenübergreifende Prüfungen) — für den zweiten Lauf
    "pro Woche vor LLM"."""
    errors = []
    weeks = skeleton['weeks']
    dates = skeleton['dates']
    race_dow = dates['race_dow']
    race_day = dates['race_day']

    by_week_num = {w['week_number']: w for w in weeks}
    scope = [only_week_num] if only_week_num is not None else sorted(by_week_num.keys())

    for week_num in scope:
        w = by_week_num[week_num]
        sessions = w['sessions']
        is_race_week = (w['phase'] == 'RACE')

        # 3. Keine Tagesdoppelbelegung
        days_seen = [s['day_of_week'] for s in sessions]
        if len(days_seen) != len(set(days_seen)):
            errors.append(f"Woche {week_num}: Tagesdoppelbelegung ({days_seen})")

        # 2. Keine Session nach race_date
        for s in sessions:
            session_date = w['week_date'] + timedelta(days=s['day_of_week'] - 1)
            if session_date > race_day:
                errors.append(f"Woche {week_num}: Session am {session_date} liegt nach race_date {race_day}")

        # 6. max_km eingehalten
        if w['target_km'] > max_km + 0.01:
            errors.append(f"Woche {week_num}: target_km={w['target_km']} > max_km={max_km}")

        avg_weekly_km = skeleton.get('avg_weekly_km')

        # Startwoche: target_run_km zwischen 90-110% von avg_weekly_km
        if week_num == 1 and avg_weekly_km and dates['actual_start_day'] == 1:
            lo, hi = avg_weekly_km * 0.90, avg_weekly_km * 1.10
            if not (lo - 0.05 <= w['target_km'] <= hi + 0.05):
                errors.append(f"Woche 1: target_km={w['target_km']} ausserhalb 90-110% von avg_weekly_km={avg_weekly_km} ({lo:.1f}-{hi:.1f})")

        # Peak-Woche: target_run_km >= avg_weekly_km — ABER nur wenn die Zielformel selbst (bei
        # ausreichender Vorbereitungszeit) mehr hergibt. Ist desired_peak_km bereits durch
        # race_distance_km*1.50 oder absolute_plan_cap_km unter avg_weekly_km gedeckelt (kurzes
        # Rennen relativ zur aktuellen Fitness, z.B. Halbmarathon bei hohem Laufniveau), ist das
        # die korrekte, beabsichtigte Formel-Ausgabe — kein Fehler.
        desired_peak_km_ref = skeleton.get('desired_peak_km')
        if w['phase'] == 'PEAK' and avg_weekly_km and desired_peak_km_ref is not None:
            expected_floor = min(avg_weekly_km, desired_peak_km_ref)
            if w['target_km'] < expected_floor - 0.05:
                errors.append(f"Woche {week_num} (PEAK): target_km={w['target_km']} < erwartete Untergrenze {expected_floor:.1f} "
                              f"(min(avg_weekly_km={avg_weekly_km}, desired_peak_km={desired_peak_km_ref}))")

        # Longrun-Anteil: max 40% normal, max 45% Peak (harte Obergrenze; niedriger ist durch
        # die eigene Progressionskette/Deckel legitim und wird NICHT als Fehler gewertet).
        # Bezugsgrösse ist target_km (Basis, unreduziert) — der Longrun wird durch Cross Training
        # bewusst NICHT verkürzt (Klarstellung 2), sein Anteil an der kleineren actual_target_run_km
        # ist entsprechend höher und kein Fehler.
        longrun_sessions_check = [s for s in sessions if s['session_type'] == 'Long Run']
        if longrun_sessions_check and w['target_km'] > 0 and w['phase'] in ('BASE', 'BUILD', 'PEAK'):
            ratio = longrun_sessions_check[0].get('distance_km', 0) / w['target_km']
            ceiling_ratio = 0.45 if w['phase'] == 'PEAK' else 0.40
            if ratio > ceiling_ratio + 0.02:
                errors.append(f"Woche {week_num}: Longrun-Anteil {ratio:.0%} > {ceiling_ratio:.0%} des Wochenziels")

        # 4./5. Wochenkilometer/-HM = target (Toleranz, Race Day ausgenommen). Regel 4: die
        # tatsächlich verteilten Laufkm müssen actual_target_run_km entsprechen (NICHT dem
        # unreduzierten base target_km — Cross Training reduziert das Laufziel gezielt, siehe
        # distribute_week_km), Fallback auf target_km für Aufrufer ohne Cross-Reduktion.
        run_km_reference = w.get('actual_target_run_km', w['target_km'])
        actual_km = sum(s.get('distance_km', 0) for s in sessions if s['session_type'] != 'Race Day')
        if run_km_reference > 0 and abs(actual_km - run_km_reference) > run_km_reference * 0.05 + 0.15:
            errors.append(f"Woche {week_num}: Summe Distanz {actual_km} weicht von actual_target_run_km {run_km_reference} ab (>5%)")

        actual_hm = sum(s.get('elevation_gain_m', 0) for s in sessions if s['session_type'] != 'Race Day')
        if w['target_hm'] > 5 and abs(actual_hm - w['target_hm']) > w['target_hm'] * 0.10 + 5:
            errors.append(f"Woche {week_num}: Summe HM {actual_hm} weicht von target_hm {w['target_hm']} ab (>10%)")

        # 7. Longrun korrekt platziert
        longrun_sessions = [s for s in sessions if s['session_type'] == 'Long Run']
        if is_race_week and longrun_sessions:
            errors.append(f"Woche {week_num}: Long Run in der Rennwoche nicht erlaubt")
        if len(longrun_sessions) > 1:
            errors.append(f"Woche {week_num}: mehr als ein Long Run")

        # 8. actual_quality == planned_quality (0 in Race/Deload, max 1 in Taper)
        actual_quality = len([s for s in sessions if s['session_type'] in QUALITY_TYPES])
        if is_race_week and actual_quality != 0:
            errors.append(f"Woche {week_num}: Quality Sessions in der Rennwoche (erwartet 0, gefunden {actual_quality})")
        if w['phase'] == 'DELOAD' and actual_quality != 0:
            errors.append(f"Woche {week_num}: Quality Sessions im Deload (erwartet 0, gefunden {actual_quality})")
        if w['phase'] == 'TAPER' and actual_quality > 1:
            errors.append(f"Woche {week_num}: mehr als 1 Quality Session im Taper (gefunden {actual_quality})")

        # 10. Keine harte Beineinheit am Tag vor Longrun (innerhalb der Woche)
        if longrun_sessions:
            lr_day = longrun_sessions[0]['day_of_week']
            before, _ = opposite_adjacent_days(lr_day)
            if before:
                before_s = next((s for s in sessions if s['day_of_week'] == before), None)
                if before_s and (before_s['session_type'] in QUALITY_TYPES or before_s['session_type'] == 'Long Run' or _is_lower_body(before_s)):
                    errors.append(f"Woche {week_num}: harte Beineinheit ({before_s['session_type']}) am Tag vor Longrun")

        # 9. Keine Quality am Tag nach Longrun (innerhalb der Woche)
        if longrun_sessions:
            lr_day = longrun_sessions[0]['day_of_week']
            _, after = opposite_adjacent_days(lr_day)
            if after:
                after_s = next((s for s in sessions if s['day_of_week'] == after), None)
                if after_s and after_s['session_type'] in QUALITY_TYPES:
                    errors.append(f"Woche {week_num}: Quality Session am Tag nach Longrun")

        # 11. Rennwoche: kein Longrun, keine Quality, kein Lower/Full Body
        if is_race_week:
            for s in sessions:
                if s['session_type'] == 'Strength Training' and s.get('_strength_focus') in ('lower', 'lower_moderate', 'full_light'):
                    errors.append(f"Woche {week_num}: Lower/Full Body Strength Training in der Rennwoche")

    # 1. Race Day exakt auf race_date (Gesamtplan-Check, unabhängig von only_week_num)
    race_weeks = [w for w in weeks if w['phase'] == 'RACE']
    if not race_weeks:
        errors.append("Keine Rennwoche im Skelett gefunden")
    else:
        rw = race_weeks[0]
        race_day_sessions = [s for s in rw['sessions'] if s['session_type'] == 'Race Day']
        if len(race_day_sessions) != 1:
            errors.append(f"Rennwoche enthält {len(race_day_sessions)} Race-Day-Sessions statt genau 1")
        else:
            rs = race_day_sessions[0]
            if rs['day_of_week'] != race_dow:
                errors.append(f"Race Day liegt auf Tag {rs['day_of_week']} statt {race_dow}")
            actual_date = rw['week_date'] + timedelta(days=rs['day_of_week'] - 1)
            if actual_date != race_day:
                errors.append(f"Race Day Datum {actual_date} != race_date {race_day}")

    # Build-Wochen progressiv: identische aufeinanderfolgende Belastungswochen nur mit
    # dokumentiertem Grund (siehe build_full_skeleton's conflicts-Eintrag) zulässig
    load_phases_check = ('BASE', 'BUILD', 'PEAK')
    conflicts_text = " ".join(skeleton.get('conflicts', []))
    for week_num in scope:
        w = by_week_num[week_num]
        prev = by_week_num.get(week_num - 1)
        if not prev or w['phase'] not in load_phases_check or prev['phase'] not in load_phases_check:
            continue
        if w['target_km'] == prev['target_km']:
            documented = f"Woche {week_num}: target_km identisch zu Woche {week_num - 1}" in conflicts_text
            if not documented:
                errors.append(f"Woche {week_num}: identisch zu Woche {week_num - 1} (target_km={w['target_km']}) ohne dokumentierten Grund")

    # 9b. Wochenübergreifend: Quality am Montag nach Longrun am Sonntag der Vorwoche
    for week_num in scope:
        w = by_week_num[week_num]
        prev = by_week_num.get(week_num - 1)
        if not prev:
            continue
        prev_sunday = next((s for s in prev['sessions'] if s['day_of_week'] == 7 and s['session_type'] == 'Long Run'), None)
        this_monday = next((s for s in w['sessions'] if s['day_of_week'] == 1), None)
        if prev_sunday and this_monday and this_monday['session_type'] in QUALITY_TYPES:
            errors.append(f"Woche {week_num}: Quality Session am Montag direkt nach Longrun am Sonntag (Woche {week_num - 1})")

    # 1b. Kein Long Run am Kalendertag direkt vor Race Day (auch wochenübergreifend)
    for week_num in scope:
        w = by_week_num[week_num]
        for s in w['sessions']:
            if s['session_type'] != 'Long Run':
                continue
            session_date = w['week_date'] + timedelta(days=s['day_of_week'] - 1)
            if session_date == race_day - timedelta(days=1):
                errors.append(f"Woche {week_num}: Long Run am {session_date}, direkt vor Race Day {race_day}")

    return (len(errors) == 0, errors)


# ══════════════════════════════════════════════════════════════════════════
# ═══ DB / LLM I/O ═══
# ══════════════════════════════════════════════════════════════════════════

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
                continue
            cairn_routines[title] = exercises or []

        if cairn_routines:
            hevy_lines = ["Verfügbare CAIRN-Routinen (NUR diese Namen verwenden):"]
            for title, exercises in cairn_routines.items():
                category = HEVY_CATEGORIES.get(title, 'Ganzkörper')
                routine_by_category.setdefault(category, title)
                hevy_lines.append(f"- {title} [{category}]: {', '.join(exercises[:4])}")
            hevy_context = "\n".join(hevy_lines)
    except Exception as e:
        print(f"Hevy Routinen Fehler: {e}")
    finally:
        conn.close()
    return hevy_context, routine_by_category


def pick_cairn_routine_name(focus, routine_by_category):
    if focus in ('lower', 'lower_moderate'):
        return routine_by_category.get('unterkörper') or routine_by_category.get('oberkörper') or 'Strength Training'
    if focus == 'full_light':
        return routine_by_category.get('full_body_light') or routine_by_category.get('oberkörper') or 'Strength Training'
    return routine_by_category.get('oberkörper') or 'Strength Training'


def fetch_athlete_context(today):
    """DB-Zugriff: avg_weekly_km, max_weekly_km_actual, athlete_paces (aus athlete_profile.pace_z1..z5)
    fliessen in die deterministische Skelett-Berechnung. HRV/Schlaf werden NICHT in der Skelett-
    Mathematik verwendet (dort nirgends referenziert) — sie sind reiner Kontext für die Flavor-Texte,
    die das LLM in Phase 2 pro Woche ergänzt.

    max_weekly_km_actual = höchste TATSÄCHLICHE Wochensumme der letzten 28 Tage (4 Sieben-Tage-
    Fenster endend heute), NICHT die längste Einzelsession — eine einzelne 22km-Session sagt nichts
    über die reale Wochenkapazität aus und darf desired_peak_km nicht künstlich deckeln."""
    avg_weekly_km = 0
    max_weekly_km_actual = 0
    athlete_paces = {}
    avg_hrv = None
    avg_sleep = None
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT date, type, distance_km FROM trainings WHERE date >= %s""", (today - timedelta(days=28),))
        rows = cur.fetchall()
        print(f"DEBUG: trainings query returned {len(rows)} rows")
        run_rows = [(r[0], float(r[2])) for r in rows if r[1] in ('Run', 'TrailRun') and r[2]]
        run_kms = [km for _, km in run_rows]
        avg_weekly_km = round(sum(run_kms) / 4.0, 1) if run_kms else 0

        weekly_buckets = [0.0, 0.0, 0.0, 0.0]
        for d, km in run_rows:
            days_ago = (today - d).days
            bucket_idx = min(3, max(0, days_ago // 7))
            weekly_buckets[bucket_idx] += km
        max_weekly_km_actual = round(max(weekly_buckets), 1) if run_rows else 0
        print(f"DEBUG: weekly_buckets(km, aktuellste zuletzt)={list(reversed([round(b,1) for b in weekly_buckets]))}, "
              f"max_weekly_km_actual={max_weekly_km_actual}")

        cur.execute("SELECT pace_z1, pace_z2, pace_z3, pace_z4, pace_z5 FROM athlete_profile ORDER BY id LIMIT 1")
        prow = cur.fetchone()
        print(f"DEBUG: athlete_profile query returned {'1 row' if prow else 'no row'}")
        if prow:
            z1, z2, z3, z4, z5 = [parse_pace_to_min_per_km(p) for p in prow]
            if z2:
                athlete_paces['easy'] = z2
                athlete_paces['long_run'] = z2
                athlete_paces['trail'] = z2
            if z4:
                athlete_paces['quality'] = z4

        cur.execute("SELECT hrv_last_night, sleep_duration_h FROM daily_logs WHERE date >= %s", (today - timedelta(days=14),))
        log_rows = cur.fetchall()
        hrv_values = [float(r[0]) for r in log_rows if r[0] is not None]
        avg_hrv = round(sum(hrv_values) / len(hrv_values), 1) if hrv_values else None
        sleep_values = [float(r[1]) for r in log_rows if r[1] is not None]
        avg_sleep = round(sum(sleep_values) / len(sleep_values), 1) if sleep_values else None
    except Exception as e:
        print(f"Athleten-Kontext Fehler: {e}")
        traceback.print_exc()
    finally:
        conn.close()
    return avg_weekly_km, max_weekly_km_actual, athlete_paces, avg_hrv, avg_sleep


def load_training_engine_excerpt():
    """Fallback-Kontext, nur genutzt wenn Terrain fehlt (siehe build_context_notes)."""
    try:
        path = os.path.join(os.path.dirname(__file__), '..', 'knowledge', 'docs', 'training_engine.md')
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content[:2000]
    except Exception as e:
        print(f"training_engine.md Fehler: {e}")
        return ""


def build_context_notes(terrain):
    """WEB SEARCH ist optional und nur bei fehlendem Terrain-Kontext relevant — primär wird
    knowledge/docs/training_engine.md geladen, keine Live-Websuche im Regelfall."""
    if terrain:
        return ""
    excerpt = load_training_engine_excerpt()
    if excerpt:
        return "TRAININGSPHILOSOPHIE (knowledge/docs/training_engine.md):\n" + excerpt
    return ""


def enrich_week_with_llm(client, week, terrain, routine_by_category, context_notes):
    """Phase 2: ein LLM-Call pro Woche. Darf NUR Flavor-Felder ergänzen, NICHT die fixierten
    Skelett-Felder ändern. Fixierte Felder werden danach hart mit den Skelett-Werten überschrieben."""
    skeleton_sessions = [{f: s.get(f) for f in FIXED_FIELDS} for s in week['sessions']]

    strength_hints = []
    for s in week['sessions']:
        if s['session_type'] == 'Strength Training':
            focus = s.get('_strength_focus', 'upper')
            routine_name = pick_cairn_routine_name(focus, routine_by_category)
            strength_hints.append(f"- Tag {s['day_of_week']} ({focus}): notes MUSS exakt \"{routine_name}\" sein.")

    prompt = f"""Du bist CAIRN Coach. Hier ist das FIXE Skelett für Woche {week['week_number']} (Phase: {week['phase']}), Terrain: {terrain}.

{json.dumps(skeleton_sessions, ensure_ascii=False)}

Diese Felder sind FIX und dürfen NICHT geändert werden: day_of_week, session_type, distance_km, elevation_gain_m, duration_min.
Du darfst NUR folgende Felder ergänzen: notes, session_zone, warmup_km, warmup_min, main_sets, main_distance_m, main_pace, recovery_m, cooldown_km, cooldown_min.
session_zone: max. 20 Zeichen (z.B. "Zone 2", "Schwelle", "VO2max"). main_pace: max. 50 Zeichen (z.B. "4:15-4:30/km").

Für Quality Sessions (Tempo/Interval/Sprint/Hill Session): fülle warmup_km, warmup_min, main_sets, main_distance_m, main_pace, recovery_m, cooldown_km, cooldown_min passend zu distance_km/duration_min. notes fasst das in einem Satz zusammen.
Für Strength Training gilt zwingend:
{chr(10).join(strength_hints) if strength_hints else '(keine Strength Training Session diese Woche)'}
Für alle anderen Session-Typen NUR notes und session_zone.
notes IMMER auf Deutsch, im ruhigen, direkten CAIRN-Coach-Ton.
{context_notes}

Antworte NUR mit JSON: {{"sessions": [{{"day_of_week": 1, "notes": "...", "session_zone": "...", "warmup_km": null, "warmup_min": null, "main_sets": null, "main_distance_m": null, "main_pace": null, "recovery_m": null, "cooldown_km": null, "cooldown_min": null}}]}}"""

    enrichment = []
    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = "".join(b.text for b in message.content if hasattr(b, 'text') and getattr(b, 'type', None) == 'text').strip()
        raw = raw.replace('```json', '').replace('```', '').strip()
        if not raw.startswith('{'):
            m = re.search(r'\{[\s\S]*"sessions"[\s\S]*\}', raw)
            if m:
                raw = m.group(0)
        enrichment = [clamp_flavor_field_lengths(e) for e in json.loads(raw).get('sessions', [])]
        print(f"DEBUG: LLM enrichment Woche {week['week_number']} ok, {len(enrichment)} sessions")
    except Exception as e:
        print(f"LLM Enrichment Fehler Woche {week['week_number']}: {e}")

    enrichment_by_day = {e.get('day_of_week'): e for e in enrichment}
    for s in week['sessions']:
        fixed_snapshot = {f: s[f] for f in FIXED_FIELDS if f in s}
        llm_data = enrichment_by_day.get(s['day_of_week'], {})
        for f in FLAVOR_FIELDS:
            if f in llm_data:
                s[f] = llm_data[f]
        s.update(fixed_snapshot)  # fixierte Felder mit Skelett-Werten überschreiben

    return week


def generate_plan(job_id, data):
    goal_type = data.get('goal_type', 'race')
    race_name = data.get('race_name', '')
    race_date = data.get('race_date', '')
    race_distance_km = data.get('race_distance_km', 0)
    terrain = data.get('terrain', '')
    race_elevation_m = data.get('race_elevation_m', 0)
    days_per_week = data.get('days_per_week', 5)
    long_run_day = data.get('long_run_day', 6)
    quality_sessions = data.get('quality_sessions', 1)
    strength_sessions = data.get('strength_sessions', 2)
    strength_days = data.get('strength_days', [])
    start_date = data.get('start_date', None)
    cross_training = data.get('cross_training', False)
    cross_training_days = data.get('cross_training_days', 0)
    # OFFENER PUNKT (siehe PDF-Spezifikation Problem 1): das Onboarding fragt aktuell NICHT ab, ob
    # Cross Training eine Laufeinheit ERSETZEN oder ZUSAETZLICH zur historischen Laufbasis kommen
    # soll. Bis das Frontend diese Entscheidung liefert, Default False (Laufziel wird durch Cross
    # NICHT reduziert) — konservativ in Richtung "historische Basis bleibt stabil", siehe
    # distribute_week_km()-Docstring. Sobald ein Fragebogen-Feld existiert, hier data.get(...) lesen.
    cross_replaces_run = data.get('cross_replaces_run', False)

    if not race_date or not start_date:
        raise SkeletonError("Eingabefehler: race_date und start_date sind zwingend erforderlich")

    today = get_today()
    hevy_context, routine_by_category = build_hevy_context()
    avg_weekly_km, max_weekly_km_actual, athlete_paces, avg_hrv, avg_sleep = fetch_athlete_context(today)

    # absolute_plan_cap_km: ein Check-in-Feld dafür existiert im Fragebogen (noch) nicht — falls die
    # App das je ergänzt, wird data['max_km'] respektiert. Ohne echten Check-in-Wert NIE aus der
    # Trainingshistorie ableiten (weder längste Einzelsession noch höchste Wochensumme), da das
    # legitime Wachstum (avg*1.30) künstlich kappen kann — siehe Bug-Analyse. Grosszügiger Fallback,
    # der die 1.30x-Formel praktisch nie beschneidet.
    absolute_plan_cap_km = data.get('max_km') or round(avg_weekly_km * 1.8, 1)

    inputs = {
        'start_date': start_date, 'race_date': race_date,
        'race_distance_km': race_distance_km, 'race_elevation_m': race_elevation_m,
        'terrain': terrain, 'long_run_day': long_run_day, 'strength_days': strength_days,
        'strength_sessions': strength_sessions, 'quality_sessions': quality_sessions,
        'days_per_week': days_per_week, 'cross_training': cross_training,
        'cross_training_days': cross_training_days, 'cross_replaces_run': cross_replaces_run,
        'avg_weekly_km': avg_weekly_km,
        'max_km': absolute_plan_cap_km, 'max_weekly_km_actual': max_weekly_km_actual,
        'athlete_paces': athlete_paces,
    }

    print("DEBUG: building deterministic skeleton (Phase 1)")
    skeleton = build_full_skeleton(inputs)
    print(f"DEBUG: skeleton built, total_weeks={skeleton['total_weeks']}, "
          f"desired_peak_km={skeleton['desired_peak_km']}, peak_km_actual={skeleton['peak_km_actual']}")
    for c in skeleton['conflicts']:
        print(f"KONFLIKT (dokumentiert, kein Fehler): {c}")

    is_valid, errors = validate_skeleton(skeleton, absolute_plan_cap_km)
    if not is_valid:
        for e in errors:
            print(f"SKELETON FEHLER: {e}")
        raise SkeletonError(f"Skelett ungültig ({len(errors)} Fehler) — kein LLM-Aufruf, kein Plan erzeugt.")
    print("DEBUG: Skeleton-Validierung (Gesamtplan) OK")

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    context_notes = build_context_notes(terrain)
    if avg_hrv is not None or avg_sleep is not None:
        context_notes += (f"\nAthleten-Kontext letzte 14 Tage: HRV {avg_hrv if avg_hrv is not None else 'keine Daten'} ms, "
                           f"Schlaf {avg_sleep if avg_sleep is not None else 'keine Daten'} h.")

    for week in skeleton['weeks']:
        is_valid_week, week_errors = validate_skeleton(skeleton, absolute_plan_cap_km, only_week_num=week['week_number'])
        if not is_valid_week:
            for e in week_errors:
                print(f"SKELETON FEHLER (Woche {week['week_number']}): {e}")
            raise SkeletonError(f"Skelett für Woche {week['week_number']} ungültig — kein LLM-Aufruf.")
        enrich_week_with_llm(client, week, terrain, routine_by_category, context_notes)

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
            race_name or f"{goal_type} Plan {skeleton['total_weeks']}W",
            goal_type, race_name, race_date, race_distance_km or 0,
            skeleton['total_weeks'], days_per_week, long_run_day,
            quality_sessions, strength_sessions
        ))
        plan_id = cur.fetchone()[0]
        print(f"DEBUG: plan metadata inserted, plan_id={plan_id}")

        cur.execute("UPDATE plans SET status='archived' WHERE status='active' AND id != %s", (plan_id,))
        cur.execute("DELETE FROM training_plan WHERE plan_id = %s OR plan_id IS NULL", (plan_id,))

        sessions_inserted = 0
        for week in skeleton['weeks']:
            for s in week['sessions']:
                cur.execute("""
                    INSERT INTO training_plan
                    (week_date, day_of_week, session_type, session_zone,
                     duration_min, distance_km, notes, phase, plan_id, plan_week,
                     warmup_km, warmup_min, main_sets, main_distance_m, main_pace,
                     recovery_m, cooldown_km, cooldown_min, elevation_gain_m)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    week['week_date'], s['day_of_week'], s['session_type'], s.get('session_zone', ''),
                    s.get('duration_min', 0), s.get('distance_km', 0), s.get('notes', ''),
                    week['phase'], plan_id, week['week_number'],
                    s.get('warmup_km'), s.get('warmup_min'), s.get('main_sets'), s.get('main_distance_m'),
                    s.get('main_pace'), s.get('recovery_m'), s.get('cooldown_km'), s.get('cooldown_min'),
                    s.get('elevation_gain_m'),
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
        for week in skeleton['weeks']:
            for s in week['sessions']:
                if s['session_type'] == 'Strength Training':
                    note = s.get('notes') or pick_cairn_routine_name(s.get('_strength_focus', 'upper'), routine_by_category)
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
Berücksichtige die Langzeitziele des Athleten.

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
            sugg_raw = "".join(b.text for b in sugg_message.content if hasattr(b, 'text')).strip()
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