# CAIRN – ChatGPT Coaching Handbook

**Version 3.0 — vollständige Referenz für Plan-Erstellung, -Verwaltung & Garmin-Push**

---

## Was ist CAIRN?

CAIRN ist Alexander Huhses persönliches Coaching-System. Es verbindet ChatGPT (Planung & Analyse) mit einer PostgreSQL-Datenbank (Trainingsplan) und Garmin Connect (Workouts auf der Uhr). Alles läuft über MCP-Tools, die du direkt aufrufen kannst.

**Grundregel:** Du planst, CAIRN speichert, Garmin zeigt es auf der Uhr.

---

## Athletenprofil

Vor jedem Plan-Import abrufen:
```
get_athlete_profile()
```
Gibt: alle Pläne inkl. IDs, aktiver Plan, aktuelle Wochenbelastung, Zielrennen, Garmin-Verbindungsstatus.

---

## 1. Neuen Plan importieren — Schritt für Schritt

### Schritt 0: Alten Plan archivieren (wenn ein bestehender Plan aktiv ist)
```
archive_plan_sessions(plan_id=<alte-plan-id>)
```
Setzt alle Sessions des alten Plans auf `status='archived'`. **Garmin-Workouts werden nicht gelöscht** — nur CAIRN-Status wird geändert. Plan-ID via `get_athlete_profile()` ermitteln.

### Schritt 1: Preview (Pflicht)
```
preview_training_block(plan)
```
Validiert alle Sessions ohne DB-Schreibzugriff. **Niemals überspringen.**

### Schritt 2: Import
```
upsert_training_block(plan, confirm_race_date_change=False)
```
Idempotent — doppelter Aufruf mit gleicher `external_id` überschreibt, keine Duplikate.

### Schritt 3: Phasen korrigieren (nach Import prüfen)
```
fix_session_phases_from_weeks()
```
Überträgt die Phase aus `plan_weeks` auf jede Session. Aufrufen wenn Sessions nach dem Import alle `phase="base"` haben.

### Schritt 4: Meilensteine
```
upsert_milestones(milestones=[...])
```
Separater Aufruf nach dem Block-Import.

### Schritt 5: Garmin-Push
```
reconcile_training_block()                              → zeigt was fehlt
push_sessions_to_garmin(session_ids=[1,2,3,4,5,6,7])   → max. 7 pro Call
```

---

## 2. Vollständige Planstruktur

```json
{
  "race": {
    "race_date":        "YYYY-MM-DD",
    "name":             "Swiss Canyon K31 2026",
    "goal_type":        "trail_race",
    "race_distance_km": 33.29,
    "race_elevation_m": 1800,
    "race_priority":    "A",
    "target_time":      "4:00:00"
  },

  "weeks": [
    {
      "week_number": 1,
      "week_focus":  "Grundlagen & Laufrhythmus aufbauen",
      "phase":       "base"
    },
    {
      "week_number": 2,
      "week_focus":  "Volumen moderat steigern",
      "phase":       "base"
    },
    {
      "week_number": 3,
      "week_focus":  "Erste Hügel & Bergspezifik",
      "phase":       "base"
    },
    {
      "week_number": 4,
      "week_focus":  "Erholung & Mobilität",
      "phase":       "base",
      "is_deload":   true
    }
  ],

  "sessions": [
    {
      "external_id":      "sc26-w01-mon",
      "date":             "YYYY-MM-DD",
      "session_type":     "Easy Run",
      "session_zone":     "easy",
      "phase":            "base",
      "name":             "Lockerer Einstieg Zone 2",
      "distance_km":      8,
      "duration_min":     55,
      "elevation_gain_m": 80,
      "notes":            "HR Zone 2, flaches Gelände",
      "sync_target":      "garmin"
    },
    {
      "external_id":      "sc26-w01-tue",
      "date":             "YYYY-MM-DD",
      "session_type":     "Interval Run",
      "session_zone":     "interval",
      "phase":            "base",
      "name":             "VO2max-Intervalle 4×4min",
      "distance_km":      10,
      "duration_min":     62,
      "notes":            "Warm-up 2km, 4×4min @ VO2max, 2min Pause, Cooldown",
      "workout_steps": [
        {"type": "warmup", "duration_secs": 720, "description": "Locker einlaufen"},
        {
          "type": "repeat",
          "iterations": 4,
          "steps": [
            {"type": "interval", "duration_secs": 240, "description": "4×4min @ VO2max (RPE 8–9)"},
            {"type": "recovery", "duration_secs": 120, "description": "2min locker traben"}
          ]
        },
        {"type": "cooldown", "duration_secs": 600, "description": "Auslaufen, locker"}
      ],
      "sync_target":      "garmin"
    },
    {
      "external_id":      "sc26-w01-wed",
      "date":             "YYYY-MM-DD",
      "session_type":     "Strength Training",
      "phase":            "base",
      "name":             "Kraft & Rumpfstabilität",
      "duration_min":     45,
      "sport":            "strength",
      "km_factor":        0,
      "sync_target":      "hevy"
    },
    {
      "external_id":      "sc26-w01-sat",
      "date":             "YYYY-MM-DD",
      "session_type":     "Long Trail Run",
      "session_zone":     "long",
      "phase":            "base",
      "name":             "Langer Trailrun mit Höhenmetern",
      "distance_km":      20,
      "duration_min":     140,
      "elevation_gain_m": 800,
      "notes":            "Gleichmässiges Tempo, Gel alle 40min testen",
      "sync_target":      "garmin"
    }
  ]
}
```

---

## 3. Felder-Referenz

### `race` — Renndaten

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `race_date` | `YYYY-MM-DD` | ✅ | Renndatum |
| `name` | string | ✅ | Rennen-Name (erscheint in der App) |
| `goal_type` | string | ✅ | `"trail_race"` / `"road_race"` / `"marathon"` |
| `race_distance_km` | float | ✅ | Distanz in km |
| `race_elevation_m` | int | ✅ **Pflicht** | Gesamthöhenmeter — erscheint als HÖHE in der App |
| `race_priority` | `"A"` / `"B"` / `"C"` | ✅ | A = Hauptrennen, B = Vorbereitungsrennen |
| `target_time` | string | empfohlen | Zielzeit, z.B. `"4:00:00"` |

### `weeks` — Wochenstruktur (**alle Wochen angeben, nicht nur Sonderwochen**)

**Pflicht: Jede Woche muss im `weeks`-Array stehen, mit eigenem `week_focus`.**
Der FOKUS-Text erscheint in der App — leere oder fehlende Einträge zeigen nur die Phase.

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `week_number` | int | ✅ | Wochenummer im Plan (1 bis N) |
| `week_focus` | string | ✅ | Fokus-Text für die App, z.B. `"Bergkraft aufbauen"` — spezifisch, nicht generisch |
| `phase` | string | ✅ | `"base"` / `"build"` / `"peak"` / `"taper"` / `"race"` |
| `is_deload` | bool | bei Deload | Explizit als Erholungswoche markieren |

### `sessions` — Einzelne Trainingseinheiten

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `external_id` | string | ✅ | Eindeutige ID pro Session, z.B. `"sc26-w01-mon"` |
| `date` | `YYYY-MM-DD` | ✅ | Trainingstag |
| `session_type` | string | ✅ | Typ (Tabelle unten) |
| `name` | string | ✅ | **Kurztitel der Einheit — muss unterscheidbar sein!** Nicht einfach "Intervals" für jede Qualitätseinheit. Beispiel: `"4×4min VO2max"`, `"Bergläufe 8×45s"`, `"Schwellenlauf 3×8min"` |
| `phase` | string | ✅ | Phase dieser Session — muss zur Woche passen |
| `session_zone` | string | ✅ | Intensität (Tabelle unten) |
| `distance_km` | float | bei Läufen/Rad | Geplante Distanz |
| `duration_min` | int | empfohlen | Geplante Dauer in Minuten |
| `elevation_gain_m` | int | empfohlen | Geplante Höhenmeter |
| `notes` | string | empfohlen | Coach-Hinweis, erscheint in App & Garmin |
| `workout_steps` | array | bei Qualitätseinheiten | Strukturierte Schritte (Warm-up, Intervalle, Cooldown) — Tabelle unten |
| `sport` | string | bei Rad | `"cycling"` — wichtig für Garmin-Kategorisierung |
| `km_factor` | float | bei Rad | `0.25` — Rennrad zählt mit 25% zum Laufvolumen |
| `sync_target` | string | auto | `"garmin"` / `"hevy"` / `"none"` — wird meist automatisch gesetzt |

### Session-Typen & Routing

| Typ | Auto-Sync | Hinweis |
|---|---|---|
| `"Easy Run"` | garmin | |
| `"Recovery Run"` | garmin | |
| `"Long Run"` | garmin | |
| `"Trail Run"` | garmin | |
| `"Interval Run"` | garmin | Mit `workout_steps` strukturieren |
| `"Interval Training"` | garmin | Mit `workout_steps` strukturieren |
| `"Threshold Run"` | garmin | Mit `workout_steps` strukturieren |
| `"Trail Threshold"` | garmin | Mit `workout_steps` strukturieren |
| `"Uphill Threshold"` | garmin | Mit `workout_steps` strukturieren |
| `"Hill Session"` | garmin | Mit `workout_steps` strukturieren |
| `"Hill Run"` | garmin | Mit `workout_steps` strukturieren |
| `"Hill Technique"` | garmin | Mit `workout_steps` strukturieren |
| `"Hill Sprints"` | garmin | Mit `workout_steps` strukturieren |
| `"Tempo Session"` | garmin | Mit `workout_steps` strukturieren |
| `"Tempo Run"` | garmin | Mit `workout_steps` strukturieren |
| `"Sprint Session"` | garmin | |
| `"Activation Run"` | garmin | |
| `"Race Activation"` | garmin | |
| `"Fartlek"` | garmin | |
| `"Strides"` | garmin | |
| `"Time Trial"` | garmin | |
| `"Long Trail Run"` | garmin | **Für lange Trailruns verwenden — nicht `"Trail Run"`** |
| `"Hill Sprints"` | garmin | Mit `workout_steps` strukturieren |
| `"Tempo Run"` | garmin | Mit `workout_steps` strukturieren |
| `"Time Trial"` | garmin | |
| `"Cycling"` | garmin | `sport: "cycling"`, `km_factor: 0.25` setzen |
| `"Rennrad"` / `"Rennrad Endurance"` | garmin | `sport: "cycling"`, `km_factor: 0.25` — Icon: Cross Training |
| `"Race Day"` / `"Race"` | garmin | |
| `"Strength Training"` | hevy | **Nie zu Garmin** |
| `"Core"` | hevy | **Nie zu Garmin** |
| `"Mobility"` | hevy | **Nie zu Garmin** |
| `"Cross Training"` | auto | Hängt von Garmin-Ziel-Frage ab |
| `"Rest Day"` | none | |

### Session-Zonen (`session_zone`)

`"easy"` / `"long"` / `"threshold"` / `"interval"` / `"recovery"` / `"trail"` / `"hill"` / `"sprint"`

### `workout_steps` — Strukturierte Einheiten

Für alle Qualitäts- und Intervall-Sessions verwenden. Die Struktur wird in der App als Schritt-Liste angezeigt **und direkt an Garmin gepusht** — das Format muss exakt stimmen.

**⚠️ Pflichtformat — Garmin-kompatibel:**

```json
"workout_steps": [
  {
    "type": "warmup",
    "duration_secs": 900,
    "description": "Locker einlaufen, letzte 3 Minuten progressiv"
  },
  {
    "type": "repeat",
    "iterations": 5,
    "steps": [
      {
        "type": "interval",
        "duration_secs": 180,
        "description": "3min @ Schwelle (RPE 7–8)"
      },
      {
        "type": "recovery",
        "duration_secs": 90,
        "description": "90s locker traben"
      }
    ]
  },
  {
    "type": "cooldown",
    "duration_secs": 600,
    "description": "Locker auslaufen"
  }
]
```

| Feld | Typ | Beschreibung |
|---|---|---|
| `type` | string | `"warmup"` / `"interval"` / `"recovery"` / `"cooldown"` / `"repeat"` |
| `duration_secs` | int | **Dauer in Sekunden** (nicht Minuten!) — z.B. 4min = 240, 15min = 900 |
| `iterations` | int | Anzahl Wiederholungen — nur bei `type: "repeat"` |
| `steps` | array | Inner Steps bei `type: "repeat"` — enthält `interval` + `recovery` |
| `description` | string | Text-Beschreibung (erscheint in App und auf Garmin-Uhr) |
| `enforce_garmin_target` | bool | `false` = kein Garmin-Target (Standard, No-Target-Policy) |

**Wichtige Regeln:**
- **`duration_secs` nicht `duration_min`** — Garmin verlangt Sekunden
- **`"interval"` nicht `"active"`** — "active" ist kein gültiger Garmin-Typ
- **Repeat-Block immer als `{"type":"repeat","iterations":N,"steps":[...]}`** — nicht als flache `repeat`/`rest_min`-Felder
- Warmup und Cooldown immer ohne Ziel (No-Target-Policy)
- Recovery-Steps niemals mit Ziel

---

## 4. Meilensteine

```json
[
  {
    "step_number":  1,
    "title":        "Trainingsrhythmus etabliert",
    "criterion":    "3 Wochen durchgehend 4 Einheiten absolviert",
    "week_number":  3,
    "target_date":  "YYYY-MM-DD",
    "status":       "open"
  },
  {
    "step_number":  2,
    "title":        "Verpflegungstest bestanden",
    "criterion":    "Gels & Hydration über 2h Lauf getestet",
    "week_number":  7,
    "target_date":  "YYYY-MM-DD",
    "status":       "open"
  }
]
```

| Feld | Typ | Beschreibung |
|---|---|---|
| `step_number` | int | Reihenfolge (1, 2, 3...) |
| `title` | string | Kurzname, erscheint in der App |
| `criterion` | string | Was genau muss erreicht sein |
| `week_number` | int | Ziel-Woche im Plan (erscheint neben Milestone in App) |
| `target_date` | `YYYY-MM-DD` | optional |
| `status` | string | `"open"` / `"achieved"` |

---

## 5. Plan anpassen

### Einzelne Session ändern
```
update_planned_workout(
  external_id="sc26-w03-sat",
  patch={
    "date":         "2026-10-10",
    "distance_km":  28,
    "phase":        "build",
    "notes":        "Verschoben wegen Wetter"
  },
  reason="Long Run um 1 Tag verschoben"
)
```

Erlaubte patch-Felder: `date`, `session_type`, `session_zone`, `phase`, `name`, `distance_km`, `duration_min`, `notes`, `elevation_gain_m`, `km_factor`, `status`, `sync_target`

→ Wenn Session bereits auf Garmin ist und Inhalt ändert: `sync_status` → `"dirty"` (muss neu gepusht werden).

### Phasen nachträglich korrigieren (ganzer Plan)
```
fix_session_phases_from_weeks()
```
Liest phase aus `plan_weeks` und setzt sie auf alle zugehörigen Sessions.

### Alten Plan archivieren
```
archive_plan_sessions(plan_id=<plan-id>)
```
Setzt alle Sessions des Plans auf `archived`. Garmin-Workouts bleiben unberührt.

### Session auf Garmin verschieben
```
move_garmin_workout(garmin_workout_id=12345, new_date="2026-10-10")
```

### Session von Garmin löschen
```
delete_garmin_workout(garmin_workout_id=12345)
```

### Mehrere Garmin-Sessions löschen
```
bulk_delete_garmin_workouts(garmin_workout_ids=[12345, 12346])
```
**Immer vorher `list_garmin_workouts` aufrufen** um IDs zu prüfen.

---

## 6. Garmin-Push-Workflow (ganzer Block)

```
1. reconcile_training_block()
   → zeigt: fehlt auf Garmin / sync_status=dirty / failed

2. push_sessions_to_garmin(session_ids=[1,2,3,4,5,6,7])
   → max. 7 IDs pro Call, in Blöcken wiederholen

3. Wiederholen bis reconcile sauber ist
```

---

## 7. Datenbank bereinigen (Neustart)

Garmin und DB vollständig leeren:
```
1. list_garmin_workouts()
   → alle Einträge mit garmin_workout_id abrufen

2. bulk_delete_garmin_workouts(garmin_workout_ids=[...])
   → alle IDs aus Schritt 1 auf einmal löschen

3. purge_all_sessions(confirm=True)
   → alle Sessions und plan_weeks aus CAIRN-DB löschen, Pläne archivieren
```
**Achtung:** `purge_all_sessions(confirm=True)` ist irreversibel. Immer erst Garmin leeren (Schritte 1–2).

---

## 8. Verbindliche Regeln

| Regel | Detail |
|---|---|
| **`race_elevation_m` immer setzen** | Pflichtfeld — erscheint als HÖHE in der App |
| **`week_focus` für JEDE Woche** | Jede Woche im `weeks`-Array braucht einen spezifischen `week_focus`-Text |
| **`name` muss unterscheidbar sein** | Nicht "Intervals" für alle Qualitätseinheiten — spezifischen Titel verwenden: `"4×4min VO2max"`, `"Schwellenläufe 3×8min"` |
| **`workout_steps` bei Qualitätseinheiten** | Alle Interval-, Threshold-, Hill-Sessions mit `workout_steps` strukturieren |
| **`duration_secs` nicht `duration_min`** | In workout_steps immer Sekunden — 4min = 240, 15min = 900 |
| **`"interval"` nicht `"active"`** | "active" ist kein gültiger Garmin-Step-Typ |
| **Repeat als verschachtelter Block** | `{"type":"repeat","iterations":N,"steps":[...]}` — nicht `repeat: N, rest_min: X` |
| **Long Trail Run ≠ Trail Run** | Lange Trailläufe immer `"Long Trail Run"` als session_type — `"Trail Run"` nur für kurze/mittlere Trails |
| **Strength / Core / Mobility nie zu Garmin** | sync_target `"hevy"` oder `"none"`, niemals `"garmin"` |
| **Cycling / Rennrad immer mit sport + km_factor** | `sport: "cycling"`, `km_factor: 0.25` — auch für "Rennrad", "Rennrad Endurance" |
| **phase bei JEDER Session setzen** | Nicht weglassen — sonst landet alles in "base" |
| **Max. 7 Sessions pro push-Call** | Sonst 504 Timeout |
| **Immer preview vor upsert** | Niemals überspringen |
| **Alten Plan erst archivieren** | Vor Neuimport `archive_plan_sessions` aufrufen |
| **Race-Date-Änderung bestätigen** | `confirm_race_date_change=True` explizit setzen |
| **Bulk-Delete erst nach list-Check** | IDs immer zuerst via `list_garmin_workouts` abrufen |
| **Keine Credentials ausgeben** | Garmin-Login bleibt Railway-intern |

---

## 9. Analyse-Tools

```
get_athlete_profile()                → Alle Pläne + IDs, aktiver Plan, Profil
get_recent_activities(days=14)       → letzte Garmin-Aktivitäten
get_training_summary(weeks=8)        → Wochenvolumen
get_health_data(days=7)              → Sleep, HRV, Body Battery
get_activity_analysis_data(id=...)   → Lauf-Splits, Elevation, HR
get_planned_workouts(days=14)        → nächste geplante Sessions
list_garmin_workouts()               → Sync-Status aller Sessions
reconcile_training_block()           → Audit: was fehlt / was ist dirty
```

---

## 10. Häufige Fehler & Lösungen

| Fehler | Ursache | Lösung |
|---|---|---|
| Sessions haben alle `phase="base"` | phase-Feld nicht in Sessions übergeben | `fix_session_phases_from_weeks()` aufrufen |
| FOKUS zeigt nur Phase, nicht Fokus-Text | `week_focus` fehlt im `weeks`-Array | Plan mit korrekten `week_focus`-Werten neu importieren |
| HÖHE zeigt `—` | `race_elevation_m` fehlt in `race` | `race_elevation_m` setzen und Plan neu importieren |
| Session-Namen zeigen alle "Intervals" | `name`-Feld nicht gesetzt oder generisch | Spezifische Namen verwenden |
| 504 Timeout beim Push | Zu viele Sessions pro Call | Max. 7 IDs pro `push_sessions_to_garmin`-Call |
| `sync_status=dirty` nach Änderung | Session wurde geändert, Garmin nicht aktualisiert | Workout auf Garmin löschen und neu pushen |
| AssertionError im Patch-Script | Falsches Patch-Target (falsche Dateiversion) | Datei auf Mac prüfen und neu patchen |
