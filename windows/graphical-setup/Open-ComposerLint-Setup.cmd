@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -STA -ExecutionPolicy RemoteSigned -File "%~dp0SetupGUI.ps1"
if errorlevel 1 pause
