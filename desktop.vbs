' Launch the source client without a console window. Portable users open PaperBridge.exe.
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
python = fs.BuildPath(root, ".venv\Scripts\pythonw.exe")
If Not fs.FileExists(python) Then
  MsgBox "Please run setup.ps1 first, or use the complete PaperBridge portable package.", 48, "PaperBridge"
  WScript.Quit 1
End If
shell.CurrentDirectory = root
shell.Run Chr(34) & python & Chr(34) & " -m paperbridge.cli gui", 0, False
