---
inclusion: always
---

# Testing and Phase Gates

## 基本原則

任何 Task 都不得以「看起來正確」取代測試。

每個 Task 必須：

1. 執行 targeted tests。
2. 執行目前 repository 文件指定的完整測試套件。
3. 保存實際命令與結果。
4. 失敗時回報實際失敗，不得宣稱 PASS。
5. 更新 `docs/COMPETITION_TASK_STATUS.yaml`。
6. 建立 commit 後才可回報完成。

## Task 狀態

```text
TODO
READY
IN_PROGRESS
REVIEW
PASS
FAIL
BLOCKED
PARTIAL
```

只有 `PASS` 才能解鎖下一個 Task。

`PARTIAL` 不等於 PASS；只有在時間切點要求降級時，由使用者明確接受後才能進下一階段。

## Gate 規則

每個 Task 完成後，至少檢查：

- Acceptance Criteria 是否逐項通過。
- 是否擴大了 Out of Scope。
- 是否破壞 offline fallback。
- 是否破壞既有測試。
- 是否新增敏感資訊。
- 是否實際建立 commit。
- 是否更新狀態與 blocker。

## 測試順序

```text
syntax / import
→ targeted unit tests
→ targeted integration smoke
→ full test suite
→ offline end-to-end
→ live smoke（只在 T8，且只執行一次）
```

## 外部服務測試

- Bedrock、外部 API 與網路服務在 unit tests 中必須 mock。
- Live smoke 不得成為完整測試套件必要條件。
- Live 失敗時，offline fallback 必須仍能產生提交物。
- 不得因網路無法連線而刪除或停用 fallback。

## 失敗處理

遇到錯誤時：

1. 先保存錯誤輸出。
2. 判斷是否屬於目前 Task。
3. 若是既有 baseline failure，記錄但不要擴大修復範圍。
4. 若是本 Task regression，必須修復。
5. blocker 超過 20 分鐘，採安全 fallback 並寫入 blocker 文件。
6. 不得隱藏 warning、跳過測試或改測試使其假通過。

## Final Gate

T8 最終至少驗證：

- 五幣輸入。
- 多源題、假設題、比較題。
- Bedrock timeout 與非法 JSON。
- News／On-chain／Social 來源缺失。
- 同源轉載。
- 未知 Evidence ID。
- fallback sole support。
- 高品質衝突。
- deadline 降級。
- 所有提交物與 manifest hash。
