@echo off
REM Roda o radar e guarda o log do dia. Chamado pelo Agendador de Tarefas.
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> output\run_diario.log
python screen.py >> output\run_diario.log 2>&1
REM depois da coleta, sobe as vagas do dia (e o que mais mudou) pro painel na nuvem
python sincronizar.py >> output\run_diario.log 2>&1
