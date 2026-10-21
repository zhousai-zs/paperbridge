"""Human revisions and self-contained reading exports."""
from datetime import datetime, timezone
import hashlib
import io
import json
import re
import time
from urllib.parse import unquote, urlsplit
import zipfile

from .core import digest_file, write_json
from .quality import check_markdown


PAGE_MARKER = re.compile(r'<!-- source_page: (\d+) -->')


def page_sections(text):
    matches = list(PAGE_MARKER.finditer(text))
    return {int(m[1]): text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
            for i, m in enumerate(matches)}


def revision_version(root):
    path = root / 'review' / 'revisions.json'
    return digest_file(path) if path.exists() else ''


def read_revisions(root):
    path = root / 'review' / 'revisions.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'pages': {}}


def save_revision(root, page, text, version):
    if version != revision_version(root):
        raise ValueError('修订已在其他窗口更新，请重新打开后保存。')
    sections = page_sections((root / 'document.md').read_text(encoding='utf-8'))
    if page not in sections:
        raise ValueError('该页不在转换结果中。')
    if PAGE_MARKER.search(text):
        raise ValueError('请仅编辑当前页正文，不要加入 source_page 页码标记。')
    directory = root / 'review'
    directory.mkdir(exist_ok=True)
    current = directory / 'revisions.json'
    if current.exists():
        history = directory / 'history'
        history.mkdir(exist_ok=True)
        (history / f'{time.time_ns()}.json').write_bytes(current.read_bytes())
    revisions = read_revisions(root)
    revisions['pages'][str(page)] = {'markdown': text, 'updated_at': datetime.now(timezone.utc).isoformat(),
                                    'original_sha256': hashlib.sha256(sections[page].encode()).hexdigest()}
    write_json(current, revisions)
    return revision_version(root)


def effective_markdown(root):
    original = (root / 'document.md').read_text(encoding='utf-8')
    revisions = read_revisions(root)['pages']
    matches = list(PAGE_MARKER.finditer(original))
    parts = [original[:matches[0].start()] if matches else original]
    for i, marker in enumerate(matches):
        page = marker[1]
        end = matches[i + 1].start() if i + 1 < len(matches) else len(original)
        content = revisions.get(page, {}).get('markdown', original[marker.end():end].strip())
        parts.append(f'<!-- source_page: {page} -->\n\n{content}\n\n')
    return ''.join(parts)


def export_bundle(root, destination=None):
    markdown = effective_markdown(root)
    problems = check_markdown(markdown, root)
    if problems:
        raise ValueError('阅读包包含缺失或无效的图片引用，请先修订：' + json.dumps(problems, ensure_ascii=False))
    files = set()
    # Keep original source, page/evidence images, and every engine image referenced by Markdown/HTML.
    for name in ('source.pdf', 'document.json', 'evidence.json', 'report.json', 'manifest.json', 'AGENT_README.md'):
        if (root / name).is_file():
            files.add(name)
    for directory in ('pages', 'evidence', 'review'):
        files.update(p.relative_to(root).as_posix() for p in (root / directory).rglob('*') if p.is_file())
    files.update(p.relative_to(root).as_posix() for p in (root / 'chunks').glob('*/images/**/*') if p.is_file())
    for ref in re.findall(r'(?:src|href)=["\']([^"\']+)|!?\[[^\]]*\]\(<?([^\s)>]+)', markdown):
        value = next((v for v in ref if v), '')
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        path = (root / unquote(parsed.path)).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('阅读包引用了结果目录以外的文件。')
        if path.is_file():
            files.add(path.relative_to(root).as_posix())
    buffer = destination if destination is not None else io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(files):
            path = (root / relative).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError('结果目录包含指向外部的文件链接。')
            archive.write(path, relative)
        archive.writestr('document.md', markdown)
        archive.writestr('original-document.md', (root / 'document.md').read_bytes())
        archive.writestr('START_HERE.md', '# Agent 阅读包\n\n先读 AGENT_README.md，再读 document.md。\n'
                          'document.md 已合入人工保存的逐页修订；原始识别稿见 original-document.md。\n'
                          'review/revisions.json 记录修订页，不表示整篇论文已人工认证。\n'
                          '关键数字、公式和表格请核对 source.pdf、pages/ 和 evidence/。\n'
                          '此包省略推理日志和检查点，不用于断点续跑。\n')
    return destination if destination is not None else buffer.getvalue()
