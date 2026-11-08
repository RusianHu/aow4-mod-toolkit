"""Opt-in local fixture checks. Only report metadata/hashes, never copy assets."""
from collections import Counter
from pathlib import Path
import time

from .core import FormatError, read_acp, read_rpk, sha


def run_corpus(roots, include_game=None):
    paths = set()
    for root in roots:
        root = Path(root).resolve()
        if not root.exists():
            raise FormatError(f"corpus input not found: {root}")
        if root.is_file():
            paths.add(root)
        else:
            paths.update(root.rglob("*.acp"))
            paths.update(root.rglob("*.rpk"))
    if include_game:
        content = Path(include_game) / "Content"
        if not content.is_dir():
            raise FormatError("game Content directory missing")
        paths.update(content.rglob("*.rpk"))
    started = time.monotonic()
    rows = []
    counts = Counter()
    for path in sorted(paths):
        raw = path.read_bytes()
        before = sha(raw)
        row = {"path": str(path), "size": len(raw), "sha256": before}
        kind = path.suffix.lower().lstrip(".")
        counts[kind + "_inputs"] += 1
        try:
            if kind == "acp":
                meta, changes = read_acp(path)
                resources = [c["resource"] for c in changes]
                row.update(name=meta["name"], overrides=len(changes), omitted=meta["omitted_metadata_fields"],
                           operations=dict(Counter(hex(c["operation_raw"]) for c in changes)))
            elif kind == "rpk":
                meta, resources = read_rpk(path)
                row.update(name=meta["name"], resources=len(resources))
            else:
                raise FormatError("corpus accepts ACP and RPK files only")
            property_errors = []
            for resource in resources:
                try:
                    resource.leaves()
                except FormatError as exc:
                    property_errors.append({"id": f"{resource.rid:016X}", "error": str(exc)})
            row["property_layout_errors"] = property_errors
            row["status"] = "parsed" if not property_errors else "partial"
            counts[kind + "_parsed"] += 1
            counts["property_layout_errors"] += len(property_errors)
        except (FormatError, OSError) as exc:
            row.update(status="unsupported_or_invalid", error=str(exc))
            counts[kind + "_failed"] += 1
        if sha(path.read_bytes()) != before:
            raise FormatError(f"corpus input changed during run: {path}")
        rows.append(row)
    return {"format": "aow4mod-corpus/1", "summary": dict(counts), "elapsed_seconds": round(time.monotonic()-started, 3),
            "inputs_unchanged": True, "files": rows,
            "coverage": "Container/identity and class field boundaries; not complete typed or gameplay validation."}
