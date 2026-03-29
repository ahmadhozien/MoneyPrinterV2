@echo off
setlocal

cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Virtual environment not found at venv\Scripts\python.exe
    echo Create it first, then install the requirements.
    pause
    exit /b 1
)

"venv\Scripts\python.exe" -m streamlit run app_gui.py

endlocal
