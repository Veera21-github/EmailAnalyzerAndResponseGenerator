import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import get_settings


def _database_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise ValueError("Only sqlite:/// database URLs are supported for this project.")
    return Path(url.replace("sqlite:///", "", 1))


@contextmanager
def get_db():
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gmail_id TEXT UNIQUE NOT NULL,
                thread_id TEXT,
                sender TEXT,
                recipient TEXT,
                subject TEXT,
                snippet TEXT,
                body TEXT,
                received_at TEXT,
                category TEXT,
                mail_type TEXT,
                confidence REAL,
                draft_reply TEXT,
                status TEXT DEFAULT 'new',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _ensure_column(db, "emails", "mail_type", "TEXT")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS reply_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id INTEGER NOT NULL,
                gmail_message_id TEXT,
                reply_body TEXT NOT NULL,
                status TEXT NOT NULL,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(email_id) REFERENCES emails(id)
            )
            """
        )


def upsert_email(message: dict) -> None:
    with get_db() as db:
        db.execute(
            """
            INSERT INTO emails (
                gmail_id, thread_id, sender, recipient, subject, snippet, body,
                received_at, category, mail_type, confidence, draft_reply, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 'new'))
            ON CONFLICT(gmail_id) DO UPDATE SET
                thread_id = excluded.thread_id,
                sender = excluded.sender,
                recipient = excluded.recipient,
                subject = excluded.subject,
                snippet = excluded.snippet,
                body = excluded.body,
                received_at = excluded.received_at,
                category = COALESCE(excluded.category, emails.category),
                mail_type = COALESCE(excluded.mail_type, emails.mail_type),
                confidence = COALESCE(excluded.confidence, emails.confidence),
                draft_reply = COALESCE(excluded.draft_reply, emails.draft_reply),
                status = COALESCE(excluded.status, emails.status),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                message["gmail_id"],
                message.get("thread_id"),
                message.get("sender"),
                message.get("recipient"),
                message.get("subject"),
                message.get("snippet"),
                message.get("body"),
                message.get("received_at"),
                message.get("category"),
                message.get("mail_type"),
                message.get("confidence"),
                message.get("draft_reply"),
                message.get("status", "new"),
            ),
        )


def remove_demo_emails() -> None:
    with get_db() as db:
        db.execute("DELETE FROM emails WHERE gmail_id LIKE 'demo-%'")


def prune_unsent_inbox(keep_gmail_ids: list[str]) -> None:
    if not keep_gmail_ids:
        with get_db() as db:
            db.execute("DELETE FROM emails WHERE status != 'sent'")
        return

    placeholders = ",".join("?" for _ in keep_gmail_ids)
    with get_db() as db:
        db.execute(
            f"""
            DELETE FROM emails
            WHERE status != 'sent'
            AND gmail_id NOT IN ({placeholders})
            """,
            keep_gmail_ids,
        )


def list_emails(
    category: str | None = None,
    status: str | None = None,
    mail_type: str | None = None,
    search: str | None = None,
):
    query = "SELECT * FROM emails WHERE 1 = 1"
    params: list[str] = []
    if category:
        query += " AND category = ?"
        params.append(category)
    if mail_type:
        query += " AND mail_type = ?"
        params.append(mail_type)
    if status:
        query += " AND status = ?"
        params.append(status)
    if search:
        query += " AND (sender LIKE ? OR subject LIKE ? OR snippet LIKE ? OR body LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term, term])
    query += " ORDER BY received_at DESC, id DESC"
    with get_db() as db:
        return db.execute(query, params).fetchall()


def get_email(email_id: int):
    with get_db() as db:
        return db.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()


def get_thread_history(thread_id: str | None, exclude_email_id: int | None = None):
    if not thread_id:
        return []
    query = "SELECT * FROM emails WHERE thread_id = ?"
    params: list[object] = [thread_id]
    if exclude_email_id:
        query += " AND id != ?"
        params.append(exclude_email_id)
    query += " ORDER BY received_at ASC, id ASC"
    with get_db() as db:
        return db.execute(query, params).fetchall()


def get_pending_emails():
    with get_db() as db:
        return db.execute(
            """
            SELECT * FROM emails
            WHERE status != 'sent'
            ORDER BY received_at DESC, id DESC
            """
        ).fetchall()


def update_classification(email_id: int, category: str, confidence: float, mail_type: str) -> None:
    with get_db() as db:
        db.execute(
            """
            UPDATE emails
            SET category = ?, confidence = ?, mail_type = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (category, confidence, mail_type, email_id),
        )


def update_draft(email_id: int, draft_reply: str) -> None:
    with get_db() as db:
        db.execute(
            """
            UPDATE emails
            SET draft_reply = ?, status = 'drafted', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (draft_reply, email_id),
        )


def mark_sent(email_id: int, reply_body: str, gmail_message_id: str | None) -> None:
    with get_db() as db:
        db.execute(
            "UPDATE emails SET status = 'sent', draft_reply = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (reply_body, email_id),
        )
        db.execute(
            """
            INSERT INTO reply_logs (email_id, gmail_message_id, reply_body, status)
            VALUES (?, ?, ?, 'sent')
            """,
            (email_id, gmail_message_id, reply_body),
        )


def list_reply_logs():
    with get_db() as db:
        return db.execute(
            """
            SELECT reply_logs.*, emails.subject, emails.sender
            FROM reply_logs
            JOIN emails ON emails.id = reply_logs.email_id
            ORDER BY reply_logs.sent_at DESC, reply_logs.id DESC
            """
        ).fetchall()


def dashboard_stats() -> dict:
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        sent = db.execute("SELECT COUNT(*) FROM emails WHERE status = 'sent'").fetchone()[0]
        drafted = db.execute("SELECT COUNT(*) FROM emails WHERE status = 'drafted'").fetchone()[0]
        new = db.execute("SELECT COUNT(*) FROM emails WHERE status = 'new'").fetchone()[0]
        categories = db.execute(
            """
            SELECT COALESCE(category, 'not classified') AS category, COUNT(*) AS count
            FROM emails
            GROUP BY COALESCE(category, 'not classified')
            ORDER BY count DESC
            """
        ).fetchall()
    return {"total": total, "sent": sent, "drafted": drafted, "new": new, "categories": categories}


def _ensure_column(db: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
