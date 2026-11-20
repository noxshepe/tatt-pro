Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
If CreateObject("Scripting.FileSystemObject").FileExists("TattPro.exe") Then
    WshShell.Run "TattPro.exe", 1, False
ElseIf CreateObject("Scripting.FileSystemObject").FileExists("TiemXamStudio.exe") Then
    WshShell.Run "TiemXamStudio.exe", 1, False
Else
    WshShell.Run "pythonw desktop_app.py", 0, False
End If

