"""
CAIRN Knowledge Loader
Reads markdown files from knowledge/docs/ and returns them as strings.
All coaching philosophy lives here — never in code.
"""

import os

KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), 'docs')

MORNING_BRIEF_DOCS = [
    'identity.md',
    'communication.md',
    'athlete_profile.md',
    'decision_engine.md',
    'recovery_framework.md',
    'coach_memory_engine.md',
    'output_formats.md',
]

def load_doc(filename: str) -> str:
    path = os.path.join(KNOWLEDGE_DIR, filename)
    if not os.path.exists(path):
        return f"[{filename} not found]"
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def load_morning_brief_knowledge() -> str:
    sections = []
    for doc in MORNING_BRIEF_DOCS:
        content = load_doc(doc)
        sections.append(f"## {doc}\n\n{content}")
    return "\n\n---\n\n".join(sections)