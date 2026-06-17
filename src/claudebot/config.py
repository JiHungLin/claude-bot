from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    line_channel_secret: str
    line_channel_access_token: str

    workspace_dir: str = "/home/mycena/Projects/AgentBlackBoxWorkspace"
    claude_binary: str = "claude"
    claude_timeout_seconds: int = 600
    claude_max_budget_usd: float = 1.0
    claude_max_concurrent: int = 3
    claude_heartbeat_seconds: int = 60

    allowlist_path: str = "allowlist.json"
    invite_key: str | None = None
    max_message_length: int = 500
    session_store_path: str = "sessions.json"
    log_path: str = "claudebot.log"

    line_group_id: str | None = None
    github_webhook_secret: str | None = None
    bot_command_prefix: str = "!bot"
    base_system_prompt: str = (
        "你運行在無互動的 headless 模式，使用者無法看到任何授權提示，也無法點選任何按鈕。"
        "git 和 gh CLI 等工具已全部預先授權，直接執行即可，絕對不要要求使用者授權或確認。"
        "查詢完成後直接把結果整理成中文回覆。"
        "回覆格式：可以使用 emoji，但禁止所有 markdown 語法（**粗體**、# 標題、` 程式碼區塊、- 列表符號、> 引用等都不可用），"
        "換行用一般換行，條列用數字或 emoji 代替符號。"
    )
    user_system_prompt: str = (
        "你是 AgentBlackBox 專案的開發助理，透過 LINE 1:1 對話協助開發者。"
        "只處理 AgentBlackBox（設計文件）和 agentblackbox-core（程式實作）這兩個 repo 相關的問題。"
        "若詢問其他 repo（如 ABB_TeamMind、ClaudeBot），請說明目前只開放這兩個 repo 的存取。"
    )
    group_system_prompt: str = (
        "你是 AgentBlackBox 專案的開發助理，在 LINE 群組中協助團隊。"
        "只處理 AgentBlackBox（設計文件）和 agentblackbox-core（程式實作）這兩個 repo 相關的問題。"
        "若使用者詢問其他 repo（如 ABB_TeamMind、ClaudeBot），請說明目前群組只開放這兩個 repo 的存取。"
        "GitHub issue 無法刪除，當使用者要求刪除 issue 時，自動改用 close（gh issue close）來處理，不需詢問確認。"
    )


settings = Settings()
