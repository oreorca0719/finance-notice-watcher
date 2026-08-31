# KB 공고 감시기 — Windows 작업 스케줄러 등록
#
#   powershell -ExecutionPolicy Bypass -File install_task.ps1
#   powershell -ExecutionPolicy Bypass -File install_task.ps1 -Time "09:00" -TaskName "KB공고감시"
#
# 등록 후에는 PC만 켜져 있으면 Claude/브라우저와 무관하게 동작합니다.
# 예약 시각에 PC가 꺼져 있었다면 부팅 직후 자동으로 따라잡아 실행합니다(-StartWhenAvailable).

param(
    [string]$Time = "09:00",
    [string]$TaskName = "KB공고감시"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition

# pythonw.exe 를 쓰면 콘솔 창이 뜨지 않습니다.
$py = "$env:LOCALAPPDATA\Programs\Python\Python313\pythonw.exe"
if (-not (Test-Path $py)) {
    $py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
}
if (-not $py) { throw "pythonw.exe 를 찾지 못했습니다. 파이썬 설치 경로를 확인하십시오." }

Write-Host "작업 폴더 : $root"
Write-Host "파이썬    : $py"
Write-Host "실행 시각 : 매일 $Time"

$action = New-ScheduledTaskAction -Execute $py `
    -Argument "`"$root\run_watch.py`"" -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger -Daily -At $Time

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "기존 작업을 제거하고 다시 등록합니다."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# 1순위: 로그온 없이도 실행되는 S4U. 권한이 없으면 대화형 로그온 방식으로 자동 강등.
$registered = $false
try {
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal `
        -Description "KB국민은행 공지사항 신규 프로젝트 공고 자동 확인 및 메일 발송" | Out-Null
    $registered = $true
    Write-Host "등록 완료 (S4U: 로그온 상태와 무관하게 실행)" -ForegroundColor Green
} catch {
    Write-Host "S4U 등록 실패 — 대화형 로그온 방식으로 전환합니다. ($($_.Exception.Message))"
}

if (-not $registered) {
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal `
        -Description "KB국민은행 공지사항 신규 프로젝트 공고 자동 확인 및 메일 발송" | Out-Null
    Write-Host "등록 완료 (Interactive: 사용자 로그온 상태에서 실행)" -ForegroundColor Green
}

Write-Host ""
Write-Host "다음 실행 예정:"
Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo |
    Select-Object LastRunTime, NextRunTime, LastTaskResult | Format-List

Write-Host "지금 즉시 한 번 돌려보려면:"
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`""
Write-Host "제거하려면:"
Write-Host "  Unregister-ScheduledTask -TaskName `"$TaskName`" -Confirm:`$false"
