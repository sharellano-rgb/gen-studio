"""Stop a CPU pass if its owning backend exits, including forced Windows stops."""
import ctypes
import os
from ctypes import wintypes


class ParentGuard:
    def __init__(self, process_id):
        self.handle = None
        self.process_id = process_id
        if os.name == 'nt':
            self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.kernel.OpenProcess(0x00100000,False,process_id)
            if not self.handle:
                raise RuntimeError('No se puede comprobar el proceso propietario de la corrección.')
        self.check()

    def check(self):
        alive = self.kernel.WaitForSingleObject(self.handle,0)==0x00000102 if self.handle else os.getppid()==self.process_id
        if not alive:
            raise RuntimeError('El backend se detuvo; la pasada facial se cancela y conserva el vídeo base.')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
