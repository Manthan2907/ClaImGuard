# ClaimGuard - Set Google Gemini API Key in AWS Systems Manager
param (
    [Parameter(Mandatory=$true)]
    [string]$ApiKey
)

Write-Host "Updating Gemini API Key in AWS Systems Manager Parameter Store..." -ForegroundColor Cyan
aws ssm put-parameter `
    --name "/claimguard/ai/gemini_api_key" `
    --value $ApiKey `
    --type "SecureString" `
    --overwrite

Write-Host "[OK] Gemini API Key securely stored in SSM Parameter: /claimguard/ai/gemini_api_key" -ForegroundColor Green
