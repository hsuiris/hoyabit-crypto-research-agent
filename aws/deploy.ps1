param(
    [string]$Region = "ap-northeast-1",
    [string]$StackName = "hoyabit-agent-mvp",
    [string]$LLMSecretArn = "",
    [ValidateSet("gemini", "openai")]
    [string]$LLMProvider = "gemini"
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
    aws s3api create-bucket --bucket $BucketName --region $Region --create-bucket-configuration LocationConstraint=$Region | Out-Null
}
$CodeKey = "releases/agent-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss')).zip"
aws s3 cp $ZipPath "s3://$BucketName/$CodeKey" --region $Region | Out-Null

aws cloudformation deploy `
    --template-file (Join-Path $PSScriptRoot "template.yaml") `
    --stack-name $StackName `
    --region $Region `
    --capabilities CAPABILITY_IAM `
    --parameter-overrides "CodeBucket=$BucketName" "CodeKey=$CodeKey" "LLMSecretArn=$LLMSecretArn" "LLMProvider=$LLMProvider"

aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs[?OutputKey=='PublicUrl'].OutputValue" --output text
