# Builds deADBoot.exe: one file, no installer. Called from CI as: pwsh -File build/windows.ps1
# Steps: install dependencies, run the unit tests, package with PyInstaller, smoke-test the exe, write its SHA-256.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Step([string]$Name, [scriptblock]$Block) {
    Write-Host "==> $Name"
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
}

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Invoke-Step 'Install dependencies' {
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt pytest pyinstaller
}

Invoke-Step 'Unit tests' {
    $env:QT_QPA_PLATFORM = 'offscreen'
    python -m pytest -q tests --ignore=tests/emulator
}

Invoke-Step 'Package with PyInstaller' {
    # Bundle data/ (themes, ROM profiles, recipes, debloat list, sources) next to the frozen package.
    python -m PyInstaller --noconfirm --clean --onefile --windowed --name deADBoot `
        --paths $root `
        --add-data "data;data" `
        --collect-submodules deadboot `
        build/deadboot_entry.py
}

$exe = Join-Path $root 'dist\deADBoot.exe'
if (-not (Test-Path $exe)) { throw "PyInstaller did not produce $exe" }

Write-Host '==> Smoke test (--self-test)'
$log = Join-Path $env:RUNNER_TEMP 'deadboot-selftest.txt'
if (-not $env:RUNNER_TEMP) { $log = Join-Path $env:TEMP 'deadboot-selftest.txt' }
$env:QT_QPA_PLATFORM = 'offscreen'
$p = Start-Process -FilePath $exe -ArgumentList @('--self-test', '--self-test-log', $log) -Wait -PassThru
if (Test-Path $log) { Get-Content $log | Write-Host }
if ($p.ExitCode -ne 0) { throw "Self-test failed with exit code $($p.ExitCode)" }

Write-Host '==> SHA-256'
$hash = (Get-FileHash -Algorithm SHA256 $exe).Hash.ToLower()
"$hash  deADBoot.exe" | Out-File -Encoding ascii (Join-Path $root 'dist\deADBoot.exe.sha256')
Write-Host "$hash  deADBoot.exe"
