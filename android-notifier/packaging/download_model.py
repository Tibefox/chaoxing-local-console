"""Fetch the pinned Apache-2.0 model before installer manifest verification."""
import hashlib
from pathlib import Path
import shutil
import sys
import urllib.request

URL='https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/a06e946bb6b655725eafa393f4a9745d460374c9/Qwen3-4B-Instruct-2507-Q4_K_M.gguf?download=true'
SHA256='3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597'
SIZE=2497281120

def verified(path):
    if not path.is_file() or path.stat().st_size!=SIZE:return False
    with path.open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()==SHA256

def ensure_model(root, local=None):
    target=root/'payload/models/chat-verified.gguf'
    if verified(target):print('Model SHA-256 OK.',flush=True);return
    target.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(target.parent).free<SIZE+256*1024**2:raise RuntimeError('At least 2.8 GB free space is required for the model.')
    temporary=target.with_suffix('.download')
    source=Path(local).open('rb') if local else urllib.request.urlopen(URL,timeout=60)
    digest=hashlib.sha256();count=0;last=0
    with source, temporary.open('wb') as output:
        while block:=source.read(4*1024**2):
            output.write(block);digest.update(block);count+=len(block)
            if count-last>=128*1024**2:
                print(f'Model: {count/1024**3:.2f} / {SIZE/1024**3:.2f} GiB',flush=True);last=count
    if count!=SIZE or digest.hexdigest()!=SHA256:raise RuntimeError('Model checksum mismatch. Retry Setup.cmd; unverified data was not installed.')
    temporary.replace(target)
    print('Model SHA-256 OK.',flush=True)

if __name__=='__main__':
    try:ensure_model(Path(__file__).resolve().parent,sys.argv[1] if len(sys.argv)>1 else None)
    except Exception as exc:print('Model preparation failed: '+str(exc));raise SystemExit(1)
