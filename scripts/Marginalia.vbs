' Launches Marginalia from source without a console window.
' Double-click this file, or use the desktop shortcut made by make-desktop-shortcut.ps1.
Option Explicit
Dim fso, sh, root, logFile
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
logFile = sh.ExpandEnvironmentStrings("%TEMP%") & "\marginalia-launch.log"
sh.CurrentDirectory = root
' 0 = hidden window, False = do not wait. Output goes to the log file in case something fails.
sh.Run "cmd /c npm run dev > """ & logFile & """ 2>&1", 0, False
