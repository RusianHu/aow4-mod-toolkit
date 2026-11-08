"""Gettext validation/compilation using polib; AoW4 key conventions are explicit."""
from collections import Counter
import copy
import gettext
import io
from pathlib import Path
import re

from .core import FormatError, sha


def library():
    try:import polib
    except ImportError as exc:
        raise FormatError('Localization needs polib 1.2.0; use the project venv or install .[localization]') from exc
    return polib


def read(path):
    polib=library();path=Path(path)
    if not path.is_file():raise FormatError(f'Localization file not found: {path}')
    try:
        if path.suffix.lower()=='.mo':return polib.mofile(str(path))
        if path.suffix.lower() in ('.po','.pot'):return polib.pofile(str(path),check_for_duplicates=False)
        raise FormatError('Expected .po, .pot or .mo')
    except (OSError,ValueError,UnicodeError) as exc:
        raise FormatError(f'Cannot parse localization file {path.name}: {exc}') from exc


def key(entry,mode):
    if mode=='context':
        if not entry.msgctxt:raise FormatError('Context-ID mode requires msgctxt on every active entry')
        if entry.msgid_plural:raise FormatError('Context-to-ID conversion for plural entries needs explicit design; unsupported')
        return entry.msgctxt
    if mode!='msgid':raise FormatError('Unknown localization key mode')
    if entry.msgctxt:raise FormatError('Context present in msgid-ID mode; select context mode explicitly')
    return entry.msgid


def placeholders(text):
    return Counter(re.findall(r'\{[A-Za-z_][A-Za-z0-9_.]*\}',text))


def validate(path,mode='msgid',reference=None):
    path=Path(path);catalog=read(path);issues=[];seen={};active=[]
    reference_text={}
    if reference:
        for e in read(reference):
            ref_mode='msgid' if Path(reference).suffix.lower()=='.mo' else mode
            if not e.obsolete:reference_text[key(e,ref_mode)]=e.msgstr or (e.msgid if ref_mode=='context' else '')
    counts=Counter(entries=len(catalog))
    for e in catalog:
        line=getattr(e,'linenum',None)
        if e.obsolete:
            counts['obsolete']+=1;continue
        counts['active']+=1
        try:k=key(e,mode)
        except FormatError as exc:
            issues.append({'severity':'error','issue':'key_convention','line':line,'detail':str(exc)});continue
        if k in seen:
            issues.append({'severity':'error','issue':'duplicate_key','key':k,'line':line,'first_line':seen[k]})
        seen[k]=line
        if '\0' in k:
            issues.append({'severity':'error','issue':'nul_in_key','key':k})
        fuzzy='fuzzy' in e.flags
        translated=e.translated() if hasattr(e,'translated') else bool(e.msgstr or (e.msgstr_plural and all(e.msgstr_plural.values())))
        if fuzzy:
            counts['fuzzy']+=1;issues.append({'severity':'warning','issue':'fuzzy_omitted_on_compile','key':k})
        if not translated:
            counts['not_compiled']+=1
            if not fuzzy:issues.append({'severity':'warning','issue':'untranslated_omitted_on_compile','key':k})
        else:counts['translated']+=1
        source=e.msgid if mode=='context' else reference_text.get(k)
        translations=list(e.msgstr_plural.values()) if e.msgid_plural else [e.msgstr]
        if source:
            counts['placeholder_checked']+=1
            for text in translations:
                if text and placeholders(source)!=placeholders(text):
                    issues.append({'severity':'error','issue':'placeholder_mismatch','key':k,
                                   'source':dict(placeholders(source)),'translation':dict(placeholders(text))})
        active.append(k)
    if reference:
        for k in reference_text.keys()-set(active):
            issues.append({'severity':'warning','issue':'missing_reference_key','key':k})
    counts['errors']=sum(i['severity']=='error' for i in issues)
    counts['warnings']=sum(i['severity']=='warning' for i in issues)
    return {'path':str(path.resolve()),'sha256':sha(path.read_bytes()),'key_mode':mode,
            'summary':dict(counts),'metadata':dict(catalog.metadata),'issues':issues,
            'coverage':{'gettext_syntax':True,'duplicate_keys':True,
                        'simple_brace_placeholders':bool(reference or mode=='context'),
                        'lingoona_grammar':False,'resource_string_usage':False}}


def compile_bytes(path,mode='msgid',reference=None,require_complete=False):
    path=Path(path)
    if path.suffix.lower() not in ('.po','.pot'):raise FormatError('Compilation requires a PO/POT source')
    report=validate(path,mode,reference)
    if report['summary']['errors']:raise FormatError('Localization validation failed; inspect loc-lint output')
    if require_complete and report['summary'].get('not_compiled',0):raise FormatError('Untranslated/fuzzy entries prevent a complete build')
    catalog=copy.deepcopy(read(path))
    if mode=='context':
        for e in catalog:
            if not e.obsolete:e.msgid=key(e,mode);e.msgctxt=None
    data=catalog.to_binary()
    compiled=library().mofile(data)
    translation=gettext.GNUTranslations(io.BytesIO(data))
    expected={e.msgid:e for e in catalog.translated_entries()}
    if len(compiled)!=len(expected):raise FormatError('Compiled entry count mismatch')
    for e in compiled:
        original=expected[e.msgid]
        if e.msgstr!=original.msgstr or e.msgstr_plural!=original.msgstr_plural:
            raise FormatError('MO round-trip changed a translation')
        if not e.msgid_plural and translation.gettext(e.msgid)!=e.msgstr:
            raise FormatError('Independent GNU gettext verification failed')
    if sha(path.read_bytes())!=report['sha256']:raise FormatError('Localization source changed during compilation')
    return data,{'source':str(path.resolve()),'source_sha256':report['sha256'],
                 'source_entries':report['summary']['entries'],'compiled_entries':len(compiled),
                 'omitted_entries':report['summary']['entries']-len(compiled),
                 'obsolete_entries':report['summary'].get('obsolete',0),
                 'warnings':report['summary']['warnings'],'sha256':sha(data),'key_mode':mode}


def compile_file(path,output,mode='msgid',reference=None,require_complete=False):
    output=Path(output).resolve();path=Path(path).resolve()
    if output.suffix.lower()!='.mo' or output==path or (reference and output==Path(reference).resolve()):
        raise FormatError('Use a separate new .mo output')
    data,report=compile_bytes(path,mode,reference,require_complete)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('xb') as f:f.write(data)
    return {**report,'output':str(output)}
