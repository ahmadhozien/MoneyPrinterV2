$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pythonPath = Join-Path $projectRoot "venv\Scripts\python.exe"

if (-not (Test-Path $pythonPath)) {
    Write-Host "Virtual environment not found at venv\Scripts\python.exe" -ForegroundColor Red
    Write-Host "Create it first, then install the requirements." -ForegroundColor Yellow
    exit 1
}

& $pythonPath -m streamlit run app_gui.py
