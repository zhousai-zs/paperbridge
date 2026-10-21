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
    files = {p.relative_to(directory).as_posix(): sha256(p)
             for p in directory.rglob('*') if p.is_file() and p.name not in ('DONE','engine.log')}
    (directory/'DONE').write_text(json.dumps({'schema_version':1,'files':files}),encoding='utf-8')


def cache_valid(directory):
    try:
        marker = json.loads((directory/'DONE').read_text(encoding='utf-8'))
        files = marker['files']
        if not {'middle_json.json','markdown.md','structured_content.json'} <= set(files):
            return False
        for relative, expected in files.items():
            path = (directory/relative).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file() or sha256(path) != expected:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False
