@echo off
REM Abre o painel de candidaturas no navegador. Feche esta janela para desligar o painel.
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
python painel.py
pause
