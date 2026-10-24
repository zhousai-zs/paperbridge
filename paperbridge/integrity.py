"""Validate saved references, including images omitted from a checkpoint manifest."""
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from .quality import check_markdown, image_references


class IntegrityError(RuntimeError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__('Result integrity check failed: ' + json.dumps(issues[:8], ensure_ascii=False))


def structure_references(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {'image_path', 'img_path'} and isinstance(item, str) and item:
                yield item
            else:
                yield from structure_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from structure_references(item)
    elif isinstance(value, str):
        yield from image_references(value)


def _check_one_image(ref, root):
    from PIL import Image
    parsed = urlsplit(ref)
    if parsed.scheme or parsed.netloc:
        return {'code': 'nonlocal_image', 'detail': ref}
    path = (root / unquote(parsed.path)).resolve()
    if not path.is_relative_to(root):
        return {'code': 'image_outside_bundle', 'detail': ref}
    if not path.is_file() or not path.stat().st_size:
        return {'code': 'missing_image', 'detail': ref}
    try:
        with Image.open(path) as image:
            image.load()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        return {'code': 'unreadable_image', 'detail': ref, 'error': str(exc)}
    return None


def check_images(refs, root):
    """Read all pixels, since verify() alone can miss truncated JPEG data."""
    root = Path(root).resolve()
    unique = sorted(set(refs))
    if len(unique) <= 2:
        return [issue for ref in unique if (issue := _check_one_image(ref, root)) is not None]
    from concurrent.futures import ThreadPoolExecutor
    import os
    workers = max(1, min(4, os.cpu_count() or 1, len(unique)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [issue for issue in pool.map(lambda ref: _check_one_image(ref, root), unique) if issue is not None]


def chunk_issues(root, expected_pages=None):
    root = Path(root)
    issues, refs = [], []
    raw = None
    for name in ('middle_json.json', 'structured_content.json'):
        try:
            value = json.loads((root / name).read_text(encoding='utf-8'))
            refs.extend(structure_references(value))
            if name == 'middle_json.json':
                raw = value
        except (OSError, ValueError) as exc:
            issues.append({'code': 'invalid_structure', 'detail': name, 'error': str(exc)})
    if not isinstance(raw, dict) or not isinstance(raw.get('pages'), list) or not raw['pages']:
        issues.append({'code': 'invalid_pages', 'detail': 'middle_json.json'})
    else:
        actual = [p.get('page_idx') for p in raw['pages'] if isinstance(p, dict)]
        if (len(actual) != len(raw['pages']) or any(type(p) is not int for p in actual)
                or len(set(actual)) != len(actual)
                or (expected_pages is not None and sorted(actual) != sorted(expected_pages))):
            issues.append({'code': 'page_coverage_mismatch', 'detail': str(actual)})
    try:
        md = (root / 'markdown.md').read_text(encoding='utf-8')
        refs.extend(image_references(md))
    except OSError as exc:
        issues.append({'code': 'missing_markdown', 'detail': str(exc)})
    return issues + check_images(refs, root)


def validate_bundle(root, report=None):
    """Read-only check usable for old results as well as new conversions."""
    root = Path(root)
    issues, refs = [], []
    try:
        if report is None:
            report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
        md = (root / 'document.md').read_text(encoding='utf-8')
        refs.extend(image_references(md))
        issues.extend(check_markdown(md, root))
        actual = [int(x) for x in re.findall(r'<!-- source_page: (\d+) -->', md)]
        if actual != report['selected_pages']:
            issues.append({'code': 'page_coverage_mismatch', 'detail': str(actual)})
        refs.extend(f'pages/page-{p:04}.png' for p in report['selected_pages'])
        for name in ('document.json', 'evidence.json'):
            value = json.loads((root / name).read_text(encoding='utf-8'))
            refs.extend(structure_references(value))
            if name == 'evidence.json':
                refs.extend(item['image'] for item in value)
            elif [p['page_idx'] + 1 for p in value['pages']] != report['selected_pages']:
                issues.append({'code': 'page_coverage_mismatch', 'detail': name})
        from .checkpoint import cache_valid, sha256
        if sha256(root / 'source.pdf') != report['config']['source_sha256']:
            issues.append({'code': 'source_hash_mismatch', 'detail': 'source.pdf'})
        for item in report['chunks']:
            chunk = root / 'chunks' / f"{item['index']:04}"
            if not cache_valid(chunk, expected_pages=item['pages']):
                issues.append({'code': 'invalid_checkpoint', 'detail': str(chunk.relative_to(root))})
                issues.extend({**issue, 'chunk': item['index']} for issue in chunk_issues(chunk, item['pages']))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        issues.append({'code': 'invalid_bundle', 'detail': str(exc)})
    issues.extend(check_images(refs, root))
    # Markdown checks and the pixel check may both identify the same missing file.
    unique = {json.dumps(i, sort_keys=True): i for i in issues}
    issues = list(unique.values())
    hard = [i for i in issues if i['code'] not in {
        'unbalanced_display_math', 'replacement_characters', 'empty_markdown'}]
    return {'passed': not issues, 'assets_passed': not hard,
            'checked_images': len(set(refs)), 'issues': issues, 'accuracy_verified': False}
