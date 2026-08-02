"""E3 補件：`report.md` 的「## 主張（Claim）」段引用必須可直接點擊。

這個檔案只驗一件事：**Claim 段的引用能不能被讀者直接追下去**。`report.md` 是獨立提交物，
讀者手上可能只有這個檔案，沒有網頁 UI 可點；若 Claim 段只寫 `EV-002`，追溯成本就落在讀者身上。

同時驗「不產生錯的連結」這條更重要的邊界：缺 URL、非 http(s) scheme、被 reject 的證據，
一律退回純文字，而不是生出一個指不到任何地方的連結。
"""

from __future__ import annotations

import re
import unittest

from src.report_renderer import render_claims_section, render_competition_report

FETCHED_AT = "2026-08-01T00:00:00+00:00"

# 抓「可點擊的 markdown 連結」：只認 http(s)，因為只有這種才是本次要求的可追溯連結。
_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def make_evidence(evidence_id="EV-001", *, url="https://example.com/EV-001",
                  verification_status="unverified", source_items=None,
                  cited_item_ids=None, source="TestSource"):
    record = {
        "evidence_id": evidence_id,
        "source": source,
        "source_url": url,
        "fetched_at": FETCHED_AT,
        "data_type": "market",
        "coin": "ETH",
        "time_range": "14d",
        "content": {"note": "fixture"},
        "reliability_score": 0.9,
        "verification_status": verification_status,
        "source_type": "market_api",
        "content_reference": {"endpoint": "x"},
        "related_claim_ids": ["CL-001"],
    }
    if source_items is not None:
        record["source_items"] = source_items
    if cited_item_ids is not None:
        record["semantic_assessment"] = {"source_item_ids": list(cited_item_ids)}
    return record


def make_claim(*, supporting=("EV-001",), contradicting=("EV-002",), facts=None):
    return {
        "claim_id": "CL-001",
        "statement": "ETH 近期訊號偏多",
        "claim_type": "market_judgment",
        "verdict": "supported",
        "facts": facts if facts is not None else [
            {"statement": "期間報酬 +8.2%，價格動能為正", "evidence_ids": list(supporting)},
        ],
        "inference": "價格動能為正，支持偏多判讀。",
        "conclusion": "整體訊號偏多。",
        "supporting_evidence_ids": list(supporting),
        "contradicting_evidence_ids": list(contradicting),
        "confidence": {
            "score": 0.5, "level": "medium", "type": "heuristic",
            "components": {"weighted_evidence_quality": 0.5}, "limiters": [],
        },
        "limitations": [], "invalidation_conditions": [], "watchpoints": [],
    }


def make_graph(claims=None):
    return {"claims": claims if claims is not None else [make_claim()],
            "scoring_version": "claim-confidence-v1"}


def claim_lines(markdown: str, prefix: str) -> list:
    return [line for line in markdown.splitlines() if line.startswith(prefix)]


def sole_line(testcase, markdown: str, prefix: str) -> str:
    lines = claim_lines(markdown, prefix)
    testcase.assertEqual(len(lines), 1, "expected exactly one %r line: %r" % (prefix, lines))
    return lines[0]


class ClickableCitationTests(unittest.TestCase):
    """正常路徑：三處引用都要變成可點擊連結，且前綴與 ID 都不能被改掉。"""

    def setUp(self):
        self.evidence = [make_evidence("EV-001", url="https://example.com/EV-001"),
                         make_evidence("EV-002", url="https://example.com/EV-002")]
        self.markdown = render_claims_section(make_graph(), ("weighted_evidence_quality",),
                                              self.evidence)

    def test_fact_line_links_its_evidence(self):
        line = sole_line(self, self.markdown, "- 事實：")
        self.assertIn("[EV-001](https://example.com/EV-001)", line)

    def test_supporting_line_links_its_evidence(self):
        line = sole_line(self, self.markdown, "- 支持證據：")
        self.assertIn("[EV-001](https://example.com/EV-001)", line)

    def test_contradicting_line_links_its_evidence(self):
        line = sole_line(self, self.markdown, "- 反方證據：")
        self.assertIn("[EV-002](https://example.com/EV-002)", line)

    def test_existing_line_prefixes_and_section_heading_are_unchanged(self):
        for required in ("## 主張（Claim）", "- 事實：", "- 推論：", "- 結論：",
                         "- 支持證據：", "- 反方證據：", "- 信心分量："):
            self.assertIn(required, self.markdown, required)

    def test_multiple_supporting_ids_are_all_linked(self):
        markdown = render_claims_section(
            make_graph([make_claim(supporting=("EV-001", "EV-002"), contradicting=())]),
            ("weighted_evidence_quality",), self.evidence)
        line = sole_line(self, markdown, "- 支持證據：")
        self.assertIn("[EV-001](https://example.com/EV-001)", line)
        self.assertIn("[EV-002](https://example.com/EV-002)", line)

    def test_no_supporting_evidence_still_says_none(self):
        markdown = render_claims_section(make_graph([make_claim(supporting=(), contradicting=())]),
                                        ("weighted_evidence_quality",), self.evidence)
        self.assertIn("- 支持證據：無", markdown)
        self.assertIn("- 反方證據：無", markdown)


class NoFakeLinkTests(unittest.TestCase):
    """不得產生錯的連結：缺 URL、非 http(s) scheme、會破壞語法的位址一律純文字。"""

    def render(self, evidence):
        return render_claims_section(make_graph([make_claim(contradicting=())]),
                                     ("weighted_evidence_quality",), evidence)

    def test_missing_url_falls_back_to_plain_id(self):
        markdown = self.render([make_evidence("EV-001", url=None)])
        line = sole_line(self, markdown, "- 支持證據：")
        self.assertEqual(line, "- 支持證據：EV-001")
        self.assertNotIn("(#)", markdown)
        self.assertNotIn("EV-001]()", markdown)

    def test_empty_url_falls_back_to_plain_id(self):
        markdown = self.render([make_evidence("EV-001", url="   ")])
        self.assertEqual(sole_line(self, markdown, "- 支持證據："), "- 支持證據：EV-001")

    def test_evidence_absent_from_the_list_falls_back_to_plain_id(self):
        markdown = self.render([make_evidence("EV-999")])
        self.assertEqual(sole_line(self, markdown, "- 支持證據："), "- 支持證據：EV-001")

    def test_javascript_scheme_is_never_linked(self):
        markdown = self.render([make_evidence("EV-001", url="javascript:alert(1)")])
        self.assertEqual(sole_line(self, markdown, "- 支持證據："), "- 支持證據：EV-001")
        self.assertNotIn("javascript:", markdown)

    def test_data_scheme_is_never_linked(self):
        markdown = self.render([make_evidence("EV-001", url="data:text/html;base64,QQ==")])
        self.assertEqual(sole_line(self, markdown, "- 支持證據："), "- 支持證據：EV-001")
        self.assertNotIn("data:text/html", markdown)

    def test_relative_and_anchor_urls_are_never_linked(self):
        for unusable in ("#", "/local/path", "example.com/EV-001", "ftp://example.com/a"):
            markdown = self.render([make_evidence("EV-001", url=unusable)])
            self.assertEqual(sole_line(self, markdown, "- 支持證據："), "- 支持證據：EV-001",
                             unusable)

    def test_url_with_markdown_breaking_characters_is_not_linked(self):
        for unusable in ("https://example.com/a b", "https://example.com/a(b)",
                         "https://example.com/a<b>", "https://example.com/a\nb",
                         "https://example.com/a\"title\""):
            markdown = self.render([make_evidence("EV-001", url=unusable)])
            self.assertEqual(sole_line(self, markdown, "- 支持證據："), "- 支持證據：EV-001",
                             unusable)

    def test_brackets_and_parens_in_the_id_do_not_break_the_link(self):
        evidence = [make_evidence("EV-[a](b)", url="https://example.com/weird")]
        markdown = render_claims_section(
            make_graph([make_claim(supporting=("EV-[a](b)",), contradicting=())]),
            ("weighted_evidence_quality",), evidence)
        line = sole_line(self, markdown, "- 支持證據：")
        # `]` 與 `(` 全部被跳脫，連結文字不會提早關閉 `[...]`，位址仍是我們給的那一個。
        self.assertEqual(line, "- 支持證據：[EV-\\[a\\]\\(b\\)](https://example.com/weird)")
        self.assertNotIn("](b)", line)


class RejectedEvidenceTests(unittest.TestCase):
    """rejected 的證據不得讀起來像可用的依據，因此絕不連結。"""

    def test_rejected_evidence_is_not_linked(self):
        evidence = [make_evidence("EV-REJ", url="https://example.com/rejected",
                                  verification_status="rejected")]
        markdown = render_claims_section(
            make_graph([make_claim(supporting=("EV-REJ",), contradicting=())]),
            ("weighted_evidence_quality",), evidence)
        line = sole_line(self, markdown, "- 支持證據：")
        self.assertEqual(line, "- 支持證據：EV-REJ")
        self.assertNotIn("https://example.com/rejected", markdown)

    def test_rejected_evidence_does_not_expand_its_source_items(self):
        evidence = [make_evidence(
            "EV-REJ", url="https://example.com/rejected", verification_status="rejected",
            source_items=[{"source_item_id": "EV-REJ-ITEM-01", "url": "https://example.com/r1"}],
            cited_item_ids=["EV-REJ-ITEM-01"])]
        markdown = render_claims_section(
            make_graph([make_claim(supporting=("EV-REJ",), contradicting=())]),
            ("weighted_evidence_quality",), evidence)
        self.assertNotIn("EV-REJ-ITEM-01", markdown)
        self.assertNotIn("https://example.com/r1", markdown)

    def test_non_rejected_statuses_stay_linkable(self):
        for status in ("unverified", "partially_confirmed", "verified", "fallback"):
            evidence = [make_evidence("EV-001", url="https://example.com/EV-001",
                                      verification_status=status)]
            markdown = render_claims_section(
                make_graph([make_claim(contradicting=())]),
                ("weighted_evidence_quality",), evidence)
            self.assertIn("[EV-001](https://example.com/EV-001)", markdown, status)


class SourceItemCitationTests(unittest.TestCase):
    """聚合來源要指出實際依據哪幾則，而不是整包來源。"""

    def aggregated(self, *, items, cited):
        return [make_evidence("EV-NEWS", url="https://example.com/news", source="NewsFeed",
                              source_items=items, cited_item_ids=cited)]

    def render(self, evidence):
        return render_claims_section(
            make_graph([make_claim(supporting=("EV-NEWS",), contradicting=())]),
            ("weighted_evidence_quality",), evidence)

    def test_cited_source_items_are_linked(self):
        markdown = self.render(self.aggregated(
            items=[{"source_item_id": "EV-NEWS-ITEM-01", "url": "https://example.com/news/1"},
                   {"source_item_id": "EV-NEWS-ITEM-02", "url": "https://example.com/news/2"}],
            cited=["EV-NEWS-ITEM-01", "EV-NEWS-ITEM-02"]))
        line = sole_line(self, markdown, "- 支持證據：")
        self.assertIn("[EV-NEWS](https://example.com/news)", line)
        self.assertIn("[EV-NEWS-ITEM-01](https://example.com/news/1)", line)
        self.assertIn("[EV-NEWS-ITEM-02](https://example.com/news/2)", line)

    def test_uncited_source_items_are_not_listed(self):
        markdown = self.render(self.aggregated(
            items=[{"source_item_id": "EV-NEWS-ITEM-01", "url": "https://example.com/news/1"},
                   {"source_item_id": "EV-NEWS-ITEM-02", "url": "https://example.com/news/2"}],
            cited=["EV-NEWS-ITEM-01"]))
        self.assertIn("EV-NEWS-ITEM-01", markdown)
        self.assertNotIn("EV-NEWS-ITEM-02", markdown)

    def test_source_item_without_url_is_plain_text(self):
        markdown = self.render(self.aggregated(
            items=[{"source_item_id": "EV-NEWS-ITEM-01", "url": None}],
            cited=["EV-NEWS-ITEM-01"]))
        line = sole_line(self, markdown, "- 支持證據：")
        self.assertIn("EV-NEWS-ITEM-01", line)
        self.assertNotIn("[EV-NEWS-ITEM-01](", line)

    def test_source_item_with_unsafe_scheme_is_plain_text(self):
        markdown = self.render(self.aggregated(
            items=[{"source_item_id": "EV-NEWS-ITEM-01", "url": "javascript:alert(1)"}],
            cited=["EV-NEWS-ITEM-01"]))
        self.assertIn("EV-NEWS-ITEM-01", markdown)
        self.assertNotIn("javascript:", markdown)

    def test_unknown_source_item_id_is_dropped(self):
        """提案端填了不存在的子項 ID 時，寧可不渲染，也不要出現指不到來源的引用。"""
        markdown = self.render(self.aggregated(
            items=[{"source_item_id": "EV-NEWS-ITEM-01", "url": "https://example.com/news/1"}],
            cited=["EV-NEWS-ITEM-99"]))
        self.assertNotIn("EV-NEWS-ITEM-99", markdown)
        self.assertIn("[EV-NEWS](https://example.com/news)", markdown)

    def test_evidence_without_source_items_has_no_item_block(self):
        markdown = self.render([make_evidence("EV-NEWS", url="https://example.com/news")])
        line = sole_line(self, markdown, "- 支持證據：")
        self.assertEqual(line, "- 支持證據：[EV-NEWS](https://example.com/news)")


class BackwardCompatibilityTests(unittest.TestCase):
    """不傳 evidence 時必須維持原本的純文字引用，既有呼叫端零修改。"""

    def test_omitting_evidence_keeps_plain_text_citations(self):
        markdown = render_claims_section(make_graph(), ("weighted_evidence_quality",))
        self.assertIn("- 支持證據：EV-001", markdown)
        self.assertIn("- 反方證據：EV-002", markdown)
        self.assertNotIn("](http", markdown)

    def test_omitting_evidence_matches_passing_an_empty_list(self):
        self.assertEqual(render_claims_section(make_graph(), ("weighted_evidence_quality",)),
                         render_claims_section(make_graph(), ("weighted_evidence_quality",), []))

    def test_malformed_evidence_input_does_not_raise(self):
        for broken in (None, "EV-001", 42, [None, "x", {}], {"evidence_id": "EV-001"}):
            try:
                markdown = render_claims_section(make_graph(), ("weighted_evidence_quality",),
                                                 broken)
            except Exception as error:  # noqa: BLE001 - 渲染層不得因輸入形狀不對而中斷報告
                self.fail("render_claims_section raised on %r: %r" % (broken, error))
            self.assertIn("- 支持證據：", markdown)

    def test_empty_graph_still_renders_the_heading(self):
        markdown = render_claims_section({}, ("weighted_evidence_quality",), [make_evidence()])
        self.assertIn("## 主張（Claim）", markdown)


class DeterminismTests(unittest.TestCase):
    """相同輸入必須逐字相同：連結是查表得來的，不含時間、隨機或 set 迭代順序。"""

    def payload(self):
        return [make_evidence("EV-001", url="https://example.com/EV-001"),
                make_evidence("EV-002", url="https://example.com/EV-002",
                              source_items=[
                                  {"source_item_id": "EV-002-ITEM-02",
                                   "url": "https://example.com/news/2"},
                                  {"source_item_id": "EV-002-ITEM-01",
                                   "url": "https://example.com/news/1"}],
                              cited_item_ids=["EV-002-ITEM-02", "EV-002-ITEM-01"]),
                make_evidence("EV-REJ", url="https://example.com/rej",
                              verification_status="rejected")]

    def test_same_input_renders_byte_identical_output(self):
        first = render_claims_section(make_graph(), ("weighted_evidence_quality",), self.payload())
        second = render_claims_section(make_graph(), ("weighted_evidence_quality",), self.payload())
        self.assertEqual(first, second)

    def test_source_item_order_follows_the_cited_list_not_a_sort(self):
        markdown = render_claims_section(
            make_graph([make_claim(supporting=("EV-002",), contradicting=())]),
            ("weighted_evidence_quality",), self.payload())
        line = sole_line(self, markdown, "- 支持證據：")
        self.assertLess(line.index("EV-002-ITEM-02"), line.index("EV-002-ITEM-01"))

    def test_rendering_does_not_mutate_the_evidence_input(self):
        evidence = self.payload()
        import copy

        snapshot = copy.deepcopy(evidence)
        render_claims_section(make_graph(), ("weighted_evidence_quality",), evidence)
        self.assertEqual(evidence, snapshot)


class CompetitionReportWiringTests(unittest.TestCase):
    """接線：`render_competition_report()` 必須把手上的 evidence 傳給 Claim 段。"""

    def payload(self):
        return {
            "run": {"run_id": "RUN-1", "coins": ["ETH"], "question": "ETH 近期狀況？"},
            "claim_graph": make_graph(),
            "evidence": [make_evidence("EV-001", url="https://example.com/EV-001"),
                         make_evidence("EV-002", url="https://example.com/EV-002")],
        }

    def claim_section(self, markdown: str) -> str:
        return markdown.split("## 主張（Claim）", 1)[1].split("\n## ", 1)[0]

    def test_claim_section_contains_clickable_links(self):
        section = self.claim_section(render_competition_report(self.payload()))
        self.assertGreater(len(_LINK_PATTERN.findall(section)), 0)
        self.assertIn("[EV-001](https://example.com/EV-001)", section)
        self.assertIn("[EV-002](https://example.com/EV-002)", section)

    def test_report_stays_byte_identical_across_calls(self):
        self.assertEqual(render_competition_report(self.payload()),
                         render_competition_report(self.payload()))

    def test_rejected_evidence_in_the_report_is_not_linked_in_the_claim_section(self):
        payload = self.payload()
        payload["evidence"].append(make_evidence("EV-REJ", url="https://example.com/rej",
                                                 verification_status="rejected"))
        payload["claim_graph"] = make_graph([make_claim(supporting=("EV-001", "EV-REJ"),
                                                        contradicting=())])
        section = self.claim_section(render_competition_report(payload))
        self.assertIn("EV-REJ", section)
        self.assertNotIn("[EV-REJ](", section)


if __name__ == "__main__":
    unittest.main()
