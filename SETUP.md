# 匯入與啟動指南

這是一份可直接複製使用的完整專案。**沒有 Git 歷史、沒有金鑰、沒有編譯快取。**

## 30 秒啟動

```bash
python -m unittest discover -s tests    # 應為 488 passed
python -m src.app                       # http://127.0.0.1:8000
```

需要 Python 3.10 以上。**不需要 pip install** —— 本專案只使用標準函式庫。

沒有設定 LLM 金鑰也能完整運作：系統會使用確定性離線推理，報告、圖表、回測、證據追溯全部正常，
只有敘事文字改由規則產生，執行記錄會標為 `offline_fallback`。

## 啟用 LLM（選用）

```bash
cp .env.example .env
```

填入 `GEMINI_API_KEY`（或 `OPENAI_API_KEY` 並設 `LLM_PROVIDER=openai`）。

**配額提醒**：Gemini 免費方案每日 20 次呼叫，每次分析用掉 3 次（推理、消息面摘要、Critic 稽核），
因此每天約只能跑 6 次完整分析。開發前端時建議用離線模式。

## 這份打包與原始倉庫的差異

| 項目 | 原始倉庫 | 這份 |
|---|---|---|
| 專案根目錄 | 巢狀在 `agent團隊/`（中文目錄名） | **展平為根目錄、全 ASCII 路徑** |
| Agent 設定 | `.agents/`（markdown 角色定義） | 轉換為 `.kiro/steering/`，原始檔一併保留於 `.agents/` |
| `.env` | 含真實金鑰 | **已排除**，僅保留 `.env.example` |
| `.git/` | 有 | 已排除 |
| `__pycache__`、`outputs*` | 有 | 已排除 |

中文目錄名是原倉庫最可能造成工具讀取問題的地方（Git 全程將其轉義為
`agent\345\234\230\351\232\212`），因此這份打包改用 ASCII 路徑。

## `.kiro/steering/` 內容

| 檔案 | 用途 |
|---|---|
| `product.md` | 產品定位、五條核心設計原則、明確的非目標 |
| `tech.md` | 零第三方相依約束、常用指令、LLM 設定、修改時的注意事項 |
| `structure.md` | 目錄結構、各層職責邊界、資料契約、測試慣例 |
| `team-roles.md` | 原專案的多角色分工（由 `.agents/` 轉換） |
| `architecture-review.md` | 架構圖繪製指引（由架構師 skill 轉換） |

> 這些檔案是依 Kiro 的 steering 慣例（`.kiro/steering/*.md`）放置的。若你的 Kiro 版本
> 使用不同的目錄或檔名慣例，直接搬移即可 —— 內容是純 markdown，沒有任何工具專屬語法。
> 原始的 `.agents/` 與 `AGENTS.md` 也一併保留，其他讀 `AGENTS.md` 慣例的工具可直接使用。

## 主要入口

- `src/app.py` — 網頁與所有頁面渲染
- `src/orchestrator.py` — 三階段管線的核心
- `docs/project-report.md` — 完整專案報告（含 32 條限制總表）
- `docs/demo-script.md` — 5 分鐘 Demo 腳本

## 已知限制

完整清單見 `docs/project-report.md` 第 8 節。最需要先知道的三項：

1. 每次分析消耗 3 次 LLM 呼叫，免費配額每日僅約 6 次
2. Critic 稽核的真實 API 往返尚未驗證（單元測試已覆蓋解析、調整與降級）
3. 回測樣本數僅 1–3 筆／幣，不足以證明策略有效
