"""產物輸出邊界的本地實作。

所有提交檔（report.md、evidence.json、execution_log.json 等）都應該經由 `ArtifactStore`
寫出，呼叫端只給相對路徑，實際落點由 store 決定。這樣之後換成 `S3ArtifactStore` 時
Orchestrator 與報告產出不需要改動，也不會有人再硬編 tmp 輸出目錄。

每次寫入都會記錄位元組數與 SHA-256，`finalize_manifest()` 因此能提供可稽核的產物清單。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def _normalise(relative_path: str) -> str:
    """把呼叫端給的相對路徑正規化成 POSIX 形式，並擋掉絕對路徑與 `..` 逃逸。"""
    raw = str(relative_path).strip().replace("\\", "/")
    candidate = PurePosixPath(raw)
    if not raw or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"Invalid artifact path: {relative_path!r}")
    return str(candidate)


class LocalArtifactStore:
    """寫入本地檔案系統的 ArtifactStore；`prefix` 通常是 RunContext.output_prefix。"""

    scheme = "file"

    def __init__(self, root: Path | str, prefix: str = "") -> None:
        self.root = Path(root)
        self.prefix = str(prefix).strip("/")
        self.base_path = self.root / self.prefix if self.prefix else self.root
        self._entries: dict[str, dict] = {}

    @classmethod
    def for_run(cls, context, root: Path | str) -> "LocalArtifactStore":
        """以 RunContext 的 output_prefix 建立 store，確保不同 run_id 各自獨立。"""
        return cls(root, context.output_prefix)

    @property
    def entries(self) -> list[dict]:
        return [self._entries[key] for key in sorted(self._entries)]

    def resolve(self, relative_path: str) -> Path:
        return self.base_path / _normalise(relative_path)

    def write_bytes(self, relative_path: str, content: bytes) -> str:
        key = _normalise(relative_path)
        target = self.base_path / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        uri = target.resolve().as_uri()
        self._entries[key] = {
            "path": key,
            "uri": uri,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "written_at": datetime.now(timezone.utc).isoformat(),
        }
        return uri

    def write_text(self, relative_path: str, content: str) -> str:
        return self.write_bytes(relative_path, content.encode("utf-8"))

    def write_json(self, relative_path: str, data: object) -> str:
        return self.write_text(relative_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def finalize_manifest(self) -> dict:
        """回傳並落地產物清單。manifest 自身不列入 files，因此重複呼叫結果穩定。"""
        manifest = {
            "artifact_root": str(self.base_path.resolve()),
            "prefix": self.prefix,
            "scheme": self.scheme,
            "artifact_count": len(self._entries),
            "total_bytes": sum(entry["bytes"] for entry in self._entries.values()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "files": self.entries,
        }
        target = self.base_path / "manifest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest
