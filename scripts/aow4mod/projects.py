"""Mod content declarations and read-only launcher playset inspection."""
from collections import defaultdict
import json
from pathlib import Path

from .core import FormatError, mod_file, read_acp, read_rpk, sha
from .discovery import user_data
from . import overlay_imports


def inspect_project(path, include_unregistered=False):
    acp = mod_file(Path(path))
    meta, changes = read_acp(acp)
    imports = overlay_imports.records(acp.read_bytes())
    definitions = [{"resource":c["resource"],"pack":c["pack"],"kind":"override",
                    "operation":c["operation_raw"],"file":str(acp)} for c in changes]
    issues, packs, available = [], [], defaultdict(list)
    for p in acp.parent.rglob("*.rpk"):
        available[p.name.casefold()].append(p)
    declared = {Path(name.replace("\\","/")).name.casefold() for name in meta["registered_resource_packs"]}
    selected, visited, external = list(sorted(declared)), set(), set()
    for record in imports:
        for dependency in record['required']+record['optional']:
            name=Path(dependency.replace('\\','/')).name.casefold()
            if name in available:selected.append(name)
            else:external.add(dependency)
    def add_pack(name, reachable):
        candidates = available.get(name,[])
        if len(candidates) != 1:
            issues.append({"issue":"registered_pack_missing_or_ambiguous_locally","pack":name,"candidates":len(candidates)})
            return []
        try:
            info, resources = read_rpk(candidates[0])
            declarations=overlay_imports.rpk_declarations(candidates[0].read_bytes())
            info['import_declarations']=declarations
            packs.append({**info,"registered":reachable,"registration":"direct" if name in declared else ("dependency" if reachable else "unregistered")})
            definitions.extend({"resource":r,"pack":info["name"],"kind":"rpk","operation":None,
                                "file":str(candidates[0])} for r in resources)
            return [item['name'] for item in declarations]
        except FormatError as exc:
            issues.append({"issue":"resource_pack_parse_error","file":str(candidates[0]),"error":str(exc)})
            return []
    while selected:
        name=selected.pop(0)
        if name in visited:
            continue
        visited.add(name)
        for dependency in add_pack(name,True):
            target=Path(dependency.replace("\\","/")).name.casefold()
            if target in available:
                selected.append(target)
            else:
                external.add(dependency)
    unregistered = [str(p) for name,paths in available.items() if name not in visited for p in paths]
    if include_unregistered:
        for name in sorted(available.keys()-visited):
            add_pack(name,False)
    metadata_path = acp.parent/".metadata/metadata.json"
    launcher_metadata = None
    if metadata_path.is_file():
        try:
            launcher_metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except (ValueError,OSError) as exc:
            issues.append({"issue":"launcher_metadata_invalid","error":str(exc)})
    return {"meta":meta,"definitions":definitions,"packs":packs,"unregistered_rpk_files":unregistered,
            "overlay_imports":imports,
            "external_pack_dependencies":sorted(external),"launcher_metadata":launcher_metadata,"issues":issues}


def load_playset(path=None):
    path = Path(path) if path else user_data()/"content_load.json"
    raw=path.read_bytes();data = json.loads(raw.decode('utf-8-sig'))
    enabled = data.get("enabledMods")
    if not isinstance(enabled,list):
        raise FormatError("Expected launcher content_load.json with enabledMods")
    members, identities, issues = [], defaultdict(list), []
    for ordinal, item in enumerate(enabled):
        if not isinstance(item,dict) or not isinstance(item.get("path"),str):
            raise FormatError("Invalid playset Mod entry")
        p = Path(item["path"])
        if not p.is_absolute():
            p = path.parent/p
        p=p.resolve()
        member={"position":ordinal,"path":str(p)}
        try:
            meta,_ = read_acp(mod_file(p))
            member.update(name=meta["name"],package_id=meta.get("package_id"),sha256=meta["sha256"])
            if meta.get("package_id"):
                identities[meta["package_id"]].append(ordinal)
        except (FormatError,OSError) as exc:
            member["error"]=str(exc)
            issues.append({"issue":"mod_unreadable","position":ordinal,"error":str(exc)})
        members.append(member)
    issues.extend({"issue":"duplicate_package_identity","package_id":k,"positions":v}
                  for k,v in identities.items() if len(v)>1)
    return {"source":str(path.resolve()),"source_sha256":sha(raw),"mods":members,"disabled_dlc":data.get("disabledDLC",[]),
            "enabled_ugc":data.get("enabledUGC",[]),"issues":issues,
            "coverage":"Order is read from launcher configuration. DLC ownership and runtime application are not verified."}


def conflicts(mods, include_unregistered=False):
    definitions, issues, projects = defaultdict(list), [], []
    for ordinal,path in enumerate(mods):
        try:
            project=inspect_project(path,include_unregistered)
        except (FormatError,OSError) as exc:
            issues.append({"issue":"project_unreadable","path":str(path),"error":str(exc)})
            continue
        projects.append({"position":ordinal,"path":str(Path(path).resolve()),"name":project["meta"]["name"],
                         "resource_definitions":len(project["definitions"]),"packs":len(project["packs"]),
                         "unregistered_rpk_files":project["unregistered_rpk_files"]})
        issues.extend({"project":project["meta"]["name"],**issue} for issue in project["issues"])
        for definition in project["definitions"]:
            r=definition["resource"]
            try:
                fields=r.leaves()
            except FormatError as exc:
                issues.append({"issue":"property_layout_unsupported","resource":f"{r.rid:016X}","error":str(exc)})
                fields=None
            definitions[r.rid].append({"project":project["meta"]["name"],"position":ordinal,
                "file":definition["file"],"pack":definition["pack"],"kind":definition["kind"],
                "type_id":r.type_id,"name":r.name,"fields":fields,"operation":definition["operation"]})
    overlaps=[]
    for rid,owners in definitions.items():
        if len(owners)<2:
            continue
        first=owners[0]
        supported=all(o["fields"] is not None and o["operation"] in (None,0x40) for o in owners)
        identical=supported and all(o["type_id"]==first["type_id"] and o["fields"]==first["fields"] for o in owners)
        keys=set().union(*(o["fields"].keys() for o in owners if o["fields"] is not None))
        differing=[k for k in sorted(keys) if len({o["fields"].get(k) if o["fields"] is not None else None for o in owners})>1]
        overlaps.append({"id":f"{rid:016X}","name":owners[-1]["name"],
                         "classification":"identical_definitions" if identical else "different_definitions_need_review",
                         "type_mismatch":len({o["type_id"] for o in owners})>1,
                         "cross_pack_id":len({o["pack"].upper() for o in owners})>1,
                         "differing_serialized_fields":differing,
                         "owners":[{k:v for k,v in o.items() if k!='fields'} for o in owners],
                         "last_listed_candidate":owners[-1]["project"]})
    return {"format":"aow4mod-conflicts/1","projects":projects,"issues":issues,"overlaps":overlaps,
            "summary":{"projects":len(projects),"shared_resource_ids":len(overlaps),
                       "different_definitions":sum(x["classification"]!='identical_definitions' for x in overlaps),
                       "issues":len(issues)},
            "coverage":"Registered RPKs and ACP overrides only unless explicitly requested. Shared IDs are candidates; last-listed hints do not prove runtime precedence or merge behavior."}
