"""Seed a candidate row and print their share URL.

Usage:
    uv run seed.py "Jane Doe" "https://docs.google.com/document/d/abc/edit"
"""
import os
import sqlite3
import secrets
import sys
from pathlib import Path

import words

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "trial.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    name, doc_url = argv[1], argv[2]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text())

    while True:
        token = secrets.choice(words.ADJECTIVES) + secrets.choice(words.NOUNS)
        try:
            conn.execute(
                "INSERT INTO candidates (token, name, doc_url) VALUES (?, ?, ?)",
                (token, name, doc_url),
            )
            conn.commit()
            break
        except sqlite3.IntegrityError:
            continue

    print(f'Created candidate "{name}": {BASE_URL}/trial/{token}')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
