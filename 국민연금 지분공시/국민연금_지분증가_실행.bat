@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0종합제조.csv" (
    python "%~dp0ps_disclosure_report.py" --metrics-file "%~dp0종합제조.csv"
) else if exist "%~dp0종합제조.xlsx" (
    python "%~dp0ps_disclosure_report.py" --metrics-file "%~dp0종합제조.xlsx"
) else (
    python "%~dp0ps_disclosure_report.py"
)
pause
