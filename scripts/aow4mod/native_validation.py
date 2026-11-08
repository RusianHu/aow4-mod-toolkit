"""Independent, explicitly partial native witnesses for serialized RPK content."""
from collections import Counter
from pathlib import Path
import re

from .core import FormatError, read_rpk
from .filesystem import file_hash
from . import native
from .content_index import text_links


def localization_tokens(resources):
    """Recognize the observed string-key + TEStringKeyReplacer envelope.

    Never assume every string containing @ is a localization reference.
    This covers serialized nonempty keys, not omitted native defaults.
    """
    tokens = []
    resource_ids = []
    for resource in resources:
        before = len(tokens)
        tokens.extend(row['key'] for row in text_links(resource, include_parameters=False))
        if len(tokens) != before:
            resource_ids.append(f'{resource.rid:016X}')
    return dict(sorted(Counter(tokens).items())), resource_ids


def diagnostics(run_dir):
    path = Path(run_dir)/'stdout.log'
    if not path.exists():
        return []
    # These are diagnostic candidates, not automatically attributable defects.
    pattern = re.compile(r'^(Cannot |Assertion |Error\b|Warning\b|Failed\b|To avoid missing texture atlas)|\bis invalid[!:]', re.I)
    with path.open(encoding='utf-8', errors='replace') as stream:
        return sorted(set(line.rstrip()[:2000] for line in stream if pattern.search(line)))


def check(path, game, cache_dir=Path('.aow4mod/native'), timeout=45,languages=(),packages=()):
    languages=tuple(languages);packages=tuple(packages)
    path = Path(path).resolve()
    if path.suffix.casefold() != '.rpk':
        raise FormatError('Native witnesses currently require one RPK; ACP merge semantics are not implemented')
    # This preflight is mandatory: native open callbacks accept truncated files.
    before = file_hash(path)
    _, resources = read_rpk(path)
    if not resources:
        raise FormatError('An empty pack cannot establish a native resource witness')
    from . import language_context,native_context
    catalogs=language_context.bind(languages);packs=native_context.bind(packages)
    protected_cache = native.check_cache_directory(cache_dir, game, [path.parent,*[Path(p['file']).parent for p in [*catalogs,*packs]]])
    registry = native.cached(game, 'types', cache_dir=protected_cache, timeout=timeout)
    known = {row['cid'] for row in registry['payload']['types']}
    missing_types = sorted({f'{r.type_id:08X}' for r in resources} - known)
    if missing_types:
        raise FormatError('Resource classes absent from the native registry: '+', '.join(missing_types))
    tokens, localized = localization_tokens(resources)
    if file_hash(path) != before:
        raise FormatError('RPK changed during structural preflight')
    checks = {'serialized_structure': 'parsed', 'resource_classes_registered': True,
              'resource_count': len(resources), 'localized_resource_ids': localized,
              'serialized_localization_tokens': sum(tokens.values())}
    result = {'format': 'aow4mod-native-check/1', 'path': str(path), 'sha256': before,
              'checks': checks, 'complete_compatibility_verified': False,
              'coverage': 'RPK structure, registered top-level classes and recognized nonempty localization tokens only. '
                          'Resource IDs, effective field values, dependencies, assets and gameplay are not verified by this witness.'}
    if not tokens:
        result.update(status='not_evaluated', reason='No recognized nonempty localization tokens for an independent native witness')
        return result
    language = Path(game).resolve()/'Language/EN/EN.MO'
    if not language.is_file():
        raise FormatError('Native witness environment requires the installed English EN.MO catalog')
    params = {'path': str(path), 'sha256': before, 'expected_tokens': tokens,
              'language': str(language), 'language_sha256': file_hash(language)}
    if catalogs:params['languages']=catalogs
    if packs:params['packs']=packs
    evidence = native.run_worker(game, 'localization-witness', params, protected_cache, timeout)
    if file_hash(path) != before:
        raise FormatError('RPK changed while the native reader was running')
    if file_hash(language) != params['language_sha256']:
        raise FormatError('Native witness language catalog changed during loading')
    if language_context.bind(languages)!=catalogs or native_context.bind(packages)!=packs:
        raise FormatError('Native witness explicit context changed during loading')
    checks['native_localization_tokens'] = 'matched'
    result.update(status='scoped_witness_matched', run=evidence['run'], binding=native.public_binding(evidence['binding']),
                  diagnostics=diagnostics(evidence['run']), native_result=evidence['payload'],
                  environment={'language': str(language), 'language_sha256': params['language_sha256'],
                               'packages': 'installed official packages plus explicit hash-bound RPK/MO lookup context; no ACP application',
                               'context_languages':catalogs,'context_packs':packs,
                               'dlc_eligibility_checked': False})
    return result
