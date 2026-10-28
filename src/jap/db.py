import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PIPELINE_STATUSES = ["queued", "scraping", "extracting", "tailoring", "answering", "rendering", "ready",
                     "needs_manual_jd", "needs_review", "failed", "paused_budget"]
USER_STATUSES = ["applied", "interviewing", "offer", "rejected", "withdrawn", "skipped"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    primary_url TEXT NOT NULL UNIQUE,
    extra_urls TEXT NOT NULL DEFAULT '[]',
    company TEXT, title TEXT, location TEXT, remote TEXT, track TEXT,
    status TEXT NOT NULL,
    applied_at TEXT,
    note TEXT, folder TEXT, error TEXT,
    cost_usd REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    urls TEXT NOT NULL DEFAULT '[]',
    jd_text TEXT,
    track TEXT, note TEXT,
    force INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    job_id INTEGER REFERENCES jobs(id),
    error TEXT
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES jobs(id),
    created_at TEXT NOT NULL,
    stage TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    cost_usd REAL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # The watcher and one-off `jap` commands may hold the database at the same time.
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def create_job(self, primary_url: str, extra_urls: list[str], note: str | None) -> int:
        ts = now()
        cur = self.conn.execute(
            "INSERT INTO jobs (created_at, updated_at, primary_url, extra_urls, status, note) VALUES (?, ?, ?, ?, 'queued', ?)",
            (ts, ts, primary_url, json.dumps(extra_urls), note))
        self.conn.commit()
        return cur.lastrowid

    def update(self, job_id: int, **fields) -> None:
        if fields.get("status") == "applied":
            fields.setdefault("applied_at", now())
        fields["updated_at"] = now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))
        self.conn.commit()

    def get(self, job_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def find_by_url(self, primary_url: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE primary_url = ?", (primary_url,)).fetchone()
        return dict(row) if row else None

    def reset_for_rerun(self, job_id: int) -> None:
        self.update(job_id, status="queued", error=None)

    def jobs(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM jobs ORDER BY id")]

    # --- queue: inbox entries waiting for the watcher ---

    def enqueue(self, source: str, urls: list[str], jd_text: str | None, track: str | None, note: str | None,
                force: bool, commit: bool = True) -> int:
        cur = self.conn.execute(
            "INSERT INTO queue (created_at, source, urls, jd_text, track, note, force) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now(), source, json.dumps(urls), jd_text, track, note, int(force)))
        if commit:
            self.conn.commit()
        return cur.lastrowid

    def next_queued(self) -> dict | None:
        row = self.conn.execute("SELECT * FROM queue WHERE status = 'pending' ORDER BY id LIMIT 1").fetchone()
        return {**dict(row), "urls": json.loads(row["urls"])} if row else None

    def queue_update(self, queue_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE queue SET {cols} WHERE id = ?", (*fields.values(), queue_id))
        self.conn.commit()

    def requeue_interrupted(self) -> int:
        """Items left 'running' by a stopped or crashed watcher go back to the front of the queue."""
        cur = self.conn.execute("UPDATE queue SET status = 'pending' WHERE status = 'running'")
        self.conn.commit()
        return cur.rowcount

    def pending_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM queue WHERE status IN ('pending', 'running')").fetchone()[0]

    def record_usage(self, job_id: int | None, stage: str, usage, cost: float | None) -> None:
        self.conn.execute(
            "INSERT INTO llm_usage (job_id, created_at, stage, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, now(), stage, usage.model, usage.input_tokens, usage.output_tokens,
             usage.cache_read_tokens, usage.cache_write_tokens, cost))
        if job_id is not None and cost:
            self.conn.execute("UPDATE jobs SET cost_usd = cost_usd + ? WHERE id = ?", (cost, job_id))
        self.conn.commit()

    def month_cost(self) -> float:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        row = self.conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE created_at LIKE ?", (f"{month}%",)).fetchone()
        return row[0]

    def usage_summary(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT substr(created_at, 1, 7) AS month, model, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens, "
            "SUM(output_tokens) AS output_tokens, SUM(cache_read_tokens) AS cache_read_tokens, "
            "SUM(cache_write_tokens) AS cache_write_tokens, ROUND(SUM(cost_usd), 4) AS cost_usd "
            "FROM llm_usage GROUP BY month, model ORDER BY month DESC, model")]
