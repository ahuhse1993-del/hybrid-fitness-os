# CAIRN · ChatGPT Handbook

Knowledge-File für den CAIRN-GPT. Tool-Schemas (Namen, Parameter, Return-Types) kennt ChatGPT bereits über MCP — hier stehen nur Regeln, Workflows und Details, die in keinem Schema stehen.

## 1. CAIRN in 3 Sätzen

CAIRN ist Alexanders persönliches Trainingssystem: ein Postgres-Backend auf Railway verbindet Trainingsplan, Garmin (Cardio/strukturierte Workouts), Hevy (Kraft-Routinen) und Aktivitäts-Analysen in einer Datenbank. ChatGPT greift per MCP (Bearer-Auth) auf alle Tools zu — Schreiboperationen gegen Garmin/Hevy laufen immer über ein preview-Tool zur Validierung, CAIRN-eigene Schreiboperationen sind idempotent (external_id/upsert). Railway: `https://web-production-297f2.up.railway.app` · Analyse-Ansicht: `https://web-production-297f2.up.railway.app/analyse?id=<training_id>`.

## 2. VERBINDLICHE REGELN

- **Strength Training, Krafttraining, Mobility, Core, Rest Day → NIEMALS zu Garmin.** Serverseitig hart abgelehnt (`NEVER_GARMIN_TYPES` in `coach/session_routing.py` + `coach/garmin_push.py`). Hevy ist die einzige Quelle für Kraft.
- **`push_sessions_to_garmin`: max. 7 Sessions pro Call** (chunk_size-Default). Antwort enthält `remaining_ids`/`total_remaining` — bei Rest erneut aufrufen, nie die volle Liste erzwingen.
- **Vor `bulk_delete_garmin_workouts` / `purge_all_sessions`:** erst `list_garmin_workouts` bzw. `reconcile_training_block` zur Bestandsaufnahme. `purge_all_sessions` verlangt zusätzlich `confirm=True`.
- **Hevy-Routinen nur mit echten `exercise_template_id` aus `list_hevy_exercise_templates`.** Keine Freinamen, keine erfundenen IDs — `preview_hevy_routine` prüft das serverseitig.
- **Immer preview vor write:** `preview_garmin_workout` → `create_garmin_workout`, `preview_hevy_routine` → `upsert_hevy_routine`, `preview_training_block` → `upsert_training_block`.
- **Race-Date-Änderung an aktivem Plan:** `upsert_training_block` lehnt ohne `confirm_race_date_change=True` ab — nie stillschweigend überschreiben.
- **Keine Credentials ausgeben.** Nie loggen, nie in Tool-Antworten wiederholen.
- **Nach `save_activity_analysis` / `save_activity_coach_analysis` immer den Analyselink zurückgeben:**
  `https://web-production-297f2.up.railway.app/analyse?id=<training_id>`
  (nicht das `frontend_url`-Feld aus der Tool-Antwort übernehmen — das zeigt auf eine andere, ältere Analyse-Seite.)

## 3. WORKFLOWS

### 3.1 Tagesstart / Überblick
1. `get_athlete_profile`
2. `sync_completed_activities` (source="both")
3. `get_recent_activities`
4. `get_planned_workouts` (days=7)
5. `get_health_data` + `get_checkins`

### 3.2 Neuen Trainingsblock importieren + zu Garmin pushen
1. `preview_training_block` — `valid`, `race_date_change`, `routing_summary` prüfen
2. Bei `race_date_change`: mit Alexander bestätigen
3. `upsert_training_block` (confirm_race_date_change nur wenn nötig)
4. Für Sessions mit `sync_target="garmin"`: `preview_garmin_workout` je Session
5. `create_garmin_workout` je Session (external_id = training_plan-ID + Datum) — oder gesammelt: `push_sessions_to_garmin`, bei `total_remaining>0` wiederholen
6. Sessions mit `sync_target="hevy"`: nicht zu Garmin pushen, siehe 3.5

### 3.3 Aktivität analysieren
1. `sync_completed_activities`
2. `get_activity_analysis_status` — fresh/stale/missing?
3. Falls stale/missing: `sync_activity_details` (No-Op wenn schon vollständig)
4. `prepare_activity_analysis` — Werte + `source_data_hash` holen
5. **Subjektives Feedback von Alexander einbeziehen** (RPE, Gefühl, Kontext aus dem Gespräch) — nicht nur die Zahlen interpretieren
6. `save_activity_coach_analysis` (volle strukturierte Analyse) oder `save_activity_analysis` (einfacher Text+Titel)
7. Analyselink zurückgeben (siehe Regel oben)

### 3.4 Einzelne Session ändern
- Nur Notizen/Distanz/Dauer/Zone/Höhenmeter: `patch_planned_workout`
- Datum, session_type, status, sync_target, phase: `update_planned_workout` (mit `reason`)
- Workout-Struktur komplett neu: `upsert_planned_workout` erneut mit gleichem `external_id`
- Bereits auf Garmin + inhaltliche Änderung: `push_sessions_to_garmin` (sync_status wird automatisch `dirty`)
- Nur Datum verschieben, bereits auf Garmin: `move_garmin_workout`

### 3.5 Hevy-Routine erstellen oder updaten
1. `list_hevy_exercise_templates` (search=...) — echte `exercise_template_id` finden
2. `preview_hevy_routine` — `valid=True`? Warnings lesen (Set-Feld vs. exercise_type)
3. Bei Fehlern korrigieren, erneut preview
4. `upsert_hevy_routine` (ohne `hevy_routine_id` = create, mit = update)
5. `list_hevy_routines` zur Bestätigung des CAIRN-Syncs

### 3.6 Wochenanalyse
1. `get_training_summary` (weeks=1)
2. `get_planned_workouts` vs. `get_recent_activities` — geplant vs. real
3. `reconcile_training_block` — Garmin-Kalender vs. CAIRN-Plan
4. `get_hevy_workouts` (days=7) — Kraftsessions der Woche
5. `get_health_data` + `get_checkins` — Erholung/Subjektives

## 4. HEVY-SPEZIFIKA (nicht im Schema)

- `exercise_template_id` ist **kein UUID** — kurzer alphanumerischer Code (z.B. `"EAC7D9C5"`, 8 Zeichen).
- CAIRN-Folder-ID: **3380361** (Env `HEVY_CAIRN_FOLDER_ID`, sonst dieser Fallback).
- Hevy hat **keinen Delete/Archive-Endpoint** für Routinen. `archive_hevy_routine` markiert nur den CAIRN-Status — die Routine bleibt in Hevy bestehen und muss dort manuell gelöscht werden.
- Hevys **Schreib-Schema weicht vom Lese-Schema ab**: Sets dürfen beim Schreiben (POST/PUT) kein `index`-Feld enthalten (Hevy vergibt es selbst nach Array-Reihenfolge) — GET liefert `index` trotzdem mit zurück. `POST /v1/routines` antwortet mit `{"routine": [...]}` (Liste), `GET /v1/routines/{id}` mit `{"routine": {...}}` (Objekt).
- Alle 10 echten `exercise_type`-Werte + unterstützte Set-Felder (ermittelt aus allen 457 echten Templates, Stand 2026-08-27):

| exercise_type | weight | reps | duration | distance |
|---|---|---|---|---|
| weight_reps | ✓ | ✓ | | |
| bodyweight_weighted | ✓ | ✓ | | |
| bodyweight_assisted | ✓ | ✓ | | |
| reps_only | | ✓ | | |
| duration | | | ✓ | |
| weight_duration | ✓ | | ✓ | |
| distance_duration | | | ✓ | ✓ |
| short_distance_weight | ✓ | | | ✓ |
| floors_duration | | | ✓ | |
| steps_duration | | | ✓ | |

## 5. FEHLERBEHANDLUNG

- **504 / Timeout bei Garmin-Batch:** Batch zu groß — weniger IDs pro Call. `push_sessions_to_garmin` macht das per `chunk_size=7` automatisch, `remaining_ids` weiterverarbeiten.
- **Garmin Auth-Fehler:** meist Rate-Limit nach vielen Logins in kurzer Zeit. Kurz warten, erneut versuchen — Session-Cache greift beim nächsten Call meist wieder.
- **Hevy 400 "Unrecognized key(s)" o.ä.:** falsches Set-Feld für den `exercise_type` der Übung. `get_hevy_exercise_template` prüfen (`supports_weight/reps/duration/distance`) und nur passende Felder senden.
- **`failed`-Sessions in `reconcile_training_block`:** einzeln `delete_garmin_workout` (räumt auch den training_plan-Sync-State auf), danach `push_sessions_to_garmin` für dieselbe Session erneut.
- **`conflict` in `reconcile_training_block`:** `garmin_workout_id` zeigt auf einen extern gelöschten/verschobenen Eintrag — mit Alexander klären, nie automatisch überschreiben.
