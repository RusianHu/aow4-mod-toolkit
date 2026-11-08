"""Explicit English/default MO inputs for a fresh native validation host."""
import gettext
import io
import struct
from pathlib import Path

from .core import FormatError,sha


def bind(paths):
    paths=list(paths)
    if len(paths)>128:raise FormatError('Native language context exceeds 128 catalogs')
    result=[];names={}
    for value in paths:
        path=Path(value).resolve()
        if path.suffix.lower()!='.mo' or not 1<=path.stat().st_size<=32*1024*1024:
            raise FormatError('Native language context needs MO files no larger than 32 MiB')
        if path.name.casefold()=='en.mo':raise FormatError('Bundled MO must not shadow the original EN.MO namespace')
        data=path.read_bytes()
        try:gettext.GNUTranslations(io.BytesIO(data))
        except (OSError,ValueError,UnicodeError,EOFError,struct.error) as exc:raise FormatError('Invalid native MO context: '+str(path)) from exc
        digest=sha(data);name=path.name.casefold()
        if name in names:
            if names[name]!=digest:raise FormatError('Ambiguous native MO filename: '+path.name)
            continue
        names[name]=digest;result.append({'file':str(path),'sha256':digest})
    return result


def english(content):
    root=Path(content).resolve()
    return [p for p in sorted(root.rglob('*'),key=lambda p:p.as_posix().casefold())
            if p.is_file() and p.suffix.casefold()=='.mo' and p.relative_to(root).parts[0].casefold()=='language'
            and (len(p.relative_to(root).parts)==2 or p.relative_to(root).parts[1].casefold()=='en')]
