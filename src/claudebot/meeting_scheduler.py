import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from claudebot.line_client import LineClient
from claudebot.meeting_store import MeetingRecord, MeetingStore

logger = logging.getLogger("claudebot.meeting_scheduler")

_TZ_TAIPEI = timezone(timedelta(hours=8))
_TIME_RE = re.compile(r"\*\*時間\*\*\s*[:：]\s*(\S+)")
_REMINDER_RE = re.compile(r"\*\*提醒\*\*\s*[:：]\s*(\d+)")


def parse_scheduled_at(body: str) -> datetime | None:
    m = _TIME_RE.search(body)
    if not m:
        return None
    try:
        dt = datetime.fromisoformat(m.group(1))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ_TAIPEI)
        return dt
    except ValueError:
        return None


def parse_reminder_minutes(body: str, default: int) -> int:
    m = _REMINDER_RE.search(body)
    if m:
        try:
            return max(1, int(m.group(1)))
        except ValueError:
            pass
    return default


async def _fetch_issue(issue_number: int, repo: str) -> dict | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "issue", "view", str(issue_number),
            "--repo", repo,
            "--json", "state,labels,body,title,number,url",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        return json.loads(stdout.decode())
    except Exception:
        logger.exception("failed to fetch issue #%d repo=%s", issue_number, repo)
        return None


async def _run_gh(*args: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        if proc.returncode != 0:
            logger.warning("gh failed args=%s stderr=%s", args, stderr.decode()[:200])
        return proc.returncode == 0
    except Exception:
        logger.exception("gh error args=%s", args)
        return False


class MeetingScheduler:
    def __init__(
        self,
        store: MeetingStore,
        line_client: LineClient,
        group_id: str | None,
        default_reminder_minutes: int,
    ) -> None:
        self._store = store
        self._line = line_client
        self._group_id = group_id
        self._default_reminder = default_reminder_minutes
        self._tasks: dict[tuple[int, str], list[asyncio.Task]] = {}

    async def start(self) -> None:
        pending = self._store.get_pending()
        for m in pending:
            self._reschedule(m)
        logger.info("meeting scheduler started, %d pending meetings loaded", len(pending))

    async def sync_webhook(
        self,
        issue_number: int,
        repo: str,
        action: str,
        labels: list[str],
        body: str,
        title: str,
    ) -> None:
        key = (issue_number, repo)

        # Meeting ended or moved past scheduling
        is_done = (
            action in ("closed", "deleted")
            or "meeting/done" in labels
            or "meeting/in-progress" in labels
        )
        if is_done:
            self._cancel_tasks(key)
            self._store.mark_done(issue_number, repo)
            logger.info("meeting #%d marked done (action=%s labels=%s)", issue_number, action, labels)
            return

        # All meeting labels removed
        if not any(l.startswith("meeting/") for l in labels):
            self._cancel_tasks(key)
            self._store.delete(issue_number, repo)
            logger.info("meeting #%d removed (no meeting labels)", issue_number)
            return

        if "meeting/scheduled" not in labels:
            return

        scheduled_at = parse_scheduled_at(body)
        if scheduled_at is None:
            logger.info("meeting #%d: no parseable time in body, skipping", issue_number)
            return

        reminder_minutes = parse_reminder_minutes(body, self._default_reminder)
        existing = self._store.get(issue_number, repo)
        time_changed = existing is not None and existing.scheduled_at != scheduled_at

        record = MeetingRecord(
            issue_number=issue_number,
            repo=repo,
            title=title,
            scheduled_at=scheduled_at,
            reminder_minutes=reminder_minutes,
            status="scheduled",
            reminder_sent=False if time_changed else (existing.reminder_sent if existing else False),
            start_sent=False if time_changed else (existing.start_sent if existing else False),
        )
        self._store.upsert(record)
        self._reschedule(record)
        logger.info(
            "meeting #%d upserted scheduled_at=%s reminder=%dmin",
            issue_number, scheduled_at.isoformat(), reminder_minutes,
        )

    def _reschedule(self, record: MeetingRecord) -> None:
        key = (record.issue_number, record.repo)
        self._cancel_tasks(key)

        now = datetime.now(tz=timezone.utc)
        target = record.scheduled_at.astimezone(timezone.utc)
        tasks = []

        if not record.reminder_sent:
            fire_at = target - timedelta(minutes=record.reminder_minutes)
            if fire_at > now:
                tasks.append(asyncio.create_task(
                    self._safe_fire(record.issue_number, record.repo, record.scheduled_at, "reminder"),
                    name=f"meeting-reminder-{record.issue_number}",
                ))

        if not record.start_sent:
            if target > now:
                tasks.append(asyncio.create_task(
                    self._safe_fire(record.issue_number, record.repo, record.scheduled_at, "start"),
                    name=f"meeting-start-{record.issue_number}",
                ))

        if tasks:
            self._tasks[key] = tasks

    def _cancel_tasks(self, key: tuple[int, str]) -> None:
        for t in self._tasks.pop(key, []):
            t.cancel()

    async def _safe_fire(self, *args, **kwargs) -> None:
        try:
            await self._fire(*args, **kwargs)
        except Exception:
            logger.exception("meeting fire task failed args=%s", args)

    async def _fire(
        self,
        issue_number: int,
        repo: str,
        scheduled_at: datetime,
        kind: str,
    ) -> None:
        record = self._store.get(issue_number, repo)
        if record is None:
            return

        now = datetime.now(tz=timezone.utc)
        target = scheduled_at.astimezone(timezone.utc)
        fire_at = (target - timedelta(minutes=record.reminder_minutes)) if kind == "reminder" else target
        delay = (fire_at - now).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        # Verify issue state before firing
        issue = await _fetch_issue(issue_number, repo)
        if issue is None:
            logger.warning("meeting #%d: issue not found, skipping %s", issue_number, kind)
            return

        if issue.get("state") == "closed":
            self._store.mark_done(issue_number, repo)
            return

        labels = [l["name"] for l in issue.get("labels", [])]
        if "meeting/scheduled" not in labels:
            logger.info("meeting #%d: no longer scheduled, skipping %s", issue_number, kind)
            return

        # Verify time hasn't changed (> 2 min drift = stale task)
        body_time = parse_scheduled_at(issue.get("body", "") or "")
        if body_time is not None:
            delta = abs((body_time.astimezone(timezone.utc) - target).total_seconds())
            if delta > 120:
                logger.info("meeting #%d: time changed, skipping stale %s", issue_number, kind)
                return

        if not self._group_id:
            logger.warning("group_id not configured, cannot push meeting notification")
            return

        title = issue.get("title", f"會議 #{issue_number}")
        url = issue.get("url", "")

        if kind == "reminder":
            self._line.push(
                self._group_id,
                f"⏰ {record.reminder_minutes} 分鐘後有會議：{title}\n{url}",
            )
            self._store.mark_reminder_sent(issue_number, repo)
            logger.info("meeting #%d: reminder sent", issue_number)
        else:
            await _run_gh(
                "gh", "issue", "edit", str(issue_number),
                "--repo", repo,
                "--remove-label", "meeting/scheduled",
                "--add-label", "meeting/in-progress",
            )
            self._line.push(
                self._group_id,
                f"🚀 會議開始：{title}\n{url}",
            )
            self._store.mark_start_sent(issue_number, repo)
            logger.info("meeting #%d: start notification sent", issue_number)
