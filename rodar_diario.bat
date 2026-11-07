@echo off
REM Roda o radar e guarda o log do dia. Chamado pelo Agendador de Tarefas.
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> output\run_diario.log
python screen.py >> output\run_diario.log 2>&1
REM Perfil 2 (vagas internacionais em dolar) logo depois do Perfil 1; roda mesmo
REM se o Perfil 1 falhar, e o sincronizar sobe os dois relatorios juntos
echo ----- Perfil 2 (internacional) %time% ----- >> output\run_diario.log
python screen.py --config config_intl.yaml >> output\run_diario.log 2>&1
REM depois da coleta, sobe as vagas do dia pro painel na nuvem e copia as candidaturas pro PC
python sincronizar.py >> output\run_diario.log 2>&1
