"""公開 Lambda Function URL 的來源 IP 白名單。

背景：Function URL 的 `AuthType` 是 NONE，AWS 這一層沒有任何認證。E1 已把公開端點限制成
test-only，但那只擋「可以做什麼」，不擋「誰可以連進來」——全世界仍然都能觸發 test 執行並
消耗 Bedrock 配額。本檔守住的是後者。

為什麼是應用層而不是網路層：Lambda Function URL 不能掛 AWS WAF，resource-based policy 也
只支援 `lambda:FunctionUrlAuthType` 與 `lambda:InvokedViaFunctionUrl` 兩個條件鍵，
`AuthType: NONE` 時 Lambda 根本不做 IAM 認證，因此 `aws:SourceIp` 寫進 policy 只會產生
「看起來有防護」的假象。真正的網路層阻擋需要 CloudFront + WAF IPSet（新基礎設施、新網址）。
因此守衛做在 `lambda_handler.handler()` 的第一行：請求仍會叫用一次 Lambda，但在路由、
`RunManager`、collector 與任何模型呼叫之前就回 403。

本檔的斷言分三層：

1. `src/ip_allowlist.py` 的解析與比對規則（純函式，無 IO）。
2. `lambda_handler.handler()` 的守衛行為，並以呼叫次數證明管線完全沒被觸發。
3. `aws/template.yaml`、`aws/deploy.sh`、`aws/verify-deployment.sh` 的靜態設定檢查，
   防止白名單在後續修改中被無聲移除或兩處分岔。

全部離線：不呼叫 AWS、不觸網、不需要憑證。
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import lambda_handler as lambda_module
from src.ip_allowlist import (ENV_VAR, OPEN, Allowlist, allowlist_from_env, format_entries,
                              is_allowed, parse_address, parse_allowlist, split_entries,
                              validate_entries)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = PROJECT_ROOT / "aws" / "template.yaml"
DEPLOY_SH = PROJECT_ROOT / "aws" / "deploy.sh"
VERIFY_SH = PROJECT_ROOT / "aws" / "verify-deployment.sh"

# 使用者指定的現場位址。這組值同時出現在 template.yaml 與 deploy.sh 的預設值，
# 下方的靜態測試會確認三處一致。
VENUE_IPS = (
    "60.250.15.18",
    "60.250.15.19",
    "60.250.15.34",
    "60.250.15.35",
    "60.250.15.36",
    "60.250.15.50",
    "60.250.15.51",
    "60.250.15.52",
)
VENUE_LIST = ",".join(VENUE_IPS)

# 同一個 /24 內但不在清單上的位址。用它測試「白名單是逐一位址，不是整段網路」。
NEIGHBOUR_IP = "60.250.15.20"
# 完全不同網路的位址（開發機當下的出口 IP 就屬於這一類）。
OUTSIDE_IP = "36.225.121.244"


def _get(path: str = "/", query=None, source_ip=None, headers=None) -> dict:
    http = {"method": "GET", "path": path}
    if source_ip is not None:
        http["sourceIp"] = source_ip
    return {
        "rawPath": path,
        "requestContext": {"http": http},
        "queryStringParameters": query or {},
        "headers": headers or {},
    }


def _post_json(payload: dict, source_ip=None, path: str = "/") -> dict:
    http = {"method": "POST", "path": path}
    if source_ip is not None:
        http["sourceIp"] = source_ip
    return {
        "rawPath": path,
        "requestContext": {"http": http},
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload, ensure_ascii=False),
    }


def _with_allowlist(value: str):
    """把白名單設成指定值。空字串代表未設定（不限制）。"""
    return patch.dict(os.environ, {ENV_VAR: value}, clear=False)


# ======================================================================================
# 1. 解析與比對規則
# ======================================================================================


class ParseAllowlistTests(unittest.TestCase):
    def test_the_eight_venue_addresses_are_all_accepted(self):
        allowlist = parse_allowlist(VENUE_LIST)

        self.assertEqual(allowlist.entry_count, 8)
        self.assertEqual(allowlist.invalid_entries, ())
        for address in VENUE_IPS:
            self.assertTrue(allowlist.allows(address), address)

    def test_addresses_outside_the_list_are_rejected(self):
        allowlist = parse_allowlist(VENUE_LIST)

        for address in (NEIGHBOUR_IP, OUTSIDE_IP, "127.0.0.1", "8.8.8.8"):
            self.assertFalse(allowlist.allows(address), address)

    def test_a_single_address_becomes_a_host_network(self):
        """單一 IP 必須收斂成 /32，不能被當成整段網路。"""
        allowlist = parse_allowlist("60.250.15.18")

        self.assertEqual([str(network) for network in allowlist.networks], ["60.250.15.18/32"])
        self.assertFalse(allowlist.allows(NEIGHBOUR_IP))

    def test_cidr_blocks_are_supported(self):
        allowlist = parse_allowlist("60.250.15.0/24")

        self.assertTrue(allowlist.allows("60.250.15.18"))
        self.assertTrue(allowlist.allows(NEIGHBOUR_IP))
        self.assertFalse(allowlist.allows("60.250.16.1"))

    def test_a_host_address_with_a_mask_is_normalised_instead_of_discarded(self):
        """`60.250.15.5/24` 是現場手寫白名單時很常見的寫法，不該整筆作廢。"""
        allowlist = parse_allowlist("60.250.15.5/24")

        self.assertEqual(allowlist.invalid_entries, ())
        self.assertEqual([str(network) for network in allowlist.networks], ["60.250.15.0/24"])

    def test_commas_semicolons_spaces_and_newlines_all_separate_entries(self):
        """現場調整白名單不該因為分隔符的寫法而失敗。"""
        mixed = "60.250.15.18, 60.250.15.19;60.250.15.34\n60.250.15.35\t60.250.15.36"
        allowlist = parse_allowlist(mixed)

        self.assertEqual(allowlist.entry_count, 5)
        self.assertEqual(allowlist.invalid_entries, ())

    def test_ipv6_addresses_and_brackets_are_supported(self):
        allowlist = parse_allowlist("[2001:db8::1], 2001:db8:1::/48")

        self.assertTrue(allowlist.allows("2001:db8::1"))
        self.assertTrue(allowlist.allows("2001:db8:1::abcd"))
        self.assertFalse(allowlist.allows("2001:db8:2::1"))

    def test_mixing_ipv4_and_ipv6_does_not_cross_match(self):
        """版本不同的位址與網段不得互相命中。"""
        allowlist = parse_allowlist("60.250.15.18, 2001:db8::/32")

        self.assertTrue(allowlist.allows("60.250.15.18"))
        self.assertTrue(allowlist.allows("2001:db8::5"))
        self.assertFalse(allowlist.allows("60.250.15.19"))

    def test_invalid_entries_are_recorded_and_valid_ones_still_work(self):
        """打錯一筆不該讓整份白名單失效，但也不能安靜地被丟掉。"""
        allowlist = parse_allowlist("60.250.15.18, 60.250.15.99999, ,not-an-ip")

        self.assertTrue(allowlist.allows("60.250.15.18"))
        self.assertEqual(allowlist.invalid_entries, ("60.250.15.99999", "not-an-ip"))
        self.assertEqual(allowlist.entry_count, 1)

    def test_a_fully_invalid_allowlist_rejects_everything(self):
        """有設定但全部無效時一律拒絕（fail closed）。

        打錯的白名單絕不能安靜地變成「對全世界開放」。`aws/deploy.sh` 會在部署前就以退出碼 2
        擋下這種輸入，因此這條路徑在正常流程中不會被走到——它是最後一道防線。
        """
        allowlist = parse_allowlist("oops, still-not-an-ip")

        self.assertTrue(allowlist.enforced)
        self.assertEqual(allowlist.entry_count, 0)
        self.assertFalse(allowlist.allows("60.250.15.18"))
        self.assertFalse(allowlist.allows(OUTSIDE_IP))


class UnsetAllowlistTests(unittest.TestCase):
    """未設定白名單時必須完全不限制，否則本機執行與既有測試會被自己的守衛擋住。"""

    def test_empty_string_means_no_restriction(self):
        for raw in ("", "   ", "\n", ",,, ;"):
            allowlist = parse_allowlist(raw)
            self.assertFalse(allowlist.enforced, repr(raw))
            self.assertTrue(allowlist.allows(OUTSIDE_IP), repr(raw))

    def test_open_allowlist_allows_even_an_unparseable_address(self):
        self.assertTrue(OPEN.allows(""))
        self.assertTrue(OPEN.allows("not-an-ip"))

    def test_default_constructed_allowlist_is_open(self):
        self.assertFalse(Allowlist().enforced)
        self.assertTrue(Allowlist().allows(OUTSIDE_IP))


class SourceAddressParsingTests(unittest.TestCase):
    def test_an_unparseable_source_address_is_rejected_when_enforcing(self):
        """讀不到來源位址時必須拒絕，不能因為「不知道是誰」就放行。"""
        allowlist = parse_allowlist(VENUE_LIST)

        for value in ("", "   ", None, "not-an-ip", "60.250.15", "60.250.15.18.1"):
            self.assertFalse(allowlist.allows(value), repr(value))

    def test_surrounding_whitespace_and_brackets_are_tolerated(self):
        allowlist = parse_allowlist(VENUE_LIST)

        self.assertTrue(allowlist.allows("  60.250.15.18  "))

    def test_parse_address_never_raises(self):
        self.assertIsNone(parse_address("garbage"))
        self.assertIsNone(parse_address(""))
        self.assertEqual(parse_address("60.250.15.18"), ipaddress.ip_address("60.250.15.18"))


class EnvironmentTests(unittest.TestCase):
    def test_allowlist_from_env_reads_the_documented_variable(self):
        self.assertEqual(ENV_VAR, "ALLOWED_SOURCE_IPS")

        with _with_allowlist(VENUE_LIST):
            allowlist = allowlist_from_env()

        self.assertEqual(allowlist.entry_count, 8)

    def test_a_blank_variable_is_treated_as_unset(self):
        with _with_allowlist("   "):
            self.assertFalse(allowlist_from_env().enforced)

    def test_an_explicit_mapping_can_be_supplied(self):
        self.assertTrue(allowlist_from_env({ENV_VAR: VENUE_LIST}).enforced)
        self.assertFalse(allowlist_from_env({}).enforced)


class HelperTests(unittest.TestCase):
    def test_split_entries_drops_blanks(self):
        self.assertEqual(split_entries(" a , , b "), ("a", "b"))

    def test_format_entries_normalises_and_deduplicates(self):
        self.assertEqual(
            format_entries(["60.250.15.18, 60.250.15.19", "60.250.15.18"]),
            "60.250.15.18,60.250.15.19")

    def test_validate_entries_reports_only_the_broken_ones(self):
        self.assertEqual(validate_entries(["60.250.15.18", "oops"]), ("oops",))
        self.assertEqual(validate_entries([VENUE_LIST]), ())

    def test_is_allowed_matches_the_method(self):
        allowlist = parse_allowlist(VENUE_LIST)

        self.assertTrue(is_allowed("60.250.15.18", allowlist))
        self.assertFalse(is_allowed(OUTSIDE_IP, allowlist))

    def test_describe_reports_state_without_listing_the_networks(self):
        summary = parse_allowlist(VENUE_LIST).describe()

        self.assertEqual(summary, {"enforced": True, "entry_count": 8, "invalid_entries": []})
        self.assertNotIn("60.250.15.18", json.dumps(summary))


# ======================================================================================
# 2. Lambda 進入點的守衛行為
# ======================================================================================


class HandlerAllowsListedSourcesTests(unittest.TestCase):
    def test_every_listed_address_reaches_the_home_page(self):
        with _with_allowlist(VENUE_LIST):
            for address in VENUE_IPS:
                response = lambda_module.handler(_get("/", source_ip=address), None)
                self.assertEqual(response["statusCode"], 200, address)
                self.assertIn("HOYA BIT", response["body"])

    def test_a_cidr_allowlist_reaches_the_home_page(self):
        with _with_allowlist("60.250.15.0/24"):
            response = lambda_module.handler(_get("/", source_ip=NEIGHBOUR_IP), None)

        self.assertEqual(response["statusCode"], 200)


class HandlerBlocksUnlistedSourcesTests(unittest.TestCase):
    """被擋下的請求必須在管線啟動之前就結束。"""

    def test_home_page_is_blocked_for_an_unlisted_address(self):
        with _with_allowlist(VENUE_LIST):
            response = lambda_module.handler(_get("/", source_ip=OUTSIDE_IP), None)

        self.assertEqual(response["statusCode"], 403)
        self.assertIn("來源位址未經授權", response["body"])

    def test_an_address_in_the_same_subnet_is_still_blocked(self):
        """白名單是逐一位址，鄰居不該因為同網段而通行。"""
        with _with_allowlist(VENUE_LIST):
            response = lambda_module.handler(_get("/", source_ip=NEIGHBOUR_IP), None)

        self.assertEqual(response["statusCode"], 403)

    def test_post_is_blocked_before_the_pipeline_runs(self):
        with _with_allowlist(VENUE_LIST), \
             patch.object(lambda_module, "_prepare_run") as mock_prepare, \
             patch.object(lambda_module, "run") as mock_run:
            response = lambda_module.handler(
                _post_json({"coin": "ETH", "question": "Q"}, source_ip=OUTSIDE_IP), None)

        self.assertEqual(response["statusCode"], 403)
        mock_prepare.assert_not_called()
        mock_run.assert_not_called()

    def test_a_missing_source_address_is_blocked_when_enforcing(self):
        """Function URL 一定會填 sourceIp；填不出來時只能拒絕，不能猜。"""
        with _with_allowlist(VENUE_LIST), \
             patch.object(lambda_module, "_prepare_run") as mock_prepare, \
             patch.object(lambda_module, "run") as mock_run:
            response = lambda_module.handler(_post_json({"coin": "ETH"}), None)

        self.assertEqual(response["statusCode"], 403)
        mock_prepare.assert_not_called()
        mock_run.assert_not_called()

    def test_every_route_is_covered_not_just_the_home_page(self):
        """守衛掛在 `handler()` 第一行，因此下載與報告路由不需要各自記得呼叫。"""
        routes = (
            _get("/", source_ip=OUTSIDE_IP),
            _get("/download", {"scope": "required"}, source_ip=OUTSIDE_IP),
            _get("/download", {"scope": "all"}, source_ip=OUTSIDE_IP),
            _get("/artifact", {"path": "report.md"}, source_ip=OUTSIDE_IP),
            _get("/report", {"run": "RUN-WHATEVER"}, source_ip=OUTSIDE_IP),
        )
        with _with_allowlist(VENUE_LIST):
            for event in routes:
                response = lambda_module.handler(event, None)
                self.assertEqual(response["statusCode"], 403, event["rawPath"])

    def test_the_guard_runs_before_the_test_only_mode_guard(self):
        """順序驗證：未授權來源不該先被判定 mode，也不該看到 mode 相關訊息。"""
        with _with_allowlist(VENUE_LIST):
            response = lambda_module.handler(
                _post_json({"coin": "ETH", "question": "Q", "mode": "formal"},
                           source_ip=OUTSIDE_IP), None)

        self.assertEqual(response["statusCode"], 403)
        self.assertIn("來源位址未經授權", response["body"])
        self.assertNotIn("test mode", response["body"])

    def test_a_fully_invalid_allowlist_blocks_everyone(self):
        with _with_allowlist("typo-only"), \
             patch.object(lambda_module, "_prepare_run") as mock_prepare:
            response = lambda_module.handler(_get("/", source_ip="60.250.15.18"), None)

        self.assertEqual(response["statusCode"], 403)
        mock_prepare.assert_not_called()


class ForwardedHeaderIsNotTrustedTests(unittest.TestCase):
    """`X-Forwarded-For` 由呼叫端自行填寫，讀它等於讓任何人自稱在白名單內。"""

    def test_a_spoofed_forwarded_header_does_not_grant_access(self):
        with _with_allowlist(VENUE_LIST):
            response = lambda_module.handler(
                _get("/", source_ip=OUTSIDE_IP,
                     headers={"x-forwarded-for": "60.250.15.18"}), None)

        self.assertEqual(response["statusCode"], 403)

    def test_a_forwarded_header_cannot_block_a_listed_address_either(self):
        with _with_allowlist(VENUE_LIST):
            response = lambda_module.handler(
                _get("/", source_ip="60.250.15.18",
                     headers={"x-forwarded-for": OUTSIDE_IP}), None)

        self.assertEqual(response["statusCode"], 200)

    def test_the_handler_reads_only_the_documented_field(self):
        self.assertEqual(
            lambda_module._source_ip(_get("/", source_ip=" 60.250.15.18 ")), "60.250.15.18")
        self.assertEqual(
            lambda_module._source_ip(_get("/", headers={"x-forwarded-for": "60.250.15.18"})), "")


class RejectionResponseTests(unittest.TestCase):
    def test_the_response_reports_the_observed_address(self):
        """現場唯一需要知道的事就是「該加哪個 IP」，因此把觀察到的位址講出來。

        那是呼叫端自己的位址，不構成額外資訊揭露。
        """
        with _with_allowlist(VENUE_LIST):
            body = lambda_module.handler(_get("/", source_ip=OUTSIDE_IP), None)["body"]

        self.assertIn(OUTSIDE_IP, body)

    def test_a_malicious_source_value_is_escaped(self):
        with _with_allowlist(VENUE_LIST):
            body = lambda_module.handler(
                _get("/", source_ip="<script>alert(1)</script>"), None)["body"]

        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_the_response_has_no_sensitive_detail(self):
        with _with_allowlist(VENUE_LIST):
            body = lambda_module.handler(_get("/", source_ip=OUTSIDE_IP), None)["body"]

        for forbidden in ("Traceback", "arn:aws", "lambda-url", ".on.aws", "Exception",
                          "AccessKey", "boto3"):
            self.assertNotIn(forbidden, body)

    def test_the_response_does_not_disclose_the_allowlist(self):
        """拒絕頁不列出白名單內容：那是給維運者看的資訊，不是給被擋下的來源。"""
        with _with_allowlist(VENUE_LIST):
            body = lambda_module.handler(_get("/", source_ip=OUTSIDE_IP), None)["body"]

        for allowed in VENUE_IPS:
            self.assertNotIn(allowed, body)

    def test_the_rejection_is_logged_with_the_address(self):
        """被拒絕的請求不會留下其他痕跡，這一行 log 是現場唯一的線索。"""
        with _with_allowlist(VENUE_LIST), \
             patch.object(lambda_module, "log_run_summary") as mock_log:
            lambda_module.handler(_get("/", source_ip=OUTSIDE_IP), None)

        mock_log.assert_called_once()
        event_name, payload = mock_log.call_args[0]
        self.assertEqual(event_name, "run-rejected")
        self.assertEqual(payload["reason"], "source_ip_not_allowed")
        self.assertEqual(payload["source_ip"], OUTSIDE_IP)
        self.assertEqual(payload["allowlist_entries"], 8)


class NoAllowlistKeepsExistingBehaviourTests(unittest.TestCase):
    """未設定白名單時，既有行為必須逐字不變——本機執行與既有測試都依賴這一點。"""

    def test_the_home_page_still_works_without_a_source_address(self):
        with _with_allowlist(""):
            response = lambda_module.handler(_get("/"), None)

        self.assertEqual(response["statusCode"], 200)
        self.assertIn("HOYA BIT", response["body"])

    def test_any_address_is_accepted(self):
        with _with_allowlist(""):
            for address in (OUTSIDE_IP, "8.8.8.8", "2001:db8::1"):
                self.assertEqual(
                    lambda_module.handler(_get("/", source_ip=address), None)["statusCode"], 200)

    def test_the_test_only_guard_still_rejects_formal_mode(self):
        """E1 的守衛不得因為本次變更而失效。"""
        with _with_allowlist(""), \
             patch.object(lambda_module, "_prepare_run") as mock_prepare, \
             patch.object(lambda_module, "run") as mock_run:
            response = lambda_module.handler(
                _post_json({"coin": "ETH", "question": "Q", "mode": "formal"}), None)

        self.assertEqual(response["statusCode"], 403)
        self.assertIn("test mode", response["body"])
        mock_prepare.assert_not_called()
        mock_run.assert_not_called()


# ======================================================================================
# 3. 部署設定的靜態檢查
# ======================================================================================


class TemplateConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = TEMPLATE.read_text(encoding="utf-8")

    def test_the_parameter_exists(self):
        self.assertIn("AllowedSourceIps:", self.text)

    def test_the_environment_variable_is_injected(self):
        """少了這一行，白名單就完全無法從雲端設定——參數會存在但沒有任何效果。"""
        self.assertIn("ALLOWED_SOURCE_IPS: !Ref AllowedSourceIps", self.text)

    def test_the_default_contains_every_requested_address(self):
        match = re.search(r"AllowedSourceIps:\s*\n\s*Type:\s*String\s*\n\s*Default:\s*(\S+)",
                          self.text)
        self.assertIsNotNone(match, "AllowedSourceIps 應宣告字串預設值")
        allowlist = parse_allowlist(match.group(1))
        self.assertEqual(allowlist.invalid_entries, ())
        for address in VENUE_IPS:
            self.assertTrue(allowlist.allows(address), address)

    def test_the_default_does_not_open_the_endpoint_to_everyone(self):
        match = re.search(r"AllowedSourceIps:\s*\n\s*Type:\s*String\s*\n\s*Default:\s*(\S+)",
                          self.text)
        allowlist = parse_allowlist(match.group(1))
        self.assertFalse(allowlist.allows(OUTSIDE_IP))
        self.assertFalse(allowlist.allows("8.8.8.8"))


class DeployScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DEPLOY_SH.read_text(encoding="utf-8")

    def test_the_parameter_is_passed_to_cloudformation(self):
        self.assertIn("AllowedSourceIps=$ALLOWED_IPS", self.text)

    def test_the_script_offers_both_flags(self):
        self.assertIn("--allowed-ips", self.text)
        self.assertIn("--allow-my-ip", self.text)

    def test_the_script_default_matches_the_template_default(self):
        """兩處分岔會讓「用腳本部署」與「直接用 CloudFormation 部署」得到不同的白名單。"""
        script = re.search(r'DEFAULT_ALLOWED_IPS="([^"]+)"', self.text)
        self.assertIsNotNone(script, "deploy.sh 應宣告 DEFAULT_ALLOWED_IPS")
        template = re.search(r"AllowedSourceIps:\s*\n\s*Type:\s*String\s*\n\s*Default:\s*(\S+)",
                             TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(format_entries([script.group(1)]),
                         format_entries([template.group(1)]))

    def test_an_explicitly_empty_list_is_not_replaced_by_the_default(self):
        """`${VAR-default}` 而非 `${VAR:-default}`：明確傳空字串必須代表「不限制」。"""
        self.assertIn("${DEPLOY_ALLOWED_SOURCE_IPS-$DEFAULT_ALLOWED_IPS}", self.text)

    def test_the_list_is_resolved_after_the_env_file_is_loaded(self):
        """順序反了的話，`.env` 裡的 DEPLOY_ALLOWED_SOURCE_IPS 會完全沒有效果。"""
        load_index = self.text.index("\nload_env_file\n")
        resolve_index = self.text.index("ALLOWED_IPS=\"${DEPLOY_ALLOWED_SOURCE_IPS")
        self.assertLess(load_index, resolve_index)

    def test_the_list_is_validated_before_deploying(self):
        """打錯字必須在部署前擋下，否則會部署出一個對所有人回 403 的網站。"""
        self.assertIn("validate_entries", self.text)


class VerifyScriptTests(unittest.TestCase):
    def test_a_403_is_explained_as_an_allowlist_block(self):
        """沒有這個分支，demo 前檢查只會顯示 HTTP 403 並建議重新部署——而重新部署沒用。"""
        text = VERIFY_SH.read_text(encoding="utf-8")

        self.assertIn("403", text)
        self.assertIn("來源位址未經授權", text)
        self.assertIn("--allow-my-ip", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
