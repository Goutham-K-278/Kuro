' kuro-launcher.vbs
' Launches Kuro completely invisibly.
' Double-click or add to startup — nothing appears on screen.
' Kuro starts in background, tray icon appears after ~3 seconds.

Dim shell
Set shell = CreateObject("WScript.Shell")

' Get the folder this .vbs file lives in
Dim projectRoot
projectRoot = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))

' Run start.ps1 completely hidden
' 0 = hide window, False = don't wait for it to finish
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & projectRoot & "start.ps1""", 0, False

Set shell = Nothing
