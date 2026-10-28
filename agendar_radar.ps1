# Cria (ou atualiza) a tarefa "Radar de Vagas" no Agendador de Tarefas do Windows.
# Roda todo dia no horário abaixo; se o PC estiver desligado nessa hora, roda
# assim que ele for ligado (StartWhenAvailable).
param([string]$Hora = "07:00")

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
# wscript + rodar_oculto.vbs = roda sem janela (com o .bat direto abria uma
# janela preta, e fechá-la matava a rodada no meio)
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$dir\rodar_oculto.vbs`"" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At $Hora
# Por padrão o Windows não inicia tarefa com o notebook na bateria (e para a
# tarefa se tirar da tomada no meio) — aqui roda em qualquer caso.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "Radar de Vagas" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Pronto: o Radar de Vagas vai rodar todo dia as $Hora."
Write-Host "Para testar agora: Start-ScheduledTask -TaskName 'Radar de Vagas'"
