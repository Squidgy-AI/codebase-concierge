"""
Q&A memory cache — SQLite, single-tenant, "memory across workflows" demo.

Similarity strategy: difflib.SequenceMatcher ratio > 0.85 over a normalized
signature of the question. Picked over Haiku-based normalization because
it adds zero API latency, zero quota burn, and is fully deterministic.
The trade-off: it'll miss paraphrases that don't share token roots
("middleware" vs. "interceptors"). Acceptable — demo is two near-duplicate
questions, not adversarial paraphrases. Easy to upgrade later.
"""
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from difflib import SequenceMatcher

CACHE_DB_PATH = os.environ.get("CACHE_DB_PATH", "cache.db")

# Render free disk is ephemeral — cache survives within a deploy, not across them.
# That's fine for the hackathon demo (pre-warm the cache before going live).

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "to", "in", "on", "at", "by", "for", "with", "from", "as",
    "and", "or", "but", "not", "this", "that", "these", "those",
    "it", "its", "do", "does", "did", "how", "what", "why", "when", "where",
    "i", "you", "we", "they", "me", "us", "them", "my", "your", "our",
}

_lock = threading.Lock()
_initialized = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(CACHE_DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    global _initialized
    if _initialized:
        return
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS qa_cache (
              id                  INTEGER PRIMARY KEY AUTOINCREMENT,
              question            TEXT NOT NULL,
              question_signature  TEXT NOT NULL,
              answer_html         TEXT NOT NULL,
              answer_md           TEXT NOT NULL,
              sources_json        TEXT NOT NULL,
              engineers_json      TEXT NOT NULL,
              original_sender     TEXT,
              created_at          TEXT NOT NULL DEFAULT (datetime('now')),
              hit_count           INTEGER NOT NULL DEFAULT 0,
              last_hit_at         TEXT,
              last_hit_sender     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_qa_signature ON qa_cache(question_signature);

            CREATE TABLE IF NOT EXISTS users (
              email         TEXT PRIMARY KEY COLLATE NOCASE,
              display_name  TEXT,
              default_mode  TEXT NOT NULL DEFAULT 'eng',
              created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS flagged_senders (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              sender      TEXT NOT NULL,
              subject     TEXT,
              preview     TEXT,
              created_at  TEXT NOT NULL DEFAULT (datetime('now')),
              resolved    INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_flagged_sender ON flagged_senders(sender);

            CREATE TABLE IF NOT EXISTS settings (
              key   TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            """
        )
        # Backwards compat for early demos that created the table without hit columns.
        existing = {r[1] for r in conn.execute("PRAGMA table_info(qa_cache)").fetchall()}
        for col, ddl in [
            ("hit_count", "ALTER TABLE qa_cache ADD COLUMN hit_count INTEGER NOT NULL DEFAULT 0"),
            ("last_hit_at", "ALTER TABLE qa_cache ADD COLUMN last_hit_at TEXT"),
            ("last_hit_sender", "ALTER TABLE qa_cache ADD COLUMN last_hit_sender TEXT"),
        ]:
            if col not in existing:
                conn.execute(ddl)
    _initialized = True


def _signature(question: str) -> str:
    """Lowercase, strip punctuation, drop stopwords, sort remaining tokens.
    Two paraphrases-of-the-same question end up with identical or
    near-identical signatures."""
    tokens = re.findall(r"[a-z0-9]+", question.lower())
    keep = [t for t in tokens if t not in _STOPWORDS and len(t) > 1]
    return " ".join(sorted(keep))


def _ttl_seconds() -> int:
    """Cache TTL in seconds. 0 means no expiry. Read fresh each lookup so admin
    edits take effect without a restart."""
    raw = (get_setting("cache_ttl_hours", "168") or "168").strip()
    try:
        return max(0, int(float(raw)) * 3600)
    except ValueError:
        return 168 * 3600


def _is_fresh(created_at: str | None) -> bool:
    ttl = _ttl_seconds()
    if ttl <= 0 or not created_at:
        return True
    try:
        if "T" in created_at:
            t = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            t = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc)
    except ValueError:
        return True  # don't punish a malformed row by always missing
    return (datetime.now(timezone.utc) - t).total_seconds() < ttl


def lookup(question: str, threshold: float = 0.85, hit_sender: str | None = None) -> dict | None:
    """Return the cached payload if a sufficiently similar question exists AND
    the entry is younger than the configured TTL.

    Result: {
        "answer_html", "answer_md", "sources", "engineers",
        "cache_hit": True, "original_sender", "original_date",
    } or None.
    """
    _init_db()
    sig = _signature(question)
    if not sig:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM qa_cache WHERE question_signature = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (sig,),
        ).fetchone()
        if not row:
            rows = conn.execute(
                "SELECT * FROM qa_cache ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
            best, best_score = None, 0.0
            for r in rows:
                score = SequenceMatcher(None, sig, r["question_signature"]).ratio()
                if score > best_score:
                    best, best_score = r, score
            if best and best_score >= threshold:
                row = best
        if not row:
            return None
        # Drop stale rows so the brain re-queries Nia (which may have re-indexed).
        if not _is_fresh(row["created_at"]):
            return None
        # Record the hit so the dashboard can show "asked X times".
        conn.execute(
            "UPDATE qa_cache SET hit_count = hit_count + 1, last_hit_at = ?, last_hit_sender = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), hit_sender, row["id"]),
        )
        return _row_to_payload(row)


def purge_cache(older_than_hours: int | None = None) -> int:
    """Delete rows. older_than_hours=None wipes everything. Returns rows deleted."""
    _init_db()
    with _lock, _connect() as conn:
        if older_than_hours is None:
            cur = conn.execute("DELETE FROM qa_cache")
        else:
            cur = conn.execute(
                "DELETE FROM qa_cache WHERE created_at < datetime('now', ?)",
                (f"-{int(older_than_hours)} hours",),
            )
        return cur.rowcount


def store(
    question: str,
    answer_html: str,
    answer_md: str,
    sources: list,
    engineers: list,
    original_sender: str | None,
) -> None:
    _init_db()
    sig = _signature(question)
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO qa_cache
              (question, question_signature, answer_html, answer_md,
               sources_json, engineers_json, original_sender, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question,
                sig,
                answer_html,
                answer_md,
                json.dumps(sources),
                json.dumps(engineers),
                original_sender,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )


def recent(limit: int = 30) -> list[dict]:
    """Return the most recent cached Q&A rows for the dashboard."""
    _init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM qa_cache ORDER BY COALESCE(last_hit_at, created_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def stats() -> dict:
    _init_db()
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM qa_cache").fetchone()[0]
        hits = conn.execute("SELECT COALESCE(SUM(hit_count), 0) FROM qa_cache").fetchone()[0]
        flagged = conn.execute(
            "SELECT COUNT(*) FROM flagged_senders WHERE resolved = 0"
        ).fetchone()[0]
    return {"questions": total, "cache_hits": hits, "flagged_senders": flagged}


# ---------- Users ----------

def list_users() -> list[dict]:
    _init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT email, display_name, default_mode, created_at "
            "FROM users ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_user(email: str, display_name: str | None, default_mode: str) -> None:
    _init_db()
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("invalid email")
    if default_mode not in ("eng", "sales", "marketing", "support", "security"):
        raise ValueError("invalid mode")
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO users (email, display_name, default_mode)
                 VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
              display_name = excluded.display_name,
              default_mode = excluded.default_mode
            """,
            (email, (display_name or "").strip() or None, default_mode),
        )


def delete_user(email: str) -> None:
    _init_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM users WHERE email = ?", ((email or "").strip().lower(),))


def lookup_user(email: str) -> dict | None:
    """Returns {email, display_name, default_mode, ...} or None."""
    _init_db()
    if not email:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT email, display_name, default_mode FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
    return dict(row) if row else None


# ---------- Flagged senders ----------

def flag_sender(sender: str, subject: str | None, preview: str | None) -> None:
    _init_db()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO flagged_senders (sender, subject, preview) VALUES (?, ?, ?)",
            (sender, subject, (preview or "")[:300] if preview else None),
        )


def list_flagged(limit: int = 50, only_unresolved: bool = True) -> list[dict]:
    _init_db()
    with _connect() as conn:
        if only_unresolved:
            rows = conn.execute(
                "SELECT * FROM flagged_senders WHERE resolved = 0 "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM flagged_senders ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def resolve_flagged(flag_id: int) -> None:
    _init_db()
    with _lock, _connect() as conn:
        conn.execute("UPDATE flagged_senders SET resolved = 1 WHERE id = ?", (flag_id,))


# ---------- Settings ----------

def get_setting(key: str, default: str | None = None) -> str | None:
    _init_db()
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    _init_db()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Plain dict for dashboard rendering (NOT the payload shape used by lookup)."""
    return {
        "id": row["id"],
        "question": row["question"],
        "answer_html": row["answer_html"],
        "sources": json.loads(row["sources_json"]),
        "engineers": json.loads(row["engineers_json"]),
        "original_sender": row["original_sender"],
        "created_at": row["created_at"],
        "hit_count": row["hit_count"],
        "last_hit_at": row["last_hit_at"],
        "last_hit_sender": row["last_hit_sender"],
    }


def _row_to_payload(row: sqlite3.Row) -> dict:
    created = row["created_at"]
    # Reduce ISO timestamp to YYYY-MM-DD for the user-facing line.
    short_date = created[:10] if isinstance(created, str) else str(created)
    return {
        "answer_html": row["answer_html"],
        "answer_md": row["answer_md"],
        "sources": json.loads(row["sources_json"]),
        "engineers": json.loads(row["engineers_json"]),
        "cache_hit": True,
        "original_sender": row["original_sender"],
        "original_date": short_date,
    }
