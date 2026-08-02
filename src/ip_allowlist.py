"""公開 Lambda Function URL 的來源 IP 白名單（deterministic）。

## 為什麼需要這個模組

`aws/template.yaml` 的 Function URL 是 `AuthType: NONE`：任何取得網址的人都能呼叫。E1 已把
公開端點限制成 test-only，但那只擋「執行性質」，不擋「誰可以連進來」。要把可連入的來源收斂
到指定的幾個 IP，就需要在請求進入任何處理流程之前先比對來源位址。

## 為什麼在應用層做，而不是在 AWS 網路層做

Lambda Function URL **沒有** IP 層的過濾能力：

- Function URL 的 resource-based policy 只支援 `lambda:FunctionUrlAuthType` 與
  `lambda:InvokedViaFunctionUrl` 兩個條件鍵（見
  https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html ），
  `AuthType: NONE` 時 Lambda 根本不做 IAM 認證，因此把 `aws:SourceIp` 寫進 policy 並不會
  產生可依賴的效果 —— 那會變成「看起來有防護」的假象，比沒有更危險。
- Function URL 也不能直接掛 AWS WAF；要做網路層 IP 阻擋必須在前面加 CloudFront（或改用
  API Gateway）並綁 WAF IPSet。那是新的基礎設施、新的網址與新的成本。

因此這一層是**應用層**過濾：請求仍然會叫用 Lambda（產生一次極短的 invocation），但會在
路由、`RunManager`、collector 與任何模型呼叫之前就回 403。要的是「不讓非白名單來源使用
服務、不消耗 Bedrock 配額」，這一層做得到；「連封包都不要進到 AWS」則做不到，需要
CloudFront + WAF。這個界線寫在這裡，避免日後被誤解成網路層防護。

## 只信任 `requestContext.http.sourceIp`

Function URL 前面沒有反向代理，`X-Forwarded-For` 完全由呼叫端自行填寫，任何人都能偽造成
白名單裡的位址。因此本模組刻意只接受由 Lambda 服務填入的 `sourceIp`，不讀任何標頭。

## 未設定時不限制

白名單為空字串（或未設定環境變數）時一律放行。這是刻意的預設：本機測試、既有測試與
`python -m src.app` 都不會突然被自己的守衛擋住，行為與加入本模組之前完全相同。

反過來，「有設定但全部無效」（例如手打錯字）會**一律拒絕**而不是放行。理由是誠實優先：
一個打錯的白名單不該安靜地變成「對全世界開放」。`aws/deploy.sh` 會在部署前就驗證格式並
以退出碼 2 擋下，因此這條 fail-closed 路徑在正常流程中不會被走到。
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence, Tuple

# Lambda 環境變數名稱。由 `aws/template.yaml` 的 AllowedSourceIps 參數注入。
ENV_VAR = "ALLOWED_SOURCE_IPS"

# 逗號、分號、空白與換行都當成分隔符：貼一整段清單、每行一個、或用逗號串起來都能接受。
# 現場調整白名單時不該因為分隔符寫法而失敗。
_SEPARATORS = (",", ";", "\n", "\r", "\t", " ")

# 允許輸入時包住 IPv6 的角括號（`[2001:db8::1]`）與成對引號。
_STRIP_CHARS = "[]'\""


@dataclass(frozen=True)
class Allowlist:
    """一份已解析的白名單。

    `networks` 是逐筆項目轉成的網段（單一位址會變成 /32 或 /128）。
    `invalid_entries` 保存無法解析的原始字串，供記錄用 —— 不能安靜丟掉，否則打錯字時
    白名單會少一筆而沒有人知道。
    `raw_entry_count` 是原始項目數（含無效者），用來區分「沒有設定」與「設定了但都無效」。
    """

    networks: Tuple[object, ...] = ()
    invalid_entries: Tuple[str, ...] = ()
    raw_entry_count: int = 0

    @property
    def enforced(self) -> bool:
        """是否要執行過濾。有寫任何東西就算有設定，即使全部無效。"""
        return self.raw_entry_count > 0

    @property
    def entry_count(self) -> int:
        """實際生效的項目數。"""
        return len(self.networks)

    def allows(self, source_ip: str) -> bool:
        """判斷某個來源位址是否可以連入。

        未設定白名單時一律放行；有設定時，位址必須落在任一網段內。位址無法解析
        （空字串、被截斷、非 IP 字串）時一律拒絕：不能因為讀不到來源就放行。
        """
        if not self.enforced:
            return True
        address = parse_address(source_ip)
        if address is None:
            return False
        return any(address in network for network in self.networks)

    def describe(self) -> dict:
        """給執行紀錄用的摘要。刻意不列出網段內容，只回報數量與無效項目。"""
        return {
            "enforced": self.enforced,
            "entry_count": self.entry_count,
            "invalid_entries": list(self.invalid_entries),
        }


# 未設定任何白名單時的共用實例（放行一切）。
OPEN = Allowlist()


def parse_address(source_ip: str):
    """把來源位址字串轉成 `ipaddress` 物件；無法解析時回 None（不拋例外）。

    這是安全路徑上的解析，任何格式問題都應該收斂成「拒絕」，而不是讓例外冒出去變成 500。
    """
    text = str(source_ip or "").strip().strip(_STRIP_CHARS)
    if not text:
        return None
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def split_entries(raw: str) -> Tuple[str, ...]:
    """把一段白名單文字切成項目清單。"""
    text = str(raw or "")
    for separator in _SEPARATORS:
        text = text.replace(separator, ",")
    return tuple(item.strip().strip(_STRIP_CHARS) for item in text.split(",") if item.strip())


def parse_allowlist(raw: str) -> Allowlist:
    """解析白名單文字。接受單一 IP（IPv4／IPv6）與 CIDR 網段。

    `strict=False` 讓 `60.250.15.5/24` 這種「主機位址帶遮罩」的寫法也能接受並收斂成
    `60.250.15.0/24`，而不是整筆作廢——現場手寫白名單時這是很常見的寫法。
    """
    networks = []
    invalid = []
    entries = split_entries(raw)
    for entry in entries:
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            invalid.append(entry)
    return Allowlist(
        networks=tuple(networks),
        invalid_entries=tuple(invalid),
        raw_entry_count=len(entries),
    )


def allowlist_from_env(env: Optional[Mapping[str, str]] = None) -> Allowlist:
    """從環境變數讀出白名單。未設定或為空字串時回傳 `OPEN`（放行一切）。"""
    source = os.environ if env is None else env
    raw = source.get(ENV_VAR, "")
    if not str(raw or "").strip():
        return OPEN
    return parse_allowlist(raw)


def is_allowed(source_ip: str, allowlist: Allowlist) -> bool:
    """便捷函式：等同 `allowlist.allows(source_ip)`。"""
    return allowlist.allows(source_ip)


def format_entries(entries: Iterable[str]) -> str:
    """把項目清單正規化成單行、以逗號分隔的字串，供部署腳本與文件顯示使用。"""
    seen = []
    for entry in entries:
        for item in split_entries(entry):
            if item not in seen:
                seen.append(item)
    return ",".join(seen)


def validate_entries(entries: Sequence[str]) -> Tuple[str, ...]:
    """回傳無法解析的項目。給部署前檢查使用：有輸出就代表白名單寫錯了。"""
    return parse_allowlist(format_entries(entries)).invalid_entries
