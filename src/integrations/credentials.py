"""Secrets live in the Windows credential store, separate from JSON metadata."""
import ctypes
from ctypes import wintypes
import os

from .models import valid_id


class CredentialError(RuntimeError):
    pass


class CredentialService:
    def __init__(self, backend=None):
        self.backend = backend if backend is not None else WindowsCredentialBackend()

    @staticmethod
    def _target(integration_id):
        return 'SnapFlow:integration:' + valid_id(integration_id)

    def get(self, integration_id):
        return self.backend.get(self._target(integration_id))

    def set(self, integration_id, secret):
        if not isinstance(secret, str) or not secret:
            raise ValueError('Credential must be non-empty text.')
        self.backend.set(self._target(integration_id), secret)

    def delete(self, integration_id):
        self.backend.delete(self._target(integration_id))


class _FILETIME(ctypes.Structure):
    _fields_ = [('low', wintypes.DWORD), ('high', wintypes.DWORD)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
        ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
        ('LastWritten', _FILETIME), ('CredentialBlobSize', wintypes.DWORD),
        ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
        ('Persist', wintypes.DWORD), ('AttributeCount', wintypes.DWORD),
        ('Attributes', ctypes.c_void_p), ('TargetAlias', wintypes.LPWSTR),
        ('UserName', wintypes.LPWSTR),
    ]


class WindowsCredentialBackend:
    _GENERIC = 1
    _LOCAL_MACHINE = 2
    _NOT_FOUND = 1168

    def __init__(self):
        if os.name != 'nt':
            raise CredentialError('OS credential storage is unavailable.')
        self.api = ctypes.WinDLL('Advapi32.dll', use_last_error=True)
        self.api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        self.api.CredWriteW.restype = wintypes.BOOL
        self.api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
        self.api.CredReadW.restype = wintypes.BOOL
        self.api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self.api.CredDeleteW.restype = wintypes.BOOL
        self.api.CredFree.argtypes = [ctypes.c_void_p]
        self.api.CredFree.restype = None

    def set(self, target, value):
        encoded = value.encode('utf-8')
        if len(encoded) > 2560:
            raise CredentialError('Credential is too large for OS storage.')
        buffer = ctypes.create_string_buffer(encoded)
        credential = _CREDENTIALW()
        credential.Type = self._GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self._LOCAL_MACHINE
        credential.UserName = 'Visual Workflow AI'
        if not self.api.CredWriteW(ctypes.byref(credential), 0):
            raise CredentialError('Unable to save credential in Windows Credential Manager.')

    def get(self, target):
        pointer = ctypes.POINTER(_CREDENTIALW)()
        if not self.api.CredReadW(target, self._GENERIC, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == self._NOT_FOUND:
                return None
            raise CredentialError('Unable to read credential from Windows Credential Manager.')
        try:
            value = pointer.contents
            return ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize).decode('utf-8')
        finally:
            self.api.CredFree(pointer)

    def delete(self, target):
        if not self.api.CredDeleteW(target, self._GENERIC, 0):
            if ctypes.get_last_error() != self._NOT_FOUND:
                raise CredentialError('Unable to remove credential from Windows Credential Manager.')
