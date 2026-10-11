# CAIRN – Coach Context for Claude Code

## Projekt
CAIRN ist eine emotionale Coach-App für Hybrid-Athleten (Trail Running + Krafttraining).
Kein Dashboard. Ein Coach. Ruhig, menschlich, auf Augenhöhe.

## Stack
- Backend: Python/Flask → `coach/api.py`
- Frontend: HTML/CSS/JS (single-file) → `files/`
- DB: PostgreSQL auf Railway
- Hosting: Railway → https://web-production-297f2.up.railway.app
- Repo: https://github.com/ahuhse1993-del/hybrid-fitness-os

## Wichtige Files
- `coach/api.py` – Flask API, alle Endpoints
- `files/cairn_app_v6.html` – Haupt-UI (Route `/app`): Woche, Plan, Sync, Aktivitäten, Profil. Aktiv gepflegt.
- `files/cairn_home_mobile.html` – ältere Mobile-UI (Route `/mobile`), seit Aug. nicht mehr weiterentwickelt
- `files/cairn_plan_onboarding.html` – Plan Setup
- `coach/morning_brief.py` – Morning Brief Generator
- `knowledge/docs/` – Coach Knowledge Base (14 Markdown Docs)
- `docs/CAIRN_ChatGPT_Handbook.md` – Knowledge-File für den CAIRN-GPT (MCP-Tools/Workflows, nicht Frontend)

## Design System
*(gilt für `cairn_app_v6.html`, die aktive Haupt-UI; `cairn_home_mobile.html` hat ein eigenes, abweichendes System mit DM Mono/DM Sans + `--accent:#E8D84A`)*
- Farben (CSS-Variablen, `light-dark()` für Hell/Dunkel, pro View neu deklariert): `--paper` (Hintergrund) `--ink` (Text) `--forest`/`--forest-ink` (dunkelgrün, Akzent/aktive Elemente) `--coral`/`--coral-ink` (Akzent/Call-to-Action) `--soft` (Karten-Hintergrund) `--sand` (zweiter Akzent) `--line` (Trennlinien) `--muted-foreground` (gedimmter Text)
- Fonts: System-Font-Stack (`-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif`). Ein `@font-face` für eine Logo-Schrift ("HV SMEGS") ist eingebettet, wird aber nirgends referenziert — das CAIRN-Wortmark ist ein inline-SVG (`BRAND_SVG`), kein Text.
- Flache Karten (`--card`-Variable + Farbe), meist ohne Schatten; einzige Ausnahme sind die Session-Kacheln im Week-Planner (`.sess-tile`, leichter Schatten zur Abgrenzung von `--paper`). Border-Radius durchgehend groß (meist `.5rem`–`1.5rem`, Pills `999px`), keine Gradients.

## Coach Voice
- Ich / Du – nie "der Coach"
- Kurze Sätze, ruhig, direkt
- Nie: "Readiness Score", "approved", "Algorithmus"

## Deploy
- git add + commit + push → Railway deployed automatisch
- DB leeren: `python3 -c "...UPDATE plans SET status='archived'..."`

## Aktuelle Prioritäten
- 14 Session-Typen einbauen (Backend + Frontend)
- Plan via Coach-Chat anpassen

## Erledigt (Week-Planner)
- Redesign: Single-Week-Pager, Tages-Zeilen mit Session-Kacheln, "Ziele Woche"-Box
- Matching: geplante Sessions ↔ absolvierte Aktivitäten (`matched_training_id`, ±1 Tag, Sportgruppe)
- Drag & Drop mit Entwurf-Modus (Verschieben erst lokal, "Bestätigen" schreibt an den Server)
- Kompaktes Layout (Mo–So ohne Scrollen auf iPhone SE), iOS-Safari-taugliches Touch-Drag
