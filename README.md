# ClaudeBot

獨立的 LINE ↔ Claude Code CLI 橋接服務。接收 LINE 訊息後，在本機觸發 `claude` CLI（唯讀工具：讀檔案、查 GitHub），執行完成後把回覆推送回 LINE。

**跟 AgentBlackBox 平台架構無關**，是獨立小工具，不經過 Input Gateway / Event Hub。

## 設計文件

- [docs/architecture.md](docs/architecture.md) — 完整架構、安全邊界、訊息流程、設定項

## 使用方式

**1:1 對話**
直接傳訊息即可，bot 會回覆。新使用者需先傳送邀請碼加入白名單。

**群組**
@mention bot 並輸入問題，mention 可放訊息任意位置：

```
@ABB_Assistant 目前有哪些 open issue？
目前有哪些 open issue？ @ABB_Assistant
請問 @ABB_Assistant 最近的 PR 狀態？
```

內建指令（@mention 後接指令名稱）：

| 指令 | 說明 |
|------|------|
| `status` | 顯示目前執行中任務數與逾時設定 |

## 技術棧

- Python（FastAPI + `line-bot-sdk`）
- 對外曝露（網域 / TLS / Tunnel）由使用者自行處理，不在本 repo 範圍內

## 設定

```bash
cp .env.example .env        # 填入必要欄位
cp allowlist.example.json allowlist.json
```

取得 `BOT_USER_ID`：啟動 server 後看 log 的 `[BOT_INFO]` 行，格式為 `userId=Uxxxxxxxxx`。

`allowlist.json` 一開始留空，未授權嘗試會記錄到 `claudebot.log`（含顯示名稱與 user_id），照格式加進 `allowed_user_ids` 即可，不用重啟服務。

## 安裝與啟動

```bash
.conda/bin/pip install -e .
.conda/bin/python dev.py     # 啟動 FastAPI（http://0.0.0.0:8000/webhook），含 --reload
```

另開一個 terminal 啟動 ngrok tunnel：

```bash
ngrok http --url=haematocryal-cora-uncarnivorously.ngrok-free.dev 8000
```

LINE webhook URL：`https://haematocryal-cora-uncarnivorously.ngrok-free.dev/webhook`
