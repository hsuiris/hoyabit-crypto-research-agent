"""驗證一次雲端 live smoke 的回應，並可選擇性地把六項提交物寫成 fixture。

用途是 D8 的驗收：判斷模型路徑是否真的跑了、六項提交物是否齊備、manifest 的 SHA-256
是否與內容相符。刻意寫成獨立腳本而不是 inline heredoc，因為回應內容含中文與大量括號，
在 shell heredoc 中容易被展開或截斷。

用法：
    python3 scripts/verify_live_smoke.py <response.json> [--write-fixture <dir>]

`--write-fixture` 只在 analyst 與 critic 都成功時才會寫出——降級的執行不得被存成
live-success，否則等於把 fallback 宣稱成模型成功。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# manifest 收錄的五個檔案（manifest 自身不列入 files，因此重複產生時內容穩定）。
JSON_ARTIFACTS = {
    "evidence.json": "evidence",
    "execution_log.json": "execution_log",
    "research_plan.json": "research_plan",
    "claims.json": "claims",
}


def artifact_bodies(payload: dict) -> dict[str, str]:
    """重建各提交物的原始位元內容，用於比對 manifest 的 SHA-256。

    JSON 檔的序列化方式必須與 LocalArtifactStore.write_json() 一致
    （ensure_ascii=False、indent=2、結尾換行），否則 hash 不會相符。
    """
    bodies = {"report.md": payload["report"]}
    for name, key in JSON_ARTIFACTS.items():
        bodies[name] = json.dumps(payload[key], ensure_ascii=False, indent=2) + "\n"
    return bodies


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("response", type=Path)
    parser.add_argument("--write-fixture", type=Path, default=None)
    args = parser.parse_args()

    payload = json.loads(args.response.read_text(encoding="utf-8"))
    manifest = payload["manifest"]
    log = payload["execution_log"]
    stages = manifest.get("stage_providers") or {}

    print("=== Run ===")
    for key in ("run_id", "run_mode", "run_status"):
        print(f"  {key:18s} {payload.get(key)}")
    print(f"  {'question_hash':18s} {manifest.get('question_hash')}")
    print(f"  {'duration_ms':18s} {manifest.get('duration_ms')}")
    print(f"  {'sdk_capability':18s} {json.dumps(payload.get('sdk_capability'), ensure_ascii=False)}")

    print("\n=== 模型階段（B2 的解除條件：analyst 與 critic 都必須是 success）===")
    ok_model = True
    for stage in ("planner", "analyst", "critic", "claims"):
        info = stages.get(stage) or {}
        provider = info.get("provider")
        status = info.get("status")
        print(f"  {stage:9s} provider={str(provider):15s} model={str(info.get('model')):24s} status={status}")
        if stage in ("analyst", "critic"):
            # 只有這兩個階段的 success 是 B2 的解除條件。planner 沒有 status 欄位；
            # claims 依設計一律由 deterministic 規則計分，provider 為模型不代表分數由模型決定。
            if provider != "bedrock" or status != "success":
                ok_model = False

    print("\n=== 六項提交物 ===")
    bodies = artifact_bodies(payload)
    missing = [k for k in ("report", "evidence", "execution_log", "research_plan", "claims", "manifest")
               if not payload.get(k)]
    for name, body in sorted(bodies.items()):
        print(f"  OK      {name:22s} {len(body.encode('utf-8')):>8} bytes")
    print(f"  OK      {'manifest.json':22s} {len(json.dumps(manifest, ensure_ascii=False)):>8} chars")
    if missing:
        print(f"  MISSING {missing}")

    print("\n=== manifest SHA-256 ===")
    bad = 0
    for entry in manifest["files"]:
        body = bodies.get(entry["path"])
        if body is None:
            print(f"  SKIP     {entry['path']}")
            continue
        actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
        match = actual == entry["sha256"]
        bad += not match
        print(f"  {'MATCH   ' if match else 'MISMATCH'} {entry['path']:22s} {entry['sha256'][:16]}...")

    validation = manifest.get("validation") or {}
    gate = validation.get("citation_gate_status")
    print("\n=== Citation Gate ===")
    print(f"  status   {gate}")
    print(f"  errors   {validation.get('citation_gate_error_count')}")
    print(f"  warnings {validation.get('citation_gate_warning_count')}")
    print(f"  semantic {validation.get('semantic_finding_count')}")
    checks = validation.get("citation_gate_checks") or {}
    non_pass = {k: v for k, v in checks.items() if v != "pass"}
    print(f"  非 pass 的 check: {non_pass or '無'}")

    print("\n=== Collector ===")
    for step in log["steps"]:
        if step.get("name") == "collect_evidence":
            details = step.get("details") or []
            good = [d for d in details if d.endswith(":success")]
            print(f"  {len(good)}/{len(details)} success")
            for d in details:
                if not d.endswith(":success"):
                    print(f"    fallback: {d}")

    print(f"\n  degradation_reasons: {log.get('degradation_reasons')}")

    gate_ok = gate in ("PASS", "PASS_WITH_WARNINGS") and not validation.get("citation_gate_error_count")
    verdict_ok = ok_model and not bad and not missing and gate_ok
    print("\n=== 判定 ===")
    print(f"  模型路徑（analyst+critic success）: {'PASS' if ok_model else 'FAIL'}")
    print(f"  manifest hash 全相符             : {'PASS' if not bad else 'FAIL'}")
    print(f"  六項提交物齊備                   : {'PASS' if not missing else 'FAIL'}")
    print(f"  Citation Gate                    : {'PASS' if gate_ok else 'FAIL'}")
    print(f"  -> B2 解除條件: {'滿足' if verdict_ok else '不滿足'}")

    if args.write_fixture:
        if not verdict_ok:
            print("\n拒絕寫出 fixture：驗收未全部通過。降級的執行不得被存成 live-success。")
            return 1
        target = args.write_fixture
        target.mkdir(parents=True, exist_ok=True)
        for name, body in bodies.items():
            (target / name).write_text(body, encoding="utf-8")
        (target / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n已寫出六項提交物到 {target}")
        # 落地後再從磁碟重算一次，確認寫出的檔案本身可被獨立驗證。
        recheck = 0
        for entry in manifest["files"]:
            path = target / entry["path"]
            if not path.is_file():
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                recheck += 1
        print(f"落地後重新驗證：{'全部相符' if not recheck else f'{recheck} 個不符'}")
        return 1 if recheck else 0

    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(main())
