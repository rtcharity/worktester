# worktester

Tiny Flask + SQLite app to record when candidates start a 4-hour timed work trial.
Each candidate gets a tokenized link (e.g. `/trial/CrispyPanda`) that opens their
Google Doc and timestamps the start.

## Quick start

```
uv sync
mkdir -p data
uv run seed.py "Jane Doe" "https://docs.google.com/document/d/abc/edit"
ADMIN_PASSWORD=secret uv run gunicorn --bind 0.0.0.0:5000 main:app
```

Or with Docker: put `ADMIN_PASSWORD=…` in `.env` and `docker compose up --build`.

Admin view: `/admin` (HTTP Basic Auth, any username, password from `ADMIN_PASSWORD`).
