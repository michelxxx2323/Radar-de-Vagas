' Deixa o sincronizar.py rodando escondido, de minuto em minuto (pedidos feitos
' pelo painel na nuvem). Chamado pelo Agendador de Tarefas ao entrar no Windows.
Set fso = CreateObject("Scripting.FileSystemObject")
pasta = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = pasta
' 0 = janela escondida; True = espera (a tarefa fica "em execução" enquanto ele roda)
WScript.Quit shell.Run("cmd /c ""set PYTHONIOENCODING=utf-8&& python sincronizar.py --continuo >> output\sincronizar.log 2>&1""", 0, True)
