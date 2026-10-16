# 클라우드 세션 작업을 PC에 반영한다 (2026-10-16).
# 실행: P2 폴더에서  powershell -ExecutionPolicy Bypass -File tools\apply-cloud-work.ps1
# 하는 일: P1·P2 브랜치 fast-forward 받아오기 → 메인 main.html에 공통 상단바 적용(백업 후) → 네 페이지 열기.
# 하지 않는 일: 커밋·푸시, 유료 API, reset, 기존 증거 수정. 예상과 다르면 바꾸지 않고 멈춘다.
param([switch]$NoOpen)

$ErrorActionPreference = 'Stop'
$Branch = 'claude/fixes-20261015-le3mg5'
$P2 = Split-Path $PSScriptRoot -Parent
$Parent = Split-Path $P2 -Parent
$P1 = Join-Path $Parent 'qa-agent-pipeline-v2'
$MainDir = Join-Path $Parent 'portfolio_export'
$Main = Join-Path $MainDir 'main.html'
$Backup = Join-Path $MainDir 'main.before-pfbar-20261016.html'
$Utf8 = New-Object System.Text.UTF8Encoding $false

function Step($message) { Write-Host "`n== $message" -ForegroundColor Cyan }
function Halt($message) { Write-Host "멈춤: $message" -ForegroundColor Red; exit 1 }

Step "폴더 확인 (부모 폴더: $Parent)"
foreach ($path in @($P1, $P2, $Main)) {
    if (-not (Test-Path $path)) { Halt "없음: $path  (세 폴더가 같은 부모 폴더 아래 있어야 합니다)" }
    Write-Host "있음: $path"
}

function Sync-Repo($repo) {
    Step "받아오기: $repo"
    $dirty = git -C $repo status --porcelain --untracked-files=no
    if ($dirty) { Write-Host ($dirty -join "`n"); Halt "저장하지 않은 변경이 있어 받아오지 않았습니다." }
    git -C $repo fetch origin $Branch
    if ($LASTEXITCODE -ne 0) { Halt "fetch 실패 (인터넷·GitHub 로그인 확인)" }
    git -C $repo merge --ff-only FETCH_HEAD
    if ($LASTEXITCODE -ne 0) { Halt "fast-forward 불가. 합치지 않았습니다. 이 화면을 Claude에게 보여 주세요." }
    git -C $repo log --oneline -1
}
Sync-Repo $P1
Sync-Repo $P2

Step "메인 페이지 상단바"
$html = [System.IO.File]::ReadAllText($Main, [System.Text.Encoding]::UTF8)
if ($html.Contains('class="pf-bar"')) {
    Write-Host "이미 적용되어 있어 바꾸지 않았습니다."
} else {
    $snippet = [System.IO.File]::ReadAllText((Join-Path $P2 'portfolio\shared-nav.html'), [System.Text.Encoding]::UTF8)
    $style = [regex]::Match($snippet, '(?s)<style id="pf-bar-style">.*?</style>').Value
    $bar = [regex]::Match($snippet, '(?s)<nav class="pf-bar".*?</nav>').Value
    if (-not $style -or -not $bar) { Halt "shared-nav.html에서 상단바를 찾지 못했습니다." }
    $navs = [regex]::Matches($html, '(?s)<nav\b[^>]*>.*?</nav>')
    if ($navs.Count -ne 1 -or ([regex]::Matches($html, '<nav\b')).Count -ne 1) { Halt "main.html의 메뉴(<nav>)가 1개가 아니라 자동으로 바꾸지 않았습니다." }
    $head = $html.IndexOf('</head>')
    if ($head -lt 0) { Halt "main.html에 </head>가 없습니다." }
    if (-not (Test-Path $Backup)) { Copy-Item $Main $Backup; Write-Host "백업: $Backup" }
    $old = $navs[0]
    $html = $html.Remove($old.Index, $old.Length).Insert($old.Index, $bar)
    $html = $html.Insert($html.IndexOf('</head>'), $style + "`n")
    [System.IO.File]::WriteAllText($Main, $html, $Utf8)
    Write-Host "적용했습니다. (되돌리기: 백업 파일을 main.html로 복사)"
}

Step "결과"
Write-Host "P1: $(git -C $P1 log --oneline -1)"
Write-Host "P2: $(git -C $P2 log --oneline -1)"
Write-Host "메인 변경은 커밋하지 않았습니다(커밋 여부는 사용자 결정)."

if (-not $NoOpen) {
    Step "페이지 열기"
    foreach ($page in @($Main, (Join-Path $P1 'project.html'), (Join-Path $P2 'portfolio\index.html'), (Join-Path $P2 'portfolio\report.html'))) {
        Write-Host $page
        Start-Process $page
    }
}
