"""
coach/hevy_client.py
Dünner Client für die Hevy API (https://api.hevyapp.com/v1). Reine HTTP-
Schicht — keine DB-Zugriffe, kein Caching hier (das macht coach/mcp_server.py).

Verifiziert per echtem, lesendem Live-Call gegen die reale API (2026-08-27):
- /v1/routines und /v1/exercise_templates paginieren über {page, page_count},
  NICHT über "leere Liste = Ende" wie /v1/workouts (siehe data/hevy_import.py).
- routine.folder_id kommt als INTEGER zurück, nicht als String.
- exercise_template_id ist KEIN UUID -- kurze alphanumerische Codes
  (z.B. "EAC7D9C5"), sowohl bei Übungen in Routinen als auch bei Templates.
- GET /v1/routines/{id} liefert {"routine": {...}} (gewrappt).
- GET /v1/exercise_templates/{id} liefert das Objekt direkt (nicht gewrappt).
- Der "search"-Query-Param auf /v1/exercise_templates wird vom Server
  ignoriert (getestet: identische Ergebnisse mit/ohne) -- Filterung muss
  client-/DB-seitig passieren, nicht über die Hevy-API.
"""
import os
import time
from typing import Optional

import requests

HEVY_BASE = "https://api.hevyapp.com/v1"
HEVY_CAIRN_FOLDER_ID = os.environ.get("HEVY_CAIRN_FOLDER_ID", "3380361")
_PAGE_DELAY_S = 0.3


def _headers() -> dict:
    key = os.environ.get("HEVY_API_KEY", "")
    if not key:
        raise ValueError("HEVY_API_KEY not set")
    return {"api-key": key, "Content-Type": "application/json"}


def _get(path: str, params: Optional[dict] = None) -> dict:
    resp = requests.get(f"{HEVY_BASE}{path}", headers=_headers(), params=params, timeout=20)
    if not resp.ok:
        raise requests.HTTPError(f"Hevy GET {path} -> {resp.status_code}: {resp.text[:300]}", response=resp)
    return resp.json()


def get_all_routines(folder_id: Optional[str] = None) -> list[dict]:
    """
    Paginated fetch aller Routinen (page/page_count-basiert, siehe Modul-
    Docstring), optional nach folder_id gefiltert. folder_id wird als String
    ODER int akzeptiert und robust verglichen, da Hevy es als int liefert.
    """
    target_folder = str(folder_id) if folder_id is not None else None
    all_routines: list[dict] = []
    page = 1
    while True:
        data = _get("/routines", {"page": page, "pageSize": 10})
        routines = data.get("routines", [])
        all_routines.extend(routines)
        page_count = data.get("page_count", page)
        if page >= page_count:
            break
        page += 1
        time.sleep(_PAGE_DELAY_S)

    if target_folder is not None:
        all_routines = [r for r in all_routines if str(r.get("folder_id")) == target_folder]
    return all_routines


def get_routine(hevy_routine_id: str) -> dict:
    """Single routine by ID. Hevy wrapt die Antwort in {"routine": {...}}."""
    data = _get(f"/routines/{hevy_routine_id}")
    return data.get("routine", data)


def create_routine(payload: dict) -> dict:
    """
    POST /v1/routines — NUR aufrufen wenn explizit freigegeben (Write-Op).
    UNGETESTET gegen die echte API (bislang nur lesende Calls gemacht,
    siehe Auftrag) — vor produktivem Einsatz mit einer Testroutine prüfen.
    """
    resp = requests.post(f"{HEVY_BASE}/routines", headers=_headers(), json=payload, timeout=20)
    if not resp.ok:
        raise requests.HTTPError(f"Hevy POST /routines -> {resp.status_code}: {resp.text[:300]}", response=resp)
    return resp.json()


def update_routine(hevy_routine_id: str, payload: dict) -> dict:
    """
    PUT /v1/routines/{id} — NUR aufrufen wenn explizit freigegeben (Write-Op).
    UNGETESTET gegen die echte API — vor produktivem Einsatz prüfen.
    """
    resp = requests.put(f"{HEVY_BASE}/routines/{hevy_routine_id}", headers=_headers(), json=payload, timeout=20)
    if not resp.ok:
        raise requests.HTTPError(
            f"Hevy PUT /routines/{hevy_routine_id} -> {resp.status_code}: {resp.text[:300]}", response=resp
        )
    return resp.json()


def get_exercise_templates(page: int = 1, page_size: int = 100) -> dict:
    """
    GET /v1/exercise_templates, eine Seite. Kein search-Param -- die Hevy-API
    ignoriert ihn nachweislich (siehe Modul-Docstring); Suche/Filter muss
    aufrufseitig (DB-Cache) passieren.
    """
    return _get("/exercise_templates", {"page": page, "pageSize": page_size})


def get_all_exercise_templates() -> list[dict]:
    """Paginated fetch ALLER Exercise Templates (aktuell ~457 Stück, ~5 Seiten à 100)."""
    all_templates: list[dict] = []
    page = 1
    while True:
        data = get_exercise_templates(page=page, page_size=100)
        items = data.get("exercise_templates", [])
        all_templates.extend(items)
        page_count = data.get("page_count", page)
        if page >= page_count:
            break
        page += 1
        time.sleep(_PAGE_DELAY_S)
    return all_templates


def get_exercise_template(exercise_template_id: str) -> dict:
    """GET /v1/exercise_templates/{id} — Antwort ist NICHT gewrappt."""
    return _get(f"/exercise_templates/{exercise_template_id}")
