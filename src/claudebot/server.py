import asyncio
import hashlib
import hmac
import json
import logging

from fastapi import FastAPI, HTTPException, Request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import GroupSource, MessageEvent, TextMessageContent, UserSource

from claudebot.allowlist import add_user, is_allowed
from claudebot.claude_invoker import ALLOWED_TOOLS_GROUP, ALLOWED_TOOLS_READONLY, ClaudeInvoker
from claudebot.claude_usage import fetch_usage, format_usage
from claudebot.config import settings
from claudebot.line_client import LineClient
from claudebot.session_store import SessionStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.FileHandler(settings.log_path), logging.StreamHandler()],
    force=True,
)
logger = logging.getLogger("claudebot.server")

parser = WebhookParser(settings.line_channel_secret)
line_client = LineClient(settings.line_channel_access_token)

# 啟動時印出 bot user ID，方便設定 @mention
try:
    from linebot.v3.messaging import ApiClient, Configuration, MessagingApi
    with ApiClient(Configuration(access_token=settings.line_channel_access_token)) as _api:
        _bot_info = MessagingApi(_api).get_bot_info()
    logger.info("[BOT_INFO] userId=%s displayName=%r", _bot_info.user_id, _bot_info.display_name)
except Exception:
    logger.warning("[BOT_INFO] failed to fetch bot info")
session_store = SessionStore(settings.session_store_path)
invoker = ClaudeInvoker(
    binary=settings.claude_binary,
    workspace_dir=settings.workspace_dir,
    timeout_seconds=settings.claude_timeout_seconds,
)

_user_locks: dict[str, asyncio.Lock] = {}
_claude_semaphore = asyncio.Semaphore(settings.claude_max_concurrent)

app = FastAPI()


def _lock_for(user_id: str) -> asyncio.Lock:
    return _user_locks.setdefault(user_id, asyncio.Lock())


# ── LINE webhook ──────────────────────────────────────────────────────────────

@app.post("/webhook")
async def webhook(request: Request) -> dict:
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")

    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="invalid signature")

    for event in events:
        logger.info("event type=%s source=%s", type(event).__name__, type(event.source).__name__)
        if not isinstance(event, MessageEvent) or not isinstance(event.message, TextMessageContent):
            continue

        source = event.source
        text = event.message.text
        reply_token = event.reply_token
        quote_token = getattr(event.message, "quote_token", None)

        mention = getattr(event.message, "mention", None)
        if isinstance(source, GroupSource):
            _handle_group_event(source, text, reply_token, quote_token, mention)
        elif isinstance(source, UserSource):
            _handle_user_event(source, text, reply_token, quote_token)

    return {"status": "ok"}


def _extract_command(text: str, mention) -> str | None:
    """Return command text with @mention removed, or None if bot not mentioned."""
    mentionees = getattr(mention, "mentionees", None) or [] if mention else []
    bot_mentions = [m for m in mentionees if getattr(m, "user_id", None) == settings.bot_user_id]
    if not bot_mentions:
        return None
    result = text
    for m in sorted(bot_mentions, key=lambda x: x.index, reverse=True):
        result = result[: m.index] + result[m.index + m.length :]
    return result.strip()


def _handle_group_event(
    source: GroupSource, text: str, reply_token: str,
    quote_token: str | None = None, mention=None,
) -> None:
    if settings.line_group_id is None or source.group_id != settings.line_group_id:
        logger.info("[GROUP_DISCOVERED] group_id=%s", source.group_id)
        return

    command_text = _extract_command(text, mention)
    if command_text is None:
        return

    user_id = source.user_id
    if not command_text:
        line_client.reply(reply_token, "請 @提及我並輸入問題，例如：@ABB_Assistant 目前有哪些 PR？", quote_token)
        return
    if command_text.lower() == "status":
        running = settings.claude_max_concurrent - _claude_semaphore._value
        line_client.reply(
            reply_token,
            f"🤖 Bot 狀態\n"
            f"執行中任務：{running} / {settings.claude_max_concurrent}\n"
            f"單任務逾時：{settings.claude_timeout_seconds} 秒",
            quote_token,
        )
        return
    if command_text.lower() == "usage":
        asyncio.create_task(_reply_usage(reply_token, source.group_id, quote_token))
        return
    if command_text.startswith("/"):
        line_client.reply(reply_token, "不支援 slash command，請直接輸入問題。", quote_token)
        return
    if len(command_text) > settings.max_message_length:
        line_client.reply(reply_token, f"訊息過長（上限 {settings.max_message_length} 字），請精簡後再試。", quote_token)
        return
    line_client.reply(reply_token, "處理中...")
    session_key = f"{source.group_id}:{user_id}"
    asyncio.create_task(_process_message(
        user_id, command_text,
        push_target=source.group_id,
        session_key=session_key,
        reply_token="",
        quote_token=quote_token,
        append_system_prompt=settings.group_system_prompt,
        allowed_tools=ALLOWED_TOOLS_GROUP,
    ))


def _handle_user_event(source: UserSource, text: str, reply_token: str, quote_token: str | None = None) -> None:
    user_id = source.user_id
    if not is_allowed(user_id, settings.allowlist_path):
        if settings.invite_key and text.strip() == settings.invite_key:
            add_user(user_id, settings.allowlist_path)
            display_name = line_client.get_display_name(user_id)
            logger.info("[INVITE_ACCEPTED] user_id=%s display_name=%r", user_id, display_name)
            line_client.reply(reply_token, "✅ 已加入白名單，歡迎使用！直接傳訊息就能開始互動。")
        else:
            display_name = line_client.get_display_name(user_id)
            logger.warning(
                "[UNAUTHORIZED] user_id=%s display_name=%r message=%r",
                user_id, display_name, text,
            )
        return

    if text.startswith("/"):
        line_client.reply(reply_token, "不支援 slash command，請直接輸入問題。")
        return
    if len(text) > settings.max_message_length:
        line_client.reply(reply_token, f"訊息過長（上限 {settings.max_message_length} 字），請精簡後再試。")
        return

    line_client.show_loading_animation(user_id)
    asyncio.create_task(_process_message(
        user_id, text, push_target=user_id, session_key=user_id, reply_token=reply_token,
        quote_token=quote_token,
        append_system_prompt=settings.user_system_prompt,
    ))


# ── GitHub webhook ────────────────────────────────────────────────────────────

@app.post("/github-webhook")
async def github_webhook(request: Request) -> dict:
    if not settings.github_webhook_secret:
        raise HTTPException(status_code=501, detail="GitHub webhook not configured")

    body_bytes = await request.body()
    sig_header = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(sig_header, expected):
        raise HTTPException(status_code=400, detail="invalid signature")

    if request.headers.get("X-GitHub-Event", "") != "issues":
        return {"status": "ignored"}

    payload = json.loads(body_bytes.decode("utf-8"))
    action = payload.get("action", "")
    if action not in ("opened", "closed"):
        return {"status": "ignored"}

    asyncio.create_task(_handle_github_issue(payload, action))
    return {"status": "ok"}


# ── async workers ─────────────────────────────────────────────────────────────

async def _reply_usage(reply_token: str, push_target: str, quote_token: str | None) -> None:
    info = await fetch_usage()
    if info is None:
        line_client.reply_or_push(reply_token, push_target, "⚠️ 無法取得用量資訊，token 可能已過期。", quote_token=quote_token)
    else:
        line_client.reply_or_push(reply_token, push_target, format_usage(info), quote_token=quote_token)


async def _heartbeat(push_target: str) -> None:
    await asyncio.sleep(settings.claude_heartbeat_seconds)
    line_client.push(push_target, "⏳ 還在處理中，請耐心等候...")


async def _process_message(
    user_id: str,
    text: str,
    *,
    push_target: str,
    session_key: str,
    reply_token: str,
    quote_token: str | None = None,
    append_system_prompt: str | None = None,
    allowed_tools: str | None = None,
) -> None:
    async with _lock_for(session_key):
        try:
            await asyncio.wait_for(_claude_semaphore.acquire(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("semaphore timeout session_key=%s", session_key)
            line_client.push(push_target, "⚠️ 系統目前忙碌，請稍後再試。")
            return

        heartbeat = asyncio.create_task(_heartbeat(push_target))
        try:
            existing_session_id = session_store.get(session_key)
            result = await invoker.run(
                text, existing_session_id,
                base_system_prompt=settings.base_system_prompt,
                append_system_prompt=append_system_prompt,
                allowed_tools=allowed_tools,
            )
        except TimeoutError:
            logger.exception("claude timed out session_key=%s", session_key)
            line_client.push(push_target, "處理逾時，請稍後再試。")
            return
        except Exception:
            logger.exception("claude failed session_key=%s", session_key)
            line_client.push(push_target, "處理失敗，請稍後再試。")
            return
        finally:
            heartbeat.cancel()
            _claude_semaphore.release()

        session_store.set(session_key, result.session_id)
        if result.is_error:
            logger.warning("claude returned error session_key=%s text=%r", session_key, result.text)
            err_lower = (result.text or "").lower()
            if any(k in err_lower for k in ("rate limit", "quota", "usage limit", "too many")):
                msg = "⚠️ Claude 用量已達上限，請稍後再試或等待額度重置。"
            else:
                msg = f"❌ {result.text or '處理時發生錯誤，請稍後再試。'}"
            line_client.reply_or_push(reply_token, push_target, msg, quote_token=quote_token)
        else:
            line_client.reply_or_push(reply_token, push_target, result.text or "(無回應內容)", quote_token=quote_token)


async def _handle_github_issue(payload: dict, action: str) -> None:
    if not settings.line_group_id:
        logger.warning("LINE_GROUP_ID not configured, skipping GitHub issue notification")
        return

    issue = payload.get("issue", {})
    repo = payload.get("repository", {})
    action_zh = "建立" if action == "opened" else "已關閉"

    prompt = (
        f"請把以下 GitHub issue 資訊整理成一段簡短的中文 LINE 群組公告（不超過 200 字）。"
        f"要包含：事件（{action_zh}）、issue 標題、連結、以及 body 的重點摘要（若有）。\n\n"
        f"Repo: {repo.get('full_name', '')}\n"
        f"Issue #{issue.get('number', '')}: {issue.get('title', '')}\n"
        f"作者: {issue.get('user', {}).get('login', '')}\n"
        f"狀態: {action_zh}\n"
        f"URL: {issue.get('html_url', '')}\n"
        f"Body:\n{(issue.get('body') or '（無說明）')[:1000]}"
    )

    try:
        result = await invoker.run(
            prompt, existing_session_id=None,
            persist_session=False,
            base_system_prompt=settings.base_system_prompt,
        )
        line_client.push(settings.line_group_id, result.text or "(無法生成公告)")
    except Exception:
        logger.exception("failed to handle github issue event")
        fallback = (
            f"[GitHub] Issue #{issue.get('number')} {action_zh}\n"
            f"{issue.get('html_url', '')}"
        )
        line_client.push(settings.line_group_id, fallback)
