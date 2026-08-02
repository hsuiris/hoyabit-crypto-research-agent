"""E3 — 雲端三報告與逐 Claim 溯源的測試。

背景（實測缺陷）：Function URL 的結果頁原本是兩個 `<pre>` —— `report.md` 與 execution log 的
raw JSON。`evidence.json` 在畫面上完全不存在，所以「每個結論都能點回原始來源」在雲端只能靠
下載 JSON 再人工比對 Evidence ID。而本機 `src/app.py` 早就有完整介面，兩邊分歧。

這組測試釘住四類行為：

1. 三份報告都在同一頁、都可閱讀，且提交物仍可下載。
2. Claim 引用的每個 Evidence ID 都有真的錨點；不存在的 ID **不得**渲染成看起來可用的連結。
3. 外部來源的 title／author／url 是不可信輸入，不得產生 `<script>` 或 `javascript:` 連結
   —— 公開端點的 AuthType 是 NONE。
4. `GET /report?run=` 只讀既有產物，不得觸發 collector、Bedrock 或 RunManager。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lambda_handler as lambda_module
from src.cloud_report_view import (NOT_PROVIDED, UNKNOWN_EVIDENCE_MARK, link,
                                   render_report_page, safe_url)
from src.orchestrator import run
from src.schemas import ARTIFACT_FILENAMES


def _get(path: str, query: dict | None = None) -> dict:
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": "GET", "path": path}},
        "queryStringParameters": query or {},
    }


def _load(run_dir: Path) -> dict:
    return {
        key: (json.loads((run_dir / name).read_text(encoding="utf-8"))
              if name.endswith(".json") else (run_dir / name).read_text(encoding="utf-8"))
        for key, name in ARTIFACT_FILENAMES.items()
    }


def _render(artifacts: dict, **overrides) -> str:
    payload = {
        "run_id": "RUN-TEST",
        "mode": "test",
        "status": "COMPLETED",
        "result": {"coin": "ETH", "question": "測試題目"},
        "report": artifacts.get("report", ""),
        "evidence": artifacts.get("evidence", []),
        "claims": artifacts.get("claims", {}),
        "execution_log": artifacts.get("execution_log", {}),
        "manifest": artifacts.get("manifest", {}),
        "artifact_filenames": tuple(ARTIFACT_FILENAMES.values()),
    }
    payload.update(overrides)
    return render_report_page(**payload)


class _RealRunFixture(unittest.TestCase):
    """共用一次離線執行的真實產物；不連網、不呼叫 LLM。"""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls._directory.name)
        cls.run_dir = cls.root / "runs" / "RUN-E3-TEST"
        run("ETH", "E3 雲端報告測試", cls.run_dir, live=False, use_llm=False)
        cls.artifacts = _load(cls.run_dir)
        cls.html = _render(cls.artifacts)

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()


class ThreeReportSectionTests(_RealRunFixture):
    """三份報告與下載入口都必須出現在同一頁。"""

    def test_all_three_report_sections_are_present(self):
        for anchor, heading in (("id='final-report'", "分析報告"),
                                ("id='evidence-list'", "證據清單"),
                                ("id='execution-log'", "執行紀錄")):
            self.assertIn(anchor, self.html, anchor)
            self.assertIn(heading, self.html, heading)

    def test_navigation_links_point_at_the_three_sections(self):
        for target in ("href='#final-report'", "href='#evidence-list'", "href='#execution-log'"):
            self.assertIn(target, self.html, target)

    def test_every_submission_artifact_is_downloadable(self):
        self.assertIn("scope=required", self.html)
        self.assertIn("scope=all", self.html)
        for filename in ARTIFACT_FILENAMES.values():
            self.assertIn(f"path={filename}", self.html, filename)

    def test_page_declares_traditional_chinese(self):
        self.assertIn("lang='zh-Hant-TW'", self.html)

    def test_raw_json_dump_is_not_the_main_view(self):
        """execution log 仍可查原文，但必須先有表格；不能只丟一坨 JSON 當主畫面。"""
        self.assertIn("<th>階段</th>", self.html)
        self.assertLess(self.html.index("<th>階段</th>"),
                        self.html.index("查看 execution_log.json 原文"))


class ClaimTraceabilityTests(_RealRunFixture):
    """Final Report 的每個引用都要能落到真的 Evidence 卡片。"""

    def test_every_cited_evidence_id_has_an_anchor_target(self):
        claims = self.artifacts["claims"]["claims"]
        self.assertTrue(claims, "測試前提失敗：本次執行沒有任何 Claim")
        cited = set()
        for claim in claims:
            cited.update(claim.get("supporting_evidence_ids") or [])
            cited.update(claim.get("contradicting_evidence_ids") or [])
            for fact in claim.get("facts") or []:
                cited.update(fact.get("evidence_ids") or [])
        self.assertTrue(cited, "測試前提失敗：沒有任何被引用的 Evidence")
        for evidence_id in cited:
            self.assertIn(f"href='#evidence-{evidence_id}'", self.html, evidence_id)
            self.assertIn(f"id='evidence-{evidence_id}'", self.html, evidence_id)

    def test_claim_cards_expose_verdict_confidence_and_limiters(self):
        claim = self.artifacts["claims"]["claims"][0]
        self.assertIn(f"id='claim-{claim['claim_id']}'", self.html)
        self.assertIn("判定：", self.html)
        self.assertIn("信心分量與生效上限", self.html)
        for limiter in claim["confidence"]["limiters"]:
            self.assertIn(limiter, self.html, limiter)

    def test_fact_inference_conclusion_are_labelled_separately(self):
        for label in ("事實（Fact）", "推論（Inference）", "結論（Conclusion）"):
            self.assertIn(label, self.html, label)

    def test_unknown_evidence_id_is_not_rendered_as_a_working_link(self):
        artifacts = dict(self.artifacts)
        claims = json.loads(json.dumps(artifacts["claims"]))
        claims["claims"][0]["supporting_evidence_ids"] = ["EV-DOES-NOT-EXIST"]
        html = _render({**artifacts, "claims": claims})

        self.assertIn("EV-DOES-NOT-EXIST", html)
        self.assertIn(UNKNOWN_EVIDENCE_MARK, html)
        self.assertNotIn("href='#evidence-EV-DOES-NOT-EXIST'", html)

    def test_source_item_ids_resolve_to_item_rows(self):
        evidence = json.loads(json.dumps(self.artifacts["evidence"]))
        evidence[0]["source_items"] = [{
            "source_item_id": "EV-001-ITEM-01", "title": "子項標題",
            "url": "https://example.com/a", "publisher": "Example",
            "author": "作者甲", "published_at": "2026-08-01T00:00:00+00:00",
        }]
        evidence[0]["semantic_assessment"] = {
            **(evidence[0].get("semantic_assessment") or {}),
            "source_item_ids": ["EV-001-ITEM-01"],
        }
        html = _render({**self.artifacts, "evidence": evidence})

        self.assertIn("id='item-EV-001-ITEM-01'", html)
        self.assertIn("href='#item-EV-001-ITEM-01'", html)


class EvidenceMetadataTests(_RealRunFixture):
    """Evidence List 必須顯示可重現條件，缺值要明說而不是留空或填 0。"""

    def test_fetch_and_publish_times_are_labelled_differently(self):
        self.assertIn("系統擷取時間", self.html)
        self.assertIn("來源發布時間", self.html)

    def test_missing_author_and_publish_time_show_not_provided(self):
        self.assertIn(NOT_PROVIDED, self.html)

    def test_reliability_relevance_and_effective_weight_are_visible(self):
        self.assertIn("可信度", self.html)
        self.assertIn("問題相關性", self.html)
        self.assertIn("有效權重", self.html)

    def test_score_components_and_limiters_are_shown(self):
        breakdown = self.artifacts["evidence"][0]["score_breakdown"]
        self.assertIn("來源品質", self.html)
        self.assertIn("可追溯性", self.html)
        for limiter in breakdown["score_limiters"]:
            self.assertIn(limiter, self.html, limiter)

    def test_semantic_assessment_source_is_disclosed(self):
        self.assertIn("問題導向語意評估", self.html)
        self.assertIn("評估來源", self.html)


class ExecutionLogTests(_RealRunFixture):
    """執行紀錄要以表格呈現時間、工具、產生的 Evidence 與降級原因。"""

    def test_step_table_has_the_required_columns(self):
        for column in ("<th>階段</th>", "<th>狀態</th>", "<th>工具／提供者</th>",
                       "<th>開始</th>", "<th>結束</th>", "<th>耗時 (ms)</th>",
                       "<th>產生的 Evidence</th>", "<th>降級原因</th>"):
            self.assertIn(column, self.html, column)

    def test_stage_providers_and_citation_gate_are_visible(self):
        self.assertIn("各階段推理提供者", self.html)
        self.assertIn("Citation Gate", self.html)

    def test_time_budget_is_reported(self):
        self.assertIn("三階段時間預算", self.html)
        self.assertIn("競賽硬上限", self.html)

    def test_degradation_reasons_are_surfaced(self):
        reasons = self.artifacts["execution_log"].get("degradation_reasons") or []
        self.assertTrue(reasons, "測試前提失敗：離線執行應該有降級紀錄")
        for reason in reasons:
            self.assertIn(reason, self.html, reason)


class UntrustedContentTests(_RealRunFixture):
    """外部來源的字串是不可信輸入；公開端點的 AuthType 是 NONE。"""

    HOSTILE = "<script>alert('xss')</script>"

    def _hostile_html(self) -> str:
        evidence = json.loads(json.dumps(self.artifacts["evidence"]))
        evidence[0]["source"] = self.HOSTILE
        evidence[0]["source_url"] = "javascript:alert(1)"
        evidence[0]["source_items"] = [{
            "source_item_id": "EV-001-ITEM-01",
            "title": self.HOSTILE,
            "url": "javascript:alert(2)",
            "author": "<img src=x onerror=alert(3)>",
            "publisher": "data:text/html;base64,PHNjcmlwdD4=",
            "published_at": None,
        }]
        return _render({**self.artifacts, "evidence": evidence})

    def test_hostile_titles_are_escaped_not_executed(self):
        """危險的是「未 escape 的標籤」，不是字面上的 `onerror=` 三個字。

        `<` 與 `>` 一旦被 escape，那串字就只是文字，無法組成標籤或屬性。所以斷言要針對
        原始標籤形式，而不是針對事件處理器的名字 —— 後者會把「已經安全的顯示內容」誤判成漏洞。
        """
        html = self._hostile_html()
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img src=x onerror", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img src=x onerror", html)

    def test_javascript_and_data_urls_never_become_links(self):
        html = self._hostile_html()
        self.assertNotIn("href='javascript:", html)
        self.assertNotIn('href="javascript:', html)
        self.assertNotIn("href='data:", html)

    def test_report_markdown_is_escaped(self):
        html = _render({**self.artifacts, "report": self.HOSTILE})
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_execution_log_is_escaped(self):
        log = {**self.artifacts["execution_log"], "degradation_reasons": [self.HOSTILE]}
        html = _render({**self.artifacts, "execution_log": log})
        self.assertNotIn("<script>alert", html)

    def test_safe_url_helper_only_allows_http_schemes(self):
        self.assertEqual(safe_url("https://example.com/a"), "https://example.com/a")
        self.assertEqual(safe_url("http://example.com/a"), "http://example.com/a")
        for hostile in ("javascript:alert(1)", "data:text/html,<script>", "vbscript:x",
                        "  javascript:alert(1)", "https://e.com/a' onmouseover='x"):
            self.assertEqual(safe_url(hostile), "", hostile)

    def test_link_falls_back_to_plain_text_for_unsafe_urls(self):
        rendered = link("javascript:alert(1)")
        self.assertNotIn("<a", rendered)
        self.assertIn("javascript:alert(1)", rendered)


class ReportRouteTests(_RealRunFixture):
    """`GET /report?run=` 必須是純讀取。"""

    def _call(self, query: dict) -> dict:
        with patch.object(lambda_module, "DEFAULT_ARTIFACT_ROOT", str(self.root)):
            return lambda_module.handler(_get("/report", query), None)

    def test_existing_run_is_rendered_without_running_the_pipeline(self):
        with patch.object(lambda_module, "run") as spy_run, \
             patch.object(lambda_module, "_prepare_run") as spy_prepare:
            response = self._call({"run": self.run_dir.name})

        self.assertEqual(response["statusCode"], 200)
        self.assertIn("證據清單", response["body"])
        self.assertIn("未重新執行任何分析", response["body"])
        spy_run.assert_not_called()
        spy_prepare.assert_not_called()

    def test_unknown_run_id_is_a_404_that_explains_the_container_limit(self):
        response = self._call({"run": "RUN-DOES-NOT-EXIST"})
        self.assertEqual(response["statusCode"], 404)
        self.assertIn("容器", response["body"])

    def test_path_traversal_run_id_is_rejected(self):
        for hostile in ("../../etc", "..", "a/b", "a\\b"):
            response = self._call({"run": hostile})
            self.assertEqual(response["statusCode"], 404, hostile)

    def test_incomplete_run_directory_is_not_rendered(self):
        partial = self.root / "runs" / "RUN-PARTIAL"
        partial.mkdir(parents=True, exist_ok=True)
        (partial / "report.md").write_text("only one file", encoding="utf-8")

        response = self._call({"run": "RUN-PARTIAL"})
        self.assertEqual(response["statusCode"], 404)


class HomePagePresentationTests(unittest.TestCase):
    """首頁與結果頁必須是同一個介面。

    背景（實測缺陷）：E3 把結果頁做成三段式排版，但首頁還是完全沒有樣式的裸 HTML ——
    瀏覽器預設字體、滿版寬度、沒有 viewport。而首頁是評審看到的第一個畫面。
    """

    def _body(self) -> str:
        return lambda_module.handler(_get("/"), None)["body"]

    def test_home_page_reuses_the_report_stylesheet(self):
        """不得各自維護一套樣式：兩邊分岔之後就再也不會一致。"""
        from src.cloud_report_view import CSS

        body = self._body()
        self.assertIn("<style>", body)
        # 取 CSS 裡一段有代表性的宣告，確認是同一份而不是另寫的。
        self.assertIn(".card{", CSS)
        self.assertIn(".card{", body)

    def test_home_page_is_declared_traditional_chinese_and_responsive(self):
        body = self._body()
        self.assertIn("lang='zh-Hant-TW'", body)
        self.assertIn("width=device-width", body)

    def test_home_page_explains_what_the_system_produces(self):
        """25% 商業應用性看可讀性：首頁要說得出這個系統產出什麼。"""
        body = self._body()
        self.assertIn("Evidence", body)
        self.assertIn("執行紀錄", body)

    def test_home_page_still_reports_sdk_capability(self):
        """降級的報告與正常報告外觀相同，所以模型路徑狀態必須在首頁就看得到。"""
        self.assertIn("Bedrock Converse", self._body())

    def test_home_page_keeps_the_form_contract(self):
        """樣式改動不得動到表單契約（E1 的守衛測試依賴這些字串）。"""
        body = self._body()
        for required in ("name='coin'", "name='question'",
                         "name='mode' value='test'", "開始分析"):
            self.assertIn(required, body, required)


class PublicGuardRegressionTests(unittest.TestCase):
    """E1 的 test-only 守衛不得因為 E3 的新路由而失效。"""

    def test_home_page_has_no_formal_selector(self):
        response = lambda_module.handler(_get("/"), None)
        self.assertEqual(response["statusCode"], 200)
        self.assertNotIn("value='formal'", response["body"])
        self.assertIn("test-only", response["body"])

    def test_report_route_does_not_bypass_the_mode_guard(self):
        """`/report` 是 GET 讀取路由，不該接受 mode／rerun 參數而觸發任何執行。"""
        with patch.object(lambda_module, "run") as spy_run:
            response = lambda_module.handler(
                _get("/report", {"run": "RUN-X", "mode": "formal", "authorized_rerun": "1"}), None)
        self.assertEqual(response["statusCode"], 404)
        spy_run.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
