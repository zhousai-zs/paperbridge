import hashlib
import json
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024*1024), b''):
            digest.update(part)
    return digest.hexdigest()


def seal_chunk(directory):
    from .integrity import chunk_issues, IntegrityError
    directory = Path(directory)
    (directory / 'DONE').unlink(missing_ok=True)
    issues = chunk_issues(directory)
    if issues:
        raise IntegrityError(issues)
    files = {p.relative_to(directory).as_posix(): sha256(p)
             for p in directory.rglob('*') if p.is_file() and p.name not in
             ('DONE', 'DONE.tmp', 'engine.log', 'worker-status.json', 'engine-timing.json')}
    temp = directory / 'DONE.tmp'
    temp.write_text(json.dumps({'schema_version':1,'files':files}),encoding='utf-8')
    temp.replace(directory / 'DONE')


def cache_valid(directory, expected_pages=None):
    try:
        marker = json.loads((directory/'DONE').read_text(encoding='utf-8'))
        if not isinstance(marker, dict):
            return False
        files = marker['files']
        if not isinstance(files, dict):
            return False
        if not {'middle_json.json','markdown.md','structured_content.json'} <= set(files):
            return False
        for relative, expected in files.items():
            path = (directory/relative).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file() or sha256(path) != expected:
                return False
        from .integrity import chunk_issues
        return not chunk_issues(directory, expected_pages)
    except (OSError, ValueError, KeyError, TypeError):
        return False
