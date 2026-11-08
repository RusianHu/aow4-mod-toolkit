"""Official launcher content-group metadata and optional Steam eligibility evidence."""
import json
from datetime import datetime,timezone
from pathlib import Path

from .core import FormatError
from .filesystem import file_hash


def inventory(game,disabled=(),steam=None):
    game=Path(game).resolve();descriptions={};inputs={};issues=[]
    if not isinstance(disabled,(list,tuple)) or not all(isinstance(v,(str,int)) for v in disabled):
        raise FormatError('Disabled DLC must be a list of launcher identifiers')
    disabled={str(v).casefold() for v in disabled}
    for path in sorted((game/'Launcher/dlc').glob('*.dlc.json')):
        try:
            data=json.loads(path.read_text(encoding='utf8'));identifier=data['id']['paradox']
            if not isinstance(identifier,str) or not identifier:raise ValueError('Invalid Paradox identifier')
            app_id=int(data['id']['steam'])
            if not 0<app_id<=0xffffffff:raise ValueError('Invalid Steam DLC app ID')
            key=identifier.casefold()
            if key in descriptions:raise ValueError('Duplicate content identifier')
            descriptions[key]={'paradox_id':identifier,'steam_app_id':app_id,'name':data.get('displayName',{}).get('en',identifier),
                               'metadata':str(path),'aliases':{key,str(app_id),path.name.removesuffix('.dlc.json').casefold()}}
            inputs[str(path)]=file_hash(path)
        except (ValueError,KeyError,TypeError) as exc:issues.append({'issue':'dlc_metadata_invalid','file':str(path),'error':str(exc)})
    eligibility=steam.dlc_status([x['steam_app_id'] for x in descriptions.values()]) if steam else {}
    groups={}
    for directory in sorted((game/'Content').iterdir()):
        if not directory.is_dir():continue
        key=directory.name.casefold();meta=descriptions.get(key)
        if key in ('title','system'):state='installed_base'
        elif meta and meta['aliases']&disabled:state='disabled_by_configuration'
        elif meta and str(meta['steam_app_id']) in eligibility:
            state='steam_owned_and_installed' if eligibility[str(meta['steam_app_id'])] else 'steam_not_owned_or_installed'
        elif meta:state='installed_ownership_unknown'
        else:state='unmapped_content'
        groups[key]={'group':directory.name,'state':state,
                     **({k:v for k,v in meta.items() if k!='aliases'} if meta else {})}
    recognized=set().union(*(m['aliases'] for m in descriptions.values())) if descriptions else set()
    for value in sorted(disabled-recognized):issues.append({'issue':'unrecognized_disabled_dlc','value':value})
    return {'format':'aow4mod-dlc/1','checked_utc':datetime.now(timezone.utc).isoformat(),
            'game':str(game),'groups':groups,'metadata_inputs':inputs,'issues':issues,
            'steam_checked':steam is not None,'disabled_dlc':sorted(disabled),
            'coverage':'Official launcher directory/ID mapping and optional Steam client owned-and-installed check. Runtime mounting and per-resource DLC rules are not proven.'}


def file_state(path,game,report):
    try:relative=Path(path).resolve().relative_to(Path(game).resolve()/'Content')
    except ValueError:return {'state':'selected_mod'}
    if len(relative.parts)==1:return {'state':'installed_base'}
    return report['groups'].get(relative.parts[0].casefold(),{'group':relative.parts[0],'state':'unmapped_content'})


UNAVAILABLE={'disabled_by_configuration','steam_not_owned_or_installed'}
