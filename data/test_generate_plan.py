"""
Tests für die deterministische Skelett-Logik in data/generate_plan.py.
Reine Domänenlogik, keine DB-/API-Zugriffe.

Ausführen: python data/test_generate_plan.py
"""
import importlib.util
import os
import re
from datetime import date

spec = importlib.util.spec_from_file_location(
    'generate_plan', os.path.join(os.path.dirname(__file__), 'generate_plan.py')
)
gp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gp)


def make_inputs(**overrides):
    base = dict(
        start_date='2026-08-17', race_date='2026-10-26',
        race_distance_km=31, race_elevation_m=1246, terrain='trail',
        long_run_day=7, strength_days=[2, 4], strength_sessions=2,
        quality_sessions=1, days_per_week=6, cross_training=True,
        cross_training_days=1, avg_weekly_km=36, max_km=55,
        athlete_paces={},
    )
    base.update(overrides)
    return base


def all_sessions(skeleton):
    return [s for w in skeleton['weeks'] for s in w['sessions']]


def week_by_num(skeleton, n):
    return next(w for w in skeleton['weeks'] if w['week_number'] == n)


# ─────────────────────────────────────────────────────────────────────────

def test_race_day_montag():
    inputs = make_inputs(race_date='2026-10-26')  # Montag
    skel = gp.build_full_skeleton(inputs)
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"Skeleton invalid: {errors}"
    race_week = week_by_num(skel, skel['total_weeks'])
    race_days = [s for s in race_week['sessions'] if s['session_type'] == 'Race Day']
    assert len(race_days) == 1
    assert race_days[0]['day_of_week'] == 1
    assert race_week['target_km'] == 0, f"Montag-Rennen sollte target_km=0 ergeben, war {race_week['target_km']}"
    print("OK: test_race_day_montag")


def test_race_day_samstag():
    inputs = make_inputs(race_date='2026-10-24')  # Samstag
    skel = gp.build_full_skeleton(inputs)
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"Skeleton invalid: {errors}"
    race_week = week_by_num(skel, skel['total_weeks'])
    race_days = [s for s in race_week['sessions'] if s['session_type'] == 'Race Day']
    assert len(race_days) == 1 and race_days[0]['day_of_week'] == 6
    assert race_week['target_km'] > 0
    print("OK: test_race_day_samstag")


def test_race_day_sonntag():
    inputs = make_inputs(race_date='2026-10-25')  # Sonntag
    skel = gp.build_full_skeleton(inputs)
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"Skeleton invalid: {errors}"
    race_week = week_by_num(skel, skel['total_weeks'])
    race_days = [s for s in race_week['sessions'] if s['session_type'] == 'Race Day']
    assert len(race_days) == 1 and race_days[0]['day_of_week'] == 7
    print("OK: test_race_day_sonntag")


def test_angebrochene_erste_woche():
    # Start Mittwoch statt Montag -> Woche 1 nur Mi-So verfuegbar
    inputs = make_inputs(start_date='2026-08-19')  # Mittwoch
    skel = gp.build_full_skeleton(inputs)
    assert skel['dates']['actual_start_day'] == 3
    week1 = week_by_num(skel, 1)
    for s in week1['sessions']:
        assert s['day_of_week'] >= 3, f"Session vor Planbeginn: {s}"
    # target_km reduziert gegenueber einer vollen Woche
    full_week_km = gp.compute_desired_peak_km(36, 31, 55)
    assert week1['target_km'] < 36, f"target_km sollte reduziert sein, war {week1['target_km']}"
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"Skeleton invalid: {errors}"
    print("OK: test_angebrochene_erste_woche")


def test_race_date_nicht_am_longrun_tag():
    inputs = make_inputs(long_run_day=6, race_date='2026-10-28')  # Mittwoch-Rennen, Longrun sonst Samstag
    skel = gp.build_full_skeleton(inputs)
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"Skeleton invalid: {errors}"
    race_week = week_by_num(skel, skel['total_weeks'])
    assert not any(s['session_type'] == 'Long Run' for s in race_week['sessions'])
    # vorletzte Woche (Taper) sollte weiterhin Longrun-frei sein falls sie selbst nicht Taper... pruefe nur Race Week
    print("OK: test_race_date_nicht_am_longrun_tag")


def test_wechselnde_gym_days():
    for days in ([1, 3], [2, 5], [3, 6], [1, 4]):
        inputs = make_inputs(strength_days=days, strength_sessions=2)
        skel = gp.build_full_skeleton(inputs)
        valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
        assert valid, f"strength_days={days}: {errors}"
    print("OK: test_wechselnde_gym_days")


def test_gymtag_kollidiert_mit_longrun():
    inputs = make_inputs(long_run_day=2, strength_days=[2, 4], strength_sessions=2)
    skel = gp.build_full_skeleton(inputs)
    week1 = week_by_num(skel, 1)
    day2_sessions = [s for s in week1['sessions'] if s['day_of_week'] == 2]
    assert len(day2_sessions) == 1
    assert day2_sessions[0]['session_type'] == 'Long Run', "Longrun muss bei Kollision bestehen bleiben"
    strength_count = len([s for s in week1['sessions'] if s['session_type'] == 'Strength Training'])
    assert strength_count == 1, f"Gym an Tag 2 sollte entfallen, gefunden {strength_count} Strength Sessions"
    assert any('kollidiert mit Race Day/Longrun' in c for c in skel['conflicts'])
    print("OK: test_gymtag_kollidiert_mit_longrun")


def test_gymtag_kollidiert_mit_race_day():
    inputs = make_inputs(race_date='2026-10-27', strength_days=[2, 4], strength_sessions=2)  # Dienstag-Rennen
    skel = gp.build_full_skeleton(inputs)
    race_week = week_by_num(skel, skel['total_weeks'])
    day2_sessions = [s for s in race_week['sessions'] if s['day_of_week'] == 2]
    assert len(day2_sessions) == 1
    assert day2_sessions[0]['session_type'] == 'Race Day'
    strength_count = len([s for s in race_week['sessions'] if s['session_type'] == 'Strength Training'])
    assert strength_count <= 1
    print("OK: test_gymtag_kollidiert_mit_race_day")


def test_kein_lower_slot_verfuegbar():
    # Nur 1 Gymtag, direkt neben dem Longrun-Tag (Sa) -> fuer Lower Body verboten (Tag vor/nach Longrun)
    inputs = make_inputs(long_run_day=6, strength_days=[5], strength_sessions=1, quality_sessions=1)
    skel = gp.build_full_skeleton(inputs)
    week1 = week_by_num(skel, 1)
    strength = [s for s in week1['sessions'] if s['session_type'] == 'Strength Training']
    assert len(strength) == 1
    assert strength[0].get('_strength_focus') in ('upper', 'upper_light'), \
        f"Sollte auf Upper ausweichen, war {strength[0].get('_strength_focus')}"
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"Skeleton invalid: {errors}"
    print("OK: test_kein_lower_slot_verfuegbar")


def test_quality_sessions_0_1_mehrere():
    for q in (0, 1, 2):
        inputs = make_inputs(quality_sessions=q, days_per_week=6, strength_sessions=1, strength_days=[3])
        skel = gp.build_full_skeleton(inputs)
        # base/build Woche pruefen (Woche 1)
        week1 = week_by_num(skel, 1)
        actual_q = len([s for s in week1['sessions'] if s['session_type'] in gp.QUALITY_TYPES])
        assert actual_q == q, f"quality_sessions={q}: gefunden {actual_q} in Woche 1 (Phase {week1['phase']})"
        valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
        assert valid, f"quality_sessions={q}: {errors}"
    print("OK: test_quality_sessions_0_1_mehrere")


def test_deload_rueckkehr_korrekt():
    inputs = make_inputs(total_weeks=None)
    skel = gp.build_full_skeleton(inputs)
    phase_by_week, _, _ = gp.compute_phase_map(skel['total_weeks'])
    deload_weeks = [w for w, p in phase_by_week.items() if p == 'DELOAD']
    assert deload_weeks, "Testszenario sollte mindestens eine Deload-Woche enthalten"
    for dw in deload_weeks:
        deload_week = week_by_num(skel, dw)
        prev_load_week_num = dw - 1
        while phase_by_week.get(prev_load_week_num) not in ('BASE', 'BUILD', 'PEAK') and prev_load_week_num > 0:
            prev_load_week_num -= 1
        if prev_load_week_num < 1:
            continue
        prev_load_km = week_by_num(skel, prev_load_week_num)['target_km']
        assert deload_week['target_km'] <= prev_load_km, "Deload muss unter der vorherigen Belastungswoche liegen"

        # Woche NACH dem Deload: Vergleich mit Woche VOR dem Deload (nicht mit der reduzierten Deload-Woche)
        next_week_num = dw + 1
        if next_week_num <= skel['total_weeks'] and phase_by_week.get(next_week_num) in ('BASE', 'BUILD', 'PEAK'):
            next_week = week_by_num(skel, next_week_num)
            assert next_week['target_km'] <= round(prev_load_km * 1.10, 1) + 0.05, (
                f"Woche nach Deload ({next_week['target_km']}) sollte gegen Vor-Deload-Woche "
                f"({prev_load_km}) begrenzt sein, nicht gegen die Deload-Woche selbst"
            )
    print("OK: test_deload_rueckkehr_korrekt")


def test_peak_ziel_nicht_erreichbar_max_km():
    inputs = make_inputs(avg_weekly_km=36, max_km=38, race_distance_km=31)
    skel = gp.build_full_skeleton(inputs)
    desired = gp.compute_desired_peak_km(36, 31, 38)
    assert desired <= 38
    for w in skel['weeks']:
        assert w['target_km'] <= 38 + 0.01, f"Woche {w['week_number']}: target_km {w['target_km']} > max_km 38"
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"Skeleton invalid: {errors}"
    print("OK: test_peak_ziel_nicht_erreichbar_max_km")


def test_cross_training_ersetzt_ausdauer():
    inputs_with = make_inputs(cross_training=True, cross_training_days=1, days_per_week=6, strength_sessions=2, strength_days=[2, 4])
    inputs_without = make_inputs(cross_training=False, cross_training_days=0, days_per_week=6, strength_sessions=2, strength_days=[2, 4])
    skel_with = gp.build_full_skeleton(inputs_with)
    skel_without = gp.build_full_skeleton(inputs_without)
    week_with = week_by_num(skel_with, 1)
    week_without = week_by_num(skel_without, 1)
    cross_count = len([s for s in week_with['sessions'] if s['session_type'] == 'Cross Training'])
    easy_with = len([s for s in week_with['sessions'] if s['session_type'] == 'Easy Run'])
    easy_without = len([s for s in week_without['sessions'] if s['session_type'] == 'Easy Run'])
    assert cross_count == 1
    assert easy_with == easy_without - 1, f"Cross Training sollte einen Easy Run ersetzen ({easy_with} vs {easy_without})"
    for s in week_with['sessions']:
        if s['session_type'] == 'Cross Training':
            assert s['distance_km'] == 0
    print("OK: test_cross_training_ersetzt_ausdauer")


def test_wochensummen_nach_rundung_korrekt():
    inputs = make_inputs()
    skel = gp.build_full_skeleton(inputs)
    for w in skel['weeks']:
        actual_km = sum(s.get('distance_km', 0) for s in w['sessions'] if s['session_type'] != 'Race Day')
        ref = w.get('actual_target_run_km', w['target_km'])
        if ref > 0:
            assert abs(actual_km - ref) <= max(ref * 0.05, 0.2) + 0.01, (
                f"Woche {w['week_number']}: Summe {actual_km} vs actual_target_run_km {ref}"
            )
    print("OK: test_wochensummen_nach_rundung_korrekt")


def test_wochenuebergreifende_quality_nach_longrun_regel():
    # Konstruiere ein Skelett manuell und verletze absichtlich die Regel, um den Validator zu pruefen
    inputs = make_inputs(long_run_day=7)  # Longrun am Sonntag
    skel = gp.build_full_skeleton(inputs)
    week1 = week_by_num(skel, 1)
    week2 = week_by_num(skel, 2)
    # Sicherstellen dass Woche 1 einen Longrun am So (Tag 7) hat
    assert any(s['day_of_week'] == 7 and s['session_type'] == 'Long Run' for s in week1['sessions'])
    # Manipuliere Woche 2 Montag zu einer Quality Session
    monday_session = next((s for s in week2['sessions'] if s['day_of_week'] == 1), None)
    if monday_session is None:
        week2['sessions'].append({'day_of_week': 1, 'session_type': 'Tempo Session', 'distance_km': 5, 'elevation_gain_m': 0, 'duration_min': 30})
    else:
        monday_session['session_type'] = 'Tempo Session'
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert not valid, "Validator sollte Quality Montag nach Longrun Sonntag als Fehler erkennen"
    assert any('Montag direkt nach Longrun' in e for e in errors)
    print("OK: test_wochenuebergreifende_quality_nach_longrun_regel")


def test_eingabefehler_strength_mismatch():
    inputs = make_inputs(strength_sessions=2, strength_days=[2, 2, 4])  # 2 eindeutige Tage waeren 2 (2,4) -> eigentlich ok
    # Echter Widerspruch: strength_sessions=3 aber nur 2 eindeutige Tage
    inputs2 = make_inputs(strength_sessions=3, strength_days=[2, 4])
    try:
        gp.build_full_skeleton(inputs2)
        assert False, "Sollte SkeletonError werfen"
    except gp.SkeletonError:
        pass
    print("OK: test_eingabefehler_strength_mismatch")


def test_max_eine_session_pro_tag():
    inputs = make_inputs()
    skel = gp.build_full_skeleton(inputs)
    for w in skel['weeks']:
        days = [s['day_of_week'] for s in w['sessions']]
        assert len(days) == len(set(days)), f"Woche {w['week_number']}: Doppelbelegung {days}"
    print("OK: test_max_eine_session_pro_tag")


# ─────────────────────────────────────────────────────────────────────────
# VOLUMEN-BUGFIX TESTS — konkrete Zahlen aus der Aufgabenstellung
# ─────────────────────────────────────────────────────────────────────────

def test_volumen_1_startwoche_34_38():
    inputs = make_inputs(avg_weekly_km=36)
    skel = gp.build_full_skeleton(inputs)
    week1 = week_by_num(skel, 1)
    assert 34 <= week1['target_km'] <= 38, f"Startwoche target_km={week1['target_km']}, erwartet 34-38"
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"Skeleton invalid: {errors}"
    print(f"OK: test_volumen_1_startwoche_34_38 (target_km={week1['target_km']})")


def test_volumen_2_desired_peak_km_46_5():
    desired = gp.compute_desired_peak_km(avg_weekly_km=36, race_distance_km=31, max_km=55)
    assert desired == 46.5, f"desired_peak_km={desired}, erwartet 46.5"
    print(f"OK: test_volumen_2_desired_peak_km_46_5 (desired_peak_km={desired})")


def test_volumen_3_peak_woche_44_48():
    inputs = make_inputs(avg_weekly_km=36, race_distance_km=31, max_km=55)
    skel = gp.build_full_skeleton(inputs)
    peak_weeks = [w for w in skel['weeks'] if w['phase'] == 'PEAK']
    assert peak_weeks, "keine PEAK-Woche im Skelett gefunden"
    peak = peak_weeks[0]
    assert 44 <= peak['target_km'] <= 48, f"Peak-Woche target_km={peak['target_km']}, erwartet 44-48"
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"Skeleton invalid: {errors}"
    print(f"OK: test_volumen_3_peak_woche_44_48 (target_km={peak['target_km']})")


def test_volumen_4_peak_longrun_19_21():
    inputs = make_inputs(avg_weekly_km=36, race_distance_km=31, max_km=55)
    skel = gp.build_full_skeleton(inputs)
    peak_weeks = [w for w in skel['weeks'] if w['phase'] == 'PEAK']
    assert peak_weeks
    peak = peak_weeks[0]
    lr = next(s for s in peak['sessions'] if s['session_type'] == 'Long Run')
    assert 19 <= lr['distance_km'] <= 21, f"Peak-Longrun={lr['distance_km']}, erwartet 19-21"
    print(f"OK: test_volumen_4_peak_longrun_19_21 (longrun={lr['distance_km']})")


def test_volumen_5_build_wochen_progressiv_oder_dokumentiert():
    inputs = make_inputs(avg_weekly_km=36, race_distance_km=31, max_km=55)
    skel = gp.build_full_skeleton(inputs)
    load_phases = ('BASE', 'BUILD', 'PEAK')
    by_num = {w['week_number']: w for w in skel['weeks']}
    conflicts_text = " ".join(skel['conflicts'])
    identical_undocumented = []
    for n in sorted(by_num):
        w, prev = by_num[n], by_num.get(n - 1)
        if not prev or w['phase'] not in load_phases or prev['phase'] not in load_phases:
            continue
        if w['target_km'] == prev['target_km']:
            documented = f"Woche {n}: target_km identisch zu Woche {n - 1}" in conflicts_text
            if not documented:
                identical_undocumented.append(n)
    assert not identical_undocumented, f"Undokumentierte identische Wochen: {identical_undocumented}"
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"Skeleton invalid: {errors}"
    print("OK: test_volumen_5_build_wochen_progressiv_oder_dokumentiert")


def test_volumen_6_nach_deload_vergleich_mit_vordeload():
    inputs = make_inputs(avg_weekly_km=36, race_distance_km=31, max_km=55)
    skel = gp.build_full_skeleton(inputs)
    phase_by_week, _, _ = gp.compute_phase_map(skel['total_weeks'])
    by_num = {w['week_number']: w for w in skel['weeks']}
    for n, phase in phase_by_week.items():
        if phase != 'DELOAD':
            continue
        prev_load_num = n - 1
        while phase_by_week.get(prev_load_num) not in ('BASE', 'BUILD', 'PEAK') and prev_load_num > 0:
            prev_load_num -= 1
        if prev_load_num < 1:
            continue
        prev_load_km = by_num[prev_load_num]['target_km']
        next_num = n + 1
        if next_num in by_num and phase_by_week.get(next_num) in ('BASE', 'BUILD', 'PEAK'):
            next_km = by_num[next_num]['target_km']
            assert next_km <= round(prev_load_km * 1.10, 1) + 0.05, (
                f"Woche {next_num} ({next_km}) sollte gegen Vor-Deload-Woche {prev_load_num} "
                f"({prev_load_km}) begrenzt sein, nicht gegen die Deload-Woche {n}"
            )
        # Deload-Longrun zaehlt nicht als neue Progressionsbasis
        prev_lr = next((s for s in by_num[prev_load_num]['sessions'] if s['session_type'] == 'Long Run'), None)
        if next_num in by_num:
            next_lr = next((s for s in by_num[next_num]['sessions'] if s['session_type'] == 'Long Run'), None)
            if prev_lr and next_lr:
                assert next_lr['distance_km'] <= round(prev_lr['distance_km'] * 1.10, 1) + 0.05, (
                    f"Longrun Woche {next_num} sollte gegen Vor-Deload-Longrun ({prev_lr['distance_km']}) "
                    f"begrenzt sein"
                )
    print("OK: test_volumen_6_nach_deload_vergleich_mit_vordeload")


def test_volumen_7_cross_replaces_run_false_reduziert_laufziel_nicht():
    """Root-Cause-Fix (PDF Problem 1): historical_run_baseline_km/base_target_run_km und die
    tatsaechlich reduzierte actual_target_run_km sind jetzt getrennte Konzepte. Bei
    cross_replaces_run=False (Default — das Frontend fragt diese Entscheidung noch nicht ab) kommt
    Cross Training ZUSAETZLICH zur historischen Laufbasis: das Laufziel wird NICHT mehr pauschal um
    20% reduziert (vorher: 36 -> 28.8km, obwohl die 36km eine stabile, tatsaechlich gelaufene
    Basis waren). Andere, unabhaengige Sicherheitsgrenzen (z.B. Regel 9: Easy Run max. 35%/14.9km)
    koennen weiterhin greifen und actual_target_run_km reduzieren — das ist kein Cross-Effekt."""
    inputs_with = make_inputs(avg_weekly_km=36, race_distance_km=31, max_km=55, cross_training=True, cross_training_days=1)
    inputs_without = make_inputs(avg_weekly_km=36, race_distance_km=31, max_km=55, cross_training=False, cross_training_days=0)
    skel_with = gp.build_full_skeleton(inputs_with)
    skel_without = gp.build_full_skeleton(inputs_without)
    week_with = week_by_num(skel_with, 1)
    week_without = week_by_num(skel_without, 1)

    assert week_with['target_km'] == week_without['target_km'] == week_with['base_target_run_km'], (
        f"base target_km/base_target_run_km sollte durch Cross unveraendert bleiben: "
        f"{week_with['target_km']} vs {week_without['target_km']}"
    )
    # cross_replaces_run=False (Default): KEINE Cross-bedingte Reduktion mehr. actual_target_run_km
    # darf trotzdem < base sein (Regel 9 Easy-Cap), aber deutlich naeher an base als die alten 80%.
    base = week_with['target_km']
    actual = week_with['actual_target_run_km']
    assert actual >= base * 0.85, (
        f"actual_target_run_km={actual} sollte bei cross_replaces_run=False nahe an base={base} bleiben "
        f"(keine pauschale Cross-Reduktion mehr)"
    )
    assert skel_with['historical_run_baseline_km'] == 36, "historical_run_baseline_km sollte die unveraenderte Trainingshistorie sein"
    print(f"OK: test_volumen_7_cross_replaces_run_false_reduziert_laufziel_nicht (base={base}, actual_target_run_km={actual})")


def test_volumen_7b_cross_replaces_run_true_reduziert_kontrolliert():
    """Gegenstück zu test_volumen_7: cross_replaces_run=True ersetzt bewusst eine Laufeinheit —
    das Laufziel wird reduziert, aber anhand eines geschaetzten ersetzten Easy-Run-Anteils, weiterhin
    gedeckelt bei max. 20% (nicht mehr, aber auch nicht mehr pauschal fix bei genau 20%)."""
    inputs = make_inputs(avg_weekly_km=36, race_distance_km=31, max_km=55, cross_training=True,
                          cross_training_days=1, cross_replaces_run=True)
    skel = gp.build_full_skeleton(inputs)
    week1 = week_by_num(skel, 1)
    base = week1['target_km']
    actual = week1['actual_target_run_km']
    assert actual < base, f"cross_replaces_run=True sollte das Laufziel reduzieren, war {actual} von base={base}"
    assert actual >= base * 0.80 - 0.05, f"actual_target_run_km={actual} unterschreitet die 80%-Untergrenze von base={base}"
    print(f"OK: test_volumen_7b_cross_replaces_run_true_reduziert_kontrolliert (base={base}, actual_target_run_km={actual})")


# ─────────────────────────────────────────────────────────────────────────
# NEUE VALIDATOR-TESTS (Session-Verteilung, Deload, Taper, Build-Plateau)
# ─────────────────────────────────────────────────────────────────────────

def test_neu_6tage_2gym_1cross_3laeufe_easy_nicht_ueber_longrun():
    inputs = make_inputs(days_per_week=6, strength_sessions=2, strength_days=[2, 4],
                          cross_training=True, cross_training_days=1, quality_sessions=1)
    skel = gp.build_full_skeleton(inputs)
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"Skeleton invalid: {errors}"
    for w in skel['weeks']:
        if w['phase'] not in ('BASE', 'BUILD', 'PEAK'):
            continue
        longrun = next((s for s in w['sessions'] if s['session_type'] == 'Long Run'), None)
        easy_sessions = [s for s in w['sessions'] if s['session_type'] in gp.ENDURANCE_RUN_TYPES]
        run_count = len(easy_sessions) + (1 if longrun else 0) + len([s for s in w['sessions'] if s['session_type'] in gp.QUALITY_TYPES])
        if run_count == 3 and longrun:
            for e in easy_sessions:
                assert e['distance_km'] <= longrun['distance_km'] + 0.05, (
                    f"Woche {w['week_number']}: Easy Run {e['distance_km']} > Longrun {longrun['distance_km']}"
                )
    print("OK: test_neu_6tage_2gym_1cross_3laeufe_easy_nicht_ueber_longrun")


def test_neu_kein_easy_ueber_35_prozent():
    inputs = make_inputs(days_per_week=6, strength_sessions=2, strength_days=[2, 4],
                          cross_training=True, cross_training_days=1, quality_sessions=1)
    skel = gp.build_full_skeleton(inputs)
    for w in skel['weeks']:
        ref = w.get('actual_target_run_km', w['target_km'])
        if ref <= 0:
            continue
        for s in w['sessions']:
            if s['session_type'] in gp.ENDURANCE_RUN_TYPES:
                assert s['distance_km'] <= ref * 0.35 + 0.15, (
                    f"Woche {w['week_number']}: Easy Run {s['distance_km']} > 35% von actual_target_run_km={ref}"
                )
                assert not (15.0 <= s['distance_km'] <= 19.0), (
                    f"Woche {w['week_number']}: Easy Run {s['distance_km']} liegt in der verbotenen 15-19km-Zone"
                )
    print("OK: test_neu_kein_easy_ueber_35_prozent")


def test_neu_cross_bleibt_in_deload():
    inputs = make_inputs(cross_training=True, cross_training_days=1)
    skel = gp.build_full_skeleton(inputs)
    deload_weeks = [w for w in skel['weeks'] if w['phase'] == 'DELOAD']
    assert deload_weeks, "Testszenario sollte mindestens eine Deload-Woche enthalten"
    for w in deload_weeks:
        cross_sessions = [s for s in w['sessions'] if s['session_type'] == 'Cross Training']
        assert cross_sessions, f"Woche {w['week_number']} (DELOAD): Cross Training fehlt, wurde durch Easy Run ersetzt"
        for c in cross_sessions:
            assert c['duration_min'] < 60, f"Woche {w['week_number']} (DELOAD): Cross-Dauer {c['duration_min']} nicht reduziert (Regel 10: -20-30%)"
    print("OK: test_neu_cross_bleibt_in_deload")


def test_neu_deload_kein_4_lauf():
    inputs = make_inputs(days_per_week=6, strength_sessions=2, strength_days=[2, 4],
                          cross_training=True, cross_training_days=1, quality_sessions=1)
    skel = gp.build_full_skeleton(inputs)
    deload_weeks = [w for w in skel['weeks'] if w['phase'] == 'DELOAD']
    assert deload_weeks
    for w in deload_weeks:
        run_types = {'Long Run'} | gp.ENDURANCE_RUN_TYPES | gp.QUALITY_TYPES
        run_sessions = [s for s in w['sessions'] if s['session_type'] in run_types]
        assert len(run_sessions) <= 3, (
            f"Woche {w['week_number']} (DELOAD): {len(run_sessions)} Laufsessions gefunden, "
            f"Cross Training sollte einen Ausdauertag belegen statt eines 4. Laufs"
        )
    print("OK: test_neu_deload_kein_4_lauf")


def test_neu_taper_vor_montagsrennen_fr_sa_und_sonntag_rest():
    inputs = make_inputs(race_date='2026-10-26')  # Montag
    skel = gp.build_full_skeleton(inputs)
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"Skeleton invalid: {errors}"
    taper_week = week_by_num(skel, skel['total_weeks'] - 1)
    assert taper_week['phase'] == 'TAPER'
    sunday_session = next((s for s in taper_week['sessions'] if s['day_of_week'] == 7), None)
    assert sunday_session is None, f"Sonntag der letzten Taper-Woche sollte Rest sein, gefunden: {sunday_session}"
    fri = next((s for s in taper_week['sessions'] if s['day_of_week'] == 5), None)
    sat = next((s for s in taper_week['sessions'] if s['day_of_week'] == 6), None)
    combined = (fri.get('distance_km', 0) if fri else 0) + (sat.get('distance_km', 0) if sat else 0)
    assert combined <= 14.05, f"Fr+Sa zusammen {combined}km > 14km (Regel 16)"
    print(f"OK: test_neu_taper_vor_montagsrennen_fr_sa_und_sonntag_rest (Fr+Sa={combined}km)")


def test_neu_max_2_identische_build_wochen():
    for max_km_val in (40, 46, 55, 70):
        inputs = make_inputs(max_km=max_km_val)
        skel = gp.build_full_skeleton(inputs)
        load_phases = ('BASE', 'BUILD', 'PEAK')
        by_num = {w['week_number']: w for w in skel['weeks']}
        run_len = 1
        for n in sorted(by_num):
            w, prev = by_num[n], by_num.get(n - 1)
            if prev and w['phase'] in load_phases and prev['phase'] in load_phases and w['target_km'] == prev['target_km']:
                run_len += 1
                assert run_len <= 2, f"max_km={max_km_val}: {run_len} identische Wochen in Folge bei Woche {n} (max. 2 erlaubt)"
            else:
                run_len = 1
    print("OK: test_neu_max_2_identische_build_wochen")


# ─────────────────────────────────────────────────────────────────────────
# BUGFIX-TESTS — Cross-Tag-Flexibilitaet, Peak-HM, Taper-Hill-Session, Warning-Konsistenz
# ─────────────────────────────────────────────────────────────────────────

def test_bugfix_cross_training_tag_flexibel_nicht_immer_montag():
    """Root-Cause-Fix Punkt 1: Cross Training darf sich am Wochenlayout orientieren statt immer den
    ersten freien Tag (i.d.R. Montag) zu nehmen. Verschiedene Wochenstrukturen muessen zu
    unterschiedlichen Cross-Tagen fuehren (sonst waere die alte 'immer erster freier Tag'-Logik
    unveraendert aktiv)."""
    combos = [
        dict(long_run_day=7, strength_days=[2, 4]),
        dict(long_run_day=6, strength_days=[1, 3]),
        dict(long_run_day=3, strength_days=[5, 7]),
        dict(long_run_day=1, strength_days=[3, 5]),
    ]
    cross_days_seen = set()
    for combo in combos:
        inputs = make_inputs(cross_training=True, cross_training_days=1, **combo)
        skel = gp.build_full_skeleton(inputs)
        week1 = week_by_num(skel, 1)
        cross = next((s['day_of_week'] for s in week1['sessions'] if s['session_type'] == 'Cross Training'), None)
        assert cross is not None, f"{combo}: keine Cross-Training-Session in Woche 1 gefunden"
        assert cross not in combo['strength_days'] and cross != combo['long_run_day'], (
            f"{combo}: Cross-Tag {cross} kollidiert mit einem harten Tag"
        )
        cross_days_seen.add(cross)
    assert len(cross_days_seen) >= 2, (
        f"Cross-Tag sollte sich mit der Wochenstruktur aendern, war fuer alle Kombinationen {cross_days_seen}"
    )
    print(f"OK: test_bugfix_cross_training_tag_flexibel_nicht_immer_montag (gesehene Tage: {sorted(cross_days_seen)})")


def test_bugfix_desired_peak_hm_rennverankert():
    """Root-Cause-Fix Punkt 2: compute_desired_peak_hm() war vorher min(race*0.85, avg_hm*1.30) —
    bei einer (mangels HM-Historie) geschaetzten avg_weekly_hm dominierte praktisch immer der
    historische Wert (36 km/Woche Trail -> avg_hm=540 -> 540*1.30=702, weit unter 1246*0.85=1059).
    Jetzt renn-verankert: ~105% der Renn-HM, hier 1246*1.05=1308.3."""
    desired = gp.compute_desired_peak_hm(avg_weekly_hm=540, race_elevation_m=1246)
    assert desired == 1308.3, f"desired_peak_hm={desired}, erwartet 1308.3 (renn-verankert, nicht mehr 702)"
    print(f"OK: test_bugfix_desired_peak_hm_rennverankert (desired_peak_hm={desired})")


def test_bugfix_peak_hm_und_peak_longrun_hm_erreichbar():
    """Bei ausreichender Vorbereitungszeit (17 Wochen) muss die Peak-Woche nahe an die renn-
    verankerte Ziel-HM herankommen (vorher: 702 Ziel, 702 erreicht — aber Ziel selbst zu niedrig).
    Der Peak-Longrun-HM-Anteil (compute_longrun_hm, vorher 0.55) muss im 75-90%-Korridor der
    Renn-HM liegen (vorher nur ~55%: 386 von 1246 HM)."""
    inputs = make_inputs(start_date='2026-08-17', race_date='2026-12-07', avg_weekly_km=36,
                          race_distance_km=31, race_elevation_m=1246, max_km=55)
    skel = gp.build_full_skeleton(inputs)
    assert skel['desired_peak_hm'] == 1308.3, f"desired_peak_hm={skel['desired_peak_hm']}"
    assert skel['peak_hm_actual'] >= skel['desired_peak_hm'] * 0.90, (
        f"peak_hm_actual={skel['peak_hm_actual']} sollte bei 17 Wochen Vorbereitung nahe am Ziel "
        f"{skel['desired_peak_hm']} liegen"
    )
    peak = next(w for w in skel['weeks'] if w['phase'] == 'PEAK')
    lr = next(s for s in peak['sessions'] if s['session_type'] == 'Long Run')
    assert 0.75 * 1246 <= lr['elevation_gain_m'] <= 0.90 * 1246, (
        f"Peak-Longrun-HM={lr['elevation_gain_m']}, erwartet 75-90% von 1246 HM (934-1121)"
    )
    print(f"OK: test_bugfix_peak_hm_und_peak_longrun_hm_erreichbar "
          f"(peak_hm_actual={skel['peak_hm_actual']}, peak_longrun_hm={lr['elevation_gain_m']})")


def test_bugfix_peak_longrun_km_hoeher_als_vorher_und_validator_gueltig():
    """Root-Cause-Fix Punkt 3: Peak-Longrun war strukturell auf ~19-20km begrenzt (fixer 42.5%-
    Anteil an einem bereits durch race_distance_km*1.50 gedeckelten Wochenziel). Jetzt renn-
    verankert (compute_desired_peak_longrun_km, Ziel ~78% der Renndistanz = 24.2km bei 31km),
    weiterhin sicher validator-gueltig (Longrun-Anteil <= 45%+2% Toleranz)."""
    inputs = make_inputs(start_date='2026-08-17', race_date='2026-12-07', avg_weekly_km=36,
                          race_distance_km=31, race_elevation_m=1246, max_km=55)
    skel = gp.build_full_skeleton(inputs)
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"Skeleton invalid: {errors}"
    peak = next(w for w in skel['weeks'] if w['phase'] == 'PEAK')
    lr = next(s for s in peak['sessions'] if s['session_type'] == 'Long Run')
    assert lr['distance_km'] >= 20.0, f"Peak-Longrun={lr['distance_km']}km, erwartet deutlich > alte ~19.7km"
    assert lr['distance_km'] / peak['target_km'] <= 0.47, "Longrun-Anteil verletzt die Validator-Obergrenze"
    print(f"OK: test_bugfix_peak_longrun_km_hoeher_als_vorher_und_validator_gueltig (peak_longrun_km={lr['distance_km']})")


def test_bugfix_taper_keine_hill_session():
    """Root-Cause-Fix Punkt 3 (PDF): in der Taper-Woche darf die (einzige) Quality Session keine
    Hill Session mehr sein (vorher: pick_quality_type waehlte fuer Trail/Mixed IMMER 'Hill Session'
    an erster Stelle, unabhaengig von der Phase) — und ihr HM-Anteil muss spuerbar reduziert sein."""
    inputs = make_inputs(start_date='2026-08-17', race_date='2026-10-26', avg_weekly_km=36,
                          race_distance_km=31, race_elevation_m=1246, max_km=55)
    skel = gp.build_full_skeleton(inputs)
    taper_weeks = [w for w in skel['weeks'] if w['phase'] == 'TAPER']
    assert taper_weeks, "Testszenario sollte mindestens eine Taper-Woche enthalten"
    found_quality = False
    for w in taper_weeks:
        for s in w['sessions']:
            if s['session_type'] in gp.QUALITY_TYPES:
                found_quality = True
                assert s['session_type'] != 'Hill Session', (
                    f"Woche {w['week_number']} (TAPER): Hill Session gefunden — sollte in Taper vermieden werden"
                )
                assert s.get('elevation_gain_m', 0) < 200, (
                    f"Woche {w['week_number']} (TAPER): Quality-HM={s.get('elevation_gain_m')} zu hoch fuer Taper "
                    f"(vorher z.B. 270 HM Hill Session)"
                )
    assert found_quality, "Testszenario sollte mindestens eine Taper-Quality-Session enthalten"
    print("OK: test_bugfix_taper_keine_hill_session")


def test_bugfix_warning_konsistenz_finaler_wert():
    """Root-Cause-Fix Bonusfund: jede 'Laufziel von X auf Y reduziert'-Konfliktmeldung muss den
    tatsaechlich finalen, gespeicherten actual_target_run_km referenzieren (vorher: eine fruehe
    Meldung z.B. '26.0 auf 24.1' konnte durch spaetere, unabhaengige Korrekturen ueberholt werden,
    die Sessions ergaben am Ende einen ganz anderen Wert, z.B. 18.9)."""
    inputs = make_inputs(start_date='2026-08-17', race_date='2026-10-26', avg_weekly_km=36,
                          race_distance_km=31, race_elevation_m=1246, max_km=55)
    skel = gp.build_full_skeleton(inputs)
    by_num = {w['week_number']: w for w in skel['weeks']}
    checked = 0
    for c in skel['conflicts']:
        if 'Laufziel von' in c and 'reduziert' in c:
            m = re.search(r'Woche (\d+): Laufziel von [\d.]+km auf ([\d.]+)km reduziert', c)
            assert m, f"Unerwartetes Format der Konfliktmeldung: {c}"
            week_num, logged_final = int(m.group(1)), float(m.group(2))
            actual_final = by_num[week_num]['actual_target_run_km']
            assert abs(logged_final - actual_final) < 0.05, (
                f"Woche {week_num}: Meldung nennt {logged_final}km, tatsaechlich gespeichert ist "
                f"{actual_final}km — Meldung ist veraltet"
            )
            checked += 1
    assert checked > 0, "Testszenario sollte mindestens eine Laufziel-Reduktions-Meldung erzeugen"
    print(f"OK: test_bugfix_warning_konsistenz_finaler_wert ({checked} Meldungen geprueft)")


def test_bugfix_flavor_field_maxlen_verhindert_db_crash():
    """Root-Cause-Fix: die GitHub-Actions-Live-Verifikation (job gha-verify-1786620435) crashte im
    training_plan-INSERT mit psycopg2.errors.StringDataRightTruncation ('value too long for type
    character varying(20)'), weil das LLM einen session_zone-Wert (DB-Spalte VARCHAR(20)) laenger
    als 20 Zeichen zurueckgab und keine Stelle im Code das vor dem INSERT abgefangen hat.
    clamp_flavor_field_lengths() muss ueberlange session_zone/main_pace-Werte auf die tatsaechliche
    DB-Spaltenlaenge kuerzen (20 bzw. 50 Zeichen), kurze Werte unveraendert lassen und Nicht-Strings
    (None, Zahlen) unangetastet durchreichen."""
    zu_lang_zone = "Zone 2 (aerober Grundlagenbereich)"  # 35 Zeichen, > 20
    zu_lang_pace = "irgendwo zwischen 4:00 und 4:30 pro Kilometer, je nach Tagesform"  # > 50 Zeichen
    llm_data = {
        'day_of_week': 3, 'notes': 'Ruhiger Lauf.', 'session_zone': zu_lang_zone,
        'main_pace': zu_lang_pace, 'warmup_km': None, 'main_sets': 4,
    }
    result = gp.clamp_flavor_field_lengths(dict(llm_data))
    assert len(result['session_zone']) <= 20, f"session_zone zu lang: {result['session_zone']!r}"
    assert len(result['main_pace']) <= 50, f"main_pace zu lang: {result['main_pace']!r}"
    assert result['session_zone'] == zu_lang_zone[:20].rstrip()
    assert result['notes'] == 'Ruhiger Lauf.', "notes (kein Laengenlimit) darf nicht veraendert werden"
    assert result['warmup_km'] is None
    assert result['main_sets'] == 4

    kurz = gp.clamp_flavor_field_lengths({'session_zone': 'Zone 2', 'main_pace': '4:15/km'})
    assert kurz['session_zone'] == 'Zone 2', "kurze Werte duerfen nicht veraendert werden"
    assert kurz['main_pace'] == '4:15/km'
    print("OK: test_bugfix_flavor_field_maxlen_verhindert_db_crash")


# ─────────────────────────────────────────────────────────────────────────
# DRY RUN — exakte Szenario-Vorgabe aus der Aufgabenstellung
# ─────────────────────────────────────────────────────────────────────────

def dry_run():
    inputs = dict(
        start_date='2026-08-17', race_date='2026-10-26',
        terrain='trail', race_distance_km=31, race_elevation_m=1246,
        avg_weekly_km=36, max_km=55,
        strength_days=[2, 4], long_run_day=7, days_per_week=6,
        strength_sessions=2, quality_sessions=1,
        cross_training=True, cross_training_days=1, cross_training_types=['Rennrad'],
        athlete_paces={},
    )
    ausdauer_days = gp.validate_inputs(inputs['strength_sessions'], inputs['strength_days'], inputs['days_per_week'])
    assert ausdauer_days == 4, f"ausdauer_days sollte 4 sein, war {ausdauer_days}"

    skel = gp.build_full_skeleton(inputs)
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"DRY RUN Skeleton invalid: {errors}"

    dates = skel['dates']
    assert dates['race_day'] == date(2026, 10, 26)
    assert dates['race_dow'] == 1, f"26.10.2026 sollte Montag (dow=1) sein, war {dates['race_dow']}"

    race_week = week_by_num(skel, skel['total_weeks'])
    race_sessions = [s for s in race_week['sessions'] if s['session_type'] == 'Race Day']
    assert len(race_sessions) == 1
    race_date_check = race_week['week_date'] + gp.timedelta(days=race_sessions[0]['day_of_week'] - 1)
    assert race_date_check == date(2026, 10, 26), f"Race Day landet auf {race_date_check}, erwartet 2026-10-26"
    print(f"DRY RUN: Race Day exakt {race_date_check} ({'Montag' if dates['race_dow']==1 else '?'}) — OK")

    # Kein Longrun So 25.10.2026 (Vorwoche)
    all_lr_dates = []
    for w in skel['weeks']:
        for s in w['sessions']:
            if s['session_type'] == 'Long Run':
                d = w['week_date'] + gp.timedelta(days=s['day_of_week'] - 1)
                all_lr_dates.append(d)
    assert date(2026, 10, 25) not in all_lr_dates, f"Longrun am 25.10.2026 gefunden: {all_lr_dates}"
    print(f"DRY RUN: kein Longrun am 25.10.2026 — OK (Longrun-Termine: {all_lr_dates[-3:]})")

    # Keine Session nach Race Day
    for w in skel['weeks']:
        for s in w['sessions']:
            d = w['week_date'] + gp.timedelta(days=s['day_of_week'] - 1)
            assert d <= date(2026, 10, 26), f"Session am {d} liegt nach Race Day"
    print("DRY RUN: keine Session nach Race Day — OK")

    # Rennwoche ohne Quality und ohne Lower/Full Body
    quality_in_race_week = [s for s in race_week['sessions'] if s['session_type'] in gp.QUALITY_TYPES]
    lower_full_in_race_week = [
        s for s in race_week['sessions']
        if s['session_type'] == 'Strength Training' and s.get('_strength_focus') in ('lower', 'lower_moderate', 'full_light')
    ]
    assert not quality_in_race_week, f"Quality in Rennwoche gefunden: {quality_in_race_week}"
    assert not lower_full_in_race_week, f"Lower/Full Body in Rennwoche gefunden: {lower_full_in_race_week}"
    print("DRY RUN: Rennwoche ohne Quality und ohne Lower/Full Body — OK")

    print(f"\nDRY RUN Zusammenfassung: total_weeks={skel['total_weeks']}, "
          f"desired_peak_km={skel['desired_peak_km']}, peak_km_actual={skel['peak_km_actual']}, "
          f"desired_peak_hm={round(skel['desired_peak_hm'])}, peak_hm_actual={round(skel['peak_hm_actual'] or 0)}")
    for w in skel['weeks']:
        session_summary = ', '.join(f"Tag{s['day_of_week']}:{s['session_type']}" for s in w['sessions'])
        print(f"  Woche {w['week_number']:2d} [{w['phase']:6s}] target_km={w['target_km']:5.1f} "
              f"target_hm={w['target_hm']:5.0f}  {session_summary}")
    if skel['conflicts']:
        print("  Konflikte (dokumentiert):")
        for c in skel['conflicts']:
            print(f"    - {c}")

    print("\nDRY RUN: ALLE ERWARTUNGEN ERFÜLLT")


def dry_run_16_wochen():
    """16-Wochen-Dry-Run, wie im PDF-Szenario gefordert (36 km/Woche historische Laufbasis, 1x
    Cross Training/Rennrad, Trailrennen 31km/1246 HM). Zeigt zusaetzlich zur Standard-Zusammenfassung
    explizit Wochensummen, Cross-Minuten, Longrun-km und Longrun-HM pro Woche."""
    inputs = dict(
        start_date='2026-08-17', race_date='2026-11-30',
        terrain='trail', race_distance_km=31, race_elevation_m=1246,
        avg_weekly_km=36, max_km=55,
        strength_days=[2, 4], long_run_day=7, days_per_week=6,
        strength_sessions=2, quality_sessions=1,
        cross_training=True, cross_training_days=1,
        athlete_paces={},
    )
    skel = gp.build_full_skeleton(inputs)
    valid, errors = gp.validate_skeleton(skel, inputs['max_km'])
    assert valid, f"DRY RUN (16 Wochen) Skeleton invalid: {errors}"
    assert skel['total_weeks'] == 16, f"total_weeks={skel['total_weeks']}, erwartet 16"

    print(f"\nDRY RUN (16 Wochen, PDF-Szenario): historical_run_baseline_km={skel['historical_run_baseline_km']}, "
          f"cross_replaces_run={skel['cross_replaces_run']}, desired_peak_km={skel['desired_peak_km']}, "
          f"peak_km_actual={skel['peak_km_actual']}, desired_peak_hm={round(skel['desired_peak_hm'], 1)}, "
          f"peak_hm_actual={round(skel['peak_hm_actual'] or 0, 1)}")
    print(f"  {'Woche':>5} {'Phase':7} {'target_km':>9} {'actual_run_km':>13} {'target_hm':>9} "
          f"{'cross_min':>9} {'longrun_km':>10} {'longrun_hm':>10}")
    for w in skel['weeks']:
        lr = next((s for s in w['sessions'] if s['session_type'] == 'Long Run'), None)
        print(f"  {w['week_number']:>5} {w['phase']:7} {w['target_km']:>9.1f} {w['actual_target_run_km']:>13.1f} "
              f"{w['target_hm']:>9.0f} {w['target_cross_minutes']:>9} "
              f"{(lr['distance_km'] if lr else 0):>10.1f} {(lr['elevation_gain_m'] if lr else 0):>10}")
    if skel['conflicts']:
        print("  Konflikte (dokumentiert):")
        for c in skel['conflicts']:
            print(f"    - {c}")
    print("\nDRY RUN (16 Wochen): ALLE ERWARTUNGEN ERFÜLLT")


if __name__ == '__main__':
    tests = [
        test_race_day_montag,
        test_race_day_samstag,
        test_race_day_sonntag,
        test_angebrochene_erste_woche,
        test_race_date_nicht_am_longrun_tag,
        test_wechselnde_gym_days,
        test_gymtag_kollidiert_mit_longrun,
        test_gymtag_kollidiert_mit_race_day,
        test_kein_lower_slot_verfuegbar,
        test_quality_sessions_0_1_mehrere,
        test_deload_rueckkehr_korrekt,
        test_peak_ziel_nicht_erreichbar_max_km,
        test_cross_training_ersetzt_ausdauer,
        test_wochensummen_nach_rundung_korrekt,
        test_wochenuebergreifende_quality_nach_longrun_regel,
        test_eingabefehler_strength_mismatch,
        test_max_eine_session_pro_tag,
        test_volumen_1_startwoche_34_38,
        test_volumen_2_desired_peak_km_46_5,
        test_volumen_3_peak_woche_44_48,
        test_volumen_4_peak_longrun_19_21,
        test_volumen_5_build_wochen_progressiv_oder_dokumentiert,
        test_volumen_6_nach_deload_vergleich_mit_vordeload,
        test_volumen_7_cross_replaces_run_false_reduziert_laufziel_nicht,
        test_volumen_7b_cross_replaces_run_true_reduziert_kontrolliert,
        test_neu_6tage_2gym_1cross_3laeufe_easy_nicht_ueber_longrun,
        test_neu_kein_easy_ueber_35_prozent,
        test_neu_cross_bleibt_in_deload,
        test_neu_deload_kein_4_lauf,
        test_neu_taper_vor_montagsrennen_fr_sa_und_sonntag_rest,
        test_neu_max_2_identische_build_wochen,
        test_bugfix_cross_training_tag_flexibel_nicht_immer_montag,
        test_bugfix_desired_peak_hm_rennverankert,
        test_bugfix_peak_hm_und_peak_longrun_hm_erreichbar,
        test_bugfix_peak_longrun_km_hoeher_als_vorher_und_validator_gueltig,
        test_bugfix_taper_keine_hill_session,
        test_bugfix_warning_konsistenz_finaler_wert,
        test_bugfix_flavor_field_maxlen_verhindert_db_crash,
    ]
    failed = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL: {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, str(e)))
            print(f"ERROR: {t.__name__}: {e}")

    print()
    if failed:
        print(f"{len(failed)}/{len(tests)} TESTS FEHLGESCHLAGEN")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        raise SystemExit(1)
    else:
        print(f"ALLE {len(tests)} TESTS GRÜN")

    print("\n" + "=" * 70)
    print("DRY RUN")
    print("=" * 70)
    dry_run()

    print("\n" + "=" * 70)
    print("DRY RUN (16 WOCHEN, PDF-SZENARIO)")
    print("=" * 70)
    dry_run_16_wochen()