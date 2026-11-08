"""Transactional SQLite resource index; on-demand reads verify cached file hashes."""
from collections import Counter
import json
from pathlib import Path
import sqlite3
import time

from .core import FormatError, Node, Resource, read_acp, read_rpk, references, sha
from . import core, content_index

SCHEMA = 2
APPLICATION_ID = 0x414F5734


def connection(path):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    application = db.execute("PRAGMA application_id").fetchone()[0]
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    legacy_own = version == 1 and tables == {"files", "resources", "links", "meta"}
    if application not in (0, APPLICATION_ID) or (application == 0 and tables and not legacy_own):
        db.close()
        raise FormatError("Refusing to modify a database not owned by this toolkit")
    if version not in (0, 1, SCHEMA):
        db.close()
        raise FormatError("Unsupported catalog version; choose a new index path")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS files (
          path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, kind TEXT NOT NULL,
          origin TEXT NOT NULL, package TEXT, size INTEGER NOT NULL,
          links_indexed INTEGER NOT NULL, error TEXT);
        CREATE TABLE IF NOT EXISTS resources (
          row_id INTEGER PRIMARY KEY, file TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
          ordinal INTEGER NOT NULL, id TEXT NOT NULL, type_id INTEGER NOT NULL,
          name TEXT, package TEXT NOT NULL, origin TEXT NOT NULL,
          kind TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
          operation INTEGER, UNIQUE(file, ordinal));
        CREATE INDEX IF NOT EXISTS resource_id ON resources(id);
        CREATE INDEX IF NOT EXISTS resource_name ON resources(name COLLATE NOCASE);
        CREATE TABLE IF NOT EXISTS links (
          source INTEGER NOT NULL REFERENCES resources(row_id) ON DELETE CASCADE,
          target TEXT NOT NULL, name TEXT, property TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS link_target ON links(target);
        CREATE INDEX IF NOT EXISTS link_source ON links(source);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    content_index.create_tables(db)
    if version == 1:
        db.execute("DELETE FROM meta WHERE key='parser_sha256'")
    db.execute(f"PRAGMA user_version={SCHEMA}")
    db.execute(f"PRAGMA application_id={APPLICATION_ID}")
    db.execute("PRAGMA journal_mode=WAL")
    return db


def parser_fingerprint():
    return sha(Path(core.__file__).read_bytes() + Path(content_index.__file__).read_bytes())


def build(path, game=None, mods=(), with_links=False, locales=(), catalogs=()):
    selected_catalogs = content_index.select_catalogs(game, mods, locales, catalogs)
    if selected_catalogs:
        content_index.localization.library()  # Fail before changing an index if the optional reader is absent.
    inputs = {}
    if game:
        for p in (Path(game) / "Content").rglob("*.rpk"):
            inputs[str(p.resolve())] = "game"
    for folder in mods:
        folder = Path(folder).resolve()
        if not folder.exists():
            raise FormatError(f"Mod/index root not found: {folder}")
        if folder.is_file():
            inputs[str(folder)] = "mod"
        else:
            for ext in ("*.acp", "*.rpk"):
                for p in folder.rglob(ext):
                    inputs[str(p.resolve())] = "mod"
    if not inputs:
        raise FormatError("No input files found")
    dest = Path(path).resolve()
    for root in ([Path(game)] if game else []) + [Path(p) for p in mods]:
        protected = root.resolve() if root.is_dir() else root.resolve().parent
        if dest == protected or protected in dest.parents:
            raise FormatError("Keep the index outside game and Mod input directories")
    for filename in selected_catalogs:
        if dest == Path(filename) or Path(filename).parent in dest.parents:
            raise FormatError('Keep the index outside localization input directories')
    started = time.monotonic()
    db = connection(path)
    counts, failures = Counter(), []
    try:
        with db:
            engine = parser_fingerprint()
            prior_engine = db.execute("SELECT value FROM meta WHERE key='parser_sha256'").fetchone()
            parser_unchanged = prior_engine is not None and prior_engine[0] == engine
            old_paths = {r[0] for r in db.execute("SELECT path FROM files")}
            for old in old_paths - inputs.keys():
                db.execute("DELETE FROM files WHERE path=?", (old,))
                counts["removed_files"] += 1
            for filename, origin in sorted(inputs.items()):
                p = Path(filename)
                data = p.read_bytes()
                fingerprint = sha(data)
                counts["input_files"] += 1
                previous = db.execute("SELECT * FROM files WHERE path=?", (filename,)).fetchone()
                if parser_unchanged and previous and previous["sha256"] == fingerprint and previous["origin"] == origin and (not with_links or previous["links_indexed"]):
                    counts["unchanged_files"] += 1
                    if previous["error"]:
                        failures.append({"path": filename, "error": previous["error"]})
                    continue
                db.execute("DELETE FROM files WHERE path=?", (filename,))
                kind, package, records, error = p.suffix.lower()[1:], None, [], None
                try:
                    if kind == "rpk":
                        meta, resources = read_rpk(p)
                        package = meta["name"]
                        records = [(r, package, "rpk", None) for r in resources]
                    elif kind == "acp":
                        meta, changes = read_acp(p)
                        records = [(c["resource"], c["pack"], "override", c["operation_raw"]) for c in changes]
                    else:
                        raise FormatError("Only ACP/RPK inputs can be indexed")
                except FormatError as exc:
                    error = str(exc)
                    failures.append({"path": filename, "error": error})
                if sha(p.read_bytes()) != fingerprint:
                    raise FormatError(f"Input changed during indexing: {p}")
                db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?)", (filename, fingerprint, kind, origin,
                           package, len(data), int(with_links), error))
                for ordinal, (r, pack, record_kind, operation) in enumerate(records):
                    cursor = db.execute("""INSERT INTO resources(file,ordinal,id,type_id,name,package,origin,kind,start,end,operation)
                                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                        (filename, ordinal, f"{r.rid:016X}", r.type_id, r.name, pack, origin,
                                         record_kind, r.node.start, r.node.end, operation))
                    text_issues = []
                    db.executemany('INSERT INTO resource_terms VALUES(?,?,?,?)',
                                   [(cursor.lastrowid, *term) for term in content_index.terms(r, text_issues)])
                    db.executemany('INSERT INTO text_scan_issues VALUES(?,?,?)',
                                   [(cursor.lastrowid, item['property'], item['issue']) for item in text_issues])
                    if with_links:
                        db.executemany("INSERT INTO links VALUES(?,?,?,?)",
                                       [(cursor.lastrowid, ref["id"], ref["name"], ref["property"]) for ref in references(r)])
                counts["updated_files"] += 1
            content_index.build_catalogs(db, selected_catalogs, counts, failures)
            db.execute("INSERT OR REPLACE INTO meta VALUES('game',?)", (str(Path(game).resolve()) if game else "",))
            db.execute("INSERT OR REPLACE INTO meta VALUES('inputs',?)", (json.dumps(inputs, sort_keys=True),))
            db.execute("INSERT OR REPLACE INTO meta VALUES('parser_sha256',?)", (engine,))
            db.execute("INSERT OR REPLACE INTO meta VALUES('catalog_inputs',?)", (json.dumps(selected_catalogs, sort_keys=True),))
        counts["resources"] = db.execute("SELECT count(*) FROM resources").fetchone()[0]
        counts["recognized_links"] = db.execute("SELECT count(*) FROM links").fetchone()[0]
        counts['resource_terms'] = db.execute('SELECT count(*) FROM resource_terms').fetchone()[0]
        counts['text_scan_issues'] = db.execute('SELECT count(*) FROM text_scan_issues').fetchone()[0]
        return {"index": str(dest), "summary": dict(counts), "failures": failures,
                "elapsed_seconds": round(time.monotonic()-started, 3),
                "coverage": "Installed-file index; not an effective load-order view. Recognized link encodings only."}
    finally:
        db.close()


def open_existing(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FormatError("Index not found; run index first")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.create_function('CASEFOLD', 1, lambda value: value.casefold() if isinstance(value, str) else value, deterministic=True)
    if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA:
        db.close()
        raise FormatError("Unsupported index schema; run index to upgrade/rebuild the toolkit catalog")
    return db


def page(items, total, limit, offset):
    return {"items": items, "total": total, "limit": limit, "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None}


def check_page(limit, offset):
    if not 1 <= limit <= 200 or offset < 0:
        raise FormatError("limit must be 1..200; offset must be nonnegative")


def search(path, query="", rid=None, type_id=None, origin=None, limit=20, offset=0,
           field='any', locale=None, exact=False):
    from .content_queries import search
    return search(path, query, rid, type_id, origin, limit, offset, field, locale, exact)


def load_resource(path, rid, filename=None):
    db = open_existing(path)
    try:
        sql = "SELECT resources.*,files.sha256 FROM resources JOIN files ON resources.file=files.path WHERE id=?"
        args = [f"{int(rid,16):016X}"]
        if filename:
            sql += " AND file=?"
            args.append(str(Path(filename).resolve()))
        rows = db.execute(sql, args).fetchall()
        if len(rows) != 1:
            raise FormatError(f"Expected one resource, found {len(rows)}; use search and --file to disambiguate")
        row = dict(rows[0])
        data = Path(row["file"]).read_bytes()
        if sha(data) != row["sha256"]:
            raise FormatError("Index is stale for this file; rebuild before inspection")
        r = Resource.parse(Node(data, row["start"], row["end"]))
        if f"{r.rid:016X}" != row["id"] or r.type_id != row["type_id"]:
            raise FormatError("Index identity mismatch")
        g = db.execute("SELECT value FROM meta WHERE key='game'").fetchone()
        return r, row, Path(g[0]) if g and g[0] else None
    finally:
        db.close()


def reverse_references(path, rid, limit=20, offset=0):
    check_page(limit, offset)
    db = open_existing(path)
    try:
        check_parser(db)
        missing = db.execute("SELECT count(*) FROM files WHERE links_indexed=0 AND error IS NULL").fetchone()[0]
        if missing:
            raise FormatError(f"References not indexed for {missing} files; rebuild with --links")
        args = [f"{int(rid,16):016X}"]
        total = db.execute("SELECT count(*) FROM links WHERE target=?", args).fetchone()[0]
        rows = db.execute("""SELECT r.id,r.name,r.package,r.file,r.origin,l.property,f.sha256
                             FROM links l JOIN resources r ON l.source=r.row_id JOIN files f ON f.path=r.file WHERE l.target=?
                             ORDER BY r.name,r.file,l.property LIMIT ? OFFSET ?""", args + [limit, offset]).fetchall()
        verify_files({row['file']: row['sha256'] for row in rows})
        result = page([dict(r) for r in rows], total, limit, offset)
        result["coverage"] = "Recognized encodings in indexed installed files; not an active-playset graph."
        result["failed_files"] = db.execute("SELECT count(*) FROM files WHERE error IS NOT NULL").fetchone()[0]
        return result
    finally:
        db.close()


def check_parser(db):
    row = db.execute("SELECT value FROM meta WHERE key='parser_sha256'").fetchone()
    if row is None or row[0] != parser_fingerprint():
        raise FormatError('Index parser is stale; rebuild with index and repeat its complete scope')


def verify_files(files):
    for filename, expected in files.items():
        if not Path(filename).is_file() or sha(Path(filename).read_bytes()) != expected:
            raise FormatError(f'Index is stale for this file; rebuild before inspection: {filename}')
