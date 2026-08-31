# 서버 실행 진입점 — 최신 코드를 받아온 뒤 감시를 실행한다.
#
# 작업 스케줄러는 run_watch.py 가 아니라 이 스크립트를 호출합니다.
#   powershell -ExecutionPolicy Bypass -File update_and_run.ps1
#
# 설계 원칙: git pull 이 실패해도 실행은 계속한다.
#   네트워크 문제나 GitHub 장애 때문에 공고 조회까지 멈추면 안 되기 때문입니다.
#   기존 코드로라도 조회하고 메일을 보내는 것이 우선입니다.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# 예약 작업은 창을 숨긴 채 돌기 때문에 화면 출력만으로는 나중에 확인할 수 없다.
# git pull 성공 여부를 사후에 따질 수 있도록 파일에도 남긴다.
$LogDir  = Join-Path $root "logs"
$LogFile = Join-Path $LogDir "update.log"
New-Item -ItemType Directory -Force $LogDir | Out-Null

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts  $msg"
    Write-Host $line
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch { }
}

# 로그 파일이 무한정 커지지 않도록 1MB 넘으면 잘라낸다.
if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 1MB)) {
    try {
        $keep = Get-Content $LogFile -Tail 300 -Encoding UTF8
        Set-Content -Path $LogFile -Value $keep -Encoding UTF8
    } catch { }
}

Log "===== 실행 시작 (계정: $env:USERNAME) ====="

# --- 1) 코드 갱신 ---------------------------------------------------------
$git = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
if (-not $git) {
    Log "git 을 찾지 못했습니다. 코드 갱신을 건너뛰고 기존 코드로 실행합니다."
} elseif (-not (Test-Path (Join-Path $root ".git"))) {
    Log "git 저장소가 아닙니다. 코드 갱신을 건너뜁니다."
} else {
    Log "코드 갱신 확인 중..."
    $before = (& $git -C $root rev-parse --short HEAD 2>$null)
    $out = (& $git -C $root pull --ff-only 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0) {
        $after = (& $git -C $root rev-parse --short HEAD 2>$null)
        if ($before -ne $after) { Log "코드 갱신됨: $before -> $after" }
        else { Log "이미 최신 상태 ($after)" }
    } else {
        Log "git pull 실패 — 기존 코드로 진행합니다."
        Log $out
    }
}

# --- 2) 파이썬 확인 -------------------------------------------------------
$py = $null
foreach ($c in @(
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python38-32\python.exe",
    "$env:ProgramFiles\Python312\python.exe"
)) { if (Test-Path $c) { $py = $c; break } }
if (-not $py) { $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
if (-not $py) { Log "파이썬을 찾지 못했습니다. 중단합니다."; exit 1 }

# --- 3) 의존 패키지 (requirements.txt 가 바뀐 경우에만) -------------------
$reqPath = Join-Path $root "requirements.txt"
$hashFile = Join-Path $root "data\.reqhash"
if (Test-Path $reqPath) {
    $cur = (Get-FileHash $reqPath -Algorithm SHA256).Hash
    $old = if (Test-Path $hashFile) { (Get-Content $hashFile -Raw).Trim() } else { "" }
    if ($cur -ne $old) {
        Log "requirements.txt 변경 감지 — 패키지를 갱신합니다."
        & $py -m pip install --quiet -r $reqPath
        if ($LASTEXITCODE -eq 0) {
            New-Item -ItemType Directory -Force (Split-Path $hashFile) | Out-Null
            Set-Content -Path $hashFile -Value $cur -Encoding ASCII
        } else {
            Log "패키지 갱신 실패 — 기존 환경으로 진행합니다."
        }
    }
}

# --- 4) 실행 --------------------------------------------------------------
Log "감시 실행"
& $py (Join-Path $root "run_watch.py") @args
$code = $LASTEXITCODE
Log "종료코드 $code"
if ($code -ne 0) { Log "!! 실행이 실패했습니다. logs\watch.log 를 확인하십시오." }
exit $code
