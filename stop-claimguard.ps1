# ClaimGuard - Cost Hibernation Script
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " ClaimGuard - Stopping AWS Compute (Saving Credits)" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Get EC2 Instance ID
$instanceId = aws ec2 describe-instances `
    --filters "Name=tag:Name,Values=ClaimGuard-Web-Server" "Name=instance-state-name,Values=running" `
    --query "Reservations[0].Instances[0].InstanceId" `
    --output text

if ($instanceId -and $instanceId -ne "None") {
    Write-Host "Stopping EC2 Web Server: $instanceId..." -ForegroundColor Yellow
    aws ec2 stop-instances --instance-ids $instanceId | Out-Null
    Write-Host "[OK] EC2 stop initiated." -ForegroundColor Green
} else {
    Write-Host "No running EC2 Web Server found." -ForegroundColor DarkGray
}

# 2. Stop RDS DB Instance
Write-Host "Stopping RDS MySQL Database (claimguard-db)..." -ForegroundColor Yellow
try {
    aws rds stop-db-instance --db-instance-identifier claimguard-db | Out-Null
    Write-Host "[OK] RDS DB stop initiated." -ForegroundColor Green
} catch {
    Write-Host "RDS is already stopped or not in a stoppable state." -ForegroundColor DarkGray
}

Write-Host "`nAll compute instances are hibernated! Zero compute credit is billed." -ForegroundColor Green
Write-Host "To turn everything back on before viva, run: .\start-claimguard.ps1" -ForegroundColor Cyan
