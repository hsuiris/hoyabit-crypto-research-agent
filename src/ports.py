"""邊界契約（ports）：模型呼叫與產物輸出的唯一入口。

這個模組只定義結構型別（Protocol），不含任何實作、不 import 專案內其他模組，
因此可以被任何一層安全引用而不會產生循環相依。

兩條邊界規則（T0.5 凍結，後續 AWS 整合不得繞過）：

1. **模型呼叫只能通過 `LLMClient`**。Planner、Analyst、Critic 等推理角色不得直接 import
   boto3、OpenAI 或 Gemini SDK，也不得自行組 HTTP 請求。實作見 `src/llm.py` 的
   `ExistingLLMClient`（包裝現有 Gemini／OpenAI／Bedrock adapter）與 `OfflineLLMClient`；
   之後要加 `BedrockLLMClient` 只需新增一個實作，呼叫端不用改。
2. **產物輸出只能通過 `ArtifactStore`**。Orchestrator、報告產出與 Evidence Registry 不得硬編
   輸出路徑（例如 tmp 輸出目錄、runs 目錄），也不得直接呼叫 S3 SDK。實作見 `src/artifact_store.py` 的
   `LocalArtifactStore`；之後的 `S3ArtifactStore` 只要滿足同一組方法即可替換。

`LLMClient` 只負責產生 JSON，不負責決定 confidence，也不得改寫原始 Evidence ——
confidence 與 hard cap 一律由 deterministic Python 程式計算。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """單一模型呼叫介面；所有參數為 keyword-only，方便新增欄位而不破壞既有呼叫端。"""

    def generate_json(
        self,
        *,
        prompt: str,
        schema: dict,
        schema_name: str,
        timeout_seconds: float,
    ) -> dict:
        """回傳符合 `schema` 必要欄位的 JSON 物件；失敗時拋出例外交由呼叫端降級。"""
        ...


@runtime_checkable
class ArtifactStore(Protocol):
    """產物寫入介面；每個 write_* 回傳可追溯的 URI（本地為 file://，S3 為 s3://）。"""

    def write_json(self, relative_path: str, data: object) -> str:
        ...

    def write_text(self, relative_path: str, content: str) -> str:
        ...

    def write_bytes(self, relative_path: str, content: bytes) -> str:
        ...

    def finalize_manifest(self) -> dict:
        """回傳本次執行寫出的所有檔案清單，含位元組數與 SHA-256。"""
        ...
