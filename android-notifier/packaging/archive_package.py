"""Archive a verified release folder and validate ZIP CRCs before publishing its checksum."""
import argparse
import hashlib
from pathlib import Path
import zipfile

def archive(folder):
    folder=Path(folder).resolve()
    target=folder.with_suffix('.zip')
    if target.exists():raise ValueError('Archive already exists')
    with zipfile.ZipFile(target,'w',allowZip64=True) as z:
        for p in sorted(folder.rglob('*')):
            if not p.is_file() or '__pycache__' in p.parts:continue
            kind=zipfile.ZIP_STORED if p.suffix.lower() in ('.gguf','.apk','.exe') else zipfile.ZIP_DEFLATED
            z.write(p,folder.name+'/'+p.relative_to(folder).as_posix(),compress_type=kind,compresslevel=1)
    with zipfile.ZipFile(target) as z:
        bad=z.testzip()
        if bad:raise ValueError('CRC failed: '+bad)
        count=len(z.infolist())
    with target.open('rb') as stream:
        digest=hashlib.file_digest(stream,'sha256').hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest+'  '+target.name+'\n',encoding='ascii')
    print(f'ZIP_OK files={count} bytes={target.stat().st_size} sha256={digest}',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder');archive(p.parse_args().folder)
