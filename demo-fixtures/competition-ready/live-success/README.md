# Live smoke 保存位置

此目錄只保留唯一一次成功的 Bedrock live smoke 六項產物；絕不放入模擬或離線輸出。

## T8 實際結果

2026-08-01 的唯一 BTC live smoke 以 `amazon.nova-lite-v1:0`／`ap-northeast-1` 執行：11 個外部 collector 成功、Citation Gate PASS、runtime 2.9 秒，但本機缺少 `boto3`，所以 Bedrock analyst 與 Critic 均轉為 deterministic fallback，狀態為 `COMPLETED_DEGRADED`。因此這裡**刻意沒有**複製該輸出；它不是 live 模型成功。

若現場 live 失敗，直接展示 `../offline-backup/`；其 `execution_log.json` 與 `manifest.json` 可說明降級與完整性。在含 AWS SDK、可用 credentials 與 model access 的競賽環境，僅執行一次成功 live smoke 後才可將完整六檔放入此目錄。
