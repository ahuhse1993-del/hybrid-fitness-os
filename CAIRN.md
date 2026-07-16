# CAIRN – Coach Context for Claude Code

## Projekt
CAIRN ist eine emotionale Coach-App für Hybrid-Athleten (Trail Running + Krafttraining).
Kein Dashboard. Ein Coach. Ruhig, menschlich, auf Augenhöhe.

## Stack
- Backend: Python/Flask → `coach/api.py`
- Frontend: HTML/CSS/JS (single-file) → `files/`
- DB: PostgreSQL auf Railway
- Hosting: Railway → https://web-production-297f2.up.railway.app
- Repo: https://github.com/ahuhse1993-del/CAIRN

## Wichtige Files
- `coach/api.py` – Flask API, alle Endpoints
- `files/cairn_home_mobile.html` – Mobile App (Haupt-UI)
- `files/cairn_plan_onboarding.html` – Plan Setup
- `coach/morning_brief.py` – Morning Brief Generator
- `knowledge/docs/` – Coach Knowledge Base (14 Markdown Docs)

## Design System
- Farben: `--bg:#F0EDE6` `--stone:#9E9B95` `--mid:#2E2C2A` `--black:#1A1918` `--accent:#E8D84A`
- Fonts: DM Mono (Labels) + DM Sans (Body)
- Kein Shadow, kein Gradient, max 2px Border

## Coach Voice
- Ich / Du – nie "der Coach"
- Kurze Sätze, ruhig, direkt
- Nie: "Readiness Score", "approved", "Algorithmus"

## Deploy
- git add + commit + push → Railway deployed automatisch
- DB leeren: `python3 -c "...UPDATE plans SET status='archived'..."`

## Aktuelle Prioritäten
- 14 Session-Typen einbauen (Backend + Frontend)
- Drag & Drop mit Save-Button
- Plan via Coach-Chat anpassen
