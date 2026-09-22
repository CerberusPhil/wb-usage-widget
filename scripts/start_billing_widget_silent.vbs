' ============================================================
' WorkBuddy billing widget - SILENT launcher (no console window)
' ------------------------------------------------------------
' Double-click THIS file for daily use.
' It runs start_billing_widget.bat with a hidden window (style 0),
' so no cmd console flashes on screen.
'
' Keep start_billing_widget.bat for debugging (it shows output).
' Note: if it seems "nothing happens", the usual cause is Python
' not found -> run start_billing_widget.bat for a visible error.
' ============================================================
Option Explicit
Dim fso, sh, sDir
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
sDir = fso.GetParentFolderName(WScript.ScriptFullName)

If Not fso.FileExists(sDir & "\start_billing_widget.bat") Then
  MsgBox "start_billing_widget.bat was not found next to this script.", 16, "WB Widget"
  WScript.Quit 1
End If

sh.CurrentDirectory = sDir
sh.Run "cmd /c """ & sDir & "\start_billing_widget.bat""", 0, False
