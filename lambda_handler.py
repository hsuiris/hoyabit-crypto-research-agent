"""AWS Lambda Function URL entry point for the HOYA BIT MVP."""

from __future__ import annotations

import base64
import json
import os
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

from src.errors import AgentInputError
from src.ip_allowlist import allowlist_from_env
from src.llm import configured_provider, llm_is_configured
from src.orchestrator import run, run_comparison
from src.run_manager import (RUN_MODE_FORMAL, RUN_MODE_TEST, VALID_RUN_MODES,
                            FormalRunAlreadyExistsError, RunManager, artifact_root)
from src.schemas import ARTIFACT_FILENAMES

# 首頁兩個下拉共用同一份清單（`src/errors.py` 的 `validate_request` 是實際的守門人；
# 這裡只負責不要在畫面上提供一個一定會被拒絕的選項）。
SUPPORTED_COINS = ("BTC", "ETH", "SOL", "BNB", "XRP")

# Lambda 的容器檔案系統只有暫存目錄可寫。輸出仍然一個 run 一個目錄
# （`<root>/runs/{run_id}/`），因此同一個容器連續處理兩個請求時，第二次不會覆寫第一次的產物。
DEFAULT_ARTIFACT_ROOT = "/tmp"


# --------------------------------------------------------------------------------------
# D4 — 執行環境的 AWS SDK 能力
# --------------------------------------------------------------------------------------

# Lambda 受管 runtime 內建的 boto3／botocore 版本常落後於 runtime 的發布時點。若它不支援
# `bedrock-runtime` 的 `Converse`，`src.llm` 會拋出例外、被 Orchestrator 攔下、降級成
# deterministic offline fallback —— 執行仍然成功、六項提交物仍然產出，只是模型路徑沒跑。
#
# 這正是 `docs/COMPETITION_BLOCKERS.md` 的 B2 在雲端的翻版，而且更難發現：本機至少會看到
# `RuntimeError: Amazon Bedrock requires boto3`，雲端卻只會看到一份「成功但已降級」的報告。
# 因此把 SDK 能力做成顯式欄位，而不是等它靜默失敗。
#
# 這個檢測**只回報事實，不改變任何控制流程**：provider 選擇仍由 `src.llm` 決定，降級決策
# 仍由 Orchestrator 負責。它存在的唯一理由是讓雲端的降級可以被區分出來。


def sdk_capability() -> dict:
    """回報執行環境是否具備 `bedrock-runtime` 的 `Converse` 操作。

    刻意只讀 service model，不建立 boto3 client：建 client 會嘗試解析憑證與 endpoint，
    因此本函式沒有網路行為、也不需要任何憑證，可以安全地在冷啟動時執行。

    任何失敗都回報為結構化結果而不拋出例外——這是可觀測性程式碼，不該成為新的失敗來源。
    """
    capability: dict = {"botocore_version": None, "converse_available": False, "detail": None}
    try:
        import botocore
        import botocore.session
    except ImportError as error:
        capability["detail"] = f"botocore unavailable: {error}"
        return capability
    capability["botocore_version"] = getattr(botocore, "__version__", None)
    try:
        model = botocore.session.get_session().get_service_model("bedrock-runtime")
    except Exception as error:  # UnknownServiceError 代表 SDK 太舊而不認得該服務
        capability["detail"] = f"{type(error).__name__}: {error}"
        return capability
    capability["converse_available"] = "Converse" in model.operation_names
    if not capability["converse_available"]:
        capability["detail"] = "bedrock-runtime service model has no Converse operation"
    return capability


SDK_CAPABILITY = sdk_capability()

# 只在 Lambda 執行環境輸出，避免污染本機測試與 CLI 的輸出。CloudWatch Logs 因此在每次
# 冷啟動都會有一行可搜尋的 `[sdk]`，不需要等到分析失敗才回頭查原因。
if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
    print(f"[sdk] {json.dumps(SDK_CAPABILITY, ensure_ascii=False)}")


def _prepare_run(question: str, coins, mode: str):
    """建立這次請求的 run record 與唯一輸出目錄。"""
    manager = RunManager(artifact_root(DEFAULT_ARTIFACT_ROOT))
    record = manager.create_run(question, coins, mode=mode)
    return record, manager.run_directory(record)


def _run_mode(raw) -> str:
    mode = str(raw or RUN_MODE_TEST).strip().lower()
    return mode if mode in VALID_RUN_MODES else RUN_MODE_TEST


def _compare_target(raw, coin: str) -> str:
    """把 `compare_with` 收斂成「要比較的第二個幣種」或空字串（代表單幣分析）。

    空值、只有空白、以及與主要幣種相同時一律回空字串：`run_comparison()` 對相同幣種會直接
    拋 `AgentInputError`，而使用者選到同一個幣種的意思顯然是「不要比較」，不是「拿自己比自己」。
    非法幣種不在這裡擋，交給既有的 `validate_request()` → `AgentInputError` → 400 路徑，
    否則就會有兩個地方各自定義合法幣種。
    """
    value = str(_first_value(raw) or "").strip().upper()
    return "" if not value or value == coin else value


# --------------------------------------------------------------------------------------
# 公開端點 test-only 守衛（E1）
# --------------------------------------------------------------------------------------
#
# 這個 Function URL 的 AuthType 是 NONE：任何取得網址的人都能呼叫。formal 執行對同一
# 「問題＋幣種」只允許一次（見 `src/run_manager.py` 的 formal lock），一旦被匿名觸發就會
# 佔用掉這唯一一次額度，且事後無法回溯是誰送出的。因此公開端點一律只接受 test mode；
# 需要 formal 執行或授權重跑（authorized_rerun／rerun_of／rerun_reason）時，必須改用
# 本機 CLI／app（`src/app.py`），不受本守衛限制，也不在本次修改範圍內。
#
# 這一層必須在建立 RunManager、呼叫 collector 或 LLM 之前擋下請求——先跑一部分才拒絕，
# 等於已經消耗了一次 formal lock 的嘗試，也提早觸發不必要的外部呼叫成本。

# 這些欄位屬於正式重跑的授權契約（見 `RunManager.create_run` 的 rerun_of／rerun_reason／
# authorized_rerun）。公開端點看到任一個帶有實際值，都視為嘗試繞過 test-only 限制。
_RERUN_FLAG_KEYS = ("authorized_rerun", "rerun_of", "rerun_reason", "rerun")
_FALSY_FLAG_VALUES = {"", "false", "0", "no", "off", "none", "null"}


def _first_value(raw):
    """把 JSON（純值）與 form（`parse_qs` 產生的 list）收斂成單一值或 None。"""
    if isinstance(raw, list):
        return raw[0] if raw else None
    return raw


def _flag_present(raw) -> bool:
    """判斷某個 rerun 相關欄位是否帶有「要求重跑」的實際值，而非缺省或明確關閉。"""
    value = _first_value(raw)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in _FALSY_FLAG_VALUES


def _rejection_logged(status: int, reason: str, message: str) -> dict:
    """記錄拒絕原因並回傳對應的 HTTP 回應。

    訊息只包含固定的中文說明或使用者自己送出的值，不包含例外內容、堆疊追蹤、
    AWS 帳號、Function URL 或 token。
    """
    log_run_summary("run-rejected", {"reason": reason})
    return _html(status, f"<meta charset='utf-8'><h1>請求被拒絕</h1><p>{message}</p>")


def _public_mode_guard(values: dict) -> dict | None:
    """公開端點的 test-only 守衛：回傳非 None 時，呼叫端必須立即回傳該回應。

    - 未帶 mode 或 mode=test（任何大小寫）：回傳 None，維持既有行為。
    - mode=formal（任何大小寫）：回傳 403。
    - 帶有 authorized_rerun／rerun_of／rerun_reason／rerun 任一實際值：回傳 403。
    - 其他未知 mode：回傳 400。

    必須在建立 run record（`_prepare_run`）之前呼叫，這樣才能保證 RunManager、
    collector 與 LLM／Bedrock 都不會被觸發——不得先跑一段再把 formal 悄悄降級成 test。
    """
    for key in _RERUN_FLAG_KEYS:
        if _flag_present(values.get(key)):
            return _rejection_logged(
                403, "public_rerun_flag_blocked",
                "公開雲端展示不支援正式重跑（rerun）。此端點只提供 test mode。")

    raw_mode = _first_value(values.get("mode"))
    if raw_mode is None or str(raw_mode).strip() == "":
        return None  # 未帶 mode：維持既有行為（視為 test）

    normalised = str(raw_mode).strip().lower()
    if normalised == RUN_MODE_TEST:
        return None
    if normalised == RUN_MODE_FORMAL:
        return _rejection_logged(
            403, "public_formal_mode_blocked",
            "公開雲端展示只提供 test mode，不支援 formal（正式）執行。"
            "正式執行僅提供受控管道使用。")
    return _rejection_logged(
        400, "public_unknown_mode_blocked",
        f"不支援的 mode：{escape(str(raw_mode)[:100])}（只接受 test）。")


# --------------------------------------------------------------------------------------
# 來源 IP 白名單守衛
# --------------------------------------------------------------------------------------
#
# E1 的 test-only 守衛限制「可以做什麼」，這一層限制「誰可以連進來」。兩者互補，不能互相
# 取代：test-only 仍然允許全世界觸發 test 執行並消耗 Bedrock 配額。
#
# 白名單由 `ALLOWED_SOURCE_IPS` 環境變數提供（`aws/template.yaml` 的 AllowedSourceIps 參數
# 注入）。**未設定時完全不限制**，因此不會影響本機執行與既有測試。
#
# 界線說明在 `src/ip_allowlist.py`：這是應用層過濾，請求仍會叫用 Lambda 一次，但會在路由、
# RunManager、collector 與任何模型呼叫之前回 403。Function URL 沒有 IP 層過濾能力（不支援
# WAF，resource policy 也只有兩個 Lambda 專屬條件鍵），要在網路層阻擋必須改掛 CloudFront。


def _allowlist():
    """每次請求重新讀環境變數，避免出現「模組常數」與「實際環境」兩份真相。

    解析 8 個項目是微秒級成本，對照單次研究執行的數十秒完全可以忽略。
    """
    return allowlist_from_env()


def _source_ip(event: dict) -> str:
    """取出 Lambda 服務填入的來源位址。

    **刻意只讀 `requestContext.http.sourceIp`，不讀 `X-Forwarded-For`。** Function URL 前面
    沒有反向代理，該標頭完全由呼叫端自行填寫，讀它等於讓任何人自稱是白名單裡的位址。
    """
    context = (event or {}).get("requestContext") or {}
    http = context.get("http") or {}
    return str(http.get("sourceIp") or "").strip()


def _source_ip_guard(event: dict):
    """非白名單來源一律回 403；回傳非 None 時，呼叫端必須立即回傳該回應。

    必須是 `handler()` 的第一道檢查——排在路由之後就等於首頁、`/report`、`/download`
    各自需要記得呼叫，漏一個就是一個沒有保護的入口。
    """
    allowlist = _allowlist()
    if not allowlist.enforced:
        return None
    source_ip = _source_ip(event)
    if allowlist.allows(source_ip):
        return None
    # 記錄實際位址：現場要判斷「白名單漏了誰」只能靠這一行，而被拒絕的請求本身不會留下
    # 任何其他痕跡。刻意不記錄請求內容。
    log_run_summary("run-rejected", {
        "reason": "source_ip_not_allowed",
        "source_ip": source_ip or "unknown",
        "allowlist_entries": allowlist.entry_count,
        "allowlist_invalid_entries": list(allowlist.invalid_entries),
    })
    # 回應把觀察到的位址顯示出來（那是呼叫端自己的位址，不是額外資訊揭露），否則現場只會
    # 看到一個沒有線索的 403，而「該加哪個 IP」正是唯一需要知道的事。
    observed = escape(source_ip[:60]) if source_ip else "無法判定"
    return _html(403,
                 "<meta charset='utf-8'><h1>來源位址未經授權</h1>"
                 "<p>本服務只接受白名單內的來源 IP 連線。</p>"
                 f"<p>偵測到的來源位址：<code>{observed}</code></p>"
                 "<p>若這是預期可用的網路，請聯絡本服務的維運者把該位址加入白名單。</p>")


# 冷啟動時把白名單狀態寫進 CloudWatch，理由與 `[sdk]` 那一行相同：部署後要確認「限制真的
# 生效」不該需要先從非白名單網路發一個請求試試看。只輸出數量與無效項目，不輸出網段內容。
if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
    print(f"[allowlist] {json.dumps(allowlist_from_env().describe(), ensure_ascii=False)}")


def _load_llm_credentials() -> bool:
    # Bedrock authenticates with the Lambda execution role / ambient AWS credential chain, never
    # with a provider API key stored in Secrets Manager.  Do not attempt a secret lookup here.
    if configured_provider() == "bedrock":
        return llm_is_configured()
    if llm_is_configured():
        return True
    secret_arn = os.getenv("LLM_SECRET_ARN") or os.getenv("OPENAI_SECRET_ARN")
    if not secret_arn:
        return False
    import boto3
    secret = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
    value = secret.get("SecretString", "")
    try:
        config = json.loads(value)
    except json.JSONDecodeError:
        config = {"OPENAI_API_KEY": value}
    if not isinstance(config, dict):
        return False
    for name in ("LLM_PROVIDER", "GEMINI_API_KEY", "GEMINI_MODEL", "OPENAI_API_KEY", "OPENAI_MODEL"):
        if config.get(name):
            os.environ[name] = str(config[name])
    return llm_is_configured()


def _html(status: int, body: str) -> dict:
    return {"statusCode": status, "headers": {"content-type": "text/html; charset=utf-8"}, "body": body}


# --------------------------------------------------------------------------------------
# 首頁
# --------------------------------------------------------------------------------------
#
# 樣式沿用 `src/cloud_report_view.CSS`，不另寫一份：首頁與結果頁本來就該是同一個介面，
# 各自維護一套樣式最後一定會分岔。該模組已經在部署套件裡，因此重用它不增加封裝大小。
#
# 這裡刻意不引入 `src/app.py` 的首頁 HTML。那一份是為 stdlib server 寫的完整介面（含比較、
# 回測等雲端沒有的路由），整段搬過來會帶進一堆連不到的連結。


def _sdk_note() -> str:
    """執行環境摘要。只顯示版本與布林值，不顯示例外訊息，且仍經 escape。

    放在首頁的理由：部署驗收不必先跑一次完整分析，就能看出模型路徑是否可用 ——
    而「成功但已降級」的報告與正常報告在外觀上一模一樣。
    """
    available = SDK_CAPABILITY["converse_available"]
    state = ("<span class='tag pos'>可用</span>" if available else
             "<span class='tag neg'>不可用（模型路徑會降級為離線推理）</span>")
    return (f"<p class='small muted'>執行環境：botocore "
            f"{escape(str(SDK_CAPABILITY['botocore_version'] or '未知'))}"
            f"　·　Bedrock Converse {state}</p>")


# 首頁專屬樣式。沿用 `cloud_report_view.CSS` 的變數與 `.card`／`.tag`／`.notice`，只補這一頁
# 需要的版面：深色 hero band、能力卡片列、表單雙欄。
#
# 為什麼是深色 hero：上一版整頁都是白卡片配灰底，資訊層級全部一樣重，第一眼沒有落點。
# 評審點開網址後看到的第一個畫面決定他對「這是不是一個做完的產品」的判斷，所以標題區需要
# 明確的視覺重量。深色帶同時讓下方的白色表單卡片自然成為第二個焦點。
#
# 仍然只用 inline CSS：不引入外部字型、CDN 或 JS，否則 Lambda 這一頁就有了對外部資源的依賴，
# 而評審的網路環境不可控。
_HOME_CSS = """
.hero{background:linear-gradient(135deg,#10243a 0%,#173c5c 55%,#1b4f76 100%);
color:#f2f6fa;border:0;border-radius:14px;padding:34px 30px;margin:0 0 20px;
box-shadow:0 10px 28px rgba(16,36,58,.18)}
.hero .eyebrow{font-size:.76rem;letter-spacing:.16em;text-transform:uppercase;
color:#9dc3e0;margin:0 0 10px;font-weight:600}
.hero h1{font-size:2rem;line-height:1.25;margin:0 0 12px;color:#fff;letter-spacing:-.01em}
.hero .lead{color:#cfe0ee;margin:0;max-width:64ch;font-size:1.02rem}
.hero .facts{display:flex;flex-wrap:wrap;gap:10px;margin:20px 0 0;padding:0;list-style:none}
.hero .facts li{background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.18);
border-radius:999px;padding:6px 14px;font-size:.85rem;color:#e6eef6}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:26px 24px;margin:0 0 20px;box-shadow:0 2px 8px rgba(27,31,36,.04)}
.panel h2{font-size:1.12rem;margin:0 0 4px}
.panel .sub{color:var(--muted);font-size:.9rem;margin:0 0 18px}
form.run{display:grid;grid-template-columns:180px 1fr;gap:16px;align-items:end}
form.run .field{display:flex;flex-direction:column;gap:7px;min-width:0}
form.run label{font-weight:600;font-size:.88rem}
form.run select,form.run input[type=text]{width:100%;padding:11px 13px;font:inherit;
color:inherit;background:#fff;border:1px solid #c9d2db;border-radius:9px}
form.run select:hover,form.run input[type=text]:hover{border-color:#9fb0c0}
form.run select:focus,form.run input[type=text]:focus,form.run button:focus-visible{
outline:3px solid #7aa8cc;outline-offset:2px;border-color:#12507f}
form.run .actions{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:14px;align-items:center}
form.run button:disabled{background:#7f95a8;cursor:progress;box-shadow:none}
.progress{display:inline-flex;align-items:center;gap:9px;color:var(--muted);font-size:.9rem}
/* `display:inline-flex` 是作者樣式，會蓋掉瀏覽器預設的 `[hidden]{display:none}`。
   少了這一條，任何加上 hidden 的 .progress 仍然會顯示 —— 實測就是「還沒按就在轉」。 */
.progress[hidden]{display:none}
/* 預設靜止。轉動只在 .running 時發生，因此「還沒點擊」與「正在執行」在視覺上不同：
   一個一直在轉的圖示會讓人以為系統已經在跑，那是錯誤的狀態訊息。 */
.spinner{width:15px;height:15px;border:2px solid #c9d2db;border-top-color:#12507f;
border-radius:50%;display:inline-block;animation:none}
.progress.running .spinner{animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.progress.running .spinner{animation:none}}
form.run button{padding:12px 30px;font:inherit;font-size:1.02rem;font-weight:700;color:#fff;
background:#12507f;border:0;border-radius:9px;cursor:pointer;
box-shadow:0 2px 6px rgba(18,80,127,.28)}
form.run button:hover{background:#0e3f66}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:0}
.steps div{border:1px solid var(--line);border-radius:11px;padding:14px 16px;background:#fbfcfd}
.steps b{display:block;font-size:.92rem;margin:0 0 4px}
.steps span{color:var(--muted);font-size:.85rem;line-height:1.5}
.foot{display:flex;flex-wrap:wrap;gap:10px 20px;align-items:center;
color:var(--muted);font-size:.85rem;margin:0}
@media (max-width:760px){
  .hero{padding:26px 20px}
  .hero h1{font-size:1.6rem}
  form.run{grid-template-columns:1fr}
  .steps{grid-template-columns:1fr}
}
"""


# 送出後的進度提示與重複送出防護。
#
# 為什麼只能做到這個程度：Lambda Function URL 是同步的請求／回應，一次 POST 從送出到回應之間
# 沒有任何可回報進度的通道。要做真正的階段進度需要非同步 job（送出後立刻回 202、前端輪詢狀態），
# 但產物寫在容器的 /tmp 且 reserved concurrency 是 5，輪詢很可能被路由到另一個容器而查不到那個
# job —— 那需要共用儲存（S3 或 DynamoDB），屬於架構變更，不是前端能補的。
#
# 因此這裡只誠實做兩件事：讓使用者知道「請求已送出、正在執行」與「已經過了多久」，並防止重複
# 送出。真正的階段細節在完成後的執行紀錄裡逐項列出。
#
# 寫成模組常數而不是嵌在 f-string 裡：JS 的大括號在 f-string 中會被當成格式化欄位，必須逐個
# 轉義成 {{ }}，那既難讀也容易漏。以常數插入就沒有這個問題。
# 刻意不用外部檔案或 CDN：Function URL 只回傳這一頁，多一個資源就多一個現場可能失敗的環節。
_HOME_JS = """
  (function () {
    var form = document.querySelector('form.run');
    var button = document.getElementById('go');
    var progress = document.getElementById('progress');
    var label = document.getElementById('progress-text');
    if (!form || !button || !progress || !label) { return; }
    form.addEventListener('submit', function () {
      // 停用而不是隱藏：位置不變、畫面不跳動，而且 disabled 的按鈕不會再次送出表單。
      button.disabled = true;
      button.textContent = '分析中…';
      // 轉動由 class 控制，不是由顯示／隱藏控制：送出前圖示是靜止的，代表「尚未開始」。
      progress.className = 'progress running';
      var started = Date.now();
      var tick = function () {
        var seconds = Math.floor((Date.now() - started) / 1000);
        label.textContent = '分析中… 已經過 ' + seconds + ' 秒（採集 → 評分 → 推理 → 稽核）';
      };
      tick();
      setInterval(tick, 1000);
    });
  })();
"""


def _home_page() -> str:
    """公開端點首頁。只呈現輸入面與環境狀態，不執行任何分析。

    測試釘住的字串（`tests/test_cloud_report_traceability.py::HomePagePresentationTests`
    與 `tests/test_final_release_guardrails.py::HomePageTestOnlyTests`）必須逐字保留：
    `test mode`、`test-only`、`name='mode' value='test'`、`name='coin'`、`name='question'`、
    「開始分析」、「執行紀錄」、`Evidence`；且不得出現 formal 選項或 `<select name='mode'>`。
    """
    from src.cloud_report_view import CSS

    coins = "".join(
        f"<option{' selected' if coin == 'ETH' else ''}>{coin}</option>"
        for coin in SUPPORTED_COINS)
    # 第一個選項必須是空值：預設就是單幣分析，比較是使用者主動選擇的加值路徑。
    compare_options = "<option value=''>不比較（單幣分析）</option>" + "".join(
        f"<option>{coin}</option>" for coin in SUPPORTED_COINS)
    return f"""<!doctype html><html lang='zh-Hant-TW'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>HOYA BIT｜加密市場分析 AI Agent</title>
<style>{CSS}{_HOME_CSS}</style></head><body>
<main class='wrap'>
  <header class='hero'>
    <p class='eyebrow'>HOYA BIT · Evidence-first Research Agent</p>
    <h1>加密市場分析 AI Agent</h1>
    <p class='lead'>輸入幣種與研究問題，系統平行採集市場、新聞、鏈上、衍生品、社群與總體經濟六個領域的
    公開資料，對每筆證據計算可信度與問題相關性，再由規則引擎決定立場與信心，產出可逐項回溯的研究報告。</p>
    <ul class='facts'>
      <li>每個結論都能點回 Evidence ID 與原始網址</li>
      <li>立場與信心由 deterministic Python 計算</li>
      <li>單一來源失敗不中斷流程</li>
      <li>三份提交物可直接下載</li>
    </ul>
  </header>

  <section class='panel'>
    <h2>開始一次研究</h2>
    <p class='sub'>選擇標的與題目後送出，系統會在時間預算內完成採集、評分、推論與稽核。</p>
    <div class='notice small' style='margin:0 0 18px'>
      <strong>公開雲端展示僅提供 test mode（test-only demo）</strong>，
      不提供 formal（正式）執行選項；正式執行僅透過受控管道進行。
    </div>
    <form method='post' class='run'>
      <div class='field'>
        <label for='coin'>幣種</label>
        <select id='coin' name='coin'>{coins}</select>
      </div>
      <div class='field'>
        <label for='question'>研究問題</label>
        <input id='question' type='text' name='question'
               value='分析近期市場狀況、主要驅動因素與風險'>
      </div>
      <div class='field'>
        <label for='compare_with'>比較標的（選填）</label>
        <select id='compare_with' name='compare_with'>{compare_options}</select>
      </div>
      <div class='field'>
        <p class='small muted' style='margin:0'>選了第二個幣種就會走雙幣比較：系統分別完成兩份
        完整分析，再產出流動性／風險敞口／市場關注度的並列比較。比較會採集兩個幣種，
        時間大約是單幣的兩倍；留空或選到同一個幣種時一律當成單幣分析。</p>
      </div>
      <input type='hidden' name='mode' value='test'>
      <div class='actions'>
        <button id='go'>開始分析</button>
        <span id='progress' class='progress' aria-live='polite'>
          <span class='spinner' aria-hidden='true'></span>
          <span id='progress-text'>點擊後開始分析</span>
        </span>
      </div>
    </form>
    <p class='small muted' style='margin:16px 0 0'>單次分析需要數十秒：六個領域的採集是平行的，
    但仍要等最慢的來源回應。送出後按鈕會停用，完成時本頁會直接換成研究報告。</p>
    <noscript><p class='small notice' style='margin:12px 0 0'>你的瀏覽器停用了 JavaScript：
    表單仍可正常送出，但不會顯示進度提示，也不會阻止重複點擊。送出後請耐心等待頁面換頁，
    不要重複按下按鈕。</p></noscript>
  </section>

  <script>{_HOME_JS}</script>

  <section class='panel'>
    <h2>你會拿到什麼</h2>
    <p class='sub'>三份提交物，同一份資料的三種檢視方式。</p>
    <div class='steps'>
      <div><b>① 分析報告</b><span>立場、事實、推論、結論、反方證據、限制與推翻條件，
      每個主張都標示 verdict 與信心分量。</span></div>
      <div><b>② 證據清單</b><span>每筆 Evidence 的原始網址、擷取與發布時間、可信度五分量、
      生效上限與問題相關性。</span></div>
      <div><b>③ 執行紀錄</b><span>各階段狀態與耗時、採集結果、時間預算使用、降級原因與
      引用檢核（Citation Gate）。</span></div>
    </div>
  </section>

  <section class='panel'>
    <h2>本次執行環境</h2>
    {_sdk_note()}
    <p class='small muted' style='margin:10px 0 0'>提交物寫在處理該次執行的容器暫存目錄，
    冷啟動或被路由到其他容器後下載連結會失效。這是 Lambda 的固有限制，不是執行失敗；
    分析完成後請立即下載。</p>
  </section>

  <p class='foot'><span>本服務僅供研究與展示用途，不構成投資建議。</span></p>
</main></body></html>"""


# --------------------------------------------------------------------------------------
# 提交物下載
# --------------------------------------------------------------------------------------

# Function URL 的路由在這裡，不在 `src/app.py` 的 `Handler.do_GET`。兩邊各自分派，因此在
# stdlib server 加的路由**不會**自動出現在雲端 —— 下載按鈕本來只在本機有效，而 Demo 跑在
# 雲端，按了沒反應。這一段就是把 `/download` 與 `/artifact` 補到 Lambda 這一側。
#
# 產物寫在容器的 `/tmp`，所以只有處理過該次執行的容器讀得到。這是 Lambda 的固有限制，
# 不是 bug：冷啟動或被路由到別的容器時就沒有可下載的 run，此時回 404 並說明原因，
# 而不是回一個空的 ZIP 讓人以為執行沒有產物。
_LATEST_RUN_DIR: Path | None = None


def _query(event: dict) -> dict:
    return event.get("queryStringParameters") or {}


def _resolve_run_dir(run_id: str = "") -> Path | None:
    if run_id:
        # 只接受單一路徑片段，帶分隔符或 `..` 一律拒絕，不做任何拼接嘗試。
        if "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
            return None
        runs_root = artifact_root(DEFAULT_ARTIFACT_ROOT) / "runs"
        candidate = (runs_root / run_id).resolve()
        if candidate.is_dir() and candidate.is_relative_to(runs_root.resolve()):
            return candidate
        return None
    return _LATEST_RUN_DIR


def _load_artifacts(run_dir: Path) -> dict | None:
    """讀出某個 run 目錄的六份提交物；缺任一份即回 None。

    只讀檔，不重跑分析：`GET /report?run=` 必須是純讀取，否則重新整理頁面就會再打一次
    collector 與 Bedrock，而正式執行的一次性額度也可能因此被消耗。
    """
    loaded: dict = {}
    for key, name in ARTIFACT_FILENAMES.items():
        target = run_dir / name
        if not target.is_file():
            return None
        text = target.read_text(encoding="utf-8")
        loaded[key] = json.loads(text) if name.endswith(".json") else text
    return loaded


def _report_page(run_id: str, artifacts: dict, *, mode: str = "", status: str = "",
                 result: dict | None = None, footer_note: str = "") -> str:
    """把提交物交給純渲染層。此處只負責取值，不做任何判斷或計算。"""
    from src.cloud_report_view import render_report_page

    lifecycle = (artifacts.get("execution_log") or {}).get("run_lifecycle") or {}
    log = artifacts.get("execution_log") or {}
    return render_report_page(
        run_id=run_id,
        mode=mode or log.get("run_mode") or lifecycle.get("run_mode") or RUN_MODE_TEST,
        status=status or log.get("run_status") or lifecycle.get("status") or "",
        result=result if result is not None else _result_from_artifacts(artifacts),
        report=artifacts.get("report") or "",
        evidence=artifacts.get("evidence") or [],
        claims=artifacts.get("claims") or {},
        execution_log=log,
        manifest=artifacts.get("manifest") or {},
        artifact_filenames=tuple(ARTIFACT_FILENAMES.values()),
        footer_note=footer_note,
    )


def _leg_evidence(run_dir: Path, coins) -> dict:
    """讀出比較執行兩腳各自的 `evidence.json`。

    渲染層是純函式，所以檔案 I/O 留在這裡。缺檔時回空清單而不是中斷：那一腳的引用會因此
    顯示成「未知引用」，那是誠實的呈現，比整頁 500 好。
    """
    loaded = {}
    for coin in coins:
        target = run_dir / coin / ARTIFACT_FILENAMES["evidence"]
        try:
            loaded[coin] = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded[coin] = []
    return loaded


def _pair_artifact_names(run_dir: Path) -> tuple:
    """比較執行在 run 根目錄實際產出的可下載檔名（兩腳的六份提交物在各自子目錄）。"""
    from src.app import DOWNLOADABLE_NAMES

    return tuple(name for name in sorted(DOWNLOADABLE_NAMES) if (run_dir / name).is_file())


def _comparison_page(record, payload: dict, run_dir: Path, *, footer_note: str = "") -> str:
    """把比較結果交給純渲染層。此處只負責取值與讀檔，不做任何判斷或計算。"""
    from src.cloud_report_view import render_comparison_page

    return render_comparison_page(
        run_id=record.run_id,
        mode=record.mode,
        status=record.status,
        payload=payload,
        evidence_by_coin=_leg_evidence(run_dir, payload.get("coins") or []),
        artifact_filenames=_pair_artifact_names(run_dir),
        footer_note=footer_note,
    )


def _result_from_artifacts(artifacts: dict) -> dict:
    """`GET /report?run=` 沒有記憶體裡的 result，改從 manifest／log 重建展示所需的最小欄位。

    只取幣種與研究問題這類標識欄位。**不重建 stance**：那是分析結果，若從別處猜一個值出來，
    畫面就會變成第二份真相來源。stance 缺失時，立場區塊直接不顯示。
    """
    manifest = artifacts.get("manifest") or {}
    plan = artifacts.get("research_plan") or {}
    coins = plan.get("coins") or manifest.get("coins") or []
    return {
        "coin": (coins[0] if isinstance(coins, list) and coins else manifest.get("coin") or ""),
        "question": plan.get("question") or manifest.get("question") or "",
    }


def _render_existing_run(event: dict) -> dict:
    """`GET /report?run=<run_id>`：只渲染既有產物，絕不觸發研究管線。"""
    params = _query(event)
    run_id = (params.get("run") or "").strip()
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return _no_run_response()
    artifacts = _load_artifacts(run_dir)
    if artifacts is None:
        return _no_run_response()
    return _html(200, _report_page(
        run_dir.name, artifacts,
        footer_note="本頁由既有提交物渲染，未重新執行任何分析。"))


def _no_run_response() -> dict:
    return _html(404, "<meta charset='utf-8'><h1>沒有可下載的執行結果</h1>"
                      "<p>提交物寫在處理該次執行的容器暫存目錄，只有那個容器讀得到。"
                      "請先在本頁執行一次分析，再回來下載。</p><p><a href='/'>返回首頁</a></p>")


def _download_bundle(event: dict) -> dict:
    """把提交物打包成 ZIP 回傳。scope=required 為命題要求的三份，scope=all 為全部產物。"""
    from src.app import DOWNLOAD_SCOPES, build_artifact_zip

    params = _query(event)
    scope = (params.get("scope") or "required").strip()
    if scope not in DOWNLOAD_SCOPES:
        return _html(400, f"<meta charset='utf-8'><h1>不支援的下載範圍</h1><p>{escape(scope)}</p>")
    run_dir = _resolve_run_dir((params.get("run") or "").strip())
    if run_dir is None:
        return _no_run_response()
    payload, names = build_artifact_zip(run_dir, scope)
    if not names:
        return _no_run_response()
    # Function URL 傳二進位必須 base64 編碼並標示，否則會被當成文字而損毀。
    return {
        "statusCode": 200,
        "headers": {
            "content-type": "application/zip",
            "content-disposition": f'attachment; filename="hoyabit-artifacts-{run_dir.name}.zip"',
        },
        "body": base64.b64encode(payload).decode("ascii"),
        "isBase64Encoded": True,
    }


def _single_artifact(event: dict) -> dict:
    """回傳單一提交物；`download=1` 時加上 Content-Disposition 讓瀏覽器直接存檔。"""
    from src.app import DOWNLOADABLE_NAMES

    params = _query(event)
    name = (params.get("path") or "").strip()
    if name not in DOWNLOADABLE_NAMES:
        return _html(404, "<meta charset='utf-8'><h1>找不到指定輸出檔案</h1>")
    run_dir = _resolve_run_dir((params.get("run") or "").strip())
    if run_dir is None:
        return _no_run_response()
    target = run_dir / name
    if not target.is_file():
        return _no_run_response()
    headers = {"content-type": "application/json; charset=utf-8" if name.endswith(".json")
               else "text/plain; charset=utf-8"}
    if params.get("download") == "1":
        headers["content-disposition"] = f'attachment; filename="{name}"'
    return {"statusCode": 200, "headers": headers, "body": target.read_text(encoding="utf-8")}


def log_run_summary(event_name: str, payload: dict) -> None:
    """輸出一行可搜尋的執行摘要到 CloudWatch。

    沒有這行的話，log 裡只有 Lambda 自己的 START／END／REPORT 與冷啟動的 `[sdk]`，
    無法從 CloudWatch 回溯到特定 `run_id`，也無法在不取得回應內容的情況下判斷模型路徑
    是否降級——而降級的報告看起來與正常的一模一樣。

    刻意**不記錄題目原文**，只記 `question_hash`：log 的保留期比單次請求長得多，
    不該把使用者輸入寫進去。同樣地也不記錄任何 Evidence 內容或憑證。
    """
    print(f"[{event_name}] {json.dumps(payload, ensure_ascii=False)}")


def _run_summary(record, manifest: dict, log: dict) -> dict:
    """組出摘要欄位。只取判斷「這次執行是否可信」所需的最小集合。"""
    stages = {
        name: (info.get("status") or info.get("provider"))
        for name, info in (manifest.get("stage_providers") or {}).items()
    }
    return {
        "run_id": record.run_id,
        "mode": record.mode,
        "status": record.status,
        "question_hash": manifest.get("question_hash"),
        "duration_ms": manifest.get("duration_ms"),
        "citation_gate": (manifest.get("validation") or {}).get("citation_gate_status"),
        "degradation_reasons": log.get("degradation_reasons") or [],
        "stages": stages,
        "converse_available": SDK_CAPABILITY["converse_available"],
    }


def _comparison_summary(record, payload: dict, coins: list) -> dict:
    """比較執行的一行摘要。沿用單幣的欄位，再補上「這是比較執行」與兩腳的引用檢核結果。"""
    manifest = payload.get("manifest") or {}
    summary = _run_summary(record, manifest,
                           {"degradation_reasons": manifest.get("degradation_reasons") or []})
    validation = manifest.get("validation") or {}
    return {**summary, "kind": "comparison", "coins": coins,
            "citation_gate_by_coin": validation.get("citation_gate_status_by_coin") or {},
            "run_status_by_coin": validation.get("run_status_by_coin") or {}}


def _comparison_response(record, output_dir: Path, coin: str, compare_with: str,
                         question: str, content_type: str) -> dict:
    """雙幣比較路徑。兩腳共用同一個 run 目錄與同一組時間預算。

    引擎完全沿用本機既有的 `src.orchestrator.run_comparison()`，這裡只負責接線、設定
    `_LATEST_RUN_DIR` 讓 `/download` 與 `/artifact` 找得到產物，以及渲染結果。
    """
    global _LATEST_RUN_DIR

    try:
        payload = run_comparison(coin, compare_with, question, output_dir,
                                 live=True, use_llm=_load_llm_credentials(), run_record=record)
    except AgentInputError as error:
        # 非法幣種在 `run_comparison()` 的第一行就被 `validate_request()` 擋下，因此走到這裡
        # 代表兩腳都還沒開始採集。
        return _html(400, f"<meta charset='utf-8'><h1>輸入錯誤</h1><p>{escape(str(error))}</p>")
    _LATEST_RUN_DIR = output_dir
    coins = payload.get("coins") or [coin, compare_with]
    log_run_summary("run", _comparison_summary(record, payload, list(coins)))
    if "application/json" in content_type:
        # 欄位風格與單幣 JSON 一致：result ＋ run 中介資料 ＋ 這次 run 根目錄實際產出的提交物。
        # 兩腳各自的 report／evidence／execution_log 在子目錄，因此另外用 evidence_by_coin
        # 把兩份證據清單帶出來，呼叫端不必回頭讀容器裡的檔案。
        artifacts = {
            key: json.loads((output_dir / name).read_text(encoding="utf-8"))
            for key, name in ARTIFACT_FILENAMES.items() if (output_dir / name).is_file()
        }
        return {"statusCode": 200, "headers": {"content-type": "application/json"},
                "body": json.dumps({"result": payload, "run_id": record.run_id,
                                    "run_mode": record.mode, "run_status": record.status,
                                    "artifact_directory": str(output_dir),
                                    "sdk_capability": SDK_CAPABILITY,
                                    "mode": "comparison", "coins": list(coins),
                                    "comparison": payload.get("comparison"),
                                    "evidence_by_coin": _leg_evidence(output_dir, coins),
                                    **artifacts}, ensure_ascii=False)}
    return _html(200, _comparison_page(
        record, payload, output_dir,
        footer_note=f"可用 /download?scope=all&run={record.run_id} 取得兩腳的完整提交物。"))


def handler(event, context):
    global _LATEST_RUN_DIR
    # 來源 IP 白名單必須是第一道檢查：放在路由之後，每條路由都得自己記得呼叫，漏一條就是
    # 一個沒有保護的入口（首頁、/report、/download、/artifact 全部涵蓋在內）。
    blocked = _source_ip_guard(event)
    if blocked is not None:
        return blocked
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = (event.get("rawPath")
            or event.get("requestContext", {}).get("http", {}).get("path", "/") or "/")
    if method == "GET" and path.rstrip("/").endswith("/download"):
        return _download_bundle(event)
    if method == "GET" and path.rstrip("/").endswith("/artifact"):
        return _single_artifact(event)
    if method == "GET" and path.rstrip("/").endswith("/report"):
        return _render_existing_run(event)
    if method == "GET":
        return _html(200, _home_page())
    raw_body = event.get("body", "")
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode("utf-8")
    content_type = event.get("headers", {}).get("content-type", "")
    if "application/json" in content_type:
        values = json.loads(raw_body or "{}")
        coin, question = values.get("coin", "ETH"), values.get("question", "分析近期市場狀況")
    else:
        values = parse_qs(raw_body)
        coin, question = values.get("coin", ["ETH"])[0], values.get("question", ["分析近期市場狀況"])[0]
    # test-only 守衛必須在建立 run record、呼叫 RunManager、collector 或 LLM／Bedrock 之前
    # 執行——這是公開端點（AuthType NONE）唯一的防線，不得偷偷把 formal 降級成 test。
    rejection = _public_mode_guard(values)
    if rejection is not None:
        return rejection
    mode = _run_mode(_first_value(values.get("mode")))
    coin = str(coin or "").strip().upper()
    # 選填的第二個幣種。空值／空白／與主要幣種相同都會收斂成空字串，也就是既有的單幣路徑。
    compare_with = _compare_target(values.get("compare_with"), coin)
    try:
        record, output_dir = _prepare_run(question, [coin, compare_with] if compare_with
                                          else [coin], mode)
    except FormalRunAlreadyExistsError as error:
        # 正式執行被 lock 擋下是預期行為，但仍要留痕：否則現場只會看到 409 而無從得知
        # 是哪一題被擋、以及原本那次正式執行是哪一個 run。
        log_run_summary("run-rejected", {"reason": "formal_run_already_exists", "detail": str(error)})
        return _html(409, f"<meta charset='utf-8'><h1>正式執行已存在</h1><p>{error}</p>")
    if compare_with:
        return _comparison_response(record, output_dir, coin, compare_with, question,
                                    content_type)
    csv_path = Path(__file__).parent / "data" / f"{coin.upper()}.csv"
    try:
        result = run(coin, question, output_dir, live=True, use_llm=_load_llm_credentials(), ohlcv_path=csv_path if csv_path.exists() else None, run_record=record)
    except AgentInputError as error:
        return _html(400, f"<meta charset='utf-8'><h1>輸入錯誤</h1><p>{error}</p>")
    # 讓 `/download` 與 `/artifact` 能找到這次的產物（同一個容器內有效）。
    _LATEST_RUN_DIR = output_dir
    artifacts = {
        key: json.loads((output_dir / name).read_text(encoding="utf-8")) if name.endswith(".json")
        else (output_dir / name).read_text(encoding="utf-8")
        for key, name in ARTIFACT_FILENAMES.items()
    }
    report, log = artifacts["report"], artifacts["execution_log"]
    log_run_summary("run", _run_summary(record, artifacts["manifest"], log))
    if "application/json" in content_type:
        # 六項提交物一次回傳，呼叫端不需要再回頭讀容器裡的檔案。
        # sdk_capability 讓呼叫端能程式化區分「模型成功」與「SDK 太舊而靜默降級」，
        # 不必回頭撈 CloudWatch Logs。
        return {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps({"result": result, "run_id": record.run_id, "run_mode": record.mode, "run_status": record.status, "artifact_directory": str(output_dir), "sdk_capability": SDK_CAPABILITY, **artifacts}, ensure_ascii=False)}
    # 三段式報告頁（E3）。原本這裡是兩個 `<pre>`：report.md 與 execution log 的 raw JSON，
    # 而 evidence.json 在畫面上完全不存在——於是「每個結論都能點回原始來源」在雲端只能靠下載
    # JSON 再人工比對 ID。渲染邏輯放在 `src/cloud_report_view.py` 的純函式，這裡只傳值。
    return _html(200, _report_page(
        record.run_id, artifacts, mode=record.mode, status=record.status, result=result,
        footer_note=f"可用 /report?run={record.run_id} 重新開啟本次結果（不會重跑分析）。"))
