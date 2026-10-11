"""Windows job ownership ensures native helper children die if SnapFlow exits."""
import ctypes
from ctypes import wintypes as w
from threading import Lock


class NativeProcessOwner:
    def __init__(self, process):
        self.handle = None
        self.lock = Lock()
        kernel = self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, w.LPCWSTR)
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD)
        kernel.AssignProcessToJobObject.argtypes = (w.HANDLE, w.HANDLE)
        kernel.CloseHandle.argtypes = (w.HANDLE,)
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64), ('flags', w.DWORD),
                ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t), ('active_limit', w.DWORD),
                ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise OSError('Native process ownership unavailable.')
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(handle, int(process._handle)):
            kernel.CloseHandle(handle)
            raise OSError('Native process ownership unavailable.')
        self.handle = handle

    def close(self):
        with self.lock:
            handle, self.handle = self.handle, None
        if handle:
            self.kernel.CloseHandle(handle)
