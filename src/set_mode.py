"""Flip the sentinel between observe and live without writing SQL.

Usage (with DATABASE_URL set, e.g. via .env):
    python -m src.set_mode live
    python -m src.set_mode observe
"""
import sys
from . import db

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("observe", "live"):
        print("usage: python -m src.set_mode [observe|live]")
        sys.exit(2)
    db.init_schema()
    db.set_mode(sys.argv[1])
    print(f"mode set to {sys.argv[1]}")
