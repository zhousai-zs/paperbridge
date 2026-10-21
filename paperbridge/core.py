from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import time
import signal

from .environment import ROOT, configure
from .quality import check_markdown, image_references, flatten_text
from .checkpoint import cache_valid


def write_json(path, value):
    temp = path.with_name(path.name + f'.{os.getpid()}.{time.time_ns()}.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def digest_file(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def select_pages(spec, count):
    if not spec or spec == "all":
        return list(range(count))
    pages = set()
    for part in spec.split(","):
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part.strip())
        if not match:
            raise ValueError(f"Invalid page range: {part!r}; use 1-3,5")
        first, last = int(match[1]), int(match[2] or match[1])
        if not 1 <= first <= last <= count:
            raise ValueError(f"Page range {part!r} outside 1-{count}")
        pages.update(range(first - 1, last))
    return sorted(pages)


@contextmanager
def job_lock(root):
    path = root / ".lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f"Job already running, or interrupted lock needs inspection: {path}") from None
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def rebase_images(markdown, prefix):
    # Engine asset names are ASCII relative paths. Keep them stable in saved JSON.
    return re.sub(r'(?<=[("])images/', prefix + "/images/", markdown)


def pdf_details(path, password=None):
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    if reader.is_encrypted and (not password or not reader.decrypt(password)):
        raise ValueError("Encrypted PDF: supply an unencrypted copy before conversion.")
    if not reader.pages:
        raise ValueError("PDF has no pages")
    return len(reader.pages)


def run_worker(cmd, log, timeout):
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
    process = subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,env=os.environ.copy(),**options)
    try:
        return subprocess.CompletedProcess(cmd,process.wait(timeout=timeout))
    except (subprocess.TimeoutExpired,KeyboardInterrupt):
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
        else:
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
        process.wait()
        raise


def render_pages(source, pages, output):
    import pypdfium2 as pdfium
    output.mkdir(exist_ok=True)
    with pdfium.PdfDocument(str(source)) as doc:
        for idx in pages:
            path = output / f"page-{idx + 1:04}.png"
            if path.exists():
                continue
            page = doc[idx]
            bitmap = page.render(scale=1.5)
            bitmap.to_pil().save(path)
            bitmap.close()
            page.close()


def rebase_structure(value, prefix):
    if isinstance(value, list):
        return [rebase_structure(v, prefix) for v in value]
    if isinstance(value, dict):
        return {k: (prefix + '/' + v if k == 'image_path' and isinstance(v, str) and v.startswith('images/')
                    else rebase_structure(v, prefix)) for k, v in value.items()}
    if isinstance(value, str):
        return rebase_images(value, prefix)
    return value


def save_evidence(source, pages, root):
    """Preserve detected equations and tables as independent source crops."""
    import pypdfium2 as pdfium
    evidence = []
    directory = root / 'evidence'
    directory.mkdir(exist_ok=True)
    with pdfium.PdfDocument(str(source)) as doc:
        for page in pages:
            visual = [b for b in page['blocks'] if b['type'] in ('equation', 'table') and b.get('bbox')]
            if not visual:
                continue
            pdfpage = doc[page['page_idx']]
            bitmap = pdfpage.render(scale=2.5)
            raster = bitmap.to_pil()
            for n, block in enumerate(visual):
                x0,y0,x1,y1 = block['bbox']
                if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                    continue
                box = (max(0,int(x0*raster.width)-3), max(0,int(y0*raster.height)-3),
                       min(raster.width,int(x1*raster.width)+4), min(raster.height,int(y1*raster.height)+4))
                relative = f"evidence/page-{page['page_idx']+1:04}-{block['type']}-{n:03}.png"
                raster.crop(box).save(root/relative)
                evidence.append({'page':page['page_idx']+1, 'type':block['type'],
                                 'block_index':block.get('index'), 'bbox':block['bbox'], 'image':relative,
                                 'recognized_content': flatten_text(block.get('content', ''))})
            raster.close()
            bitmap.close()
            pdfpage.close()
    write_json(root / 'evidence.json', evidence)
    return evidence


def convert(source, output, *, tier="standard", pages="all", chunk_size=20,
            timeout=1800, ocr="auto", progress=print, on_event=None):
    configure()
    if tier not in {'basic', 'standard', 'advanced'} or ocr not in {'auto', 'txt', 'ocr'}:
        raise ValueError('Unsupported tier or OCR mode')
    source = Path(source).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF file: {source}")
    count = pdf_details(source)
    selected = select_pages(pages, count)
    if chunk_size < 1 or timeout <= 0:
        raise ValueError("chunk-size and timeout must be positive")
    config = {
        "source_sha256": digest_file(source), "pages": selected, "tier": tier,
        "ocr": ocr, "chunk_size": chunk_size, "mineru": importlib.metadata.version("mineru"),
        "paperbridge_schema": 1,
    }
    job_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
    stem = re.sub(r"[^\w.-]", "_", source.stem)[:65] or "paper"
    root = Path(output).expanduser().resolve() / f"{stem}-{job_hash}"
    root.mkdir(parents=True, exist_ok=True)
    with job_lock(root):
        return _convert_locked(source, root, config, count, selected, timeout, progress, on_event)


def _convert_locked(source, root, config, count, selected, timeout, progress, on_event=None):
    start = time.monotonic()
    report = {
        "schema_version": 1, "status": "running", "config": config,
        "source_name": source.name, "source_pages": count,
        "selected_pages": [i + 1 for i in selected],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "chunks": [], "issues": [], "accuracy_verified": False,
    }
    previous = root / 'report.json'
    if previous.exists():
        history = root / 'history'
        history.mkdir(exist_ok=True)
        shutil.copyfile(previous, history / f'report-{time.time_ns()}.json')
    write_json(previous, report)
    snapshot = root / "source.pdf"
    write_json(root / "manifest.json", config)
    chunks = [selected[i:i + config["chunk_size"]] for i in range(0, len(selected), config["chunk_size"])]
    def notify(stage, **extra):
        report.update(stage=stage, **extra)
        write_json(root / "report.json", report)
        if on_event:
            on_event({"stage": stage, "output": str(root),
                      "owner_pid": os.getpid(),
                      "total_pages": len(selected), "completed_pages": report.get("completed_pages", 0),
                      **extra})

    try:
        notify("preparing", completed_pages=0)
        if not snapshot.exists():
            shutil.copyfile(source, snapshot)
        if digest_file(snapshot) != config['source_sha256']:
            raise RuntimeError(f'Source changed during conversion or snapshot was modified: {snapshot}')
        for number, indices in enumerate(chunks):
            notify("parsing", current_pages=[i + 1 for i in indices],
                   chunk=number + 1, total_chunks=len(chunks))
            chunk = root / "chunks" / f"{number:04}"
            chunk.mkdir(parents=True, exist_ok=True)
            page_spec = ",".join(str(i + 1) for i in indices)
            progress(f"[{number + 1}/{len(chunks)}] pages {page_spec}", flush=True)
            cached = cache_valid(chunk)
            if not cached:
                (chunk / 'DONE').unlink(missing_ok=True)
                cmd = [sys.executable, "-m", "paperbridge.worker", str(snapshot), str(chunk),
                       "--tier", config["tier"], "--pages", page_spec, "--ocr", config["ocr"]]
                with (chunk / "engine.log").open("w", encoding="utf-8") as log:
                    result = run_worker(cmd, log, timeout)
                if result.returncode:
                    raise RuntimeError(f"Parser failed ({result.returncode}). See {chunk / 'engine.log'}")
                if not (chunk / "DONE").is_file():
                    raise RuntimeError(f"Parser produced no completion marker: {chunk}")
            report["chunks"].append({"index": number, "pages": indices, "cached": cached})
            notify("parsing", completed_pages=sum(len(c['pages']) for c in report['chunks']))
        notify("assembling")
        assemble(root, selected, report)
        notify("rendering")
        render_pages(snapshot, selected, root / "pages")
        report["status"] = "needs_review" if report["issues"] else "complete"
        notify("finished", status=report["status"])
    except (Exception, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["issues"].append({"code": "conversion_failed", "detail": str(exc) or type(exc).__name__})
        raise
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - start, 2)
        write_json(root / "report.json", report)
    return root, report


def assemble(root, selected, report):
    from mineru.parser import ParseResult
    sections, all_pages = [], []
    for item in report["chunks"]:
        relative = f"chunks/{item['index']:04}"
        chunk = root / relative
        raw = json.loads((chunk / "middle_json.json").read_text(encoding="utf-8"))
        for page in raw["pages"]:
            idx = page["page_idx"]
            one = {**raw, "pages": [page], "is_full_document": False}
            md = ParseResult.from_dict(one).markdown()
            md = rebase_images(md, relative)
            if not md.strip():
                report["issues"].append({"code": "empty_page", "page": idx + 1})
            for block in page['blocks']:
                if block['type'] == 'equation':
                    expression = flatten_text(block.get('content',''))
                    if not expression.strip():
                        report['issues'].append({'code':'formula_image_only','page':idx+1,'block_index':block.get('index')})
                    elif report['config']['tier'] == 'basic' and re.search('[\u4e00-\u9fff]',expression):
                        report['issues'].append({'code':'cjk_formula_needs_review','page':idx+1,
                            'detail':'Basic mode recognized Chinese text inside a formula. Use standard mode and compare source crop.'})
            sections.append((idx, f'<!-- source_page: {idx + 1} -->\n\n'
                             f'[原页 {idx + 1}](pages/page-{idx + 1:04}.png)\n\n{md.strip()}\n'))
            all_pages.append(page)
            page['_asset_prefix'] = relative
    actual = [i for i, _ in sections]
    if sorted(actual) != selected:
        raise RuntimeError(f"Page coverage mismatch: expected {selected}, got {actual}")
    sections.sort(key=lambda x: x[0])
    evidence = save_evidence(root / 'source.pdf', all_pages, root)
    for i, (idx, text) in enumerate(sections):
        matches = [e for e in evidence if e['page'] == idx + 1]
        if matches:
            text += '\n\n原图核对：' + ' · '.join(
                f"[{'公式' if e['type']=='equation' else '表格'} {n+1}]({e['image']})" for n,e in enumerate(matches)) + '\n'
        sections[i] = (idx, text)
    preface = (
        f"# {report['source_name']}\n\n"
        "> 自动解析结果；公式、表格及阅读顺序未经逐项人工认证。"
        "核对数值或符号时请打开对应原页。图片通过相对路径引用，移动时请保留整个文件夹。\n\n"
        f"- 原始 PDF：[source.pdf](source.pdf)\n"
        f"- 解析模式：MinerU {report['config']['mineru']} / {report['config']['tier']}\n"
        f"- 页面：{len(selected)} / {report['source_pages']}\n\n"
    )
    markdown = preface + "\n\n---\n\n".join(md for _, md in sections)
    report["issues"].extend(check_markdown(markdown, root))
    report["counts"] = {
        "pages": len(actual), "images": len(image_references(markdown)),
        "display_math": markdown.count("$$") // 2,
        "html_tables": len(re.findall(r"<table\b", markdown, flags=re.I)),
        "characters": len(markdown),
        "source_crops": len(evidence),
    }
    (root / "document.md").write_text(markdown, encoding="utf-8")
    rebased_pages = []
    for page in all_pages:
        prefix = page.pop('_asset_prefix')
        rebased_pages.append(rebase_structure(page, prefix))
    write_json(root / "document.json", {"schema": "paperbridge.document", "schema_version": 1,
        "pages": sorted(rebased_pages,key=lambda p:p['page_idx']), "source_sha256": report["config"]["source_sha256"]})
    (root / "AGENT_README.md").write_text(
        "# Agent 阅读入口\n\n"
        "先读 document.md；相对路径以本文件夹为根目录。"
        "正文中的 source_page 注释对应 source.pdf 的 1 起始页码。\n\n"
        "report.json 的 complete 仅表示机械完整性检查通过，不代表识别准确率为 100%。"
        "needs_review 表示存在明确问题，failed 表示解析未完成。\n\n"
        "公式以 LaTeX 输出；检查关键公式、数字、合并单元格时使用 pages/ 原页图。"
        "无法读取图像的纯文本 agent 不能从图片链接获得图中全部信息。"
        "document.json 保留块结构；原始引擎结构和素材保存在 chunks/。\n",
        encoding="utf-8",
    )
