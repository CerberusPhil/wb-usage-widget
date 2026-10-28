"""Widget-owned credentials only: Keychain on macOS, user DPAPI on Windows.

No plaintext fallback. Legacy widget files are removed only after protected
storage readback succeeds. OS prompts/denials are surfaced without secret text.
File permissions isolate other users, not malicious processes running as you.
"""
import ctypes
import json
import os
import stat
import sys
import tempfile
import threading

from browser_runtime import app_data_dir


class StorageError(RuntimeError):
    pass


class StorageCancelled(StorageError):
    pass


_LOCK = threading.RLock()
_SERVICE = b'cn.workbuddy.usage-widget.credentials'
_LEGACY = {'cookie': 'server_usage.json', 'rt': 'server_usage_rt.json',
           'at': 'server_usage_at.json'}


def _cancel(event):
    if event is not None and event.is_set():
        raise StorageCancelled('Authorization cancelled; credentials not saved.')


def _check_path(path, directory=False):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise StorageError('Private storage must not use links or reparse points.')
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise StorageError('Invalid private storage path.')
    if hasattr(os, 'getuid') and info.st_uid != os.getuid():
        raise StorageError('Private storage is not owned by the current user.')


def _windows_acl(path, directory):
    from ctypes import wintypes as w
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.SetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p]
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    token, sid_text, descriptor = w.HANDLE(), w.LPWSTR(), ctypes.c_void_p()
    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise StorageError('Unable to secure private storage permissions.')
        size = w.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        data = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, 1, data, size, ctypes.byref(size)):
            raise StorageError('Unable to secure private storage permissions.')
        sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise StorageError('Unable to secure private storage permissions.')
        sddl = 'D:P(A;%s;FA;;;%s)' % ('OICI' if directory else '', sid_text.value)
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise StorageError('Unable to secure private storage permissions.')
        if not adv.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise StorageError('Unable to secure private storage permissions.')
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        if token:
            kernel.CloseHandle(token)


def private_directory(path):
    path = os.path.abspath(path)
    os.makedirs(path, mode=0o700, exist_ok=True)
    _check_path(path, directory=True)
    if sys.platform == 'win32':
        _windows_acl(path, True)
    else:
        os.chmod(path, 0o700)
    return path


def secure_file(path):
    _check_path(path)
    if sys.platform == 'win32':
        _windows_acl(path, False)
    else:
        os.chmod(path, 0o600, follow_symlinks=False)


def read_private(path):
    secure_file(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        return stream.read()


def atomic_write(path, data, cancel_event=None):
    _cancel(cancel_event)
    parent = private_directory(os.path.dirname(os.path.abspath(path)))
    fd, temp = tempfile.mkstemp(prefix='.private-', dir=parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            secure_file(temp)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _cancel(cancel_event)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def private_tree(path):
    """Tighten existing dedicated Chromium data, never follow filesystem links."""
    private_directory(path)
    for root, directories, files in os.walk(path, followlinks=False):
        for name in directories:
            private_directory(os.path.join(root, name))
        for name in files:
            candidate = os.path.join(root, name)
            # Chromium's Unix SingletonLock/Socket are intentional symlinks.
            if os.path.islink(candidate):
                continue
            secure_file(candidate)


def _keychain(kind, value=None, cancel_event=None, delete=False, interactive=False):
    """Legacy Security interaction flag is process-global: serialize and restore."""
    with _LOCK:
        sec = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        sec.SecKeychainGetUserInteractionAllowed.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
        sec.SecKeychainGetUserInteractionAllowed.restype = ctypes.c_int32
        sec.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]
        sec.SecKeychainSetUserInteractionAllowed.restype = ctypes.c_int32
        previous = ctypes.c_ubyte()
        if sec.SecKeychainGetUserInteractionAllowed(ctypes.byref(previous)):
            raise StorageError('Unable to query Keychain interaction policy.')
        if sec.SecKeychainSetUserInteractionAllowed(bool(interactive)):
            raise StorageError('Unable to enforce Keychain interaction policy.')
        try:
            return _keychain_operation(kind, value, cancel_event, delete)
        finally:
            if sec.SecKeychainSetUserInteractionAllowed(previous.value):
                raise StorageError('Unable to restore Keychain interaction policy.')


def _keychain_operation(kind, value=None, cancel_event=None, delete=False):
    sec = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    ptr = ctypes.c_void_p
    uint = ctypes.c_uint32
    sec.SecKeychainFindGenericPassword.argtypes = [ptr, uint, ptr, uint, ptr, ctypes.POINTER(uint), ctypes.POINTER(ptr), ctypes.POINTER(ptr)]
    sec.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    sec.SecKeychainAddGenericPassword.argtypes = [ptr, uint, ptr, uint, ptr, uint, ptr, ctypes.POINTER(ptr)]
    sec.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    sec.SecKeychainItemModifyAttributesAndData.argtypes = [ptr, ptr, uint, ptr]
    sec.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
    sec.SecKeychainItemFreeContent.argtypes = [ptr, ptr]
    sec.SecKeychainItemDelete.argtypes = [ptr]
    sec.SecKeychainItemDelete.restype = ctypes.c_int32
    cf.CFRelease.argtypes = [ptr]
    account = kind.encode('ascii')
    length, data, item = uint(), ptr(), ptr()
    status = sec.SecKeychainFindGenericPassword(None, len(_SERVICE), _SERVICE,
                                               len(account), account, ctypes.byref(length),
                                               ctypes.byref(data), ctypes.byref(item))
    try:
        if status not in (0, -25300):
            raise StorageError('Unlock/allow the widget in macOS Keychain, then retry authorization.')
        if delete:
            if status == 0 and sec.SecKeychainItemDelete(item):
                raise StorageError('Unable to restore cancelled credentials in Keychain.')
            return None
        if value is None:
            return ctypes.string_at(data, length.value) if status == 0 else None
        _cancel(cancel_event)
        if status == 0:
            status = sec.SecKeychainItemModifyAttributesAndData(item, None, len(value), value)
        else:
            status = sec.SecKeychainAddGenericPassword(None, len(_SERVICE), _SERVICE,
                                                      len(account), account, len(value), value, None)
        if status:
            raise StorageError('Unlock/allow the widget in macOS Keychain, then retry authorization.')
    finally:
        if data:
            sec.SecKeychainItemFreeContent(None, data)
        if item:
            cf.CFRelease(item)


def _dpapi(data, decrypt=False):
    from ctypes import wintypes as w
    class Blob(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.POINTER(Blob)]
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise StorageError('Windows protected credential storage is unavailable; retry under the original Windows account.')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def _protected_read(kind, interactive=False):
    if sys.platform == 'darwin':
        return _keychain(kind, interactive=interactive)
    if sys.platform == 'win32':
        path = os.path.join(app_data_dir(), 'credentials', kind + '.dpapi')
        try:
            return _dpapi(read_private(path), decrypt=True)
        except FileNotFoundError:
            return None
    raise StorageError('Protected credentials require macOS Keychain or Windows DPAPI.')


def _protected_write(kind, data, cancel_event):
    _cancel(cancel_event)
    if sys.platform == 'darwin':
        _keychain(kind, data, cancel_event)
    elif sys.platform == 'win32':
        encrypted = _dpapi(data)
        atomic_write(os.path.join(app_data_dir(), 'credentials', kind + '.dpapi'), encrypted, cancel_event)
    else:
        raise StorageError('Protected credentials require macOS Keychain or Windows DPAPI.')

def _protected_delete(kind):
    if sys.platform == 'darwin':
        _keychain(kind, delete=True)
    elif sys.platform == 'win32':
        path = os.path.join(app_data_dir(), 'credentials', kind + '.dpapi')
        try:
            secure_file(path)
            os.unlink(path)
        except FileNotFoundError:
            pass
    else:
        raise StorageError('Protected credentials require macOS Keychain or Windows DPAPI.')



def _legacy_path(kind):
    return os.path.join(os.path.expanduser('~'), '.workbuddy', _LEGACY[kind])


def _decode(data):
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError):
        raise StorageError('Stored widget credentials are invalid; please authorize again.') from None


def load_record(kind='cookie', cancel_event=None):
    """Passive reads never display OS UI or wait behind an authorization prompt."""
    if kind not in _LEGACY:
        raise ValueError('Unknown credential record')
    if not _LOCK.acquire(blocking=False):
        raise StorageError('Credential storage is busy with an authorization attempt; retry shortly.')
    try:
        _cancel(cancel_event)
        data = _protected_read(kind)
        _cancel(cancel_event)
        legacy = _legacy_path(kind)
        if data is not None:
            result = _decode(data)
            if os.path.lexists(legacy):
                # Only remove an identical interrupted-migration leftover. A
                # different legacy account is preserved until explicit reauth.
                old = _decode(read_private(legacy))
                if old == result:
                    _cancel(cancel_event)
                    os.unlink(legacy)
            return result
        try:
            data = read_private(legacy)
        except FileNotFoundError:
            return {}
        result = _decode(data)
        save_record(kind, result, cancel_event=cancel_event, interactive=False)
        return result
    finally:
        _LOCK.release()


def save_record(kind, record, cancel_event=None, interactive=False):
    if kind not in _LEGACY:
        raise ValueError('Unknown credential record')
    data = json.dumps(record, ensure_ascii=False).encode('utf-8')
    with _LOCK:
        _cancel(cancel_event)
        # Only an explicit user operation can allow this one Keychain query.
        # All writes/readback/rollback remain noninteractive, even after denial.
        previous = _protected_read(kind, interactive=True) if interactive else _protected_read(kind)
        _protected_write(kind, data, cancel_event)
        verified = _protected_read(kind)
        if cancel_event is not None and cancel_event.is_set():
            if previous is None:
                _protected_delete(kind)
            else:
                _protected_write(kind, previous, None)
            raise StorageCancelled('Authorization cancelled; previous credentials restored.')
        if verified != data:
            raise StorageError('Protected credential verification failed; existing legacy credentials were retained.')
        legacy = _legacy_path(kind)
        if os.path.lexists(legacy):
            secure_file(legacy)
            os.unlink(legacy)
