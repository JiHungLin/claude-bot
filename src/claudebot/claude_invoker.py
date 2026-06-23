import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("claudebot.claude_invoker")

TOOLS = "Read,Grep,Glob,Bash"

_BASE_ALLOWED = [
    "Read",
    "Grep",
    "Glob",
    # gh CLI 唯讀
    "Bash(gh issue list:*)",
    "Bash(gh issue view:*)",
    "Bash(gh pr list:*)",
    "Bash(gh pr view:*)",
    "Bash(gh pr diff:*)",
    "Bash(gh repo view:*)",
    "Bash(gh search:*)",
    "Bash(gh api repos:*)",
    # git 唯讀
    "Bash(git log:*)",
    "Bash(git diff:*)",
    "Bash(git status:*)",
    "Bash(git show:*)",
    "Bash(git branch:*)",
    "Bash(git remote:*)",
    "Bash(git ls-files:*)",
    "Bash(git grep:*)",
    "Bash(git -C:*)",
]

ALLOWED_TOOLS_READONLY = ",".join(_BASE_ALLOWED)

ALLOWED_TOOLS_GROUP = ",".join(
    _BASE_ALLOWED + [
        # 群組專用寫入（issue 操作）
        "Bash(gh issue create:*)",
        "Bash(gh issue comment:*)",
        "Bash(gh issue close:*)",
        "Bash(gh issue edit:*)",
    ]
)


@dataclass
class ClaudeResult:
    text: str
    session_id: str
    is_error: bool


class ClaudeInvoker:
    def __init__(self, binary: str, workspace_dir: str, timeout_seconds: int):
        self._binary = binary
        self._workspace_dir = workspace_dir
        self._timeout_seconds = timeout_seconds

    async def run(
        self,
        message: str,
        existing_session_id: Optional[str],
        *,
        persist_session: bool = True,
        append_system_prompt: Optional[str] = None,
        base_system_prompt: Optional[str] = None,
        allowed_tools: Optional[str] = None,
    ) -> ClaudeResult:
        if not persist_session:
            session_args = ["--no-session-persistence"]
        elif existing_session_id:
            session_args = ["--resume", existing_session_id]
        else:
            session_args = ["--session-id", str(uuid.uuid4())]

        system_prompt = base_system_prompt or ""
        if append_system_prompt:
            system_prompt = (system_prompt + "\n\n" + append_system_prompt).strip()

        cmd = [
            self._binary,
            "-p", message,
            "--output-format", "json",
            "--tools", TOOLS,
            "--allowedTools", allowed_tools or ALLOWED_TOOLS_READONLY,
            *session_args,
            *(["--append-system-prompt", system_prompt] if system_prompt else []),
        ]

        logger.info(
            "invoking claude message_len=%d cwd=%s resume=%s persist=%s",
            len(message), self._workspace_dir, bool(existing_session_id), persist_session,
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self._workspace_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout_seconds
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"claude CLI exceeded {self._timeout_seconds}s timeout") from None

        try:
            payload = json.loads(stdout.decode(errors="replace"))
        except json.JSONDecodeError:
            raise RuntimeError(
                f"claude CLI exited {proc.returncode} with non-JSON output: "
                f"{stderr.decode(errors='replace')[:500]}"
            ) from None
        return ClaudeResult(
            text=payload.get("result", ""),
            session_id=payload.get("session_id") or existing_session_id or "",
            is_error=bool(payload.get("is_error", False)),
        )
