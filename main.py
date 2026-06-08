import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

import words

TRIAL_DURATION = timedelta(hours=4)
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "trial.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD env var must be set")

SUBMISSIONS_DIR = DATA_DIR / "submissions"
MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", ADMIN_PASSWORD)
app.config["MAX_CONTENT_LENGTH"] = MAX_PDF_BYTES


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA_PATH.read_text())


init_db()


def generate_token(conn: sqlite3.Connection) -> str:
    while True:
        token = secrets.choice(words.ADJECTIVES) + secrets.choice(words.NOUNS)
        row = conn.execute(
            "SELECT 1 FROM candidates WHERE token = ?", (token,)
        ).fetchone()
        if row is None:
            return token


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


@app.template_filter("localtime")
def localtime_filter(value):
    if value is None:
        return ""
    dt = value if isinstance(value, datetime) else parse_iso(value)
    return dt.astimezone().strftime("%a %b %-d, %Y at %-I:%M %p %Z")


@app.template_filter("utciso")
def utciso_filter(value):
    if value is None:
        return ""
    dt = value if isinstance(value, datetime) else parse_iso(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


@app.get("/healthz")
def healthz():
    return "ok", 200


@app.get("/trial/<token>")
def trial_page(token: str):
    row = get_db().execute(
        "SELECT token, name, doc_url, started_at FROM candidates WHERE token = ?",
        (token,),
    ).fetchone()
    if row is None:
        abort(404)

    if row["started_at"]:
        started = parse_iso(row["started_at"])
        deadline = started + TRIAL_DURATION
        expired = datetime.now(timezone.utc) > deadline
        submission_dir = DATA_DIR / "submissions" / token
        submitted_filename = None
        if (submission_dir / "submission.pdf").exists():
            name_file = submission_dir / "filename.txt"
            submitted_filename = name_file.read_text() if name_file.exists() else "submission.pdf"
        return render_template(
            "started.html",
            candidate=row,
            started_at=started,
            deadline=deadline,
            expired=expired,
            just_started=request.args.get("just_started") == "1",
            submitted_filename=submitted_filename,
        )

    projected_deadline = datetime.now(timezone.utc) + TRIAL_DURATION
    return render_template(
        "start.html",
        candidate=row,
        projected_deadline=projected_deadline,
        trial_hours=int(TRIAL_DURATION.total_seconds() // 3600),
    )


@app.post("/trial/<token>/start")
def trial_start(token: str):
    if request.form.get("acknowledged") != "on":
        abort(400, description="Acknowledgment required.")

    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    cursor = db.execute(
        "UPDATE candidates SET started_at = ? "
        "WHERE token = ? AND started_at IS NULL",
        (now_iso, token),
    )
    db.commit()

    exists = db.execute(
        "SELECT 1 FROM candidates WHERE token = ?", (token,)
    ).fetchone()
    if exists is None:
        abort(404)

    just_started = "1" if cursor.rowcount == 1 else "0"
    return redirect(url_for("trial_page", token=token, just_started=just_started))


def _check_admin_auth() -> bool:
    auth = request.authorization
    return bool(auth and secrets.compare_digest(auth.password or "", ADMIN_PASSWORD))


@app.get("/admin")
def admin():
    if not _check_admin_auth():
        return (
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="worktester admin"'},
        )

    rows = get_db().execute(
        "SELECT token, name, doc_url, started_at FROM candidates "
        "ORDER BY started_at IS NULL, started_at DESC, name"
    ).fetchall()

    base_url = request.host_url.rstrip("/")
    enriched = []
    for r in rows:
        started = parse_iso(r["started_at"]) if r["started_at"] else None
        submission_dir = SUBMISSIONS_DIR / r["token"]
        has_submission = (submission_dir / "submission.pdf").exists()
        name_file = submission_dir / "filename.txt"
        submitted_filename = name_file.read_text() if has_submission and name_file.exists() else None
        enriched.append(
            {
                "token": r["token"],
                "name": r["name"],
                "doc_url": r["doc_url"],
                "started_at": started,
                "deadline": started + TRIAL_DURATION if started else None,
                "share_url": f"{base_url}/trial/{r['token']}",
                "submitted_filename": submitted_filename,
            }
        )
    return render_template("admin.html", candidates=enriched)


@app.post("/trial/<token>/submit")
def trial_submit(token: str):
    db = get_db()
    row = db.execute(
        "SELECT name, started_at FROM candidates WHERE token = ?", (token,)
    ).fetchone()
    if row is None:
        abort(404)
    if not row["started_at"]:
        abort(400, description="Trial has not been started.")

    deadline = parse_iso(row["started_at"]) + TRIAL_DURATION
    if datetime.now(timezone.utc) > deadline:
        flash("The trial period has ended. Submissions are no longer accepted.", "error")
        return redirect(url_for("trial_page", token=token))

    file = request.files.get("pdf")
    if not file or file.filename == "":
        flash("Please select a PDF file to upload.")
        return redirect(url_for("trial_page", token=token))

    if not file.filename.lower().endswith(".pdf"):
        flash("Only PDF files are accepted.")
        return redirect(url_for("trial_page", token=token))

    dest_dir = SUBMISSIONS_DIR / token
    dest_dir.mkdir(parents=True, exist_ok=True)

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > MAX_PDF_BYTES:
        flash("File is too large. Maximum size is 50 MB.")
        return redirect(url_for("trial_page", token=token))

    file.save(dest_dir / "submission.pdf")
    (dest_dir / "filename.txt").write_text(secure_filename(file.filename) or "submission.pdf")
    return redirect(url_for("trial_page", token=token))


@app.get("/trial/<token>/submission")
def trial_submission(token: str):
    row = get_db().execute(
        "SELECT 1 FROM candidates WHERE token = ?", (token,)
    ).fetchone()
    if row is None:
        abort(404)

    submission_dir = SUBMISSIONS_DIR / token
    if not (submission_dir / "submission.pdf").exists():
        abort(404)

    name_file = submission_dir / "filename.txt"
    display_name = name_file.read_text() if name_file.exists() else "submission.pdf"

    return send_from_directory(
        submission_dir.resolve(),
        "submission.pdf",
        download_name=display_name,
        as_attachment=False,
    )


@app.post("/admin/<token>/delete")
def admin_delete(token: str):
    if not _check_admin_auth():
        return (
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="worktester admin"'},
        )

    db = get_db()
    row = db.execute("SELECT name FROM candidates WHERE token = ?", (token,)).fetchone()
    if row is None:
        abort(404)
    db.execute("DELETE FROM candidates WHERE token = ?", (token,))
    db.commit()
    flash(f'Deleted "{row["name"]}".')
    return redirect(url_for("admin"))


@app.get("/admin/new")
def admin_new_form():
    if not _check_admin_auth():
        return (
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="worktester admin"'},
        )
    return render_template("admin_new.html", form={}, error=None)


@app.post("/admin/new")
def admin_new_submit():
    if not _check_admin_auth():
        return (
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="worktester admin"'},
        )

    name = (request.form.get("name") or "").strip()
    doc_url = (request.form.get("doc_url") or "").strip()

    error = None
    if not name or not doc_url:
        error = "Name and Google Doc URL are both required."
    elif not (doc_url.startswith("http://") or doc_url.startswith("https://")):
        error = "Google Doc URL must start with http:// or https://."

    if error:
        return render_template(
            "admin_new.html",
            form={"name": name, "doc_url": doc_url},
            error=error,
        ), 400

    db = get_db()
    token = generate_token(db)
    db.execute(
        "INSERT INTO candidates (token, name, doc_url) VALUES (?, ?, ?)",
        (token, name, doc_url),
    )
    db.commit()

    flash(f'Added "{name}". Share link: {request.host_url.rstrip("/")}/trial/{token}')
    return redirect(url_for("admin"))
