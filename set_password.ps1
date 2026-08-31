# 후이즈메일 비밀번호를 .env 에 안전하게 저장합니다.
#
#   powershell -ExecutionPolicy Bypass -File set_password.ps1
#
# 입력한 비밀번호는 화면에 표시되지 않고, 이 창의 명령 기록에도 남지 않습니다.
# 저장 후 곧바로 실제 SMTP 인증까지 검증합니다.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$envPath = Join-Path $root ".env"

if (-not (Test-Path $envPath)) {
    Copy-Item (Join-Path $root ".env.example") $envPath
    Write-Host ".env 를 새로 만들었습니다."
}

Write-Host ""
Write-Host "발신 계정: bjkim@pron.co.kr (smtp.whoisworks.com:587)" -ForegroundColor Cyan
Write-Host "후이즈메일 비밀번호를 입력하십시오. 입력 내용은 화면에 보이지 않습니다."
$secure = Read-Host -AsSecureString "비밀번호"

$bstr  = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

if ([string]::IsNullOrWhiteSpace($plain)) {
    Write-Host "입력이 비어 있어 중단합니다." -ForegroundColor Red
    exit 1
}

# SMTP_PASSWORD 줄만 교체하고 나머지는 그대로 둔다.
$lines = Get-Content $envPath -Encoding UTF8
$found = $false
$out = foreach ($l in $lines) {
    if ($l -match '^\s*SMTP_PASSWORD\s*=') { $found = $true; "SMTP_PASSWORD=$plain" }
    else { $l }
}
if (-not $found) { $out += "SMTP_PASSWORD=$plain" }
Set-Content -Path $envPath -Value $out -Encoding UTF8

$plain = $null
[GC]::Collect()

Write-Host ""
Write-Host ".env 저장 완료." -ForegroundColor Green

# .env 파일 접근 권한을 현재 사용자로 제한
try {
    $acl = Get-Acl $envPath
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($id in @($env:USERNAME, "NT AUTHORITY\SYSTEM", "BUILTIN\Administrators")) {
        try {
            $acl.SetAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
                $id, "FullControl", "Allow")))
        } catch { Write-Host "  ACL 항목 추가 실패($id)" }
    }
    Set-Acl -Path $envPath -AclObject $acl
    Write-Host ".env 접근 권한 제한: $env:USERNAME + SYSTEM + Administrators" -ForegroundColor Green
} catch {
    Write-Host "권한 제한은 건너뜁니다: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "=== SMTP 인증 및 테스트 메일 발송 ===" -ForegroundColor Cyan
$py = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
if (-not (Test-Path $py)) { $py = (Get-Command python.exe).Source }

Push-Location $root
& $py "run_watch.py" "--test-mail"
$code = $LASTEXITCODE
Pop-Location

Write-Host ""
if ($code -eq 0) {
    Write-Host "성공. bjkim@pron.co.kr 메일함을 확인하십시오." -ForegroundColor Green
    Write-Host "다음: powershell -ExecutionPolicy Bypass -File install_task.ps1"
} else {
    Write-Host "실패했습니다. 위에 출력된 오류 메시지를 그대로 확인하십시오." -ForegroundColor Red
    Write-Host "  SMTPAuthenticationError  -> 비밀번호가 틀렸거나 SMTP 사용이 차단됨" -ForegroundColor Yellow
    Write-Host "  SSLError / TLS           -> 서버 TLS 설정 문제" -ForegroundColor Yellow
    Write-Host "  그 외 Traceback          -> 프로그램 결함이므로 오류 전문을 전달하십시오." -ForegroundColor Yellow
}
