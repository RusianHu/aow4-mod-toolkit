"""Immutable snapshots and three-way comparisons independent of ACP metadata."""
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path

from . import catalog
from .core import FormatError, Node, Resource, audit, describe, mod_file, read_acp, read_rpk, references, sha

FORMAT = "aow4mod-snapshot/2"


def key(pack, rid):
    return pack.upper() + ":" + f"{rid:016X}"


def record(resource, **extra):
    return {"id": f"{resource.rid:016X}", "name": resource.name,
            "type_id": f"0x{resource.type_id:08X}",
            "fields": {k: v.hex() for k,v in resource.leaves().items()}, **extra}


def capture(path, game, index=None, dependency_depth=0, max_dependencies=250):
    if not 0 <= dependency_depth <= 8 or not 1 <= max_dependencies <= 10000:
        raise FormatError("dependency depth must be 0..8; maximum dependencies 1..10000")
    acp = mod_file(Path(path))
    meta, changes = read_acp(acp)
    report, legacy = audit(acp, Path(game))
    original_files={p['path']:p['sha256'] for p in report['touched_original_packs']}
    originals = {k: {**v["identity"], "fields": v["fields"], "location": v["location"], "role": "override_base"}
                 for k,v in legacy["resources"].items()}
    modified, queue, source_files = {}, deque(), {str(acp): meta["sha256"]}
    for change in changes:
        r = change["resource"]
        k = key(change["pack"],r.rid)
        if k in modified:
            raise FormatError("Duplicate override identity cannot be snapshotted unambiguously")
        modified[k] = record(r, kind="override", operation=change["operation_raw"], file=str(acp), package=change["pack"])
        queue.extend((ref["id"], 1) for ref in references(r))
    for p in sorted(acp.parent.rglob("*.rpk")):
        info, objects = read_rpk(p)
        source_files[str(p)] = info["sha256"]
        for r in objects:
            k = key(info["name"],r.rid)
            if k in modified:
                raise FormatError(f"Duplicate Mod resource identity: {k}")
            modified[k] = record(r, kind="local_rpk", file=str(p), package=info["name"])
            queue.extend((ref["id"],1) for ref in references(r))
    dependency_issues, captured = [], 0
    if dependency_depth:
        if index is None:
            raise FormatError("Dependency capture requires --index")
        db = catalog.open_existing(index)
        visited = {v["id"] for v in originals.values()} | {v["id"] for v in modified.values()}
        file_cache = {}
        try:
            while queue:
                rid, depth = queue.popleft()
                if rid in visited or depth > dependency_depth:
                    continue
                visited.add(rid)
                if captured >= max_dependencies:
                    dependency_issues.append({"issue": "dependency_limit_reached", "limit": max_dependencies})
                    break
                rows = db.execute("""SELECT r.*,f.sha256 FROM resources r JOIN files f ON r.file=f.path
                                     WHERE r.id=? AND r.origin='game'""", (rid,)).fetchall()
                if len(rows) != 1:
                    dependency_issues.append({"id":rid,"issue":"not_unique_in_original_index","candidates":len(rows)})
                    continue
                row = rows[0]
                if row["file"] not in file_cache:
                    data = Path(row["file"]).read_bytes()
                    if sha(data) != row["sha256"]:
                        raise FormatError("Dependency index is stale; rebuild it")
                    file_cache[row["file"]] = data
                    original_files[row['file']]=row['sha256']
                r = Resource.parse(Node(file_cache[row["file"]],row["start"],row["end"]))
                originals[key(row["package"],r.rid)] = record(r,role="dependency",depth=depth,
                    location={"path":row["file"],"pack":row["package"],"origin":"game"})
                queue.extend((ref["id"],depth+1) for ref in references(r))
                captured += 1
        finally:
            db.close()
    fingerprint = sha(json.dumps({"records": {k:{x:v for x,v in r.items() if x not in ("file","name")}
                                  for k,r in modified.items()}, "registered_packs":meta["registered_resource_packs"]},
                                 sort_keys=True,separators=(",",":")).encode())
    result = {"format":FORMAT,"created_utc":datetime.now(timezone.utc).isoformat(),
              "mod":meta,"game":report["game"],"mod_gameplay_sha256":fingerprint,
              "source_files":source_files,"original_source_files":original_files,"originals":originals,"modified":modified,
              "audit_summary":report["summary"],"audit_issues":report["issues"],
              "dependency_capture":{"depth":dependency_depth,"captured":captured,"issues":dependency_issues},
              "coverage":"Serialized fields and recognized references; not complete semantics or gameplay."}
    for source, expected in source_files.items():
        if sha(Path(source).read_bytes()) != expected:
            raise FormatError("Mod input changed during capture")
    for source,expected in original_files.items():
        if sha(Path(source).read_bytes())!=expected:
            raise FormatError('Original input changed during capture')
    return result


def load(path):
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    if result.get("format") != FORMAT:
        raise FormatError("Expected a version 2 toolkit snapshot; legacy audit snapshots are separate")
    for section in ("originals","modified"):
        if not isinstance(result.get(section),dict):
            raise FormatError("Invalid snapshot resource section")
        for record in result[section].values():
            if not isinstance(record.get("fields"),dict):
                raise FormatError("Invalid snapshot fields")
            for value in record["fields"].values():
                bytes.fromhex(value)
    return result


def compare(old, new):
    if old.get("format") != FORMAT or new.get("format") != FORMAT:
        raise FormatError("Only version 2 snapshots can be compared")
    a,b = old["mod"].get("package_id"),new["mod"].get("package_id")
    if a and b and a != b:
        raise FormatError("Snapshots identify different Mod package IDs")
    rows, stats = [], {"upstream_field_changes":0,"safe_rebase_candidates":0,"manual_conflicts":0,
                      "mod_field_changes":0,"dependency_field_changes":0}
    for k in sorted(old["originals"].keys() | new["originals"].keys() | old["modified"].keys() | new["modified"].keys()):
        oa,ob = old["originals"].get(k),new["originals"].get(k)
        ma,mb = old["modified"].get(k),new["modified"].get(k)
        row = {"resource":k,"name":(mb or ma or ob or oa).get("name"),"fields":[]}
        if ma is not None or mb is not None:
            row["mod_presence"] = "retained" if ma and mb else ("added" if mb else "removed")
        if oa is None or ob is None:
            row["original_presence"] = "not_captured" if not oa and not ob else ("added_to_snapshot" if ob else "removed_from_snapshot")
            if oa and not ob and mb:
                row["action"] = "review_missing_original"
                stats["manual_conflicts"] += 1
        elif oa["type_id"] != ob["type_id"]:
            row["action"] = "review_original_type_change"
            stats["manual_conflicts"] += 1
        if ma and mb and ma["type_id"] != mb["type_id"]:
            row["mod_type_changed"] = True
        if oa and ob:
            af,bf = oa["fields"],ob["fields"]
            am,bm = ma["fields"] if ma else {},mb["fields"] if mb else {}
            for prop in sorted(af.keys() | bf.keys() | am.keys() | bm.keys()):
                upstream = af.get(prop) != bf.get(prop)
                mod_changed = bool(ma and mb and am.get(prop) != bm.get(prop))
                if not upstream and not mod_changed:
                    continue
                classification = "mod_only_change"
                if upstream:
                    stats["upstream_field_changes"] += 1
                    if not mb:
                        classification = "dependency_or_unmodified_original_change"
                        stats["dependency_field_changes"] += 1
                    elif bm.get(prop) == bf.get(prop):
                        classification = "already_matches_new_original"
                    elif ma and am.get(prop) == af.get(prop) and bm.get(prop) == am.get(prop) and "action" not in row and not row.get("mod_type_changed"):
                        classification = "safe_rebase_candidate"
                        stats["safe_rebase_candidates"] += 1
                    else:
                        classification = "overlapping_change_needs_review"
                        stats["manual_conflicts"] += 1
                if mod_changed:
                    stats["mod_field_changes"] += 1
                def value(raw):
                    return describe(bytes.fromhex(raw)) if raw is not None else describe(None)
                row["fields"].append({"property":prop,"classification":classification,
                    "old_original":value(af.get(prop)),"new_original":value(bf.get(prop)),
                    "old_mod":value(am.get(prop)),"new_mod":value(bm.get(prop))})
        if row["fields"] or row.get("action") or row.get("mod_type_changed") or row.get("mod_presence") in ("added","removed"):
            rows.append(row)
    return {"format":"aow4mod-comparison/1","old_game":old["game"],"new_game":new["game"],
            "metadata_changed":old["mod"].get("metadata_sha256",old["mod"]["sha256"]) != new["mod"].get("metadata_sha256",new["mod"]["sha256"]),
            "gameplay_changed":old["mod_gameplay_sha256"] != new["mod_gameplay_sha256"],
            "summary":stats,"resources":rows,
            "limitations":["Raw serialized comparisons; equivalent explicit/default values may need semantic review.",
                           "Safe rebase candidates are proposals, not applied edits. Snapshot capture failures remain relevant."],
            "old_capture_issues":old.get("audit_issues",[]),"new_capture_issues":new.get("audit_issues",[])}
