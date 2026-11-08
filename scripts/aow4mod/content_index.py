"""Source-bound text discovery for research, not runtime dictionary selection."""
from pathlib import Path
import re

from . import localization
from .core import FormatError, decode_link, sha

RESOURCE_METADATA = {0x13, 0x14, 0x15, 0x17, 0x18, 0x12C}


def locale_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}', value):
        raise FormatError('Locale must be a language identifier, e.g. ZH or EN')
    return value.upper()


def walk(node, path, depth=0, issues=None, marker=None):
    """Observed structural containers only; scalar strings are terminal."""
    if depth > 64:
        if issues is not None:
            issues.append({'property': path, 'issue': 'depth_limit'})
            return
        raise FormatError('Content text traversal exceeded its depth bound')
    yield path, node
    if marker is not None and marker not in node.raw():
        return
    if decode_link(node):
        return
    try:
        node.string()
        return
    except FormatError:
        pass
    candidates = []
    try:
        cid, candidate = node.typed()
        if 0x10000 <= cid < 0x10000000:
            candidates.append(candidate)
    except FormatError:
        pass
    try:
        candidates.append(node.mapping())
    except FormatError:
        pass
    if len(candidates) != 1:
        if len(candidates) > 1 and issues is not None:
            issues.append({'property': path, 'issue': 'ambiguous_container'})
        return
    fields = candidates[0]
    for key, child in fields.items():
        yield from walk(child, path + f'/0x{key:X}', depth + 1, issues, marker)


def resource_nodes(resource, issues=None, marker=None):
    count = 0
    for key, node in resource.fields.items():
        if key in RESOURCE_METADATA:
            continue
        path = f'0x{key:08X}' if key >= 0x10000 else f'meta/0x{key:02X}'
        for item in walk(node, path, issues=issues, marker=marker):
            count += 1
            if count > 1000000:
                if issues is not None:
                    issues.append({'property': path, 'issue': 'node_limit'})
                    return
                raise FormatError('Content text traversal exceeded its node bound')
            yield item


def text_links(resource, issues=None, include_parameters=True):
    links = []
    marker = b'\x04\x16\x40\x00'  # TEStringKeyReplacer class field in every recognized envelope.
    if marker not in resource.node.raw():
        return links
    for path, node in resource_nodes(resource, issues, marker):
        try:
            fields = node.mapping()
            if set(fields) != {1, 2} or 0x401604 not in fields[2].mapping():
                continue
            key = fields[1].string()
            if not key:
                continue
        except FormatError:
            continue
        parameters = []
        parameter_nodes = walk(fields[2], path + '/0x2', issues=issues) if include_parameters else ()
        for parameter_path, child in parameter_nodes:
            try:
                items = child.mapping()
                # A TEStringKeyItem's class section contains its placeholder
                # in field 2 and an optional explicit replacement in field 3.
                if not parameter_path.upper().endswith('/0X401602') or 2 not in items:
                    continue
                parameters.append({'property': parameter_path, 'placeholder': items[2].string(),
                                   'replacement': items[3].string() if 3 in items else None})
            except FormatError:
                continue
        links.append({'key': key, 'property': path, 'parameters': parameters})
    return links


def terms(resource, issues=None):
    rows = []
    if 0x12C in resource.fields:
        try:
            tag = resource.fields[0x12C].string()
        except FormatError:
            if issues is None:
                raise
            issues.append({'property': 'meta/0x12C', 'issue': 'invalid_data_store_tag'})
            tag = None
        if tag:
            rows.append(('tag', 'meta/0x12C', tag))
    rows.extend(('key', row['property'], row['key']) for row in text_links(resource, issues, include_parameters=False))
    return rows


def create_tables(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS resource_terms (
          source INTEGER NOT NULL REFERENCES resources(row_id) ON DELETE CASCADE,
          kind TEXT NOT NULL, property TEXT NOT NULL, value TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS term_source ON resource_terms(source);
        CREATE INDEX IF NOT EXISTS term_value ON resource_terms(kind,value);
        CREATE TABLE IF NOT EXISTS text_scan_issues (
          source INTEGER NOT NULL REFERENCES resources(row_id) ON DELETE CASCADE,
          property TEXT NOT NULL, issue TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS text_files (
          path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, locale TEXT NOT NULL,
          origin TEXT NOT NULL, error TEXT);
        CREATE TABLE IF NOT EXISTS text_entries (
          row_id INTEGER PRIMARY KEY, file TEXT NOT NULL REFERENCES text_files(path) ON DELETE CASCADE,
          ordinal INTEGER NOT NULL, key TEXT NOT NULL, context TEXT NOT NULL,
          plural_index INTEGER NOT NULL, text TEXT NOT NULL, UNIQUE(file,ordinal,plural_index));
        CREATE INDEX IF NOT EXISTS text_key ON text_entries(key);
    ''')


def discover(game=None, mods=()):
    """Directories identify candidates, never the dictionary selected by game."""
    result = []
    if game:
        root = Path(game).resolve() / 'Language'
        if root.is_dir():
            for folder in sorted(root.iterdir()):
                if not folder.is_dir():
                    continue
                for path in sorted(folder.rglob('*')):
                    if path.is_file() and path.suffix.casefold() == '.mo':
                        result.append({'file': str(path.resolve()), 'locale': locale_name(folder.name),
                                       'origin': 'game', 'language_source': 'installed_language_directory'})
    for root in mods:
        root = Path(root).resolve()
        folder = root if root.is_dir() else root.parent
        for path in sorted(folder.rglob('*')):
            if path.is_file() and path.suffix.casefold() == '.mo':
                result.append({'file': str(path.resolve()), 'locale': None, 'origin': 'mod',
                               'language_source': 'requires_explicit_catalog_assignment'})
    return list({row['file']: row for row in result}.values())


def select_catalogs(game=None, mods=(), locales=(), catalogs=()):
    selected = {}
    wanted = {locale_name(value) for value in locales}
    available = discover(game) if wanted else []
    found = {r['locale'] for r in available}
    if wanted - found:
        raise FormatError('Requested installed language catalog not found: ' + ', '.join(sorted(wanted - found)))
    for row in available:
        if row['locale'] in wanted:
            selected[row['file']] = row
    for spec in catalogs:
        language, sep, filename = spec.partition('=')
        if not sep or not filename:
            raise FormatError('Explicit catalog uses LOCALE=path/to/catalog.mo')
        path = Path(filename).resolve()
        if not path.is_file() or path.suffix.casefold() != '.mo':
            raise FormatError(f'Expected an existing MO catalog: {path}')
        language = locale_name(language)
        if str(path) in selected and selected[str(path)]['locale'] != language:
            raise FormatError('One catalog was assigned multiple languages')
        origin = 'external'
        for root, label in [(game, 'game')] + [(p, 'mod') for p in mods]:
            if root is not None:
                root = Path(root).resolve()
                root = root if root.is_dir() else root.parent
                if root in path.parents:
                    origin = label
        selected[str(path)] = {'file': str(path), 'locale': language, 'origin': origin,
                               'language_source': 'explicit_assignment'}
    return selected


def build_catalogs(db, selected, counts, failures):
    old = {r[0] for r in db.execute('SELECT path FROM text_files')}
    for path in old - selected.keys():
        db.execute('DELETE FROM text_files WHERE path=?', (path,))
        counts['removed_text_files'] += 1
    for path, spec in sorted(selected.items()):
        data = Path(path).read_bytes()
        fingerprint = sha(data)
        before = db.execute('SELECT * FROM text_files WHERE path=?', (path,)).fetchone()
        counts['text_files'] += 1
        if before and before['sha256'] == fingerprint and before['locale'] == spec['locale'] and before['origin'] == spec['origin']:
            counts['unchanged_text_files'] += 1
            if before['error']:
                failures.append({'path': path, 'error': before['error']})
            continue
        rows, error = [], None
        try:
            for i, entry in enumerate(localization.read(path)):
                if entry.obsolete or not entry.msgid:
                    continue
                variants = entry.msgstr_plural if entry.msgid_plural else {-1: entry.msgstr}
                rows.extend((path, i, entry.msgid, entry.msgctxt or '', int(plural), value)
                            for plural, value in variants.items())
        except FormatError as exc:
            error = str(exc)
            failures.append({'path': path, 'error': error})
            rows = []
        if sha(Path(path).read_bytes()) != fingerprint:
            raise FormatError(f'Catalog changed during indexing: {path}')
        db.execute('DELETE FROM text_files WHERE path=?', (path,))
        db.execute('INSERT INTO text_files VALUES(?,?,?,?,?)', (path, fingerprint, spec['locale'], spec['origin'], error))
        db.executemany('INSERT INTO text_entries(file,ordinal,key,context,plural_index,text) VALUES(?,?,?,?,?,?)', rows)
        counts['updated_text_files'] += 1
    counts['text_entries'] = db.execute('SELECT count(*) FROM text_entries').fetchone()[0]


def placeholders(text):
    """Return expressions as evidence; no Lingoona evaluation is claimed."""
    return list(dict.fromkeys(re.findall(r'\{([^{}]+)\}', text)))
