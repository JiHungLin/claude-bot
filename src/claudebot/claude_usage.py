import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from claudebot.config import settings

logger = logging.getLogger("claudebot.claude_usage")

_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_TOKEN_MIN_TTL_MS = 5 * 60 * 1000  # 5 分鐘

_cache: "UsageInfo | None" = None
_cache_ts: float = 0.0


@dataclass
class UsageInfo:
    five_hour_pct: float
    seven_day_pct: float
    resets_at: datetime | None
    extra_used_cents: int | None
    extra_limit_cents: int | None
    fetched_at: datetime = None  # type: ignore[assignment]


def _load_token() -> str | None:
    try:
        creds = json.loads(_CREDENTIALS_PATH.read_text(encoding="utf-8"))
        oauth = creds.get("claudeAiOauth", {})
        token = oauth.get("accessToken")
        expires_at_ms = oauth.get("expiresAt", 0)
        if not token:
            return None
        now_ms = time.time() * 1000
        if expires_at_ms and (expires_at_ms - now_ms) < _TOKEN_MIN_TTL_MS:
            logger.warning("claude oauth token expires soon or already expired")
            return None
        return token
    except Exception:
        logger.debug("failed to read claude credentials")
        return None


async def fetch_usage() -> UsageInfo | None:
    global _cache, _cache_ts
    if _cache is not None and (time.monotonic() - _cache_ts) < settings.usage_cache_ttl_seconds:
        logger.debug("usage cache hit (age=%.0fs)", time.monotonic() - _cache_ts)
        return _cache

    token = _load_token()
    if not token:
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                _USAGE_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": "oauth-2025-04-20",
                },
            )
        if resp.status_code != 200:
            logger.warning("usage API returned %d", resp.status_code)
            return None
        data = resp.json()
    except Exception:
        logger.exception("failed to fetch claude usage")
        return None

    five_h = data.get("five_hour", {})
    seven_d = data.get("seven_day", {})
    extra = data.get("extra_usage", {})

    resets_at = None
    resets_str = five_h.get("resets_at") or seven_d.get("resets_at")
    if resets_str:
        try:
            resets_at = datetime.fromisoformat(resets_str).astimezone()
        except Exception:
            pass

    result = UsageInfo(
        five_hour_pct=float(five_h.get("utilization") or 0),
        seven_day_pct=float(seven_d.get("utilization") or 0),
        resets_at=resets_at,
        extra_used_cents=extra.get("used_credits"),
        extra_limit_cents=extra.get("monthly_limit"),
        fetched_at=datetime.now().astimezone(),
    )
    _cache = result
    _cache_ts = time.monotonic()
    return result


def format_usage(u: UsageInfo) -> str:
    reset_str = u.resets_at.strftime("%m/%d %H:%M") if u.resets_at else "—"
    lines = [
        "📊 Claude 用量",
        f"Current Session（5h）：{u.five_hour_pct:.0f}%  重置 {reset_str}",
        f"本週（7d）：{u.seven_day_pct:.0f}%",
    ]
    if u.extra_used_cents is not None and u.extra_limit_cents:
        used = u.extra_used_cents / 100
        limit = u.extra_limit_cents / 100
        balance = limit - used
        pct = used / limit * 100 if limit else 0
        if balance > 0:
            lines.append(f"額外用量：${used:.2f}/${limit:.0f}（{pct:.0f}%）餘 ${balance:.2f}")
        else:
            lines.append("額外用量：已耗盡")
    if u.fetched_at:
        next_update = u.fetched_at + timedelta(seconds=settings.usage_cache_ttl_seconds)
        lines.append(f"資料時間 {u.fetched_at.strftime('%H:%M')}・下次更新 {next_update.strftime('%H:%M')}")
    return "\n".join(lines)
