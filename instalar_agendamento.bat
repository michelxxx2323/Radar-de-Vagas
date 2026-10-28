@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0agendar_radar.ps1" %*
pause
