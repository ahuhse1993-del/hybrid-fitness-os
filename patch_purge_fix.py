#!/usr/bin/env python3
"""
Fix: purge_all_sessions — updated_at aus plans-UPDATE entfernen (Spalte existiert nicht).
Run from repo root: python patch_purge_fix.py
"""

PATH = "coach/mcp_server.py"
with open(PATH, "r") as f:
    src = f.read()

OLD = '''            cur.execute("UPDATE plans SET status='archived', updated_at=now() WHERE status='active'")'''
NEW = '''            cur.execute("UPDATE plans SET status='archived' WHERE status='active'")'''

assert OLD in src, "Patch-Ziel nicht gefunden"
src = src.replace(OLD, NEW, 1)

with open(PATH, "w") as f:
    f.write(src)

print("✓ purge_all_sessions gefixt (updated_at entfernt)")
