"""Lightweight, read-only hardware facts for local AI configuration.

No probe installs software, starts vendor utilities, or guarantees accelerator
support. Unknown memory values remain ``None`` instead of invented estimates.
"""
from dataclasses import dataclass
import ctypes
import os
from pathlib import Path
import platform


@dataclass(frozen=True)
class GPUDevice:
    name: str
    vendor: str | None = None
    total_vram_mb: int | None = None
    available_vram_mb: int | None = None
    backend_support: tuple[str, ...] = ()


@dataclass(frozen=True)
class HardwareProfile:
    os_version: str
    cpu_name: str | None
    logical_cpu_count: int
    total_ram_mb: int | None
    available_ram_mb: int | None
    gpu_devices: tuple[GPUDevice, ...] = ()
    npu_devices: tuple[str, ...] = ()
    windows_ml_available: bool = False
    available_execution_providers: tuple[str, ...] = ()
    detection_notes: tuple[str, ...] = ()


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64)]


class _DisplayDevice(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32), ("DeviceName", ctypes.c_wchar * 32),
                ("DeviceString", ctypes.c_wchar * 128), ("StateFlags", ctypes.c_uint32),
                ("DeviceID", ctypes.c_wchar * 128), ("DeviceKey", ctypes.c_wchar * 128)]


class HardwareProfileService:
    """Call ``detect`` in a worker when the hardware panel is requested."""

    def __init__(self, *, execution_provider_probe=None):
        self._provider_probe = execution_provider_probe

    def detect(self):
        notes = []
        try:
            total, available = self._memory_mb()
        except Exception:
            total, available = None, None
            notes.append("RAM detection unavailable.")
        try:
            gpus = tuple(self._gpu_devices())
        except Exception:
            gpus = ()
            notes.append("GPU detection unavailable.")
        providers = ()
        if self._provider_probe is not None:
            try:
                providers = tuple(str(name) for name in self._provider_probe())
            except Exception:
                notes.append("Execution provider detection unavailable.")
        notes.append("VRAM, NPU, and accelerator compatibility require runtime verification.")
        try:
            system = platform.platform()
            cpu = platform.processor().strip() or os.environ.get("PROCESSOR_IDENTIFIER") or None
        except Exception:
            system, cpu = "Unavailable", None
        try:
            cpu_count = max(1, os.cpu_count() or 1)
        except Exception:
            cpu_count = 1
        winml = False
        try:
            if platform.system() == "Windows":
                # A system component's presence does not imply a model/provider works.
                windows_dir = os.environ.get("WINDIR", "")
                if windows_dir:
                    winml = (Path(windows_dir) / "System32" / "Windows.AI.MachineLearning.dll").is_file()
        except Exception:
            notes.append("Windows ML detection unavailable.")
        return HardwareProfile(system, cpu, cpu_count, total, available, gpus,
                               windows_ml_available=winml,
                               available_execution_providers=providers,
                               detection_notes=tuple(notes))

    @staticmethod
    def _memory_mb():
        if platform.system() == "Windows":
            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(status)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            if not kernel.GlobalMemoryStatusEx(ctypes.byref(status)):
                raise OSError("GlobalMemoryStatusEx failed")
            return status.ullTotalPhys // (1024 ** 2), status.ullAvailPhys // (1024 ** 2)
        if hasattr(os, "sysconf"):
            page_size = os.sysconf("SC_PAGE_SIZE")
            total_pages = os.sysconf("SC_PHYS_PAGES")
            available_pages = os.sysconf("SC_AVPHYS_PAGES")
            if min(page_size, total_pages, available_pages) < 0:
                raise OSError("Memory information unavailable")
            return total_pages * page_size // (1024 ** 2), available_pages * page_size // (1024 ** 2)
        raise OSError("Memory detection unavailable")

    @staticmethod
    def _gpu_devices():
        if platform.system() != "Windows":
            return ()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        devices = []
        names = set()
        for index in range(32):
            device = _DisplayDevice()
            device.cb = ctypes.sizeof(device)
            if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
                break
            name = device.DeviceString.strip()
            if not name or name in names or device.StateFlags & 0x8:  # mirroring driver
                continue
            names.add(name)
            vendor = next((vendor for vendor in ("NVIDIA", "AMD", "Intel", "Qualcomm")
                           if vendor.lower() in name.lower()), None)
            devices.append(GPUDevice(name=name, vendor=vendor))
        return tuple(devices)
