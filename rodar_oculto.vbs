' Roda o rodar_diario.bat sem abrir janela (chamado pelo Agendador de Tarefas).
' Com janela visível, fechar ou apertar Ctrl+C nela matava a rodada no meio.
Set fso = CreateObject("Scripting.FileSystemObject")
pasta = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = pasta
' 0 = janela escondida; True = espera terminar (a tarefa só acaba quando o radar acaba)
WScript.Quit shell.Run("cmd /c """ & pasta & "\rodar_diario.bat""", 0, True)
