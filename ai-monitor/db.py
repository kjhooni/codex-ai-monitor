import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path("/data/monitor.db")


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                node            TEXT NOT NULL,
                owner           TEXT NOT NULL,
                metric          TEXT NOT NULL,
                value           REAL NOT NULL,
                threshold       REAL NOT NULL,
                status          TEXT DEFAULT 'open',
                detected_at     TEXT NOT NULL,
                resolved_at     TEXT,
                duration_min    INTEGER,
                claude_analysis TEXT,
                recommended_action TEXT,
                action_taken    TEXT,
                notified        INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                token           TEXT PRIMARY KEY,
                incident_id     INTEGER NOT NULL,
                node            TEXT NOT NULL,
                metric          TEXT NOT NULL,
                command         TEXT NOT NULL,
                description     TEXT,
                node_config     TEXT NOT NULL,
                status          TEXT DEFAULT 'pending',
                created_at      TEXT NOT NULL
            )
        """)
        try:
            conn.execute("ALTER TABLE pending_actions ADD COLUMN description TEXT")
        except Exception:
            pass


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def open_incident(node, owner, metric, value, threshold, claude_analysis=None, recommended_action=None):
    with _conn() as conn:
        cur = conn.execute("""
            INSERT INTO incidents
                (node, owner, metric, value, threshold, detected_at, claude_analysis, recommended_action)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (node, owner, metric, value, threshold, _now(), claude_analysis, recommended_action))
        return cur.lastrowid


def get_open_incident(node, metric):
    with _conn() as conn:
        return conn.execute("""
            SELECT * FROM incidents
            WHERE node = ? AND metric = ? AND status = 'open'
            ORDER BY detected_at DESC LIMIT 1
        """, (node, metric)).fetchone()


def resolve_incident(incident_id, detected_at):
    now = datetime.now()
    detected = datetime.fromisoformat(detected_at)
    duration = int((now - detected).total_seconds() / 60)
    with _conn() as conn:
        conn.execute("""
            UPDATE incidents
            SET status = 'resolved', resolved_at = ?, duration_min = ?
            WHERE id = ?
        """, (now.isoformat(), duration, incident_id))


def update_action(incident_id, action_taken):
    with _conn() as conn:
        conn.execute("UPDATE incidents SET action_taken = ? WHERE id = ?", (action_taken, incident_id))


def get_history(node, metric, limit=10):
    with _conn() as conn:
        rows = conn.execute("""
            SELECT detected_at, value, threshold, duration_min, claude_analysis, action_taken, status
            FROM incidents
            WHERE node = ? AND metric = ?
            ORDER BY detected_at DESC LIMIT ?
        """, (node, metric, limit)).fetchall()
        return [dict(r) for r in rows]


def store_pending_action(token, incident_id, node, metric, command, node_config, description=None):
    import json
    with _conn() as conn:
        conn.execute("""
            INSERT INTO pending_actions (token, incident_id, node, metric, command, description, node_config, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (token, incident_id, node, metric, command, description, json.dumps(node_config), _now()))


def get_pending_action(token):
    import json
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM pending_actions WHERE token = ?", (token,)
        ).fetchone()
        if row:
            d = dict(row)
            d["node_config"] = json.loads(d["node_config"])
            return d
        return None


def update_pending_action_status(token, status):
    with _conn() as conn:
        conn.execute("UPDATE pending_actions SET status = ? WHERE token = ?", (status, token))


def _now():
    return datetime.now().isoformat()
