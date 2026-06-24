# ClaudeBot

LINE ↔ Claude Code CLI 橋接服務。接收 LINE 訊息後，在本機觸發 `claude` CLI 執行查詢或操作，執行完成後把回覆推送回 LINE。

**跟 AgentBlackBox 平台架構無關**，是獨立小工具，不經過 Input Gateway / Event Hub。

---

## 功能總覽

| 功能 | 說明 |
|------|------|
| 1:1 對話 | 白名單使用者直接與 Claude 對話 |
| 群組問答 | @mention bot 發問，Claude 查詢 GitHub / 程式碼後回覆 |
| 會議模組 | 安排、記錄、結束會議，自動提醒、建立 Action Item Issues |
| GitHub Issue 通知 | Issue 建立 / 關閉時推播群組 |
| GitHub Discussion 通知 | 公告類 Discussion 建立 / 更新時推播群組 |
| Claude 用量查詢 | 查詢目前 5h / 7d 使用率 |

---

## 使用方式

### 1:1 對話

直接傳訊息即可。新使用者需先傳送邀請碼（`INVITE_KEY`）加入白名單。

### 群組

@mention bot 並輸入問題，mention 可放訊息任意位置：

```
@ABB_Assistant 目前有哪些 open issue？
目前有哪些 open issue？ @ABB_Assistant
請問 @ABB_Assistant 最近的 PR 狀態？
```

#### 內建指令

| 指令 | 說明 |
|------|------|
| `status` | 顯示目前執行中任務數與逾時設定 |
| `usage` | 顯示 Claude 5h / 7d 用量與重置時間 |

### 會議模組

會議模組在群組中使用，需設定 `MEETING_DEFAULT_REPO`。

| 觸發詞 | 動作 |
|--------|------|
| `安排會議 [時間] [目標]` | 建立會議 Issue，排程提醒 |
| `記錄：[內容]` | 在進行中的會議 Issue 新增 Comment |
| `結束會議` | 摘要 + 建立 Action Item Issues + 關閉會議 |
| `會議狀況` | 列出目前排程中 / 進行中的會議 |

**時間格式**：bot 一律以台灣時區（UTC+8）解析時間，例如「週四 14:00」。

**提醒時間**：預設提前 `MEETING_REMINDER_MINUTES` 分鐘推播，建立時可指定，例如「安排會議 週五 14:00 討論 API 設計，提醒 30 分鐘前」。

**狀態流程**：
```
meeting/scheduled → meeting/in-progress → meeting/done
```

會議開始時 bot 自動將 label 切換為 `in-progress` 並推播群組；結束時建立的 Action Item Issues 帶有 `action-item` label 與「來自會議 #N」說明。

**注意**：使用前需在 GitHub repo 手動建立以下 labels：
`meeting/scheduled`、`meeting/in-progress`、`meeting/done`、`action-item`

---

## 安裝與啟動

```bash
cp .env.example .env          # 填入必要欄位
cp allowlist.example.json allowlist.json
.conda/bin/pip install -e .
.conda/bin/python dev.py      # 啟動 FastAPI（http://0.0.0.0:8090），含 --reload
```

另開 terminal 啟動 tunnel：

```bash
ngrok http --url=<your-ngrok-domain> 8090
```

LINE webhook URL：`https://<your-ngrok-domain>/webhook`
GitHub webhook URL：`https://<your-ngrok-domain>/github-webhook`

---

## 設定項

### 必填

| 環境變數 | 說明 |
|----------|------|
| `LINE_CHANNEL_SECRET` | LINE Channel Secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Channel Access Token |
| `BOT_USER_ID` | Bot 的 LINE user ID（啟動後看 log `[BOT_INFO]` 行取得） |

### 選填

| 環境變數 | 預設值 | 說明 |
|----------|--------|------|
| `LINE_GROUP_ID` | — | 目標群組 ID（群組功能必填） |
| `INVITE_KEY` | — | 1:1 白名單邀請碼（不設則關閉邀請） |
| `GITHUB_WEBHOOK_SECRET` | — | GitHub webhook secret（不設則停用 GitHub 通知） |
| `WORKSPACE_DIR` | `/home/mycena/Projects/AgentBlackBoxWorkspace` | Claude 工作目錄 |
| `CLAUDE_BINARY` | `claude` | Claude CLI 執行檔路徑 |
| `CLAUDE_TIMEOUT_SECONDS` | `600` | 單次 Claude 呼叫逾時（秒） |
| `CLAUDE_MAX_CONCURRENT` | `3` | 最大同時執行任務數 |
| `CLAUDE_HEARTBEAT_SECONDS` | `60` | 長時間處理時推播「還在處理中」的間隔（秒） |
| `USAGE_CACHE_TTL_SECONDS` | `120` | Claude 用量快取時間（秒） |
| `MEETING_DEFAULT_REPO` | — | 會議 Issue 預設 repo（格式：`org/repo`，留空停用會議模組） |
| `MEETING_REMINDER_MINUTES` | `15` | 會議提醒預設提前時間（分鐘） |
| `MEETING_DB_PATH` | `meetings.db` | 會議排程本地資料庫路徑 |
| `ALLOWLIST_PATH` | `allowlist.json` | 1:1 白名單檔案路徑 |
| `SESSION_STORE_PATH` | `sessions.json` | Claude session 記憶檔案路徑 |
| `LOG_PATH` | `claudebot.log` | Log 檔案路徑 |
| `MAX_MESSAGE_LENGTH` | `500` | 使用者訊息長度上限（字元） |

### 白名單管理

未授權嘗試會記錄到 log（含顯示名稱與 user_id）。將 user_id 加入 `allowlist.json` 即可授權，不需重啟服務：

```json
{
  "allowed_user_ids": ["Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"]
}
```

---

## GitHub Webhook 設定

在 GitHub repo Settings → Webhooks 新增：

- **Payload URL**：`https://<your-domain>/github-webhook`
- **Content type**：`application/json`
- **Secret**：與 `GITHUB_WEBHOOK_SECRET` 相同
- **Events**：勾選 `Issues` 和 `Discussions`

---

## 技術棧

- Python 3.11+（FastAPI + `line-bot-sdk` v3 + `httpx`）
- SQLite（會議排程）
- 對外曝露（網域 / TLS / Tunnel）由使用者自行處理
