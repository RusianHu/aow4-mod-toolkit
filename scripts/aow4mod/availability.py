"""Read-only current-installation support; no native code, UI or database writes."""
import sqlite3
import importlib.util

from . import native, reflection, semantics, launcher
from .core import FormatError
from .discovery import find_game
from .filesystem import file_hash


def report(game=None, database=None, launcher_asar=None):
    result={'scope':'Installed-file/profile readiness, not content validation, a server-latest check or gameplay acceptance.'}
    try:
        selected=find_game(game)
        result['game']=str(selected)
        n=native.status(selected)
        result['native']={k:n[k] for k in ('platform_supported','profile_supported','profile','profile_scope',
                                         'dll_sha256','available_operations','unreviewed_operations')}
        tool=selected/'ResourceED.exe';digest=file_hash(tool) if tool.is_file() else None
        missing=[name for name in ('capstone','pefile') if importlib.util.find_spec(name) is None]
        result['schema_extraction']={'available':digest in reflection.PROFILES and not missing,'tool_sha256':digest,
                                     'profile_supported':digest in reflection.PROFILES,'missing_dependencies':missing}
        s=semantics.schema_status(selected)
        result['gameplay_explanation']={'available':s['gameplay_rules_verified'],'ruleset':s['gameplay_ruleset']}
        from .editor_bridge import EDITORS
        result['official_editor_bridge']={name:{'available':(selected/filename).is_file() and file_hash(selected/filename)==expected}
                                         for name,(filename,expected) in EDITORS.items()}
    except (FormatError,OSError,ValueError) as exc:
        result['game_error']=str(exc)
        result.setdefault('native',{'profile_supported':False,'profile_scope':'unavailable','available_operations':[],'reason':str(exc)})
        result.setdefault('schema_extraction',{'available':False,'reason':str(exc)})
        result.setdefault('gameplay_explanation',{'available':False,'reason':str(exc)})
    try:
        binding=launcher.launcher_binding(launcher_asar);profile=launcher.binding_profile(binding)
        path=launcher.database_path(database);before=file_hash(path);con=launcher.connect(path)
        try:
            matches=launcher.schema_hash(con,profile)==profile['schema_sha256']
            migrations=launcher.migration_state(con,profile)
        finally:con.close()
        if file_hash(path)!=before:raise FormatError('Launcher database changed during readiness inspection')
        result['launcher']={'available':matches and migrations['supported'],'profile':profile['name'],
                            'schema_matches':matches,'migrations':migrations,
                            'scope':'Reviewed app/schema pair; individual playset, idle-process and transaction checks still apply.'}
    except (FormatError,OSError,ValueError,sqlite3.Error) as exc:
        result['launcher']={'available':False,'reason':str(exc)}
    return result
