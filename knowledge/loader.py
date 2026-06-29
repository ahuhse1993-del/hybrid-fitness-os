"""
CAIRN Knowledge Loader
Reads markdown files from knowledge/docs/ and returns them as strings.

Load only what each task needs.
Never load all docs for every request.
"""

import os

KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), 'docs')

# ── Task-specific doc sets ──────────────────────────────────────────────────

DOCS = {
    'morning_brief': [
        'identity.md',
        'communication.md',
        'athlete_profile.md',
        'decision_engine.md',
        'recovery_framework.md',
        'coach_memory_engine.md',
        'output_formats.md',
    ],
    'workout_analysis': [
        'identity.md',
        'communication.md',
        'athlete_profile.md',
        'training_engine.md',
        'coach_memory_engine.md',
        'output_formats.md',
    ],
    'plan_generation': [
        'identity.md',
        'communication.md',
        'athlete_profile.md',
        'planning_engine.md',
        'training_engine.md',
        'strength_framework.md',
        'nutrition_framework.md',
        'output_formats.md',
    ],
    'plan_adaptation': [
        'identity.md',
        'communication.md',
        'athlete_profile.md',
        'decision_engine.md',
        'adaptation_engine.md',
        'recovery_framework.md',
        'coach_memory_engine.md',
        'output_formats.md',
    ],
    'race_brief': [
        'identity.md',
        'communication.md',
        'athlete_profile.md',
        'race_framework.md',
        'nutrition_framework.md',
        'recovery_framework.md',
        'output_formats.md',
    ],
    'strength_session': [
        'identity.md',
        'communication.md',
        'athlete_profile.md',
        'strength_framework.md',
        'training_engine.md',
        'output_formats.md',
    ],
    'nutrition_advice': [
        'identity.md',
        'communication.md',
        'athlete_profile.md',
        'nutrition_framework.md',
        'training_engine.md',
        'coach_memory_engine.md',
        'output_formats.md',
    ],
}


def load_doc(filename: str) -> str:
    path = os.path.join(KNOWLEDGE_DIR, filename)
    if not os.path.exists(path):
        return f"[{filename} not found]"
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def load_knowledge(task: str) -> str:
    """
    Load only the docs needed for a specific coaching task.
    task: one of the keys in DOCS above.
    """
    doc_list = DOCS.get(task, [])
    sections = []
    for doc in doc_list:
        content = load_doc(doc)
        sections.append(f"## {doc}\n\n{content}")
    return "\n\n---\n\n".join(sections)


# ── Convenience functions ───────────────────────────────────────────────────

def load_morning_brief_knowledge() -> str:
    return load_knowledge('morning_brief')

def load_workout_analysis_knowledge() -> str:
    return load_knowledge('workout_analysis')

def load_plan_generation_knowledge() -> str:
    return load_knowledge('plan_generation')

def load_plan_adaptation_knowledge() -> str:
    return load_knowledge('plan_adaptation')

def load_race_brief_knowledge() -> str:
    return load_knowledge('race_brief')

def load_strength_session_knowledge() -> str:
    return load_knowledge('strength_session')

def load_nutrition_advice_knowledge() -> str:
    return load_knowledge('nutrition_advice')