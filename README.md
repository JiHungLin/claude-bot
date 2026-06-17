# ClaudeBot

獨立的 LINE ↔ Claude Code CLI 橋接服務。接收 LINE 訊息後，在本機觸發 `claude` CLI（唯讀工具：讀檔案、查 GitHub），執行完成後把回覆推送回 LINE。

**跟 AgentBlackBox 平台架構無關**，是獨立小工具，不經過 Input Gateway / Event Hub。

## 設計文件

- [docs/architecture.md](docs/architecture.md) — 完整架構、安全邊界、訊息流程、設定項

## 現況

核心程式骨架已完成（`src/claudebot/`），已用真實 `claude` CLI 驗證過 invoke / resume / 權限邊界。尚未做：對外曝露、長時間運行測試。

## 技術棧

- Python（FastAPI + `line-bot-sdk`）
- 對外曝露（網域 / TLS / Tunnel）由使用者自行處理，不在本 repo 範圍內

## 設定

```bash
cp .env.example .env        # 填入 LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN
cp allowlist.example.json allowlist.json   # 一開始留空陣列即可
```

`allowlist.json` 一開始留空，發訊息給 bot 的未授權嘗試會記錄到 `claudebot.log`（含顯示名稱與 user_id），照格式手動加進 `allowed_user_ids` 即可，不用重啟服務。

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
