"""Current-user Windows DPAPI vault. Credentials never enter model prompts."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parent
VAULT=ROOT/'runtime'/'credentials'/'chaoxing.dpapi'

class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def transform(data,decrypt=False):
    if os.name!='nt':raise RuntimeError('账密容器仅支持当前Windows用户')
    buf=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));dest=Blob()
    crypt=ctypes.windll.crypt32
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL
    ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype=ctypes.c_void_p
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(dest)):
        raise OSError('账密容器解密失败' if decrypt else '账密容器加密失败')
    try:return ctypes.string_at(dest.data,dest.size)
    finally:ctypes.windll.kernel32.LocalFree(dest.data)

def restrict(path):
    path=Path(path)
    user=os.environ['USERDOMAIN']+'\\'+os.environ['USERNAME']
    rights='(OI)(CI)(F)' if path.is_dir() else '(F)'
    subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',user+':'+rights,'*S-1-5-18:'+rights],
                   check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)

def save(username,password,path=None):
    path=Path(path) if path is not None else VAULT
    if not username.strip() or not password:raise ValueError('账号和密码不能为空')
    plain=json.dumps({'username':username.strip(),'password':password},ensure_ascii=False).encode('utf-8')
    encrypted=transform(plain)
    path.parent.mkdir(parents=True,exist_ok=True);restrict(path.parent)
    tmp=path.with_suffix('.tmp');tmp.write_bytes(encrypted);restrict(tmp);os.replace(tmp,path)
    # Verify save/overwrite without logging contents.
    assert transform(path.read_bytes(),True)==plain

def load(path=None):
    path=Path(path) if path is not None else VAULT
    value=json.loads(transform(path.read_bytes(),True))
    if not value.get('username') or not value.get('password'):raise ValueError('账密容器无效')
    return value


def account_label():
    if not VAULT.exists():return '尚未保存'
    value=None
    try:
        value=load();name=value['username']
        return name[:2]+'…'+name[-2:] if len(name)>4 else name[:1]+'…'
    except Exception:return '容器不可读取，请重新保存'
    finally:
        if value is not None:value.clear()
