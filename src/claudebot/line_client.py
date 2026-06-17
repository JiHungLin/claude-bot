import logging

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    ShowLoadingAnimationRequest,
    TextMessage,
)

logger = logging.getLogger("claudebot.line_client")

LINE_MAX_MESSAGE_LENGTH = 5000
LINE_MAX_MESSAGES_PER_CALL = 5


def _split_text(text: str, limit: int = LINE_MAX_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


class LineClient:
    def __init__(self, channel_access_token: str):
        self._configuration = Configuration(access_token=channel_access_token)

    def show_loading_animation(self, chat_id: str) -> None:
        """Show typing indicator (1:1 chats only; silently ignored for groups)."""
        try:
            with ApiClient(self._configuration) as api_client:
                MessagingApi(api_client).show_loading_animation(
                    ShowLoadingAnimationRequest(chat_id=chat_id, loading_seconds=60)
                )
        except Exception:
            logger.debug("show_loading_animation failed chat_id=%s", chat_id)

    def reply(self, reply_token: str, text: str, quote_token: str | None = None) -> None:
        msg = TextMessage(text=text)
        if quote_token:
            msg.quote_token = quote_token
        with ApiClient(self._configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=reply_token, messages=[msg])
            )

    def reply_or_push(self, reply_token: str, push_target: str, text: str, quote_token: str | None = None) -> None:
        """Try reply_token first (free); fall back to push if token expired or absent."""
        if not reply_token:
            self.push(push_target, text, quote_token=quote_token)
            return
        chunks = _split_text(text)
        try:
            with ApiClient(self._configuration) as api_client:
                messages = [TextMessage(text=c) for c in chunks[:LINE_MAX_MESSAGES_PER_CALL]]
                if quote_token:
                    messages[0].quote_token = quote_token
                MessagingApi(api_client).reply_message(
                    ReplyMessageRequest(reply_token=reply_token, messages=messages)
                )
            return
        except Exception:
            logger.info("reply_token expired, falling back to push target=%s", push_target)
        self.push(push_target, text, quote_token=quote_token)

    def push(self, target: str, text: str, quote_token: str | None = None) -> None:
        chunks = _split_text(text)[:LINE_MAX_MESSAGES_PER_CALL]
        messages = [TextMessage(text=c) for c in chunks]
        if quote_token:
            messages[0].quote_token = quote_token
        with ApiClient(self._configuration) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(to=target, messages=messages)
            )

    def get_display_name(self, user_id: str, group_id: str | None = None) -> str:
        with ApiClient(self._configuration) as api_client:
            try:
                if group_id:
                    profile = MessagingApi(api_client).get_group_member_profile(group_id, user_id)
                else:
                    profile = MessagingApi(api_client).get_profile(user_id)
                return profile.display_name
            except Exception:
                logger.debug("failed to fetch profile user_id=%s group_id=%s", user_id, group_id)
                return "(unknown)"
