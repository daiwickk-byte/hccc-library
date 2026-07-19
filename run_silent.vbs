Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = "C:\Users\kyagnar\Downloads\HCCC_Form_Sync"
objShell.Run "python hccc_library_sync.py", 0, False
objShell.Run "python hccc_server.py", 0, False
