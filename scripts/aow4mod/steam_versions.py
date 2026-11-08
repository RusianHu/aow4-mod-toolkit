"""Steam branch/build comparisons with explicit capture and online provenance."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from .core import FormatError, sha
from .filesystem import file_hash, new_directory, outside, reject_reparse, save_record
from .discovery import user_data

APP_ID = 1669000
MAX_TEXT = 8 * 1024 * 1024
TOKEN = re.compile(r'"((?:\\.|[^"\\])*)"|([{}])|(//[^\r\n]*)|(\s+)|(.)', re.S)


def read_text_bytes(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_TEXT + 1)
    if len(raw) > MAX_TEXT:
        raise FormatError('Steam metadata exceeds 8 MiB')
    return raw


def tokens(text, start=0):
    if len(text.encode('utf-8')) > MAX_TEXT:
        raise FormatError('Steam metadata exceeds 8 MiB')
    count = 0
    for match in TOKEN.finditer(text, start):
        if match[3] or match[4]:
            continue
        if match[5]:
            raise FormatError('Unsupported unquoted Steam metadata token')
        count += 1
        if count > 200000:
            raise FormatError('Steam metadata token limit exceeded')
        value = re.sub(r'\\([\\"])', r'\1', match[1]) if match[1] is not None else match[2]
        yield ('string' if match[1] is not None else 'brace'), value, match.end()


def parse(text):
    stream = iter(tokens(text))
    def object_value(depth=0, nested=False):
        if depth > 48:
            raise FormatError('Steam metadata nesting limit exceeded')
        result = {}
        for kind, key, _ in stream:
            if kind == 'brace' and key == '}' and nested:
                return result
            if kind != 'string':
                raise FormatError('Expected a quoted Steam metadata key')
            folded = key.casefold()
            if folded in result:
                raise FormatError('Duplicate Steam metadata key: ' + key)
            try:
                kind, value, _ = next(stream)
            except StopIteration:
                raise FormatError('Steam metadata value is missing') from None
            if kind == 'brace' and value == '{':
                value = object_value(depth + 1, True)
            elif kind != 'string':
                raise FormatError('Unexpected Steam metadata brace')
            result[folded] = value
        if nested:
            raise FormatError('Steam metadata object is truncated')
        return result
    return object_value()


def appinfo(text):
    matches = list(re.finditer(r'(?m)^\s*"1669000"\s*\{', text))
    if len(matches) != 1:
        raise FormatError('Expected exactly one complete AoW4 app_info_print block')
    start = matches[0].start(); depth = 0
    for kind, value, end in tokens(text, start):
        if kind == 'brace':
            depth += 1 if value == '{' else -1
            if depth == 0:
                return parse(text[start:end])
    raise FormatError('AoW4 app-info block is truncated')


def number(value, name, zero=False):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,10}', value):
        raise FormatError('Invalid Steam ' + name)
    value = int(value)
    if not (0 if zero else 1) <= value <= 0xffffffff:
        raise FormatError('Steam ' + name + ' is outside uint32')
    return value


def local(game):
    game = Path(game).resolve(); path = game.parent.parent / 'appmanifest_1669000.acf'
    raw = read_text_bytes(path)
    state = parse(raw.decode('utf-8-sig')).get('appstate')
    if not isinstance(state, dict) or state.get('appid') != str(APP_ID) or state.get('installdir', '').casefold() != game.name.casefold():
        raise FormatError('Steam appmanifest does not identify this AoW4 directory')
    branches = {state[k]['betakey'].casefold() for k in ('userconfig', 'mountedconfig')
                if isinstance(state.get(k), dict) and state[k].get('betakey')}
    if len(branches) > 1:
        raise FormatError('Installed Steam branch settings disagree')
    return {'manifest': str(path), 'manifest_sha256': sha(raw),
            'build_id': number(state.get('buildid'), 'installed BuildID'),
            'branch': next(iter(branches), 'public'), 'state_flags': state.get('stateflags'),
            'target_build_id': number(state.get('targetbuildid', '0'), 'target BuildID', True)}


def compare(installed, metadata, branch=None, online=False):
    if type(online) is not bool:
        raise FormatError('Online provenance must be an explicit boolean')
    target = (branch or installed['branch']).casefold()
    if not re.fullmatch(r'[a-z0-9_.-]{1,80}', target):
        raise FormatError('Invalid Steam branch name')
    try:
        app = metadata[str(APP_ID)]; row = app['depots']['branches'][target]
    except (KeyError, TypeError):
        raise FormatError('Requested AoW4 branch BuildID is unavailable') from None
    if not isinstance(app, dict) or app.get('appid', str(APP_ID)) != str(APP_ID) or not isinstance(row, dict):
        raise FormatError('Invalid AoW4 app-info identity or branch')
    build = number(row.get('buildid'), 'reported branch BuildID')
    settled = installed['state_flags'] == '4' and installed['target_build_id'] == 0
    same_branch = target == installed['branch']; matches = installed['build_id'] == build
    status = ('installation_not_settled' if not settled else 'different_installed_branch' if not same_branch else
              'matches_reported_branch' if matches else 'differs_from_reported_branch')
    return {'format': 'aow4mod-steam-version/1', 'app_id': APP_ID, 'installed': installed,
            'reported_branch': {'name': target, 'build_id': build, 'timeupdated': row.get('timeupdated')},
            'status': status, 'build_ids_equal': matches, 'same_branch': same_branch,
            'online_query_verified': online, 'server_latest_verified': bool(online and settled and same_branch and matches),
            'coverage': 'Steam-reported branch BuildID at query time; not installed-file integrity, Mod compatibility or native ABI support.'}


def from_capture(game, path, branch=None):
    path = Path(path).resolve(); raw = read_text_bytes(path)
    result = compare(local(game), appinfo(raw.decode('utf-8-sig', errors='replace')), branch)
    result['capture'] = {'file': str(path), 'sha256': sha(raw), 'fresh_online_query': False}
    return result


def signature(path):
    script = "$ErrorActionPreference='Stop';$s=Get-AuthenticodeSignature -LiteralPath $env:AOW4_STEAMCMD_EXE;@{status=[string]$s.Status;subject=$s.SignerCertificate.Subject}|ConvertTo-Json -Compress"
    env = dict(os.environ, AOW4_STEAMCMD_EXE=str(path)); env.pop('PSModulePath', None)
    proc = subprocess.run([shutil.which('pwsh') or 'powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                          env=env, capture_output=True, text=True, timeout=30,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if proc.returncode:
        raise FormatError('Cannot verify the SteamCMD Authenticode signature')
    record = json.loads(proc.stdout)
    if record.get('status') != 'Valid' or not re.search(r'(?:^|,\s*)O=Valve(?: Corp\.)?(?:,|$)', record.get('subject') or ''):
        raise FormatError('SteamCMD must have a valid Valve Authenticode signature')
    return record


def query(game, executable, output, branch=None, timeout=120):
    if os.name != 'nt':
        raise FormatError('Reviewed SteamCMD execution currently requires Windows')
    if type(timeout) is not int or not 30 <= timeout <= 600:
        raise FormatError('SteamCMD query timeout must be 30..600 seconds')
    game = Path(game).resolve(); executable = Path(executable).resolve()
    reject_reparse(executable)
    if executable.name.casefold() != 'steamcmd.exe':
        raise FormatError('Select the official steamcmd.exe')
    outside(executable, [game.parent.parent.parent, user_data() / 'Mods'])
    installed = local(game); signer = signature(executable)
    output = outside(output, [game.parent.parent.parent, user_data() / 'Mods', executable.parent])
    command = [str(executable), '-language', 'english', '+login', 'anonymous', '+app_info_update', '1', '+app_info_print', str(APP_ID), '+quit']
    record = {'format': 'aow4mod-steam-version-query/1', 'command': command, 'phase': 'prepared',
              'installed': installed, 'signature_before': signer, 'executable_sha256_before': file_hash(executable)}
    with new_directory(output, [game, executable.parent]) as stage:
        save_record(stage / 'query.json', record)
        shutil.copyfile(installed['manifest'], stage / 'installed.acf')
        if file_hash(stage / 'installed.acf') != installed['manifest_sha256']:
            raise FormatError('Steam appmanifest changed before capture')
    output = Path(output).resolve()
    try:
        with (output / 'stdout.log').open('wb') as log:
            proc = subprocess.run(command, cwd=executable.parent, stdin=subprocess.DEVNULL, stdout=log,
                                  stderr=subprocess.STDOUT, timeout=timeout,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        raw = read_text_bytes(output / 'stdout.log')
        text = raw.decode('utf-8-sig', errors='replace')
        connected = bool(re.search(r'Connecting anonymously to Steam Public\s*\.\.\.\s*OK', text, re.I))
        if proc.returncode or not connected or re.search(r'Failed to request AppInfo update|Failed to get app info|Not logged on|No Connection', text, re.I):
            raise FormatError('SteamCMD did not confirm a successful anonymous online query')
        if file_hash(installed['manifest']) != installed['manifest_sha256']:
            raise FormatError('Installed Steam version changed during query')
        record['signature_after'] = signature(executable)
        result = compare(installed, appinfo(text), branch, online=True)
        result['checked_utc'] = datetime.now(timezone.utc).isoformat()
        result['query'] = {'directory': str(output), 'stdout_sha256': sha(raw),
                           'forced_app_info_update_requested': True, 'anonymous_connection_confirmed': True,
                           'cache_freshness': 'Official forced-refresh request; no independent server timestamp attestation.'}
        record.update(phase='completed', exit_code=proc.returncode, executable_sha256_after=file_hash(executable))
        save_record(output / 'result.json', result); save_record(output / 'query.json', record)
        return result
    except Exception as exc:
        record.update(phase='failed', error=str(exc), server_latest_verified=False)
        save_record(output / 'query.json', record)
        if isinstance(exc, subprocess.TimeoutExpired):
            raise FormatError('SteamCMD query timed out; its owned process was terminated and evidence retained') from exc
        raise
