# Cria (ou atualiza) a tarefa "Radar de Vagas - Sincronizar" no Agendador de
# Tarefas: ao entrar no Windows, deixa o sincronizar.py --continuo rodando
# escondido, para os pedidos feitos no painel na nuvem serem atendidos.
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$dir\sincronizar_oculto.vbs`"" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# sem limite de tempo (roda enquanto o PC estiver ligado); se cair, tenta de novo
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "Radar de Vagas - Sincronizar" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "Radar de Vagas - Sincronizar"
Write-Host "Pronto: o sincronizador esta rodando e vai iniciar sozinho sempre que voce entrar no Windows."
Write-Host "Log em output\sincronizar.log. Para desligar: Unregister-ScheduledTask -TaskName 'Radar de Vagas - Sincronizar'"
