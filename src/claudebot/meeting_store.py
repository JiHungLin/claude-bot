import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger("claudebot.meeting_store")


@dataclass
class MeetingRecord:
    issue_number: int
    repo: str
    title: str
    scheduled_at: datetime
    reminder_minutes: int
    status: str  # scheduled | in_progress | done
    reminder_sent: bool
    start_sent: bool


class MeetingStore:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS meetings (
                    issue_number  INTEGER NOT NULL,
                    repo          TEXT    NOT NULL,
                    title         TEXT    NOT NULL DEFAULT '',
                    scheduled_at  TEXT    NOT NULL,
                    reminder_min  INTEGER NOT NULL DEFAULT 15,
                    status        TEXT    NOT NULL DEFAULT 'scheduled',
                    reminder_sent INTEGER NOT NULL DEFAULT 0,
                    start_sent    INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (issue_number, repo)
                )
            """)

    def upsert(self, r: MeetingRecord) -> None:
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO meetings
                    (issue_number, repo, title, scheduled_at, reminder_min,
                     status, reminder_sent, start_sent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(issue_number, repo) DO UPDATE SET
                    title         = excluded.title,
                    scheduled_at  = excluded.scheduled_at,
                    reminder_min  = excluded.reminder_min,
                    status        = excluded.status,
                    reminder_sent = excluded.reminder_sent,
                    start_sent    = excluded.start_sent
            """, (
                r.issue_number, r.repo, r.title,
                r.scheduled_at.isoformat(),
                r.reminder_minutes, r.status,
                int(r.reminder_sent), int(r.start_sent),
            ))

    def get(self, issue_number: int, repo: str) -> MeetingRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM meetings WHERE issue_number=? AND repo=?",
                (issue_number, repo),
            ).fetchone()
        return self._to_record(row) if row else None

    def get_pending(self) -> list[MeetingRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM meetings WHERE status != 'done'"
            ).fetchall()
        return [self._to_record(r) for r in rows]

    def mark_reminder_sent(self, issue_number: int, repo: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE meetings SET reminder_sent=1 WHERE issue_number=? AND repo=?",
                (issue_number, repo),
            )

    def mark_start_sent(self, issue_number: int, repo: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE meetings SET start_sent=1 WHERE issue_number=? AND repo=?",
                (issue_number, repo),
            )

    def mark_done(self, issue_number: int, repo: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE meetings SET status='done' WHERE issue_number=? AND repo=?",
                (issue_number, repo),
            )

    def delete(self, issue_number: int, repo: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM meetings WHERE issue_number=? AND repo=?",
                (issue_number, repo),
            )

    def _to_record(self, row: sqlite3.Row) -> MeetingRecord:
        return MeetingRecord(
            issue_number=row["issue_number"],
            repo=row["repo"],
            title=row["title"],
            scheduled_at=datetime.fromisoformat(row["scheduled_at"]),
            reminder_minutes=row["reminder_min"],
            status=row["status"],
            reminder_sent=bool(row["reminder_sent"]),
            start_sent=bool(row["start_sent"]),
        )
