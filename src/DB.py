import sqlite3
from datetime import datetime
import os

TOTAL_GPUS = 20
TOTAL_BUDGET = 43200
DB_PATH = os.environ.get("LEDGER_DB_PATH", "./ledger.db")

def init_db(conn: sqlite3.Connection):
    conn.execute(
        """
            CREATE TABLE IF NOT EXISTS requests (
            id               TEXT PRIMARY KEY,
            user             TEXT NOT NULL,
            gpus             INTEGER NOT NULL,
            requested_hours  REAL NOT NULL,
            status           TEXT NOT NULL,
            requested_at     TEXT NOT NULL,
            approved_at      TEXT,
            expires_at       TEXT,
            ended_at         TEXT,
            charged_hours    REAL
            )      """
    )
    conn.commit()


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn

def get_available_gpus(conn):
    used = conn.execute(
        "SELECT COALESCE(SUM(gpus), 0) FROM requests WHERE status IN ('allocated', 'overrun')"
    ).fetchone()[0]
    return TOTAL_GPUS - used

def get_budget_used(conn):
    """Live GPU-hours actually burned so far (elapsed time for active sessions). For display/status."""
    now = datetime.now()
    running_rows = conn.execute(
        "SELECT gpus, approved_at FROM requests WHERE status IN ('allocated', 'overrun')"
    ).fetchall()
    running = sum(
        row['gpus'] * (now - datetime.fromisoformat(row['approved_at'])).total_seconds() / 3600
        for row in running_rows
    )
    ended = conn.execute(
        "SELECT COALESCE(SUM(charged_hours), 0) FROM requests WHERE status = 'ended'"
    ).fetchone()[0]
    return running + ended

def get_committed_budget(conn):
    """GPU-hours reserved against the budget: full requested duration for active sessions
    (worst-case cost if they run to term), plus actual charge for ended ones. For admission control."""
    reserved = conn.execute(
        "SELECT COALESCE(SUM(gpus * requested_hours), 0) FROM requests WHERE status IN ('allocated', 'overrun')"
    ).fetchone()[0]
    ended = conn.execute(
        "SELECT COALESCE(SUM(charged_hours), 0) FROM requests WHERE status = 'ended'"
    ).fetchone()[0]
    return reserved + ended

def get_next_request_id(conn):
    count = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    return f"req_{count + 1:03d}"

def get_queue_depth(conn):
    return conn.execute(
        "SELECT COUNT(*) FROM requests WHERE status = 'pending'"
    ).fetchone()[0]