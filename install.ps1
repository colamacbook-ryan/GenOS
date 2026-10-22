#Requires -Version 5.1
<#
Gen-Harness — bootstrap một lệnh (Windows, PowerShell 5.1+).

    irm https://github.com/Genesis-ryan-84-0567536339/Gen-Harness/releases/latest/download/install.ps1 | iex

Việc duy nhất của tệp này: tải đúng binary genh.exe cho máy, kiểm
SHA-256, thêm vào PATH người dùng (không cần quyền admin), rồi giao lại
cho `genh install` (xem docs/handoff/05-installer.md). Chỉ giả định có
PowerShell 5.1 + mạng — không cần Docker/git/Node/Python có sẵn.
#>

$ErrorActionPreference = 'Stop'

$Repo = 'Genesis-ryan-84-0567536339/Gen-Harness'
$ReleaseBase = "https://github.com/$Repo/releases/latest/download"

$InstallRoot = $env:GEN_HARNESS_HOME
if (-not $InstallRoot) {
    $InstallRoot = Join-Path $env:LOCALAPPDATA 'GenHarness'
}
$BinDir = Join-Path $InstallRoot 'bin'
# SecretsFile: dung config.Paths.ConfigDir()/secrets.json ma secretgen.Ensure
# ghi o Buoc 4 - co nghia may nay DA co mot ban cai (xem Main() ben duoi).
$SecretsFile = Join-Path (Join-Path $InstallRoot 'config') 'secrets.json'

function Write-Log {
    param([string]$Message)
    Write-Host "genh: $Message"
}

function Fail {
    param([string]$Message)
    Write-Host "genh: $Message" -ForegroundColor Red
    exit 1
}

function Get-Arch {
    # ARM64 bao qua PROCESSOR_ARCHITECTURE tren Windows 11 ARM; con lai (amd64/WOW64) -> amd64.
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) {
        $arch = $env:PROCESSOR_ARCHITEW6432
    }
    if ($arch -eq 'ARM64') {
        return 'arm64'
    }
    return 'amd64'
}

function Get-Sha256 {
    param([string]$Path)
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Test-Checksum {
    param(
        [string]$FilePath,
        [string]$FileName,
        [string]$ChecksumsPath
    )

    $want = $null
    foreach ($line in Get-Content -Path $ChecksumsPath) {
        $parts = $line -split '\s+' | Where-Object { $_ -ne '' }
        if ($parts.Count -lt 2) { continue }
        $name = $parts[1] -replace '^\./', ''
        if ($name -eq $FileName) {
            $want = $parts[0].ToLowerInvariant()
            break
        }
    }
    if (-not $want) {
        Fail "khong tim thay $FileName trong checksums.txt cua ban phat hanh - dung, khong chay."
    }

    $got = Get-Sha256 -Path $FilePath
    if ($got -ne $want) {
        Fail "SHA-256 cua $FileName khong khop checksums.txt (muon $want, duoc $got) - dung, khong chay."
    }
}

function Add-ToUserPath {
    param([string]$Dir)

    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($null -eq $current) { $current = '' }

    $entries = $current -split ';' | Where-Object { $_ -ne '' }
    if ($entries -contains $Dir) {
        return
    }

    $updated = if ($current.Trim() -eq '') { $Dir } else { "$current;$Dir" }
    [Environment]::SetEnvironmentVariable('Path', $updated, 'User')
}

function Invoke-Download {
    param([string]$Url, [string]$Dest)
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Dest
}

function Main {
    $arch = Get-Arch
    $asset = "genh-windows-${arch}.exe"

    Write-Log "dang tai $asset tu ban phat hanh moi nhat..."

    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) "genh-install-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

    $exitCode = 0
    try {
        $assetPath = Join-Path $tmpDir $asset
        $checksumsPath = Join-Path $tmpDir 'checksums.txt'

        Invoke-Download -Url "$ReleaseBase/$asset" -Dest $assetPath
        Invoke-Download -Url "$ReleaseBase/checksums.txt" -Dest $checksumsPath

        Test-Checksum -FilePath $assetPath -FileName $asset -ChecksumsPath $checksumsPath

        $finalPath = Join-Path $BinDir 'genh.exe'
        Move-Item -Path $assetPath -Destination $finalPath -Force

        Add-ToUserPath -Dir $BinDir
        $env:Path = "$BinDir;$env:Path"

        Write-Log "da cai vao $finalPath - mo cua so PowerShell moi de PATH co hieu luc lau dai."

        # May DA CAI (co secrets.json): CHAY LUON "genh update" thay vi "genh
        # install" (se dung lai container, bo qua backup) - an toan tu v0.1.5
        # vi genh update TU CO backup + rollback va TU tai ban genh moi nhat
        # (internal/selfupdate); "genh auto-update" lo viec chay dinh ky.
        if (Test-Path -Path $SecretsFile -PathType Leaf) {
            Write-Log "may nay da cai Gen-Harness tu truoc - dang chay 'genh update' de nang cap dich vu len dung ban moi..."
            & $finalPath update
            $exitCode = $LASTEXITCODE
        }
        else {
            & $finalPath install
            $exitCode = $LASTEXITCODE
        }
    }
    finally {
        Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit $exitCode
}

Main
