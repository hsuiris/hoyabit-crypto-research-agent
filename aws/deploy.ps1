# Windows 部署腳本。POSIX 環境請用 aws/deploy.sh（行為對等）。
#
# Region 與 BedrockModelId 是一組，不可分開改：模型可用形式依 region 而異。
# us-west-2 + amazon.nova-lite-v1:0 已於 2026-08-01 實測支援 ON_DEMAND。
# 換 region 前先跑 aws/verify-permissions.sh 的第 3 項重新實測。
param(
    [string]$Region = "us-west-2",
    [string]$StackName = "hoyabit-agent-mvp",
    [string]$LLMSecretArn = "",
    [ValidateSet("bedrock", "gemini", "openai", "none")]
    [string]$LLMProvider = "bedrock",
    [string]$BedrockModelId = "amazon.nova-lite-v1:0"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BuildRoot = Join-Path $PSScriptRoot ".build"
$StageRoot = Join-Path $BuildRoot "package"
$ZipPath = Join-Path $BuildRoot "agent.zip"

if (Test-Path -LiteralPath $BuildRoot) { Remove-Item -LiteralPath $BuildRoot -Recurse -Force }
New-Item -ItemType Directory -Path $StageRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot "lambda_handler.py") -Destination $StageRoot
Copy-Item -LiteralPath (Join-Path $ProjectRoot "src") -Destination $StageRoot -Recurse
if (Test-Path -LiteralPath (Join-Path $ProjectRoot "data")) { Copy-Item -LiteralPath (Join-Path $ProjectRoot "data") -Destination $StageRoot -Recurse }
Get-ChildItem -LiteralPath $StageRoot -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Compress-Archive -Path (Join-Path $StageRoot "*") -DestinationPath $ZipPath

$AccountId = aws sts get-caller-identity --query Account --output text
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($AccountId)) {
    throw "AWS credentials are not configured. Deployment package is ready at $ZipPath. Run 'aws login' and retry."
}
$BucketName = "hoyabit-agent-deploy-$AccountId-$Region"
aws s3api head-bucket --bucket $BucketName 2>$null
if ($LASTEXITCODE -ne 0) {
    # us-east-1 是特例：CreateBucket 不接受 LocationConstraint，帶了會失敗。
    if ($Region -eq "us-east-1") {
        aws s3api create-bucket --bucket $BucketName --region $Region | Out-Null
    } else {
        aws s3api create-bucket --bucket $BucketName --region $Region --create-bucket-configuration LocationConstraint=$Region | Out-Null
    }
    aws s3api put-public-access-block --bucket $BucketName --region $Region `
        --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" 2>$null | Out-Null
}
$CodeKey = "releases/agent-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss')).zip"
aws s3 cp $ZipPath "s3://$BucketName/$CodeKey" --region $Region | Out-Null

aws cloudformation deploy `
    --template-file (Join-Path $PSScriptRoot "template.yaml") `
    --stack-name $StackName `
    --region $Region `
    --capabilities CAPABILITY_IAM `
    --no-fail-on-empty-changeset `
    --parameter-overrides "CodeBucket=$BucketName" "CodeKey=$CodeKey" "LLMSecretArn=$LLMSecretArn" "LLMProvider=$LLMProvider" "BedrockModelId=$BedrockModelId"

aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs[?OutputKey=='PublicUrl'].OutputValue" --output text
