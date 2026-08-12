"""
Tests für die deterministische Skelett-Logik in data/generate_plan.py.
Reine Domänenlogik, keine DB-/API-Zugriffe.

Ausführen: python data/test_generate_plan.py
"""
import importlib.util
import os
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
        if w['target_km'] > 0:
            assert abs(actual_km - w['target_km']) <= max(w['target_km'] * 0.05, 0.2) + 0.01, (
                f"Woche {w['week_number']}: Summe {actual_km} vs target {w['target_km']}"
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
