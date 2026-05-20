@echo off
setlocal
cd /d "%~dp0"
python "%~dp0ps_disclosure_report.py" --days 0
pause
