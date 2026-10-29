"""User-scoped settings; remembered API keys use Windows DPAPI."""
import base64
import ctypes
import os
from ctypes import wintypes

from .storage import USER_ROOT, atomic_json, read_json
from .translate import DEFAULT_CONFIG


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(value, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('此系统不支持Windows密钥保护；请只在本次运行中使用密钥。')
    data = base64.b64decode(value) if decrypt else value.encode('utf-8')
    buffer = ctypes.create_string_buffer(data)
    src = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)):
        raise RuntimeError('Windows无法保护或读取API密钥。')
    try:
        result = ctypes.string_at(dst.data, dst.size)
        return result.decode('utf-8') if decrypt else base64.b64encode(result).decode()
    finally:
        ctypes.windll.kernel32.LocalFree(dst.data)


def load():
    data = read_json(USER_ROOT / 'settings.json', {})
    cfg = dict(DEFAULT_CONFIG, **data.get('config', {}))
    key = ''
    if data.get('encrypted_key'):
        try:
            key = crypt(data['encrypted_key'], True)
        except Exception:
            pass
    return cfg, key, data


def save(config, key='', remember=False, recent=None):
    payload = {'config': {k: v for k, v in config.items() if 'key' not in k.lower()},
               'recent_project': str(recent or ''), 'remember': remember}
    if remember and key:
        payload['encrypted_key'] = crypt(key)
    atomic_json(USER_ROOT / 'settings.json', payload)
