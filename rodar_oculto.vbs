' Roda o rodar_diario.bat sem abrir janela (chamado pelo Agendador de Tarefas).
' Com janela visível, fechar ou apertar Ctrl+C nela matava a rodada no meio.
Set fso = CreateObject("Scripting.FileSystemObject")
pasta = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = pasta
' Antes da rodada, traz o código novo do GitHub (pedido do Michel, 06/11/2026),
' pra ele não precisar atualizar a pasta à mão. Fica aqui e não no .bat porque
' o cmd lê o .bat aos pedaços enquanto roda: se o pull trocasse o próprio .bat,
' a rodada podia executar linhas pela metade. --ff-only: se a pasta tiver
' mudança local que não está no GitHub, o pull só falha (fica no log) e a
' rodada segue com o código que está aí. GIT_TERMINAL_PROMPT=0: sem janela
' pedindo senha, que travaria a tarefa escondida.
shell.Run "cmd /c ""set GIT_TERMINAL_PROMPT=0&& git pull --ff-only >> output\run_diario.log 2>&1""", 0, True
' 0 = janela escondida; True = espera terminar (a tarefa só acaba quando o radar acaba)
WScript.Quit shell.Run("cmd /c """ & pasta & "\rodar_diario.bat""", 0, True)
