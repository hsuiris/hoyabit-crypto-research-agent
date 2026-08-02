"""D7：部署護欄在 CloudFormation 模板中的存在性檢查。

這些是模板的靜態檢查，不呼叫 AWS。目的是防止護欄在後續修改中被無聲移除——
`aws/template.yaml` 的 Function URL 是公開無認證端點，護欄是它唯一的成本與暴露天花板。

實際的生效驗證（reserved concurrency 真的是 5、log 保留真的是 7 天）屬於部署後的
人工／CLI 驗收，記錄在 .kiro/specs/hoyabit-aws-deployment/status.yaml 的 D7。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "aws" / "template.yaml"
DEPLOY_SH = Path(__file__).resolve().parent.parent / "aws" / "deploy.sh"
VERIFY_SH = Path(__file__).resolve().parent.parent / "aws" / "verify-deployment.sh"


class TemplateGuardrailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = TEMPLATE.read_text(encoding="utf-8")

    def test_reserved_concurrency_is_configured(self):
        # 沒有這個上限，公開端點的成本沒有天花板。
        self.assertIn("ReservedConcurrentExecutions:", self.text)
        self.assertIn("ReservedConcurrency", self.text)

    def test_reserved_concurrency_max_respects_account_headroom(self):
        # 本帳號 UnreservedConcurrentExecutions 只有 110，而 AWS 要求設定 reserved
        # concurrency 後帳號仍須保留至少 100，因此上限不能超過 10。
        match = re.search(r"ReservedConcurrency:.*?MaxValue:\s*(\d+)", self.text, re.S)
        self.assertIsNotNone(match, "ReservedConcurrency 應宣告 MaxValue")
        self.assertLessEqual(int(match.group(1)), 10)

    def test_log_group_is_explicit_with_retention(self):
        # 顯式管理 log group 才能設保留期；否則 Lambda 會自建一個永久保留的。
        self.assertIn("AWS::Logs::LogGroup", self.text)
        self.assertIn("RetentionInDays:", self.text)

    def test_log_retention_is_not_unlimited(self):
        match = re.search(r"LogRetentionDays:.*?AllowedValues:\s*\[([^\]]+)\]", self.text, re.S)
        self.assertIsNotNone(match, "LogRetentionDays 應限制在有限的天數清單內")
        values = [v.strip() for v in match.group(1).split(",")]
        self.assertTrue(all(v.isdigit() and int(v) > 0 for v in values), values)

    def test_function_depends_on_log_group(self):
        # 順序很重要：Lambda 若先建立，會自己建一個沒有保留期的同名 group，
        # CloudFormation 之後就無法接管（資源已存在）。
        match = re.search(r"AgentFunction:\s*\n\s*Type:\s*AWS::Lambda::Function\s*\n\s*DependsOn:\s*AgentLogGroup",
                          self.text)
        self.assertIsNotNone(match, "AgentFunction 必須 DependsOn AgentLogGroup")

    def test_auth_type_is_parameterised_with_aws_iam_option(self):
        # 要能在不改模板的情況下收斂暴露面。
        self.assertIn("FunctionUrlAuthType", self.text)
        match = re.search(r"FunctionUrlAuthType:.*?AllowedValues:\s*\[([^\]]+)\]", self.text, re.S)
        self.assertIsNotNone(match)
        self.assertIn("AWS_IAM", match.group(1))

    def test_public_invoke_permissions_are_conditional(self):
        # 切成 AWS_IAM 時，公開叫用權限必須一併消失，否則認證等於沒設。
        self.assertIn("IsPublicUrl", self.text)
        for block in ("AgentFunctionUrlPermission", "AgentFunctionInvokePermission"):
            segment = self.text.split(block, 1)[1][:220]
            self.assertIn("Condition: IsPublicUrl", segment, f"{block} 應受 IsPublicUrl 條件控制")

    def test_bedrock_permission_stays_scoped_to_one_model(self):
        # D7 不得放寬 D2 建立的最小權限邊界。
        self.assertIn("foundation-model/${BedrockModelId}", self.text)
        self.assertNotIn("AdministratorAccess", self.text)
        self.assertNotIn("bedrock:*", self.text)


class DeployScriptGuardrailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DEPLOY_SH.read_text(encoding="utf-8")

    def test_deploy_script_passes_all_three_guardrails(self):
        for param in ("FunctionUrlAuthType=", "ReservedConcurrency=", "LogRetentionDays="):
            self.assertIn(param, self.text, f"deploy.sh 應傳遞 {param}")

    def test_auth_type_is_validated(self):
        self.assertIn("AWS_IAM", self.text)
        self.assertRegex(self.text, r'case "\$AUTH_TYPE" in')

    def test_teardown_instruction_is_present(self):
        # 拆除是公開端點最有效的收斂手段，腳本輸出必須帶上它。
        self.assertIn("delete-stack", self.text)

    def test_no_variable_is_followed_directly_by_a_non_ascii_character(self):
        """`$VAR（…` 在 `set -u` 下會中止腳本，而既有的驗證方式都抓不到。

        bash 會把緊接在 `$VAR` 後的非 ASCII 位元組算進變數名，於是 `$MODEL_ID（` 展開成一個
        不存在的變數；腳本開頭是 `set -uo pipefail`，因此直接中止。

        這個缺陷曾實際發生在 line 334，而且**兩道既有防線都攔不住**：
        `bash -n` 只檢查語法，未綁定變數是執行期錯誤；`--dry-run` 在步驟 1 就 exit，
        永遠走不到步驟 5 的那行 log。結果是部署在上傳 ZIP 之後、`cloudformation deploy`
        之前中止 —— 看起來像「跑了但沒生效」。

        正解是一律寫 `${VAR}`（腳本裡既有的 `${LOG_RETENTION} 天` 就是這樣寫的）。
        這條規則對本專案特別重要：所有面向使用者的輸出都是繁體中文，全形標點緊接變數
        是很自然的寫法。
        """
        pattern = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)(?=[^\x00-\x7f])")
        for path in (DEPLOY_SH, VERIFY_SH):
            offenders = [
                "%s:%d: $%s 後面緊接非 ASCII 字元，請改用 ${%s}"
                % (path.name, number, match.group(1), match.group(1))
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                for match in pattern.finditer(line)
            ]
            self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
