# KB 공고 감시기 — 서버 설치 부트스트랩
#
# [사용법] 이 폴더 전체를 서버로 복사한 뒤, 서버에서 관리자 권한 PowerShell 로 실행:
#
#     powershell -ExecutionPolicy Bypass -File deploy_bootstrap.ps1
#
# 하는 일:
#   1) 파이썬 존재 확인 (없으면 설치 안내)
#   2) 의존 패키지 설치
#   3) .env 비밀번호 입력 (화면 비표시)
#   4) SMTP 인증 + 테스트 메일 검증
#   5) 기준선 등록
#   6) 작업 스케줄러 등록 — 로그오프 상태에서도 실행되도록 S4U 우선

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

function Step($n, $msg) { Write-Host ""; Write-Host "[$n] $msg" -ForegroundColor Cyan }

# --- 관리자 권한 확인 ---
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "관리자 권한이 아닙니다." -ForegroundColor Yellow
    Write-Host "PC 로그오프 상태에서도 실행되게 하려면 관리자 권한이 필요합니다."
    Write-Host "PowerShell 을 '관리자 권한으로 실행' 후 다시 시도하십시오." -ForegroundColor Yellow
    $ans = Read-Host "그래도 계속하시겠습니까? (로그온 상태에서만 실행됨) [y/N]"
    if ($ans -ne "y") { exit 1 }
}

# --- 1) 파이썬 ---
Step 1 "파이썬 확인"
$py = $null
foreach ($c in @(
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:ProgramFiles\Python313\python.exe",
    "$env:ProgramFiles\Python312\python.exe"
)) { if (Test-Path $c) { $py = $c; break } }
if (-not $py) { $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }

if (-not $py) {
    Write-Host "파이썬을 찾지 못했습니다." -ForegroundColor Red
    Write-Host "https://www.python.org/downloads/ 에서 3.12 이상을 설치하십시오."
    Write-Host "설치 시 'Add Python to PATH' 를 반드시 체크하십시오."
    exit 1
}
$pyw = $py -replace "python\.exe$", "pythonw.exe"
Write-Host "  파이썬: $py"
& $py --version

# --- 2) 의존 패키지 ---
Step 2 "의존 패키지 설치"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "패키지 설치 실패" -ForegroundColor Red; exit 1 }
Write-Host "  완료: requests / pydantic / PyYAML / python-dotenv"

# --- 3) 비밀번호 ---
Step 3 "발신 계정 비밀번호 설정"
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
$envText = Get-Content ".env" -Encoding UTF8
$hasPw = $envText | Where-Object { $_ -match '^\s*SMTP_PASSWORD\s*=\s*\S' }

if ($hasPw) {
    Write-Host "  이미 설정되어 있습니다. 건너뜁니다."
} else {
    Write-Host "  발신 계정: bjkim@pron.co.kr (smtp.whoisworks.com:587)"
    $secure = Read-Host -AsSecureString "  후이즈메일 비밀번호"
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    if ([string]::IsNullOrWhiteSpace($plain)) { Write-Host "입력이 비어 중단합니다." -ForegroundColor Red; exit 1 }

    $found = $false
    $out = foreach ($l in $envText) {
        if ($l -match '^\s*SMTP_PASSWORD\s*=') { $found = $true; "SMTP_PASSWORD=$plain" } else { $l }
    }
    if (-not $found) { $out += "SMTP_PASSWORD=$plain" }
    Set-Content -Path ".env" -Value $out -Encoding UTF8
    $plain = $null; [GC]::Collect()

    try {
        $acl = Get-Acl ".env"
        $acl.SetAccessRuleProtection($true, $false)
        # 실행 주체(SYSTEM)와 관리자를 반드시 포함해야 한다.
        # 사용자만 허용하면 SYSTEM 계정으로 등록된 예약 작업이 .env 를 읽지 못해
        # 매일 09시에 "SMTP 설정이 비어 있습니다" 로 조용히 실패한다.
        foreach ($id in @($env:USERNAME, "NT AUTHORITY\SYSTEM", "BUILTIN\Administrators")) {
            try {
                $acl.SetAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
                    $id, "FullControl", "Allow")))
            } catch { Write-Host "  ACL 항목 추가 실패($id): $($_.Exception.Message)" }
        }
        Set-Acl -Path ".env" -AclObject $acl
        Write-Host "  .env 접근 권한 제한: $env:USERNAME + SYSTEM + Administrators"
    } catch { Write-Host "  권한 제한 건너뜀: $($_.Exception.Message)" }
}

# --- 4) 발송 검증 ---
Step 4 "SMTP 인증 및 테스트 메일"
& $py "run_watch.py" "--test-mail"
if ($LASTEXITCODE -ne 0) {
    Write-Host "발송 검증 실패. 위 오류를 확인하십시오." -ForegroundColor Red
    exit 1
}

# --- 5) 기준선 ---
Step 5 "기준선 등록"
if (Test-Path "data\state.sqlite3") {
    Write-Host "  기존 상태 파일이 있습니다. 이어서 사용합니다(중복 발송 방지 이력 유지)."
} else {
    & $py "run_watch.py" "--seed"
}

# --- 6) 스케줄러 ---
Step 6 "작업 스케줄러 등록"
$taskName = "KB공고감시"
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$root\run_watch.py`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At "09:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew -WakeToRun

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$done = $false
if ($isAdmin) {
    try {
        $p = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $p -Description "KB국민은행 신규 프로젝트 공고 자동 알림" | Out-Null
        $done = $true
        Write-Host "  등록 완료 (SYSTEM 계정: 로그오프 상태에서도 실행)" -ForegroundColor Green
    } catch { Write-Host "  SYSTEM 등록 실패: $($_.Exception.Message)" }
}
if (-not $done) {
    $p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $p -Description "KB국민은행 신규 프로젝트 공고 자동 알림" | Out-Null
    Write-Host "  등록 완료 (Interactive: 로그온 상태에서만 실행)" -ForegroundColor Yellow
}

Write-Host ""
Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo |
    Select-Object NextRunTime, LastRunTime, LastTaskResult | Format-List
Write-Host "설치 완료." -ForegroundColor Green
