# ClaudeBot 架構設計

## 目的

接收 LINE 訊息 → 在本機觸發 `claude` CLI（可讀檔案 / 查 GitHub）→ 把回覆推送回該 LINE 使用者。獨立於 AgentBlackBox 平台之外，不經過 Input Gateway / Event Hub。

## 整體流程

```
LINE Platform
   │ HTTPS POST /webhook（帶 X-Line-Signature）
   ▼
Webhook Server（常駐 process，Python / FastAPI）
   ├─ 驗證 X-Line-Signature（HMAC-SHA256，channel secret）
   ├─ allowlist 檢查 user_id → 不在名單就記錄到 log 檔，不回覆、不觸發 claude
   ├─ 用 reply_token 立即回「處理中…」（30 秒內必須用掉）
   ├─ 查 session store：此 user_id 有沒有對應的 session_id？
   │     無 → claude -p "<text>" --session-id <new-uuid> ...
   │     有 → claude -p "<text>" --resume <stored-uuid> ...
   │            （subprocess，cwd 限定在 AgentBlackBoxWorkspace）
   ▼
claude CLI 執行完成，stdout 為 JSON（--output-format json）
   ▼
Webhook Server 解析結果文字
   ├─ 若第一次：把 claude 回傳的 session_id 寫入 session store
   ├─ 若文字超過 LINE 單則訊息長度限制：分段
   └─ 用 Push Message API（channel access token）推送正式答案給 user_id
```

## 已確認的設計決策

| 決策點 | 結論 |
|--------|------|
| 回覆機制 | 先用 `reply_token` 回「處理中」，完成後用 Push Message API 補正式答案（不受 30 秒限制） |
| 觸發權限 | Allowlist 限制特定 LINE `user_id`（目前僅本人） |
| 工具範圍 | 唯讀（`Read` / `Grep` / `Glob` + `gh` 唯讀查詢），不開放 `Write` / `Edit` / 任意 `Bash` |
| 對話延續性 | 保留 session（`--session-id` / `--resume`），需要 `user_id ↔ session_id` 持久化對應 |
| 檔案存取範圍 | 限定在 `/home/mycena/Projects/AgentBlackBoxWorkspace`（含 AgentBlackBox、ABB_TeamMind、agentblackbox-core、ClaudeBot 本身） |
| 未授權嘗試的可見性 | 不在 allowlist 的 user_id 只記錄到 log 檔（不回覆對方、不推播），由使用者自行查看後手動加入 allowlist |
| 技術棧 | Python（FastAPI + `line-bot-sdk`） |
| 對外曝露（網域/TLS/Tunnel） | 使用者自行處理，不在本 repo 範圍內 |

## claude CLI 呼叫方式

實際旗標已用 `claude -p --help`（v2.1.178）核對：

**首次對話：**
```bash
claude -p "<使用者訊息>" \
  --session-id <新產生的 uuid> \
  --output-format json \
  --tools "Read,Grep,Glob,Bash" \
  --allowedTools "Read" "Grep" "Glob" \
    "Bash(gh issue list:*)" "Bash(gh issue view:*)" \
    "Bash(gh pr list:*)" "Bash(gh pr view:*)" "Bash(gh pr diff:*)" \
    "Bash(gh repo view:*)" "Bash(gh search:*)"
```
（執行時 `cwd` 設為 `/home/mycena/Projects/AgentBlackBoxWorkspace`）

**後續對話（同一 user_id）：**
```bash
claude -p "<使用者訊息>" \
  --resume <已儲存的 uuid> \
  --output-format json \
  --tools "Read,Grep,Glob,Bash" \
  --allowedTools ...（同上）
```

### 安全邊界的分層設計

1. **`--tools "Read,Grep,Glob,Bash"`**：從根本上不載入 `Write` / `Edit` / `NotebookEdit` / `WebFetch` 等工具，模型連看都看不到這些工具存在。
2. **`--allowedTools`**：在已載入的工具中，預先核准 `Read` / `Grep` / `Glob`（無條件）與 `Bash` 的特定 `gh` 唯讀子指令（白名單式 pattern，例如 `Bash(gh issue view:*)`）。
3. **cwd 限制**：`claude` 預設把檔案工具的存取範圍限定在 cwd 及其子目錄，不另外加 `--add-dir`，所以唯讀範圍就是整個 AgentBlackBoxWorkspace。
4. **Headless 無 TTY**：`-p` 模式下沒有互動視窗，任何不在 allowedTools 名單內的工具呼叫（例如裸 `Bash` 指令、`gh issue create`、`gh pr merge`）無法跳出確認對話框，會直接被拒絕，不會卡住或繞過限制。
5. **不使用** `--dangerously-skip-permissions` / `--allow-dangerously-skip-permissions`：這會整個跳過權限檢查，等於 1–2 點的限制全部失效，因此本服務明確不用。
6. **建議加上** `--max-budget-usd <N>`：替每次呼叫設一個花費上限，避免單次任務意外跑出超長/超貴的呼叫鏈（細節數字留到實作時再定）。

> `gh` 唯讀子指令清單目前列出的是常用查詢（`issue/pr list/view/diff`、`repo view`、`search`），之後若有新需求再補；刻意不含 `gh api`（可能被用來發送 POST/DELETE）。

## 未授權嘗試的記錄

allowlist 檢查不過時，Webhook Server 呼叫 LINE Get Profile API（`GET /v2/bot/profile/{userId}`）取得顯示名稱，跟 user_id、時間戳一起寫進 log（一般 log 檔即可，不需要獨立檔案），方便直接複製 user_id 貼進 allowlist 設定：

```
2026-06-16T15:32:10+08:00 [UNAUTHORIZED] user_id=U4af... display_name="王小明" message="..."
```

allowlist 設定改完即生效（每次請求都重新讀取 allowlist 檔，不需要重啟服務）。

## 元件拆解

| 元件 | 職責 |
|------|------|
| Webhook Server | 接收 LINE webhook、驗證簽章、allowlist 檢查、立即 reply | 
| Session Store | `user_id → session_id` 持久化對應（JSON 檔即可，使用人數極少） |
| Claude Invoker | 組裝指令、以 subprocess 執行 `claude`、逾時控制、解析 JSON 輸出 |
| LINE Client | 包裝 `line-bot-sdk` 的 reply / push 呼叫，處理訊息長度分段 |

## 訊息流程時序

```
t=0s   LINE 收到使用者訊息 → POST webhook
t≈0.1s Webhook Server 驗證簽章 + allowlist 通過 → reply_token 回「處理中…」
t=0.1s~N分鐘  claude CLI 執行（讀檔案/查 GitHub）
t=N    claude 完成 → 解析 stdout → Push Message API 送出正式答案
```

## 待實作時再確認的細節

- 逾時秒數（claude 執行多久算超時、超時要不要 push 一則錯誤訊息）
- 同一 user_id 同時收到多則訊息時的併發控制（建議：per-user 鎖，序列化處理）
- LINE 單則文字訊息長度上限（約 5000 字），超過時的分段規則
- Push Message 的額度/方案限制（依 LINE 開發者帳號方案而定，需使用者自行確認）
- Session Store 的過期/清理機制（要不要訂一個 session 閒置多久後視為過期）
- 日誌記錄（至少記錄 user_id、時間、是否成功、耗時，方便追蹤這個會讀檔案/GitHub 的工具被怎麼用）

## 目錄結構（預定）

```
ClaudeBot/
  README.md
  docs/
    architecture.md          ← 本文件
  src/
    claudebot/
      server.py              ← FastAPI app + webhook endpoint
      line_client.py         ← reply / push 包裝
      allowlist.py
      session_store.py
      claude_invoker.py      ← subprocess 組裝與執行
      config.py              ← 環境變數設定
  pyproject.toml
  .env.example
  .gitignore
```
