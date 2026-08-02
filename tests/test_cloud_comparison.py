"""E7 — 雲端雙幣比較（cloud dual-coin comparison）測試。

背景（實測缺陷）：`src/app.py` 的 stdlib server 早就支援雙幣比較，Function URL 完全沒有 ——
首頁沒有比較欄位，`handler()` 的 POST 也只認得單幣。而競賽的第三題正是比較題，Demo 跑在雲端。

這組測試釘住五類行為：

1. 首頁有 `compare_with` 下拉，第一個選項是空值（預設仍是單幣分析），且 label／id 有綁定。
2. POST 帶有效的 `compare_with` 走 `run_comparison()`；空值、空白、與主要幣種相同時走單幣 `run()`。
3. 比較頁同時呈現兩腳的數值與各自引用的 Evidence ID，且錨點真的存在 —— 兩腳的 ID 都從
   EV-001 起編號，錨點若不帶幣種前綴就會有一半指錯。
4. 外部來源的 title／author／url 是不可信輸入，不得產生 `<script>` 或可點的 `javascript:` 連結。
5. E1 的 test-only 守衛與既有的 `/download`、`/artifact`、`/report`、首頁 GET 都不受影響。

全部離線：以測試替身強制走 deterministic 離線管線（`live=False, use_llm=False`），不觸網、
不呼叫任何模型；被拒絕的路徑則以呼叫次數證明管線完全沒有被觸發。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lambda_handler as lambda_module
import src.orchestrator as orchestrator_module
from src.cloud_report_view import UNKNOWN_EVIDENCE_MARK, render_comparison_page
from src.orchestrator import run as real_run
from src.orchestrator import run_comparison as real_run_comparison
from src.run_manager import RUN_MODE_FORMAL

PAIR_ARTIFACTS = ("claims.json", "comparison.json", "comparison.md",
                  "manifest.json", "research_plan.json")


def _get(path: str, query: dict | None = None) -> dict:
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": "GET", "path": path}},
        "queryStringParameters": query or {},
    }


def _post_form(body: str) -> dict:
    return {
        "rawPath": "/",
        "requestContext": {"http": {"method": "POST", "path": "/"}},
        "headers": {"content-type": "application/x-www-form-urlencoded"},
        "body": body,
    }


def _post_json(payload: dict) -> dict:
    return {
        "rawPath": "/",
        "requestContext": {"http": {"method": "POST", "path": "/"}},
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload, ensure_ascii=False),
    }


def _offline_single(coin, question, output_dir, **kwargs):
    """測試替身：不論呼叫端傳什麼 live／use_llm，一律走 deterministic 離線單幣管線。"""
    kwargs["live"] = False
    kwargs["use_llm"] = False
    return real_run(coin, question, output_dir, **kwargs)


def _offline_pair(coin_a, coin_b, question, output_dir, **kwargs):
    """同上，比較版本。仍呼叫真正的 `run_comparison()`，因此產物是真的。"""
    kwargs["live"] = False
    kwargs["use_llm"] = False
    return real_run_comparison(coin_a, coin_b, question, output_dir, **kwargs)


def _render(payload: dict, evidence_by_coin: dict, **overrides) -> str:
    kwargs = {
        "run_id": "RUN-E7-TEST",
        "mode": "test",
        "status": "COMPLETED",
        "payload": payload,
        "evidence_by_coin": evidence_by_coin,
        "artifact_filenames": PAIR_ARTIFACTS,
    }
    kwargs.update(overrides)
    return render_comparison_page(**kwargs)


class HomePageComparisonFieldTests(unittest.TestCase):
    """首頁的比較欄位（驗收 1）。"""

    def _body(self) -> str:
        return lambda_module.handler(_get("/"), None)["body"]

    def test_home_page_offers_a_compare_with_select(self):
        body = self._body()
        self.assertIn("name='compare_with'", body)
        self.assertIn("<select id='compare_with' name='compare_with'>", body)
        self.assertIn("<label for='compare_with'>", body)

    def test_compare_select_starts_with_an_empty_option_then_five_coins(self):
        """第一個選項必須是空值：預設仍是單幣分析。"""
        body = self._body()
        options = body.split("name='compare_with'>")[1].split("</select>")[0]
        self.assertTrue(options.lstrip().startswith("<option value=''>"), options[:80])
        for coin in ("BTC", "ETH", "SOL", "BNB", "XRP"):
            self.assertIn(f"<option>{coin}</option>", options, coin)

    def test_home_page_warns_that_a_comparison_takes_roughly_twice_as_long(self):
        self.assertIn("時間大約是單幣的兩倍", self._body())

    def test_compare_select_does_not_reintroduce_a_mode_selector(self):
        """E1 守衛：新增下拉不得讓 formal 選項或 mode 下拉回來。"""
        body = self._body()
        self.assertNotIn("<select name='mode'>", body)
        self.assertNotIn("<option value='formal'>", body)
        self.assertNotIn(">Formal<", body)
        self.assertIn("name='mode' value='test'", body)

    def test_existing_home_page_contract_is_intact(self):
        body = self._body()
        for required in ("name='coin'", "name='question'", "開始分析", "id='go'",
                         "button.disabled = true", "<noscript>", "點擊後開始分析",
                         ".progress[hidden]{display:none}", "prefers-reduced-motion",
                         "test mode", "test-only", "Bedrock Converse", "執行紀錄"):
            self.assertIn(required, body, required)


class ComparisonDispatchTests(unittest.TestCase):
    """POST 分派（驗收 2、3）。"""

    def _post(self, event) -> tuple:
        """回傳 `(response, run spy, run_comparison spy)`；產物寫進臨時目錄。"""
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"ARTIFACT_ROOT": directory}, clear=False), \
                 patch.object(lambda_module, "run", side_effect=_offline_single) as spy_run, \
                 patch.object(lambda_module, "run_comparison",
                              side_effect=_offline_pair) as spy_pair, \
                 patch.object(lambda_module, "_LATEST_RUN_DIR", None):
                return lambda_module.handler(event, None), spy_run, spy_pair

    def test_compare_with_routes_to_run_comparison(self):
        response, spy_run, spy_pair = self._post(
            _post_form("coin=ETH&question=Q&mode=test&compare_with=BTC"))

        self.assertEqual(response["statusCode"], 200)
        spy_pair.assert_called_once()
        spy_run.assert_not_called()
        self.assertEqual(spy_pair.call_args.args[:2], ("ETH", "BTC"))
        self.assertIn("ETH", response["body"])
        self.assertIn("BTC", response["body"])

    def test_lowercase_compare_with_is_uppercased(self):
        response, _, spy_pair = self._post(
            _post_form("coin=eth&question=Q&mode=test&compare_with=btc"))

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(spy_pair.call_args.args[:2], ("ETH", "BTC"))

    def test_empty_whitespace_or_identical_compare_with_stays_single_coin(self):
        """不得變成自己比自己：`run_comparison()` 對相同幣種會直接拒絕。"""
        for body in ("coin=ETH&question=Q&mode=test&compare_with=",
                     "coin=ETH&question=Q&mode=test&compare_with=%20%20",
                     "coin=ETH&question=Q&mode=test&compare_with=ETH",
                     "coin=ETH&question=Q&mode=test&compare_with=eth",
                     "coin=ETH&question=Q&mode=test"):
            with self.subTest(body=body):
                response, spy_run, spy_pair = self._post(_post_form(body))

                self.assertEqual(response["statusCode"], 200)
                spy_pair.assert_not_called()
                spy_run.assert_called_once()

    def test_json_compare_with_is_accepted_too(self):
        response, spy_run, spy_pair = self._post(
            _post_json({"coin": "SOL", "question": "Q", "compare_with": "BNB"}))

        self.assertEqual(response["statusCode"], 200)
        spy_run.assert_not_called()
        self.assertEqual(spy_pair.call_args.args[:2], ("SOL", "BNB"))


class _PairFixture(unittest.TestCase):
    """共用一次真實的離線比較執行；不連網、不呼叫 LLM。"""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls._directory.name)
        cls.run_dir = cls.root / "runs" / "RUN-E7-TEST"
        cls.payload = real_run_comparison("ETH", "BTC", "ETH 與 BTC 哪個風險較低？",
                                          cls.run_dir, live=False, use_llm=False)
        cls.evidence = {
            coin: json.loads((cls.run_dir / coin / "evidence.json").read_text(encoding="utf-8"))
            for coin in ("ETH", "BTC")
        }
        cls.html = _render(cls.payload, cls.evidence)

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def _copy(self) -> tuple:
        return (json.loads(json.dumps(self.payload)), json.loads(json.dumps(self.evidence)))


class ComparisonPageTests(_PairFixture):
    """比較結果頁的內容（驗收 4、5）。"""

    def test_both_coins_question_and_run_id_are_shown(self):
        self.assertIn("ETH", self.html)
        self.assertIn("BTC", self.html)
        self.assertIn("ETH 與 BTC 哪個風險較低？", self.html)
        self.assertIn("RUN-E7-TEST", self.html)

    def test_download_entry_points_are_preserved(self):
        self.assertIn("/download?scope=required&run=RUN-E7-TEST", self.html)
        self.assertIn("/download?scope=all&run=RUN-E7-TEST", self.html)
        for name in ("comparison.md", "comparison.json"):
            self.assertIn(f"path={name}&run=RUN-E7-TEST", self.html, name)

    def test_comparison_artifacts_are_in_the_download_whitelist(self):
        from src.app import DOWNLOADABLE_NAMES

        self.assertIn("comparison.md", DOWNLOADABLE_NAMES)
        self.assertIn("comparison.json", DOWNLOADABLE_NAMES)

    def test_side_by_side_values_and_verdicts_are_rendered(self):
        for label in ("平均日成交額（USD）", "綜合暴險分數（0-100）", "社群互動總量（無抓取上限）"):
            self.assertIn(label, self.html, label)
        for key in ("liquidity", "risk_exposure", "attention"):
            self.assertIn(self.payload["comparison"][key]["verdict"], self.html, key)
        self.assertIn(self.payload["comparison"]["summary"], self.html)

    def test_every_cited_evidence_id_has_a_matching_anchor_per_side(self):
        """兩腳的 ID 都從 EV-001 起編號，所以錨點必須帶幣種前綴才不會指錯。"""
        for coin in ("ETH", "BTC"):
            profile = self.payload["profiles"][coin]
            cited = {profile["liquidity"]["evidence_id"], *profile["risk_exposure"]["cited_evidence_ids"],
                     *profile["attention"]["cited_evidence_ids"]}
            cited = {i for i in cited if i}
            self.assertTrue(cited, f"測試前提失敗：{coin} 沒有任何引用")
            for evidence_id in cited:
                self.assertIn(f"href='#evidence-{coin}-{evidence_id}'", self.html,
                              f"{coin}/{evidence_id}")
                self.assertIn(f"id='evidence-{coin}-{evidence_id}'", self.html,
                              f"{coin}/{evidence_id}")

    def test_unknown_evidence_id_is_not_rendered_as_a_working_link(self):
        payload, evidence = self._copy()
        payload["profiles"]["ETH"]["risk_exposure"]["cited_evidence_ids"] = ["EV-DOES-NOT-EXIST"]
        html = _render(payload, evidence)

        self.assertIn("EV-DOES-NOT-EXIST", html)
        self.assertIn(UNKNOWN_EVIDENCE_MARK, html)
        self.assertNotIn("href='#evidence-ETH-EV-DOES-NOT-EXIST'", html)

    def test_limitations_and_disclaimer_are_shown(self):
        self.assertIn(self.payload["comparison"]["caveat"], self.html)
        self.assertIn("不構成投資建議", self.html)

    def test_page_is_traditional_chinese_and_responsive(self):
        self.assertIn("lang='zh-Hant-TW'", self.html)
        self.assertIn("width=device-width", self.html)

    def test_page_uses_no_external_resources(self):
        for pattern in ("src='http", 'src="http', "cdn.", "googleapis", "unpkg", "jsdelivr"):
            self.assertNotIn(pattern, self.html, pattern)


class ComparisonPageUntrustedContentTests(_PairFixture):
    """外部來源的字串是不可信輸入；公開端點的 AuthType 是 NONE（驗收 6）。"""

    HOSTILE = "<script>alert('xss')</script>"

    def _hostile_html(self) -> str:
        payload, evidence = self._copy()
        evidence["ETH"][0]["source"] = self.HOSTILE
        evidence["ETH"][0]["source_url"] = "javascript:alert(1)"
        evidence["ETH"][0]["source_items"] = [{
            "source_item_id": "EV-001-ITEM-01",
            "title": self.HOSTILE,
            "author": "<img src=x onerror=alert(3)>",
            "publisher": "data:text/html;base64,PHNjcmlwdD4=",
            "url": "javascript:alert(2)",
        }]
        payload["comparison"]["summary"] = self.HOSTILE
        payload["markdown"] = self.HOSTILE
        return _render(payload, evidence)

    def test_hostile_titles_and_authors_are_escaped_not_executed(self):
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


class ComparisonJsonApiTests(unittest.TestCase):
    """JSON API（驗收 7）。"""

    def _post(self, event) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"ARTIFACT_ROOT": directory}, clear=False), \
                 patch.object(lambda_module, "run", side_effect=_offline_single), \
                 patch.object(lambda_module, "run_comparison", side_effect=_offline_pair), \
                 patch.object(lambda_module, "_LATEST_RUN_DIR", None):
                return lambda_module.handler(event, None)

    def test_json_with_compare_with_returns_the_comparison_payload(self):
        response = self._post(_post_json({"coin": "ETH", "question": "Q", "compare_with": "BTC"}))

        self.assertEqual(response["statusCode"], 200)
        payload = json.loads(response["body"])
        self.assertEqual(payload["mode"], "comparison")
        self.assertEqual(payload["coins"], ["ETH", "BTC"])
        self.assertEqual(payload["result"]["coins"], ["ETH", "BTC"])
        for key in ("liquidity", "risk_exposure", "attention", "summary", "caveat"):
            self.assertIn(key, payload["comparison"], key)
        self.assertEqual(sorted(payload["evidence_by_coin"]), ["BTC", "ETH"])
        # run 中介資料的欄位風格與單幣一致。
        for key in ("run_id", "run_mode", "run_status", "artifact_directory", "sdk_capability",
                    "manifest", "claims", "research_plan"):
            self.assertIn(key, payload, key)

    def test_single_coin_json_contract_is_unchanged(self):
        response = self._post(_post_json({"coin": "ETH", "question": "Q"}))

        self.assertEqual(response["statusCode"], 200)
        payload = json.loads(response["body"])
        for key in ("result", "run_id", "run_mode", "run_status", "artifact_directory",
                    "sdk_capability", "report", "evidence", "execution_log",
                    "research_plan", "claims", "manifest"):
            self.assertIn(key, payload, key)
        self.assertEqual(payload["result"]["coin"], "ETH")
        self.assertNotIn("comparison", payload)


class ComparisonGuardTests(unittest.TestCase):
    """非法輸入與 E1 守衛在比較路徑上同樣有效（驗收 8、9）。"""

    def test_unsupported_compare_coin_is_a_400_without_any_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"ARTIFACT_ROOT": directory}, clear=False), \
                 patch.object(lambda_module, "run_comparison", side_effect=_offline_pair), \
                 patch.object(orchestrator_module, "collect_evidence_detailed") as spy_collect, \
                 patch.object(lambda_module, "_LATEST_RUN_DIR", None):
                response = lambda_module.handler(
                    _post_form("coin=ETH&question=Q&mode=test&compare_with=DOGE"), None)

        self.assertEqual(response["statusCode"], 400)
        self.assertIn("僅支援", response["body"])
        spy_collect.assert_not_called()

    def test_unsupported_primary_coin_is_a_400_on_the_comparison_path(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"ARTIFACT_ROOT": directory}, clear=False), \
                 patch.object(lambda_module, "run_comparison", side_effect=_offline_pair), \
                 patch.object(orchestrator_module, "collect_evidence_detailed") as spy_collect, \
                 patch.object(lambda_module, "_LATEST_RUN_DIR", None):
                response = lambda_module.handler(
                    _post_form("coin=DOGE&question=Q&mode=test&compare_with=BTC"), None)

        self.assertEqual(response["statusCode"], 400)
        spy_collect.assert_not_called()

    def test_formal_mode_is_blocked_before_the_comparison_run_is_created(self):
        with patch.object(lambda_module, "_prepare_run") as spy_prepare, \
             patch.object(lambda_module, "run_comparison") as spy_pair:
            response = lambda_module.handler(
                _post_form(f"coin=ETH&question=Q&compare_with=BTC&mode={RUN_MODE_FORMAL}"), None)

        self.assertEqual(response["statusCode"], 403)
        spy_prepare.assert_not_called()
        spy_pair.assert_not_called()

    def test_rerun_flags_are_blocked_on_the_comparison_path(self):
        with patch.object(lambda_module, "_prepare_run") as spy_prepare, \
             patch.object(lambda_module, "run_comparison") as spy_pair:
            response = lambda_module.handler(
                _post_json({"coin": "ETH", "question": "Q", "compare_with": "BTC",
                            "authorized_rerun": True}), None)

        self.assertEqual(response["statusCode"], 403)
        spy_prepare.assert_not_called()
        spy_pair.assert_not_called()


class ExistingRouteRegressionTests(unittest.TestCase):
    """既有路由不得回歸（驗收 10）。"""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls._directory.name)
        cls.run_dir = cls.root / "runs" / "RUN-E7-REGRESSION"
        real_run("ETH", "E7 既有路由回歸測試", cls.run_dir, live=False, use_llm=False)

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_home_page_get_still_returns_the_form(self):
        response = lambda_module.handler(_get("/"), None)
        self.assertEqual(response["statusCode"], 200)
        self.assertIn("開始分析", response["body"])

    def test_download_and_artifact_routes_still_work(self):
        with patch.object(lambda_module, "_LATEST_RUN_DIR", self.run_dir):
            bundle = lambda_module.handler(_get("/download", {"scope": "required"}), None)
            single = lambda_module.handler(_get("/artifact", {"path": "report.md"}), None)

        self.assertEqual(bundle["statusCode"], 200)
        self.assertTrue(bundle["isBase64Encoded"])
        self.assertEqual(single["statusCode"], 200)
        self.assertIn("text/plain", single["headers"]["content-type"])

    def test_report_route_still_renders_an_existing_single_run(self):
        with patch.object(lambda_module, "DEFAULT_ARTIFACT_ROOT", str(self.root)), \
             patch.object(lambda_module, "run") as spy_run, \
             patch.object(lambda_module, "run_comparison") as spy_pair:
            response = lambda_module.handler(_get("/report", {"run": self.run_dir.name}), None)

        self.assertEqual(response["statusCode"], 200)
        self.assertIn("證據清單", response["body"])
        spy_run.assert_not_called()
        spy_pair.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
