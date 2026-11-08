"""Build a checked Skill ZIP from an immutable, clean Git checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import zipfile

NAME = 'aow4-mod-audit'
REQUIRED = {'SKILL.md', 'agents/openai.yaml', 'scripts/aow4_mod_audit.py',
            'scripts/aow4mod/__init__.py', 'README.md', 'README.en.md', 'docs/STATUS.md',
            'LICENSE', 'THIRD_PARTY_NOTICES.md'}
FORBIDDEN = {'.git', '__pycache__', '.venv', '.aow4mod', 'node_modules', 'reports', 'private-evidence'}
BINARIES = {'.exe', '.dll', '.acp', '.rpk', '.clb', '.tam', '.mo', '.pyc', '.pyo', '.key', '.pem'}


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE).decode('utf-8').strip()


def inspect_archive(path):
    files = {}; seen = set(); total = 0
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            p = PurePosixPath(item.filename)
            if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] != NAME or ':' in item.filename or '\\' in item.filename:
                raise ValueError('Unsafe distribution path: ' + item.filename)
            if item.is_dir():
                continue
            relative = PurePosixPath(*p.parts[1:]); name = relative.as_posix()
            if name.casefold() in seen or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('Duplicate or symbolic distribution member: ' + name)
            if (FORBIDDEN.intersection(relative.parts) or relative.parts[:2] == ('docs', 'archive')
                    or relative.suffix.lower() in BINARIES or any(part == '.env' or part.startswith('.env.') and part != '.env.example' for part in relative.parts)):
                raise ValueError('Local/private/generated artifact in distribution: ' + name)
            total += item.file_size
            if total > 64 * 1024 * 1024:
                raise ValueError('Skill payload exceeds 64 MiB')
            raw = archive.read(item)
            files[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
            seen.add(name.casefold())
    if REQUIRED - files.keys():
        raise ValueError('Distribution is missing required files: ' + ', '.join(sorted(REQUIRED - files.keys())))
    return files


def package(repo, output):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    if Path(git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo:
        raise ValueError('Package the Skill repository itself, not a parent repository')
    if output == repo or repo in output.parents:
        raise ValueError('Distribution output must be outside the Skill checkout')
    if output.exists():
        raise ValueError('Distribution output directory already exists')
    if git(repo, 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('Commit or preserve working changes before packaging')
    commit = git(repo, 'rev-parse', 'HEAD')
    source = git(repo, 'show', commit + ':scripts/aow4mod/__init__.py')
    match = re.search(r'__version__\s*=\s*["\']([0-9][A-Za-z0-9.+-]*)["\']', source)
    if not match:
        raise ValueError('Committed package version is missing')
    version = match[1]; output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aow4mod-distribution-', dir=output.parent) as tmp:
        stage = Path(tmp); filename = f'{NAME}-{version}.zip'; target = stage / filename
        subprocess.run(['git', '-C', str(repo), 'archive', '--format=zip', '--prefix=' + NAME + '/',
                        '--output=' + str(target), commit], check=True, capture_output=True,
                       env={**os.environ, 'TZ': 'UTC'})
        files = inspect_archive(target); digest = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest = {'format': 'aow4mod-skill-distribution/1', 'name': NAME, 'version': version,
                    'git_commit': commit, 'archive': filename, 'archive_sha256': digest,
                    'file_count': len(files), 'files': files,
                    'scope': 'Skill source/runtime only; no game binaries or private acceptance data.'}
        (stage / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (stage / 'SHA256SUMS').write_text(digest + '  ' + filename + '\n', encoding='ascii')
        if output.exists():
            raise ValueError('Output appeared during packaging; refusing to replace it')
        output.mkdir()
        for item in stage.iterdir():
            (output / item.name).write_bytes(item.read_bytes())
    return {'directory': str(output), 'archive': filename, 'sha256': digest, 'version': version,
            'commit': commit, 'files': len(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(package(Path(__file__).resolve().parents[1], args.output), indent=2))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
