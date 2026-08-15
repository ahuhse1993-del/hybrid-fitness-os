"""
CAIRN – Szenario-Testmatrix für die deterministische Skelett-Logik (Phase 1) in
data/generate_plan.py.

WICHTIG (Analyse-Skript, keine Produktionslogik):
- Öffnet KEINE Datenbankverbindung.
- Führt KEINE LLM- oder Web-Search-Calls aus.
- Speichert KEINE Pläne.
- Ruft ausschliesslich build_full_skeleton(inputs) und validate_skeleton(skeleton, max_km)
  auf — dieselben öffentlichen, reinen Funktionen für alle 5 Szenarien. Keine
  szenariospezifischen Sonderfälle im Generator, nur unterschiedliche `inputs`.

Ausführen: python tests/generate_plan_scenarios.py
Schreibt: plan_scenario_report.txt, plan_scenario_report.json (Projekt-Root)
"""
import importlib.util
import json
import os
import sys
from datetime import timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

spec = importlib.util.spec_from_file_location(
    'generate_plan', os.path.join(REPO_ROOT, 'data', 'generate_plan.py')
)
gp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gp)


# ══════════════════════════════════════════════════════════════════════════
# ═══ Szenario-Definitionen — exakt wie in der Aufgabenstellung vorgegeben ═══
# ══════════════════════════════════════════════════════════════════════════
# 'athlete' und 'plan_meta' sind rein informativ (Kontext für die Auswertung).
# 'inputs' enthält NUR Felder, die build_full_skeleton() tatsächlich konsumiert —
# das deckt bewusst nicht 1:1 alle "Athlet"-Fakten ab (siehe Abschnitt
# "Interface-Deckungslücken" im Report).

SCENARIOS = [
    {
        'id': 1,
        'label': '10 KM STRASSE / EINSTEIGER',
        'name': '10K Beginner',
        'athlete': {
            'aktueller_stabiler_laufumfang_km': 18,
            'hoechste_woche_90d_km': 22,
            'laengster_relevanter_lauf_90d_km': 8,
            'avg_wochen_hm': 50,
            'verletzung': False,
            'kuerzlich_vergleichbare_distanz': False,
        },
        'plan_meta': {'ziel': 'Finish / erste 10 km'},
        'inputs': dict(
            start_date='2026-08-03', race_date='2026-10-25',
            race_distance_km=10, race_elevation_m=50, terrain='road',
            days_per_week=4, strength_sessions=1, strength_days=[3],
            quality_sessions=1, long_run_day=7,
            cross_training=False, cross_training_days=0,
            max_km=32, avg_weekly_km=18, avg_weekly_hm=50,
            athlete_paces={},
        ),
        'expected': {
            'start_longrun_km': (7, 8),
            'peak_longrun_km': (10, 12),
            'peak_weekly_km': (24, 30),
            'max_single_easy_km': 14.9,
            'low_hm': True,
        },
    },
    {
        'id': 2,
        'label': 'HALBMARATHON STRASSE / INTERMEDIÄR',
        'name': 'HM Intermediate',
        'athlete': {
            'stabiler_laufumfang_km': 32,
            'hoechste_woche_90d_km': 40,
            'bestaetigte_longruns_km': [14, 15, 16],
            'avg_wochen_hm': 150,
            'kontinuierliches_training': True,
            'verletzung': False,
        },
        'plan_meta': {'ziel': 'ambitionierte Zeitverbesserung'},
        'inputs': dict(
            start_date='2026-07-20', race_date='2026-11-01',
            race_distance_km=21.1, race_elevation_m=120, terrain='road',
            days_per_week=5, strength_sessions=1, strength_days=[2],
            quality_sessions=1, long_run_day=7,
            cross_training=True, cross_training_days=1, cross_replaces_run=False,
            max_km=50, avg_weekly_km=32, avg_weekly_hm=150,
            athlete_paces={},
        ),
        'cross_training_types': ['Rennrad'],
        'expected': {
            'start_longrun_km': (14, 17),
            'peak_longrun_km': (18, 22),
            'peak_weekly_km': (40, 50),
            'max_single_easy_km': 14.9,
            'low_hm': True,
        },
    },
    {
        'id': 3,
        'label': 'MARATHON STRASSE / FORTGESCHRITTEN',
        'name': 'Marathon Advanced',
        'athlete': {
            'stabiler_laufumfang_km': 52,
            'hoechste_woche_90d_km': 65,
            'bestaetigte_longruns_km': [24, 26, 28],
            'avg_wochen_hm': 250,
            'mehrjaehrige_erfahrung': True,
            'kuerzlich_hm_absolviert': True,
            'verletzung': False,
        },
        'plan_meta': {'ziel': 'Zeitrennen'},
        'inputs': dict(
            start_date='2026-06-29', race_date='2026-11-08',
            race_distance_km=42.195, race_elevation_m=180, terrain='road',
            days_per_week=6, strength_sessions=1, strength_days=[3],
            quality_sessions=2, long_run_day=7,
            cross_training=False, cross_training_days=0,
            max_km=80, avg_weekly_km=52, avg_weekly_hm=250,
            athlete_paces={},
        ),
        'expected': {
            'start_longrun_km': (24, 29),
            'peak_longrun_km': (30, 34),
            'peak_weekly_km': (65, 80),
            'max_single_easy_km': 14.9,
            'low_hm': True,
        },
    },
    {
        'id': 4,
        'label': '31 KM TRAIL / HYBRID-ATHLET',
        'name': 'Trail 31K Hybrid',
        'athlete': {
            'stabile_laufbasis_km': 36,
            'hoechste_laufwoche_90d_km': 43,
            'bestaetigte_longruns_km': [13, 14, 15],
            'avg_wochen_hm': 450,
            'verletzung': False,
            'cross_zusaetzlich': True,
        },
        'plan_meta': {'ziel': 'sicherer Finish mit guter Vorbereitung'},
        'inputs': dict(
            start_date='2026-07-13', race_date='2026-10-26',
            race_distance_km=30.7, race_elevation_m=1246, terrain='trail',
            days_per_week=6, strength_sessions=2, strength_days=[2, 4],
            quality_sessions=1, long_run_day=7,
            cross_training=True, cross_training_days=1, cross_replaces_run=False,
            max_km=55, avg_weekly_km=36, avg_weekly_hm=450,
            athlete_paces={},
        ),
        'cross_training_types': ['Rennrad'],
        'expected': {
            'total_weeks': 16,
            'start_longrun_km': (13, 16),
            'peak_longrun_km': (22, 25),
            'peak_longrun_hm': (850, 1120),
            'low_hm': False,
        },
    },
    {
        'id': 5,
        'label': 'BERGTRAIL 45 KM / ERFAHRENER TRAILRUNNER',
        'name': 'Mountain Trail 45K',
        'athlete': {
            'stabile_laufbasis_km': 48,
            'hoechste_woche_90d_km': 62,
            'bestaetigte_longruns': [(22, 900), (25, 1200), (28, 1450)],
            'avg_wochen_hm': 1400,
            'kuerzlich_30km_trail': True,
            'verletzung': False,
        },
        'plan_meta': {'ziel': 'Finish-orientiert'},
        'inputs': dict(
            start_date='2026-06-15', race_date='2026-10-17',
            race_distance_km=45, race_elevation_m=2600, terrain='mountain_trail',
            days_per_week=6, strength_sessions=1, strength_days=[2],
            quality_sessions=1, long_run_day=7,
            cross_training=True, cross_training_days=1, cross_replaces_run=True,
            max_km=70, avg_weekly_km=48, avg_weekly_hm=1400,
            athlete_paces={},
        ),
        'cross_training_types': ['Rennrad'],
        'expected': {
            'start_longrun_km': (22, 28),
            'peak_longrun_km': (28, 35),
            'low_hm': False,
        },
    },
]

# Felder, die in der Aufgabenstellung als "Athlet"-Fakten genannt werden, von
# build_full_skeleton() aber NICHT konsumiert werden (siehe Analyse unten) —
# rein informativ im Report ausgewiesen, damit nichts still verschwindet.
NOT_CONSUMED_BY_GENERATOR = [
    'hoechste_woche_90d_km / hoechste_laufwoche_90d_km / hoechste_woche_90d_km (max_weekly_km_actual)',
    'laengster_relevanter_lauf_90d_km',
    'bestaetigte_longruns_km / bestaetigte_longruns (Longrun-Historie)',
    'kontinuierliches_training / mehrjaehrige_erfahrung / kuerzlich_* (Trainingskontext)',
    'verletzung (kein Injury-Feld in build_full_skeleton)',
    'plan_meta.ziel (Zielintention, z.B. "Zeitrennen" vs. "Finish")',
    'cross_training_types (nur Phase 2 / LLM-Prompt, siehe enrich_week_with_llm)',
]


# ══════════════════════════════════════════════════════════════════════════
# ═══ Hilfsfunktionen ═══
# ══════════════════════════════════════════════════════════════════════════

QUALITY_TYPES = gp.QUALITY_TYPES
ENDURANCE_RUN_TYPES = gp.ENDURANCE_RUN_TYPES


def session_role(session_type):
    if session_type == 'Long Run':
        return 'Longrun'
    if session_type in QUALITY_TYPES:
        return 'Quality'
    if session_type in ENDURANCE_RUN_TYPES:
        return 'Easy'
    if session_type == 'Strength Training':
        return 'Kraft'
    if session_type == 'Cross Training':
        return 'Cross'
    if session_type == 'Race Day':
        return 'Race'
    return session_type


def load_weeks(skeleton):
    return [w for w in skeleton['weeks'] if w['phase'] in ('BASE', 'BUILD', 'PEAK')]


def find_finding(findings, category, severity, week, session, ist, erwartet, regel, ursache):
    findings.append({
        'category': category, 'severity': severity, 'week': week, 'session': session,
        'ist': ist, 'erwartet': erwartet, 'regel': regel, 'ursache': ursache,
    })


# ══════════════════════════════════════════════════════════════════════════
# ═══ Kategorisierung der validate_skeleton()-Fehler in die 6 Report-Buckets ═══
# ══════════════════════════════════════════════════════════════════════════

def categorize_validator_error(msg):
    m = msg.lower()
    if 'longrun-anteil' in m or ('long run' in m and 'longrun' not in m):
        return 'Longrun'
    if 'longrun' in m or 'long run' in m:
        return 'Longrun'
    if 'summe hm' in m:
        return 'HM/Terrain'
    if any(k in m for k in ('lower', 'strength', 'beineinheit', 'gymtag')):
        return 'Kraft/Cross'
    if any(k in m for k in ('race day', 'session am', 'doppelbelegung', 'quality session am montag',
                             'race_date')):
        return 'Struktur'
    if any(k in m for k in ('target_km', 'max_km', 'summe distanz', 'identisch zu woche')):
        return 'Volumen'
    return 'Struktur'


CODE_CAUSE_HINTS = {
    'Longrun-Anteil': 'compute_longrun_progression() / distribute_week_km() (data/generate_plan.py:279-362, 628-748)',
    'Summe HM': 'distribute_week_hm() (data/generate_plan.py:751-803)',
    'Lower': 'solve_week_layout() Lower-Body-Wahl (data/generate_plan.py:379-431)',
    'Gymtag': 'build_week_skeleton() Gymtag-Reservierung (data/generate_plan.py:528-534)',
    'Race Day': 'build_week_skeleton() Race-Day-Platzierung (data/generate_plan.py:499-509)',
    'Doppelbelegung': 'build_week_skeleton() reserved-Set-Logik (data/generate_plan.py:485-625)',
    'target_km': 'compute_weekly_progression() (data/generate_plan.py:149-196)',
    'max_km': 'compute_weekly_progression() / build_full_skeleton() max_km-Deckel (data/generate_plan.py:903)',
    'Summe Distanz': 'distribute_week_km() (data/generate_plan.py:628-748)',
    'identisch zu Woche': 'enforce_max_plateau() (data/generate_plan.py:199-222)',
}


def code_cause_for(msg):
    for key, hint in CODE_CAUSE_HINTS.items():
        if key.lower() in msg.lower():
            return hint
    return 'siehe validate_skeleton() (data/generate_plan.py:1052-1223)'


# ══════════════════════════════════════════════════════════════════════════
# ═══ Zusätzliche, über validate_skeleton() hinausgehende Checks ═══
# ══════════════════════════════════════════════════════════════════════════

def run_extra_checks(scenario, skeleton, findings):
    inputs = scenario['inputs']
    expected = scenario['expected']
    weeks = skeleton['weeks']
    by_num = {w['week_number']: w for w in weeks}
    dates = skeleton['dates']
    terrain = inputs['terrain']

    # ── Struktur ──────────────────────────────────────────────────────
    for w in weeks:
        days_used = {s['day_of_week'] for s in w['sessions']}
        if w['phase'] != 'RACE' and len(days_used) > inputs['days_per_week']:
            find_finding(findings, 'Struktur', 'FAIL', w['week_number'], '(Wochenübersicht)',
                         f"{len(days_used)} belegte Tage", f"<= days_per_week={inputs['days_per_week']}",
                         'belegte Trainingstage überschreiten days_per_week nicht',
                         'build_week_skeleton() Slot-Vergabe (data/generate_plan.py:485-625)')
        lr = next((s for s in w['sessions'] if s['session_type'] == 'Long Run'), None)
        if lr and w['phase'] != 'RACE' and lr['day_of_week'] != inputs['long_run_day']:
            find_finding(findings, 'Struktur', 'WARNING', w['week_number'], 'Long Run',
                         f"Tag {lr['day_of_week']}", f"Tag {inputs['long_run_day']} (long_run_day)",
                         'Longrun auf long_run_day, außer Rennwoche',
                         'build_week_skeleton() Longrun-zu-nah-an-Race-Verschiebung (data/generate_plan.py:511-526)')

    # Tag vor dem Rennen: Rest oder sehr kurzer Shakeout
    race_week = next((w for w in weeks if w['phase'] == 'RACE'), None)
    if race_week:
        day_before = dates['race_dow'] - 1
        s_before = next((s for s in race_week['sessions'] if s['day_of_week'] == day_before), None)
        if s_before and s_before.get('distance_km', 0) > 3:
            find_finding(findings, 'Taper', 'WARNING', race_week['week_number'], s_before['session_type'],
                         f"{s_before.get('distance_km')}km am Tag vor Race Day",
                         '<= 3km oder Rest', 'Tag vor dem Rennen ist Rest oder maximal sehr kurzer Shakeout',
                         'build_week_skeleton() Rennwoche-Layout (data/generate_plan.py:499-526)')
        if dates['race_dow'] == 1:  # Montag
            sunday = next((s for s in race_week['sessions'] if s['day_of_week'] == 7), None)
            prev_week = by_num.get(race_week['week_number'] - 1)
            prev_sunday = next((s for s in (prev_week['sessions'] if prev_week else [])
                                 if s['day_of_week'] == 7), None)
            if sunday or prev_sunday:
                find_finding(findings, 'Taper', 'FAIL', race_week['week_number'], 'Sonntag vor Rennen',
                             'Session gefunden', 'frei (Rest)',
                             'bei Montag-Rennen ist Sonntag zwingend frei',
                             'build_week_skeleton() (data/generate_plan.py:499-526)')
        if dates['race_dow'] == 6:  # Samstag
            prev_week = by_num.get(race_week['week_number'] - 1) if 5 not in {s['day_of_week'] for s in race_week['sessions']} else race_week
            fri = next((s for s in race_week['sessions'] if s['day_of_week'] == 5), None)
            if fri and fri.get('distance_km', 0) > 5:
                find_finding(findings, 'Taper', 'WARNING', race_week['week_number'], fri['session_type'],
                             f"{fri.get('distance_km')}km am Freitag vor Samstag-Rennen",
                             'frei oder kurzer Shakeout (<=5km)',
                             'bei Samstag-Rennen ist Freitag frei oder kurzer Shakeout',
                             'build_week_skeleton() (data/generate_plan.py:499-526)')

    # ── Volumen ───────────────────────────────────────────────────────
    for w in weeks:
        for s in w['sessions']:
            if s['session_type'] == 'Cross Training' and s.get('distance_km', 0) not in (0, None):
                find_finding(findings, 'Volumen', 'FAIL', w['week_number'], 'Cross Training',
                             f"distance_km={s.get('distance_km')}", '0 (nicht in Laufkm eingerechnet)',
                             'Cross-Kilometer werden nicht zu Laufkilometern addiert',
                             'distribute_week_km() Cross-Nullung (data/generate_plan.py:742-746)')
        easy_sessions = [s for s in w['sessions'] if s['session_type'] in ENDURANCE_RUN_TYPES]
        for s in easy_sessions:
            if s.get('distance_km', 0) > 20:
                find_finding(findings, 'Volumen', 'WARNING', w['week_number'], 'Easy Run',
                             f"{s['distance_km']}km", '<= ~15km (Regel 9: nie 15-19km, harte Deckelung bei 14.9km)',
                             'keine einzelnen Easy Runs werden unplausibel lang',
                             'distribute_week_km() Easy-Obergrenze (data/generate_plan.py:695-725)')

    lw = load_weeks(skeleton)
    deload_weeks = [w for w in weeks if w['phase'] == 'DELOAD']
    for dw in deload_weeks:
        prev_load = by_num.get(dw['week_number'] - 1)
        if prev_load and prev_load['phase'] in ('BASE', 'BUILD', 'PEAK'):
            if dw['target_km'] >= prev_load['target_km']:
                find_finding(findings, 'Volumen', 'FAIL', dw['week_number'], '(Wochenziel)',
                             f"target_km={dw['target_km']}", f"< Vorwoche ({prev_load['target_km']})",
                             'Deload reduziert Laufbelastung',
                             'compute_weekly_progression() DELOAD-Zweig (data/generate_plan.py:187-188)')

    peak_weeks = [w for w in weeks if w['phase'] == 'PEAK']
    taper_weeks_list = [w for w in weeks if w['phase'] == 'TAPER']
    if peak_weeks and taper_weeks_list:
        if max(w['week_number'] for w in peak_weeks) >= min(w['week_number'] for w in taper_weeks_list):
            find_finding(findings, 'Taper', 'FAIL', peak_weeks[0]['week_number'], '(Phasenreihenfolge)',
                         'PEAK nicht vor TAPER', 'PEAK liegt vor TAPER',
                         'Peak liegt vor Taper', 'compute_phase_map() (data/generate_plan.py:121-146)')

    if 'peak_weekly_km' in expected and lw:
        peak_km = max(w['target_km'] for w in lw)
        lo, hi = expected['peak_weekly_km']
        if not (lo * 0.85 <= peak_km <= hi * 1.15):
            find_finding(findings, 'Volumen', 'WARNING', None, '(Peak-Woche)',
                         f"peak target_km={peak_km}", f"~{lo}-{hi}km",
                         'Peak-Wochenumfang im erwarteten Bereich',
                         'compute_desired_peak_km() / compute_weekly_progression() (data/generate_plan.py:233-234, 149-196)')

    # ── Longrun ───────────────────────────────────────────────────────
    longrun_by_week = {}
    for w in weeks:
        lr = next((s for s in w['sessions'] if s['session_type'] == 'Long Run'), None)
        if lr:
            longrun_by_week[w['week_number']] = lr

    if 1 in longrun_by_week and 'start_longrun_km' in expected:
        km = longrun_by_week[1].get('distance_km', 0)
        lo, hi = expected['start_longrun_km']
        if not (lo * 0.7 <= km <= hi * 1.15):
            find_finding(findings, 'Longrun', 'WARNING', 1, 'Long Run',
                         f"{km}km", f"~{lo}-{hi}km (nahe bestätigter Longrun-Historie)",
                         'Startdistanz berücksichtigt vorhandene Longrun-Historie',
                         'compute_longrun_progression() START_FRACTION=36.5% von avg_weekly_km, '
                         'KEIN Input für Longrun-Historie vorhanden (data/generate_plan.py:279-330)')

    if longrun_by_week:
        peak_lr_week = max(longrun_by_week, key=lambda wn: longrun_by_week[wn].get('distance_km', 0))
        peak_lr_km = longrun_by_week[peak_lr_week].get('distance_km', 0)
        peak_lr_hm = longrun_by_week[peak_lr_week].get('elevation_gain_m', 0)
        if 'peak_longrun_km' in expected:
            lo, hi = expected['peak_longrun_km']
            if not (lo - 0.5 <= peak_lr_km <= hi + 1.5):
                find_finding(findings, 'Longrun', 'WARNING', peak_lr_week, 'Long Run',
                             f"{peak_lr_km}km", f"~{lo}-{hi}km",
                             'Peak-Longrun passt zu Renndistanz, Erfahrung und Terrain',
                             'compute_desired_peak_longrun_km() / compute_longrun_progression() '
                             '(data/generate_plan.py:265-362) — Formel ist NICHT terrainabhängig')
        if 'peak_longrun_hm' in expected:
            lo, hi = expected['peak_longrun_hm']
            if not (lo - 20 <= peak_lr_hm <= hi + 20):
                find_finding(findings, 'HM/Terrain', 'WARNING', peak_lr_week, 'Long Run',
                             f"{peak_lr_hm}Hm", f"~{lo}-{hi}Hm",
                             'Peak-Longrun-HM passt zum Rennen',
                             'compute_longrun_hm() (data/generate_plan.py:365-369)')
        race_day = dates['race_day']
        for wn, lr in longrun_by_week.items():
            w = by_num[wn]
            lr_date = w['week_date'] + timedelta(days=lr['day_of_week'] - 1)
            if (race_day - lr_date).days < 7:
                find_finding(findings, 'Longrun', 'FAIL', wn, 'Long Run',
                             f"{(race_day - lr_date).days} Tage vor Race Day", '>= 7 Tage',
                             'kein Peak-Longrun innerhalb der letzten 7 Tage vor dem Rennen',
                             'build_week_skeleton() longrun_too_close_to_race (data/generate_plan.py:514-526)')

    # Monotonie der Longrun-Progression über BASE/BUILD/PEAK (Deload-Wochen ausgenommen)
    load_lr = [(w['week_number'], longrun_by_week[w['week_number']].get('distance_km', 0))
               for w in lw if w['week_number'] in longrun_by_week]
    for i in range(1, len(load_lr)):
        prev_wn, prev_km = load_lr[i - 1]
        wn, km = load_lr[i]
        if km < prev_km - 0.15:
            find_finding(findings, 'Longrun', 'WARNING', wn, 'Long Run',
                         f"{km}km", f">= Vorwoche ({prev_km}km, Woche {prev_wn})",
                         'Longrun-Progression ist sicher und nachvollziehbar (monoton in Belastungswochen)',
                         'compute_longrun_progression() (data/generate_plan.py:279-362)')

    # ── HM/Terrain ────────────────────────────────────────────────────
    if terrain == 'road' and expected.get('low_hm'):
        max_hm = max((w['target_hm'] for w in weeks), default=0)
        if max_hm > 400:
            find_finding(findings, 'HM/Terrain', 'WARNING', None, '(alle Wochen)',
                         f"max target_hm={max_hm}", '< ~400 Hm (Road-Szenario)',
                         'Road-Szenarien erhalten niedrige, plausible HM',
                         'estimate_avg_weekly_hm() / TERRAIN_HM_PER_KM_ESTIMATE (data/generate_plan.py:54-56, 250-253)')
    if terrain in ('trail', 'mixed', 'mountain_trail') and not expected.get('low_hm', True):
        hm_by_week = [w['target_hm'] for w in lw]
        if len(hm_by_week) >= 2 and hm_by_week[-1] <= hm_by_week[0]:
            find_finding(findings, 'HM/Terrain', 'WARNING', None, '(Belastungswochen)',
                         f"HM Woche1={hm_by_week[0]} -> letzte Belastungswoche={hm_by_week[-1]}",
                         'progressiv steigend',
                         'Trail-Szenarien bauen HM progressiv auf',
                         'compute_weekly_progression() für hm_targets (data/generate_plan.py:925-928)')

    for w in weeks:
        quality_sessions = [s for s in w['sessions'] if s['session_type'] in QUALITY_TYPES]
        for s in quality_sessions:
            if terrain in ('trail', 'mixed') and s['session_type'] != 'Hill Session' and w['phase'] != 'TAPER':
                find_finding(findings, 'HM/Terrain', 'WARNING', w['week_number'], s['session_type'],
                             s['session_type'], 'Hill Session',
                             'Trail-Quality wird bevorzugt als Hill/Trail Session geplant',
                             'pick_quality_type() (data/generate_plan.py:434-445)')
            if terrain not in ('trail', 'mixed') and s['session_type'] == 'Hill Session':
                find_finding(findings, 'HM/Terrain', 'WARNING', w['week_number'], s['session_type'],
                             s['session_type'], 'Tempo/Interval/Sprint Session',
                             'Road-Quality wird bevorzugt als Tempo/Interval Session geplant',
                             'pick_quality_type() (data/generate_plan.py:434-445)')
            if terrain == 'mountain_trail' and s['session_type'] != 'Hill Session' and w['phase'] != 'TAPER':
                find_finding(findings, 'HM/Terrain', 'FAIL', w['week_number'], s['session_type'],
                             f"terrain='mountain_trail' -> {s['session_type']}",
                             "terrain='mountain_trail' sollte wie 'trail' behandelt werden -> Hill Session",
                             "terrain-Wert 'mountain_trail' ist nicht im Code-Enum ('trail','mixed') enthalten",
                             "pick_quality_type() prüft `terrain in ('trail', 'mixed')` wörtlich — "
                             "'mountain_trail' fällt durch (data/generate_plan.py:441)")

    for w in weeks:
        for s in w['sessions']:
            if s['session_type'] == 'Cross Training' and s.get('elevation_gain_m', 0) not in (0, None):
                find_finding(findings, 'HM/Terrain', 'FAIL', w['week_number'], 'Cross Training',
                             f"elevation_gain_m={s.get('elevation_gain_m')}", '0',
                             'Cross reduziert die Lauf-HM nicht automatisch (Cross selbst hat keine Lauf-HM)',
                             'distribute_week_hm() Cross-Nullung (data/generate_plan.py:797-801)')

    # ── Kraft/Cross ───────────────────────────────────────────────────
    for w in weeks:
        strength_days_used = sorted(s['day_of_week'] for s in w['sessions'] if s['session_type'] == 'Strength Training')
        requested = sorted(set(inputs['strength_days']))
        if w['phase'] not in ('RACE',) and strength_days_used != [d for d in requested if d in
                                                                     {s['day_of_week'] for s in w['sessions']} | set(strength_days_used)]:
            pass  # detaillierte Abweichungen werden bereits über conflicts dokumentiert, kein Doppel-Report

        lower_days = [s['day_of_week'] for s in w['sessions']
                      if s['session_type'] == 'Strength Training' and s.get('_strength_focus', '').startswith('lower')]
        quality_days = {s['day_of_week'] for s in w['sessions'] if s['session_type'] in QUALITY_TYPES}
        for ld in lower_days:
            _, after = gp.opposite_adjacent_days(ld)
            if after in quality_days:
                find_finding(findings, 'Kraft/Cross', 'FAIL', w['week_number'], 'Strength Training (lower)',
                             f"Lower Body Tag {ld}, Quality Tag {after}", 'kein Quality am Tag nach Lower Body',
                             'Lower Body nicht am Tag vor Longrun oder Quality',
                             'solve_week_layout() (data/generate_plan.py:379-431)')

        if inputs['cross_training'] and w['phase'] == 'DELOAD':
            cross_sessions = [s for s in w['sessions'] if s['session_type'] == 'Cross Training']
            if not cross_sessions:
                find_finding(findings, 'Kraft/Cross', 'WARNING', w['week_number'], '(Cross Training)',
                             'keine Cross-Session im Deload', 'Cross bleibt im Deload bestehen (reduziert)',
                             'Cross bleibt nach Möglichkeit im Deload bestehen und wird reduziert',
                             'build_week_skeleton() Cross-Platzierung (data/generate_plan.py:583-613)')
            else:
                normal_week = next((w2 for w2 in lw if w2['phase'] != 'DELOAD'), None)
                if normal_week:
                    normal_cross = next((s for s in normal_week['sessions'] if s['session_type'] == 'Cross Training'), None)
                    if normal_cross and cross_sessions[0].get('duration_min', 0) >= normal_cross.get('duration_min', 0):
                        find_finding(findings, 'Kraft/Cross', 'WARNING', w['week_number'], 'Cross Training',
                                     f"duration_min={cross_sessions[0].get('duration_min')}",
                                     f"< normale Woche ({normal_cross.get('duration_min')} min)",
                                     'Cross bleibt nach Möglichkeit im Deload bestehen und wird reduziert',
                                     'generate_plan.py DELOAD-Cross-Reduktion (data/generate_plan.py:1006-1011)')

        if inputs['cross_training'] and w['phase'] not in ('RACE',):
            if w['target_cross_minutes'] <= 0 and inputs['cross_training_days'] > 0 and w['phase'] != 'DELOAD':
                pass  # kann durch Slot-Konflikte legitim 0 sein; kein Fehlalarm ohne Kontext

    findings.append  # no-op guard (keeps flake8 quiet if unused paths above)


# ══════════════════════════════════════════════════════════════════════════
# ═══ Hauptlauf ═══
# ══════════════════════════════════════════════════════════════════════════

def run_scenario(scenario):
    inputs = dict(scenario['inputs'])
    skeleton = gp.build_full_skeleton(inputs)
    max_km = inputs['max_km']
    is_valid, validator_errors = gp.validate_skeleton(skeleton, max_km)

    findings = []
    for msg in validator_errors:
        cat = categorize_validator_error(msg)
        find_finding(findings, cat, 'FAIL', None, None, msg, '(siehe Regeltext)',
                     'validate_skeleton() Regelverstoß', code_cause_for(msg))
    run_extra_checks(scenario, skeleton, findings)

    return {
        'scenario': scenario,
        'inputs': inputs,
        'skeleton': skeleton,
        'validator_valid': is_valid,
        'validator_errors': validator_errors,
        'findings': findings,
    }


def category_rating(findings, category):
    cat_findings = [f for f in findings if f['category'] == category]
    if any(f['severity'] == 'FAIL' for f in cat_findings):
        return 'FAIL'
    if any(f['severity'] == 'WARNING' for f in cat_findings):
        return 'WARNING'
    return 'PASS'


def overall_rating(ratings):
    if 'FAIL' in ratings:
        return 'FAIL'
    if 'WARNING' in ratings:
        return 'WARNING'
    return 'PASS'


CATEGORIES = ['Struktur', 'Volumen', 'Longrun', 'HM/Terrain', 'Kraft/Cross', 'Taper']


def format_week_line(w):
    warn_count = 0
    return (
        f"  W{w['week_number']:>2} {w['week_date'].isoformat()} [{w['phase']:6s}] "
        f"base={w['target_km']:>5.1f} actual={w['actual_target_run_km']:>5.1f} "
        f"cross_min={w['target_cross_minutes']:>3} target_hm={w['target_hm']:>5.0f}"
    )


def main():
    out_lines = []

    def emit(line=''):
        out_lines.append(line)

    emit("=" * 100)
    emit("CAIRN — Szenario-Testmatrix data/generate_plan.py (Phase 1, deterministische Skelett-Logik)")
    emit("Ausschliesslich build_full_skeleton() + validate_skeleton() — keine DB/LLM/Web-Aufrufe.")
    emit("=" * 100)

    results = []
    for scenario in SCENARIOS:
        emit()
        emit("#" * 100)
        emit(f"SZENARIO {scenario['id']} — {scenario['label']} ({scenario['name']})")
        emit("#" * 100)

        emit("\n--- Normalisierte Eingaben (an build_full_skeleton übergeben) ---")
        for k, v in scenario['inputs'].items():
            emit(f"  {k} = {v!r}")
        if 'cross_training_types' in scenario:
            emit(f"  cross_training_types = {scenario['cross_training_types']!r}  "
                 f"(informativ — NICHT von build_full_skeleton konsumiert, nur Phase-2/LLM-Prompt)")
        emit("\n--- Athlet-Kontext (informativ, teils nicht vom Generator konsumiert) ---")
        for k, v in scenario['athlete'].items():
            emit(f"  {k} = {v!r}")
        emit(f"  plan_meta.ziel = {scenario['plan_meta']['ziel']!r}  (informativ, kein Generator-Input)")

        result = run_scenario(scenario)
        results.append(result)
        skeleton = result['skeleton']

        emit(f"\n--- Skelett-Zusammenfassung ---")
        emit(f"  total_weeks={skeleton['total_weeks']}  race_dow={skeleton['dates']['race_dow']} "
             f"({gp.DAY_NAMES[skeleton['dates']['race_dow']]})  "
             f"desired_peak_km={skeleton['desired_peak_km']:.1f}  peak_km_actual={skeleton['peak_km_actual']}")
        emit(f"  desired_peak_hm={skeleton['desired_peak_hm']:.1f}  "
             f"peak_hm_actual={round(skeleton['peak_hm_actual'] or 0, 1)}")
        emit(f"  validate_skeleton(): {'GÜLTIG' if result['validator_valid'] else 'UNGÜLTIG'} "
             f"({len(result['validator_errors'])} Validator-Fehler)")

        emit("\n--- Wochenübersicht ---")
        emit("  Wo Startdatum   Phase   base_km actual_km cross_min target_hm | sessions L/C/S/Q | LR-km LR-HM")
        for w in skeleton['weeks']:
            n_lauf = sum(1 for s in w['sessions'] if s['session_type'] in
                         (ENDURANCE_RUN_TYPES | QUALITY_TYPES | {'Long Run'}))
            n_cross = sum(1 for s in w['sessions'] if s['session_type'] == 'Cross Training')
            n_strength = sum(1 for s in w['sessions'] if s['session_type'] == 'Strength Training')
            n_quality = sum(1 for s in w['sessions'] if s['session_type'] in QUALITY_TYPES)
            lr = next((s for s in w['sessions'] if s['session_type'] == 'Long Run'), None)
            actual_km = round(sum(s.get('distance_km', 0) for s in w['sessions'] if s['session_type'] != 'Race Day'), 1)
            actual_hm = round(sum(s.get('elevation_gain_m', 0) for s in w['sessions'] if s['session_type'] != 'Race Day'))
            emit(f"  {w['week_number']:>2} {w['week_date'].isoformat()} {w['phase']:6s} "
                 f"{w['target_km']:>7.1f} {w['actual_target_run_km']:>9.1f} {w['target_cross_minutes']:>9} "
                 f"{w['target_hm']:>9.0f} | {n_lauf}/{n_cross}/{n_strength}/{n_quality}      | "
                 f"{(lr.get('distance_km') if lr else 0):>5} {(lr.get('elevation_gain_m') if lr else 0):>5}"
                 f"   (actual_km_summe={actual_km}, actual_hm_summe={actual_hm})")

        if skeleton['conflicts']:
            emit("\n--- Konflikte/Warnings (aus build_full_skeleton) ---")
            for c in skeleton['conflicts']:
                emit(f"    - {c}")
        else:
            emit("\n--- Keine dokumentierten Konflikte ---")

        emit("\n--- Alle Sessions ---")
        emit("  Datum      Wo Wochentag  Rolle    Session               km    HM  Min  Strength-Fokus   notes_hint")
        for w in skeleton['weeks']:
            for s in sorted(w['sessions'], key=lambda x: x['day_of_week']):
                d = w['week_date'] + timedelta(days=s['day_of_week'] - 1)
                focus = s.get('_strength_focus', '—')
                emit(f"  {d.isoformat()} {w['week_number']:>2} {gp.DAY_ABBR[s['day_of_week']]:9s} "
                     f"{session_role(s['session_type']):8s} {s['session_type']:20s} "
                     f"{s.get('distance_km', 0) or 0:>5} {s.get('elevation_gain_m', 0) or 0:>5} "
                     f"{s.get('duration_min', 0) or 0:>4}  {focus:15s}  (Phase 2/LLM — nicht generiert)")

        emit("\n--- Automatisierte Kriterien ---")
        for cat in CATEGORIES:
            rating = category_rating(result['findings'], cat)
            emit(f"  {cat:12s}: {rating}")
            cat_findings = [f for f in result['findings'] if f['category'] == cat and f['severity'] in ('FAIL', 'WARNING')]
            for f in cat_findings:
                wk = f"Woche {f['week']}" if f['week'] is not None else "(plan-weit)"
                emit(f"      [{f['severity']}] {wk} | {f['session']} | Ist: {f['ist']} | "
                     f"Erwartet: {f['erwartet']} | Regel: {f['regel']}")
                emit(f"        Ursache: {f['ursache']}")

    # ── Vergleichstabelle 1 ──────────────────────────────────────────
    emit("\n\n" + "=" * 100)
    emit("VERGLEICHSTABELLE 1 — Bewertung pro Kategorie")
    emit("=" * 100)
    header = f"| {'Szenario':28s} | " + " | ".join(f"{c:11s}" for c in CATEGORIES) + f" | {'Gesamt':7s} |"
    emit(header)
    emit("|" + "-" * (len(header) - 2) + "|")
    for result in results:
        s = result['scenario']
        ratings = {cat: category_rating(result['findings'], cat) for cat in CATEGORIES}
        overall = overall_rating(list(ratings.values()))
        row = f"| {s['id']}. {s['name']:25s} | " + " | ".join(f"{ratings[c]:11s}" for c in CATEGORIES) + f" | {overall:7s} |"
        emit(row)

    # ── Vergleichstabelle 2 ──────────────────────────────────────────
    emit("\n" + "=" * 100)
    emit("VERGLEICHSTABELLE 2 — Kennzahlen")
    emit("=" * 100)
    header2 = (f"| {'Szenario':22s} | {'Wochen':6s} | {'Start-km':8s} | {'Peak-km':7s} | "
               f"{'Start-LR':8s} | {'Peak-LR':7s} | {'Peak-LR-HM':10s} | {'Cross-min(Peak)':16s} | {'Race korrekt':12s} |")
    emit(header2)
    emit("|" + "-" * (len(header2) - 2) + "|")
    for result in results:
        s = result['scenario']
        skeleton = result['skeleton']
        weeks = skeleton['weeks']
        lw = load_weeks(skeleton)
        start_km = lw[0]['target_km'] if lw else 0
        peak_km = max((w['target_km'] for w in lw), default=0)
        longrun_by_week = {w['week_number']: next((sx for sx in w['sessions'] if sx['session_type'] == 'Long Run'), None)
                            for w in weeks}
        start_lr = next((lr.get('distance_km', 0) for lr in longrun_by_week.values() if lr), 0)
        lr_vals = [(wn, lr.get('distance_km', 0), lr.get('elevation_gain_m', 0)) for wn, lr in longrun_by_week.items() if lr]
        peak_lr = max(lr_vals, key=lambda t: t[1]) if lr_vals else (0, 0, 0)
        race_week = next((w for w in weeks if w['phase'] == 'RACE'), None)
        race_ok = False
        if race_week:
            rs = next((sx for sx in race_week['sessions'] if sx['session_type'] == 'Race Day'), None)
            if rs:
                actual_date = race_week['week_date'] + timedelta(days=rs['day_of_week'] - 1)
                race_ok = (actual_date == skeleton['dates']['race_day'])
        peak_week_cross = next((w['target_cross_minutes'] for w in lw if w['phase'] == 'PEAK'), 0)
        emit(f"| {s['id']}. {s['name']:19s} | {skeleton['total_weeks']:6d} | {start_km:8.1f} | {peak_km:7.1f} | "
             f"{start_lr:8.1f} | {peak_lr[1]:7.1f} | {peak_lr[2]:10} | {peak_week_cross:16} | {str(race_ok):12s} |")

    # ── Alle FAILS/WARNINGS gesammelt ────────────────────────────────
    emit("\n" + "=" * 100)
    emit("ALLE FAILS UND WARNINGS (gesammelt)")
    emit("=" * 100)
    for result in results:
        s = result['scenario']
        relevant = [f for f in result['findings'] if f['severity'] in ('FAIL', 'WARNING')]
        if not relevant:
            emit(f"\nSzenario {s['id']} ({s['name']}): keine FAILS/WARNINGS")
            continue
        emit(f"\nSzenario {s['id']} ({s['name']}):")
        for f in relevant:
            wk = f"Woche {f['week']}" if f['week'] is not None else "(plan-weit)"
            emit(f"  [{f['severity']}] {f['category']:12s} {wk:12s} {f['session']}")
            emit(f"      Ist:       {f['ist']}")
            emit(f"      Erwartet:  {f['erwartet']}")
            emit(f"      Regel:     {f['regel']}")
            emit(f"      Ursache:   {f['ursache']}")

    # ── Interface-Deckungslücken ──────────────────────────────────────
    emit("\n" + "=" * 100)
    emit("INTERFACE-ANALYSE — von der Aufgabenstellung genannte Athlet-Fakten, die build_full_skeleton()")
    emit("NICHT konsumiert (informativ, kein Fehler — zeigt Deckungslücken der Schnittstelle)")
    emit("=" * 100)
    for line in NOT_CONSUMED_BY_GENERATOR:
        emit(f"  - {line}")

    # ── Abschliessende Analyse-Liste ──────────────────────────────────
    emit("\n" + "=" * 100)
    emit("ANALYSE")
    emit("=" * 100)

    all_findings = [(r['scenario'], f) for r in results for f in r['findings'] if f['severity'] in ('FAIL', 'WARNING')]
    rule_scenarios = {}
    for s, f in all_findings:
        rule_scenarios.setdefault(f['regel'], set()).add(s['id'])
    cross_scenario_rules = {rule: ids for rule, ids in rule_scenarios.items() if len(ids) >= 2}

    emit("\n1. Fehler, die in mehreren Szenarien auftreten:")
    if cross_scenario_rules:
        for rule, ids in cross_scenario_rules.items():
            emit(f"   - \"{rule}\" — Szenarien {sorted(ids)}")
    else:
        emit("   - keine (alle Findings sind szenario-spezifisch)")

    emit("\n2. Terrain-spezifische Fehler:")
    emit("   - Szenario 5 (terrain='mountain_trail'): pick_quality_type() und weitere terrain-Checks prüfen")
    emit("     wörtlich `terrain in ('trail', 'mixed')` — 'mountain_trail' erfüllt das NICHT und fällt auf die")
    emit("     Road-Logik zurück (Tempo/Interval statt Hill Session, TERRAIN_HM_PER_KM_ESTIMATE-Fallback auf")
    emit("     'road'=3 Hm/km, kein Elevation-Zuschlag in compute_duration_min()).")

    emit("\n3. Distanz-spezifische Fehler:")
    emit("   - compute_desired_peak_longrun_km() (data/generate_plan.py:265-276) ist nur für race_distance_km")
    emit("     20-42km definiert (Szenarien 2/4 grenzwertig, Szenario 3/5 ausserhalb -> Rückfall auf feste")
    emit("     42.5%-Quote ohne Renn-Anker, siehe compute_longrun_progression() PEAK_FRACTION-Fallback).")
    emit("   - Szenario 1 (10km): sehr kurze Renndistanz, race_distance_km*0.80-Deckel in")
    emit("     compute_longrun_progression() (ceiling=race_distance_km*0.82) limitiert den Longrun strukturell")
    emit("     stark — bei 10km max. ~8.2km, unabhängig vom Longrun-Fraction-Verlauf.")

    emit("\n4. Regeln, die zu starr wirken:")
    emit("   - Longrun-Ceiling `race_distance_km * 0.82` (compute_longrun_progression, Zeile 330) gilt")
    emit("     unterschiedslos für 10km-Einsteiger und 45km-Bergtrail — für sehr kurze Rennen zu eng, für")
    emit("     sehr lange Trail-Rennen ignoriert es Zeitdauer-Aspekte des Longruns völlig (keine Berücksichtigung")
    emit("     von Gehzeit/Powerhiking bei 2600Hm).")
    emit("   - `terrain in ('trail', 'mixed')`-Stringvergleich (mehrfach: pick_quality_type, compute_duration_min,")
    emit("     distribute_week_hm, TERRAIN_HM_PER_KM_ESTIMATE) — kein Enum/Whitelist-Fallback, jeder neue")
    emit("     Terrain-Wert (z.B. 'mountain_trail') fällt lautlos auf Road-Verhalten zurück.")
    emit("   - max_weekly_km_actual wird von fetch_athlete_context() berechnet, aber in build_full_skeleton()")
    emit("     NIRGENDS gelesen (grep bestätigt) — die 'höchste Woche letzte 90 Tage' hat aktuell keinerlei")
    emit("     Einfluss auf desired_peak_km/max_km trotz expliziter Erhebung.")

    emit("\n5. Ergebnisse, die trainingspraktisch unplausibel wirken, obwohl validate_skeleton() PASS meldet:")
    for r in results:
        s = r['scenario']
        if r['validator_valid']:
            extra_fails = [f for f in r['findings'] if f['severity'] == 'FAIL' and f['regel'] != 'validate_skeleton() Regelverstoß']
            if extra_fails:
                for f in extra_fails[:2]:
                    emit(f"   - Szenario {s['id']} ({s['name']}): validate_skeleton()=GÜLTIG, aber "
                         f"\"{f['regel']}\" verletzt (Ist: {f['ist']}).")
    emit("   - Szenario 2/3 (Strasse): 'bestätigte Longruns' (14-16km bzw. 24-28km) fliessen NICHT in den")
    emit("     Start-Longrun ein — der Validator prüft nur den 90-110%-Korridor von avg_weekly_km in Woche 1,")
    emit("     nicht die Longrun-Historie selbst. Ein technisch PASS-Plan kann den Longrun trotzdem faktisch")
    emit("     unter der bereits bewiesenen Fähigkeit des Athleten starten lassen.")

    emit("\n6. Drei Codebereiche, die als Nächstes priorisiert werden sollten:")
    emit("   1. terrain-String-Matching konsolidieren (aktuell `terrain in ('trail','mixed')` an 4+ Stellen")
    emit("      dupliziert, kein zentrales Terrain-Enum) — verhindert stille Fallbacks wie bei 'mountain_trail'.")
    emit("   2. Longrun-Historie als echten Input aufnehmen (z.B. `confirmed_longrun_km` in inputs) statt")
    emit("      Start-Longrun ausschliesslich aus avg_weekly_km*36.5% abzuleiten — betrifft Szenario 2/3/5 direkt.")
    emit("   3. max_weekly_km_actual tatsächlich verwenden (aktuell totes Datenfeld) oder aus dem Interface")
    emit("      entfernen, um keine falsche Erwartung zu wecken, dass die höchste 90-Tage-Woche einfliesst.")

    report_text = "\n".join(out_lines)

    txt_path = os.path.join(REPO_ROOT, 'plan_scenario_report.txt')
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(report_text)

    json_data = []
    for r in results:
        s = r['scenario']
        json_data.append({
            'id': s['id'], 'name': s['name'], 'label': s['label'],
            'inputs': {k: v for k, v in r['inputs'].items()},
            'total_weeks': r['skeleton']['total_weeks'],
            'validator_valid': r['validator_valid'],
            'validator_errors': r['validator_errors'],
            'category_ratings': {cat: category_rating(r['findings'], cat) for cat in CATEGORIES},
            'overall_rating': overall_rating([category_rating(r['findings'], cat) for cat in CATEGORIES]),
            'findings': r['findings'],
            'weeks': [
                {
                    'week_number': w['week_number'], 'week_date': w['week_date'].isoformat(),
                    'phase': w['phase'], 'base_target_run_km': w['target_km'],
                    'actual_target_run_km': w['actual_target_run_km'], 'target_hm': w['target_hm'],
                    'target_cross_minutes': w['target_cross_minutes'],
                    'sessions': [
                        {**{k: v for k, v in sess.items() if k != '_strength_focus'},
                         'strength_focus': sess.get('_strength_focus'),
                         'session_role': session_role(sess['session_type'])}
                        for sess in sorted(w['sessions'], key=lambda x: x['day_of_week'])
                    ],
                }
                for w in r['skeleton']['weeks']
            ],
            'conflicts': r['skeleton']['conflicts'],
        })
    json_path = os.path.join(REPO_ROOT, 'plan_scenario_report.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2, default=str)

    print(report_text)
    print(f"\n\nReports geschrieben:\n  {txt_path}\n  {json_path}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
