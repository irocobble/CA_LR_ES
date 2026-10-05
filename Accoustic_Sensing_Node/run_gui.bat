@echo off
REM Double-click launcher. Keeps the window open if Python raises, so you can
REM actually read the error instead of watching it flash past.
cd /d "%~dp0"
python sar_gui.py
if errorlevel 1 pause
