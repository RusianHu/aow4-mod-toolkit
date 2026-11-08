"""Frozen, offline FBX-to-CLB source checks for v2 skeletal authoring."""
import json
from pathlib import Path,PurePosixPath
import shutil

from . import source_correspondence
from .core import FormatError
from .filesystem import file_hash,save_record,reject_reparse,copy_directory

FORMAT='aow4mod-skeletal-source-release/1'
FILE='skeletal-source-validation.json'
FIELDS=('skeletal_source_validation_requested','skeletal_source_validation_file','skeletal_source_validation_sha256','skeletal_source_validation_summary')


def child(root,value):
    if not isinstance(value,str):raise FormatError('Source evidence path must be relative')
    p=PurePosixPath(value.replace('\\','/'))
    if p.is_absolute() or '..' in p.parts or ':' in value:raise FormatError('Source evidence path escapes its root')
    root=Path(root).resolve();path=(root/p.as_posix()).resolve()
    if root not in path.parents:raise FormatError('Source evidence path escapes its root')
    reject_reparse(path);return path


def required(manifest):
    return [a for a in manifest.get('draft_skeletal_assets',[]) if a.get('source_correspondence_required') is True or a.get('source_receipt','').endswith('.aow4mod-skeleton-v2.json')]


def collect(source,content,root,source_files,assets):
    selected=required({'draft_skeletal_assets':assets})
    if not selected:return {'skeletal_source_validation_requested':False}
    if len(selected)>32:raise FormatError('Skeletal source evidence exceeds 32 assets')
    rows=[];total=0;source=Path(source);root=Path(root)
    for i,asset in enumerate(selected):
        marker=child(source,asset['source_receipt']);receipt=json.loads(marker.read_text(encoding='utf-8'))
        if receipt.get('format')!='aow4mod-skeletal-import-receipt/2' or file_hash(marker)!=asset['source_receipt_sha256']:raise FormatError('Skeletal source receipt differs')
        original=(PurePosixPath(asset['source_receipt']).parent/'prepared.fbx').as_posix();fbx=child(source,original)
        if file_hash(fbx)!=receipt.get('prepared_source_sha256') or source_files[original]['sha256']!=receipt['prepared_source_sha256']:raise FormatError('Retained skeletal source changed after import')
        total+=fbx.stat().st_size
        if total>128*1024*1024:raise FormatError('Skeletal source evidence exceeds 128 MiB')
        relative=f'source-evidence/{i}/prepared.fbx';target=root/relative;target.parent.mkdir(parents=True);shutil.copyfile(fbx,target)
        library=child(content,asset['file']);mesh=receipt['change']['mesh'];model=asset['model']
        report=source_correspondence.inspect(target,library,mesh,model)
        rows.append({'file':asset['file'],'library_sha256':file_hash(library),'mesh':mesh,'model':model,'source_receipt':asset['source_receipt'],'source_receipt_sha256':asset['source_receipt_sha256'],'source_original_relative':original,'source_file':relative,'source_sha256':file_hash(target),'assessment':report['assessment']})
    summary={'checks':len(rows),'passed':len(rows),'local_geometry_weights_defaults_verified':True,'source_animation_world_bind_rendering_verified':False}
    report={'format':FORMAT,'assets':rows,'summary':summary};save_record(root/FILE,report)
    return dict(skeletal_source_validation_requested=True,skeletal_source_validation_file=FILE,skeletal_source_validation_sha256=file_hash(root/FILE),skeletal_source_validation_summary=summary)


def verify(root,manifest,content):
    assets=required(manifest)
    if not assets:
        if manifest.get('skeletal_source_validation_requested') or manifest.get('skeletal_source_validation_file'):raise FormatError('Unexpected skeletal source evidence without required assets')
        return None
    if manifest.get('skeletal_source_validation_requested') is not True or manifest.get('skeletal_source_validation_file')!=FILE:raise FormatError('Required skeletal source validation is missing')
    path=child(root,FILE)
    if file_hash(path)!=manifest.get('skeletal_source_validation_sha256'):raise FormatError('Skeletal source report hash differs')
    report=json.loads(path.read_text(encoding='utf-8'));rows=report.get('assets',[])
    if report.get('format')!=FORMAT or len(rows)!=len(assets) or not 1<=len(rows)<=32:raise FormatError('Skeletal source evidence coverage differs')
    total=0
    for i,(asset,row) in enumerate(zip(assets,rows)):
        for key in ('file','model','source_receipt','source_receipt_sha256'):
            if row.get(key)!=asset.get(key):raise FormatError('Skeletal source asset/receipt policy differs')
        original=(PurePosixPath(asset['source_receipt']).parent/'prepared.fbx').as_posix()
        if row.get('source_file')!=f'source-evidence/{i}/prepared.fbx' or row.get('source_original_relative')!=original:raise FormatError('Skeletal source snapshot path differs')
        fbx=child(root,row['source_file']);library=child(content,row['file']);total+=fbx.stat().st_size
        source_files=manifest.get('source_files',{})
        if total>128*1024*1024 or file_hash(fbx)!=row['source_sha256'] or source_files.get(original,{}).get('sha256')!=row['source_sha256'] or source_files.get(asset['source_receipt'],{}).get('sha256')!=row['source_receipt_sha256']:raise FormatError('Frozen skeletal source bytes/provenance differ')
        if file_hash(library)!=row['library_sha256'] or manifest['files'].get(row['file'])!=row['library_sha256']:raise FormatError('Source-checked payload library differs')
        observed=source_correspondence.inspect(fbx,library,row['mesh'],row['model'])['assessment']
        if observed!=row['assessment']:raise FormatError('Frozen skeletal source assessment differs from recomputation')
    summary={'checks':len(rows),'passed':len(rows),'local_geometry_weights_defaults_verified':True,'source_animation_world_bind_rendering_verified':False}
    if report.get('summary')!=summary or manifest.get('skeletal_source_validation_summary')!=summary:raise FormatError('Skeletal source summary differs')
    return summary


def stage(manifest,target):
    if not required(manifest):return
    root=Path(manifest['_skeletal_source_root']);verify(root,manifest,manifest['content_directory'])
    target=Path(target);shutil.copyfile(root/FILE,target/FILE)
    with copy_directory(root/'source-evidence',target/'source-evidence'):pass
    manifest['_skeletal_source_root']=str(target)
