Set sh = CreateObject("WScript.Shell")
' MinerU parse service (hidden), run from .local so its blobs/lock files stay out of the repo.
' If it is already running, the new process exits on its own (port in use).
sh.CurrentDirectory = "E:\Projects\PaperDesk\.local"
sh.Run "E:\Projects\PaperDesk\.local\mineru\Scripts\mineru-kit.exe api-server --host 127.0.0.1 --port 8765 --tier standard", 0, False
sh.CurrentDirectory = "E:\Projects\PaperDesk"
sh.Run "cmd /c npm run dev", 0, False
