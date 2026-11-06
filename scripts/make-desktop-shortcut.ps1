# Creates a "Marginalia" shortcut on the desktop that starts the app from this source folder.
# Usage: powershell -ExecutionPolicy Bypass -File scripts/make-desktop-shortcut.ps1
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop 'Marginalia.lnk'
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($link)
$sc.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
$sc.Arguments = '"' + (Join-Path $root 'scripts\Marginalia.vbs') + '"'
$sc.WorkingDirectory = $root
$sc.IconLocation = (Join-Path $root 'build\icon.ico') + ',0'
$sc.Description = 'Marginalia - paper reading assistant'
$sc.Save()
Write-Output "Created $link"
