"""Filesystem invariants shared by staging and release preparation."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import json

from .core import FormatError


def file_hash(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            result.update(chunk)
    return result.hexdigest()


def save_record(path,record):
    """Flush a complete JSON replacement before atomically replacing a receipt."""
    path=Path(path)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf8',dir=path.parent,prefix='.receipt-',suffix='.tmp',delete=False) as f:
        json.dump(record,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno());temporary=Path(f.name)
    try:os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()


def reject_reparse(path):
    p=Path(path)
    if p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400:
        raise FormatError(f'Reparse points are unsupported in source/payload trees: {p}')


def inventory(root):
    root=Path(root).absolute()
    reject_reparse(root)
    if not root.is_dir():raise FormatError('Expected a source directory')
    result={}
    for base,dirs,files in os.walk(root,followlinks=False):
        dirs.sort();files.sort()
        for name in dirs+files:reject_reparse(Path(base)/name)
        for name in files:
            p=Path(base)/name
            result[p.relative_to(root).as_posix()]={'sha256':file_hash(p),'size':p.stat().st_size}
    return result


def outside(path,roots):
    dest=Path(path).resolve()
    for root in roots:
        root=Path(root).resolve()
        if dest==root or root in dest.parents:
            raise FormatError(f'Output must be outside protected input: {root}')
    return dest


@contextmanager
def new_directory(output,protected=()):
    output=outside(output,protected)
    if output.exists():raise FormatError('Output directory already exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aow4mod-stage-',dir=output.parent) as temporary:
        stage=Path(temporary).resolve()
        if stage.parent!=output.parent:raise FormatError('Unexpected staging location')
        payload=stage/'payload';payload.mkdir()
        yield payload
        if output.exists():raise FormatError('Output appeared while staging; refusing to replace it')
        os.rename(payload,output)


@contextmanager
def copy_directory(source,output,protected=()):
    source=Path(source).resolve()
    before=inventory(source)
    with new_directory(output,[source,*protected]) as payload:
        shutil.copytree(source,payload,dirs_exist_ok=True)
        if inventory(payload)!=before:raise FormatError('Source changed during copy')
        yield payload,before
        if inventory(source)!=before:raise FormatError('Source tree changed during operation')
