#!/usr/bin/env python3
"""Review scanned links and record what the team wants done with each one."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import checker

DATABASE_PATH = os.environ.get("DECISIONS_DB", "decisions.db")
REPORT_JSON_PATH = os.environ.get("REPORT_JSON", checker.REPORT_JSON_PATH)
USERS_PATH = os.environ.get("USERS_FILE", "users.json")


def hash_password_cli() -> None:
    import getpass

    password = getpass.getpass("Password: ")
    again = getpass.getpass("Repeat: ")
    if not password or password != again:
        sys.exit("The passwords did not match.")
    print(generate_password_hash(password))


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "hash-password":
    hash_password_cli()
    raise SystemExit

app = Flask(__name__)


def load_users() -> dict:
    try:
        with open(USERS_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        sys.exit(
            f"Missing {USERS_PATH}. Copy users.example.json to users.json "
            "and replace the example passwords."
        )
    except json.JSONDecodeError as exc:
        sys.exit(f"{USERS_PATH} is not valid JSON: {exc}")
    secret = data.get("secret")
    users = data.get("users")
    if not isinstance(secret, str) or len(secret) < 16:
        sys.exit(f"{USERS_PATH} needs a secret string of at least 16 characters")
    if not isinstance(users, list) or not users:
        sys.exit(f"{USERS_PATH} needs a non-empty users list")
    by_name = {}
    for index, user in enumerate(users, start=1):
        if not isinstance(user, dict):
            sys.exit(f"User {index} must be an object")
        username = user.get("username")
        password_hash = user.get("password_hash")
        role = user.get("role")
        if not isinstance(username, str) or not username.strip():
            sys.exit(f"User {index} needs a username")
        if not isinstance(password_hash, str) or not password_hash.startswith("scrypt:"):
            sys.exit(f"User {index} needs a password_hash from: python web.py hash-password")
        if role not in {"admin", "member"}:
            sys.exit(f"User {index} role must be admin or member")
        by_name[username] = {"password_hash": password_hash, "role": role}
    return {"secret": secret, "users": by_name}


USERS = load_users()
app.secret_key = USERS["secret"]
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        connection = sqlite3.connect(DATABASE_PATH)
        connection.row_factory = sqlite3.Row
        g.db = connection
    return g.db


@app.teardown_appcontext
def close_db(_exc) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db() -> None:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS scan (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            generated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY,
            site TEXT NOT NULL,
            url TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            sources TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS decisions (
            url TEXT PRIMARY KEY,
            verdict TEXT,
            verdict_by TEXT,
            verdict_at TEXT,
            action TEXT,
            alternative_url TEXT,
            action_note TEXT,
            action_by TEXT,
            action_at TEXT
        );
        """
    )
    connection.commit()
    connection.close()


def now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def decision_label(row) -> str:
    verdict = row["verdict"]
    if not verdict:
        return "Needs confirmation"
    if verdict == "not_broken":
        return "Not broken"
    if row["action"] == "replace":
        return "Replacement suggested"
    if row["action"] == "delete":
        return "Deletion requested"
    return "Confirmed broken"


def sync_report() -> dict | None:
    """Load report.json into the review queue. Decisions are kept by URL."""
    try:
        with open(REPORT_JSON_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None
    generated_at = data.get("generatedAt")
    sites = data.get("sites")
    if not isinstance(generated_at, str) or not isinstance(sites, list):
        return None

    connection = get_db()
    row = connection.execute("SELECT generated_at FROM scan WHERE id = 1").fetchone()
    if row is not None and row["generated_at"] == generated_at:
        return data

    connection.execute("DELETE FROM findings")
    for site in sites:
        if not isinstance(site, dict):
            continue
        site_url = site.get("url") or ""
        if site.get("error"):
            continue
        for link in site.get("links") or []:
            if not isinstance(link, dict) or not isinstance(link.get("url"), str):
                continue
            sources = link.get("sources") or []
            if not isinstance(sources, list):
                sources = []
            connection.execute(
                """
                INSERT INTO findings (site, url, kind, status, sources)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    site_url,
                    link["url"],
                    link.get("kind") or "broken",
                    link.get("status") or "",
                    json.dumps([source for source in sources if isinstance(source, str)]),
                ),
            )
    connection.execute(
        """
        INSERT INTO scan (id, generated_at) VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET generated_at = excluded.generated_at
        """,
        (generated_at,),
    )
    connection.commit()
    return data


def current_user() -> dict | None:
    username = session.get("username")
    if not username or username not in USERS["users"]:
        return None
    record = USERS["users"][username]
    return {"username": username, "role": record["role"]}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("login", next=request.path))
        if user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def csrf_token() -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_hex(16)
        session["csrf"] = token
    return token


def reject_bad_csrf() -> None:
    if not secrets.compare_digest(request.form.get("csrf", ""), session.get("csrf", "")):
        abort(400)


def finding_or_404(finding_id: int) -> sqlite3.Row:
    row = get_db().execute(
        """
        SELECT findings.*, decisions.verdict, decisions.verdict_by, decisions.verdict_at,
               decisions.action, decisions.alternative_url, decisions.action_note,
               decisions.action_by, decisions.action_at
        FROM findings
        LEFT JOIN decisions ON decisions.url = findings.url
        WHERE findings.id = ?
        """,
        (finding_id,),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def queue_rows(view: str) -> list[sqlite3.Row]:
    clauses = {
        "open": "(decisions.verdict IS NULL OR (decisions.verdict = 'broken' AND decisions.action IS NULL))",
        "broken": "decisions.verdict = 'broken'",
        "clear": "decisions.verdict = 'not_broken'",
        "all": "1 = 1",
    }
    where = clauses.get(view, clauses["open"])
    return get_db().execute(
        f"""
        SELECT findings.*, decisions.verdict, decisions.action, decisions.alternative_url
        FROM findings
        LEFT JOIN decisions ON decisions.url = findings.url
        WHERE {where}
        ORDER BY findings.site, findings.url
        """
    ).fetchall()


def counts() -> dict[str, int]:
    connection = get_db()
    total = connection.execute("SELECT COUNT(*) AS n FROM findings").fetchone()["n"]
    open_count = connection.execute(
        """
        SELECT COUNT(*) AS n FROM findings
        LEFT JOIN decisions ON decisions.url = findings.url
        WHERE decisions.verdict IS NULL
           OR (decisions.verdict = 'broken' AND decisions.action IS NULL)
        """
    ).fetchone()["n"]
    broken = connection.execute(
        "SELECT COUNT(*) AS n FROM findings JOIN decisions ON decisions.url = findings.url WHERE verdict = 'broken'"
    ).fetchone()["n"]
    clear = connection.execute(
        "SELECT COUNT(*) AS n FROM findings JOIN decisions ON decisions.url = findings.url WHERE verdict = 'not_broken'"
    ).fetchone()["n"]
    return {"all": total, "open": open_count, "broken": broken, "clear": clear}


def sources_of(row) -> list[str]:
    try:
        parsed = json.loads(row["sources"])
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]


@app.context_processor
def inject_globals():
    return {
        "csrf_token": csrf_token,
        "current_user": current_user(),
        "decision_label": decision_label,
    }


@app.get("/login")
def login():
    if current_user() is not None:
        return redirect(url_for("index"))
    return render_template("login.html")


@app.post("/login")
def login_submit():
    reject_bad_csrf()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    record = USERS["users"].get(username)
    if record is None or not check_password_hash(record["password_hash"], password):
        flash("That username or password is not recognized.")
        return render_template("login.html"), 401
    session.clear()
    session["username"] = username
    session["csrf"] = secrets.token_hex(16)
    nxt = request.args.get("next") or request.form.get("next") or url_for("index")
    if not nxt.startswith("/"):
        nxt = url_for("index")
    return redirect(nxt)


@app.post("/logout")
def logout():
    reject_bad_csrf()
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    report = sync_report()
    view = request.args.get("view", "open")
    if view not in {"open", "broken", "clear", "all"}:
        view = "open"
    rows = queue_rows(view) if report else []
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row["site"], []).append(row)
    site_errors = []
    if report:
        for site in report.get("sites") or []:
            if isinstance(site, dict) and site.get("error"):
                site_errors.append({"url": site.get("url") or "", "error": site["error"]})
    return render_template(
        "index.html",
        generated_at=report.get("generatedAt") if report else None,
        view=view,
        counts=counts() if report else {"all": 0, "open": 0, "broken": 0, "clear": 0},
        grouped=grouped,
        site_errors=site_errors,
    )


@app.get("/links/<int:finding_id>")
@login_required
def link_detail(finding_id: int):
    sync_report()
    row = finding_or_404(finding_id)
    return render_template("link.html", link=row, sources=sources_of(row))


@app.post("/links/<int:finding_id>/verdict")
@admin_required
def set_verdict(finding_id: int):
    reject_bad_csrf()
    row = finding_or_404(finding_id)
    verdict = request.form.get("verdict")
    if verdict not in {"broken", "not_broken"}:
        abort(400)
    user = current_user()
    assert user is not None
    connection = get_db()
    if verdict == "not_broken":
        connection.execute(
            """
            INSERT INTO decisions (url, verdict, verdict_by, verdict_at, action, alternative_url, action_note, action_by, action_at)
            VALUES (?, 'not_broken', ?, ?, NULL, NULL, NULL, NULL, NULL)
            ON CONFLICT(url) DO UPDATE SET
                verdict = 'not_broken',
                verdict_by = excluded.verdict_by,
                verdict_at = excluded.verdict_at,
                action = NULL,
                alternative_url = NULL,
                action_note = NULL,
                action_by = NULL,
                action_at = NULL
            """,
            (row["url"], user["username"], now_label()),
        )
        flash("Marked as not broken.")
    else:
        connection.execute(
            """
            INSERT INTO decisions (url, verdict, verdict_by, verdict_at)
            VALUES (?, 'broken', ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                verdict = 'broken',
                verdict_by = excluded.verdict_by,
                verdict_at = excluded.verdict_at
            """,
            (row["url"], user["username"], now_label()),
        )
        flash("Confirmed broken. Choose a replacement or ask for the link to be deleted.")
    connection.commit()
    return redirect(url_for("link_detail", finding_id=finding_id))


@app.post("/links/<int:finding_id>/action")
@login_required
def set_action(finding_id: int):
    reject_bad_csrf()
    row = finding_or_404(finding_id)
    if row["verdict"] != "broken":
        flash("An admin has to confirm the link is broken before the team chooses an action.")
        return redirect(url_for("link_detail", finding_id=finding_id))
    action = request.form.get("action")
    note = " ".join(request.form.get("note", "").split())[:500]
    alternative = ""
    if action == "replace":
        alternative = checker.normalize_url(request.form.get("alternative_url", ""))
        if not alternative:
            flash("Enter a full http or https URL for the replacement.")
            return redirect(url_for("link_detail", finding_id=finding_id))
    elif action != "delete":
        abort(400)
    user = current_user()
    assert user is not None
    get_db().execute(
        """
        UPDATE decisions
        SET action = ?, alternative_url = ?, action_note = ?, action_by = ?, action_at = ?
        WHERE url = ?
        """,
        (
            action,
            alternative or None,
            note or None,
            user["username"],
            now_label(),
            row["url"],
        ),
    )
    get_db().commit()
    if action == "replace":
        flash("Saved the replacement link.")
    else:
        flash("Requested that the link be deleted.")
    return redirect(url_for("link_detail", finding_id=finding_id))


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port)
