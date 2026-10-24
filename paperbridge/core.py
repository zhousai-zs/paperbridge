from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
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
import threading
import time
import signal

from .environment import ROOT, configure
from .quality import check_markdown, image_references, flatten_text
from .checkpoint import cache_valid
from .integrity import IntegrityError, chunk_issues, validate_bundle
from .performance import log_progress, summarize_log
from .processes import (check_cancelled, ConversionCancelled, hidden_options, module_command,
                        process_alive, terminate_tree)


def write_json(path, value):
    temp = path.with_name(path.name + f'.{os.getpid()}.{time.time_ns()}.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(6):
            try:
                temp.replace(path)
                break
            except PermissionError:
                # Windows readers / antivirus can briefly deny an atomic replacement.
                if os.name != 'nt' or attempt == 5:
                    raise
                time.sleep(0.02 * 2 ** attempt)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def archive_batch_report(output):
    """Move the previous batch summary into history/ before a new batch reuses the folder."""
    current = Path(output) / 'batch-report.json'
    if current.is_file():
        history = current.parent / 'history'
        history.mkdir(exist_ok=True)
        current.replace(history / f'batch-report-{time.time_ns()}.json')


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
    for attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            # Reclaim a lock left by a crashed or killed coordinator; keep any lock
            # whose owner is alive or cannot be identified.
            try:
                owner = int(path.read_text(encoding='utf-8').strip())
            except (OSError, ValueError):
                owner = None
            if attempt or owner is None or process_alive(owner):
                raise RuntimeError(f"Job already running, or interrupted lock needs inspection: {path}") from None
            path.unlink(missing_ok=True)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def rebase_images(markdown, prefix):
    # Engine asset names are ASCII relative paths. Keep them stable in saved JSON.
    return re.sub(r'''(?<=[("'<])images/''', prefix + "/images/", markdown)


def pdf_details(path, password=None):
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    # Permission-only PDFs (owner password, empty user password) open without a prompt.
    if reader.is_encrypted and not reader.decrypt(password or ''):
        raise ValueError("Encrypted PDF: supply an unencrypted copy before conversion.")
    if not reader.pages:
        raise ValueError("PDF has no pages")
    return len(reader.pages)


def run_worker(cmd, log, timeout, on_update=None, engine=None):
    if engine is not None and engine.reuse:
        return engine.run(cmd, log, timeout, on_update)
    process = subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,env=os.environ.copy(),**hidden_options())
    try:
        deadline = time.monotonic() + timeout
        next_update = 0
        while process.poll() is None:
            check_cancelled()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(cmd, timeout)
            if on_update and time.monotonic() >= next_update:
                next_update = time.monotonic() + 1
                status = Path(log.name).parent / 'worker-status.json'
                event = {}
                try:
                    event = json.loads(status.read_text(encoding='utf-8'))
                    if event.get('worker_step') == 'inference':
                        with Path(log.name).open('rb') as reader:
                            reader.seek(max(0, reader.seek(0, 2) - 8192))
                            event.update(log_progress(reader.read().decode('utf-8', errors='replace')))
                except (OSError, ValueError):
                    pass
                on_update(event)
            time.sleep(0.1)
        check_cancelled()
        return subprocess.CompletedProcess(cmd, process.returncode)
    except BaseException:
        terminate_tree(process)
        raise


_PDFIUM_LOCK = threading.Lock()
_RENDER_ABORT = threading.local()


def _render_worker_count():
    return max(1, min(4, os.cpu_count() or 1))


def _check_render_cancelled():
    check_cancelled()
    event = getattr(_RENDER_ABORT, 'event', None)
    if event is not None and event.is_set():
        raise ConversionCancelled('Page rendering aborted.')


def _save_image(image, path):
    try:
        image.save(path)
    finally:
        image.close()


def _drain_futures(pending, limit=0):
    while len(pending) > limit:
        future, _ = pending.popleft()
        future.result()


def _cancel_futures(pending):
    while pending:
        future, image = pending.popleft()
        if future.cancel():
            image.close()


def render_pages(source, pages, output):
    import pypdfium2 as pdfium
    output = Path(output)
    output.mkdir(exist_ok=True)
    _check_render_cancelled()
    from .integrity import check_images
    existing = [f"page-{idx + 1:04}.png" for idx in pages if (output / f"page-{idx + 1:04}.png").exists()]
    invalid = {issue['detail'] for issue in check_images(existing, output)} if existing else set()
    todo = [idx for idx in pages if f"page-{idx + 1:04}.png" not in existing or f"page-{idx + 1:04}.png" in invalid]
    if not todo:
        return
    workers = _render_worker_count()
    pending = deque()
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(source))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            try:
                for idx in todo:
                    _check_render_cancelled()
                    path = output / f"page-{idx + 1:04}.png"
                    _drain_futures(pending, workers * 2 - 1)
                    with _PDFIUM_LOCK:
                        page = doc[idx]
                        try:
                            bitmap = page.render(scale=1.5)
                            try:
                                image = bitmap.to_pil()
                            finally:
                                bitmap.close()
                        finally:
                            page.close()
                    pending.append((pool.submit(_save_image, image, path), image))
                _drain_futures(pending)
            except BaseException:
                _cancel_futures(pending)
                raise
    finally:
        with _PDFIUM_LOCK:
            doc.close()


def rebase_structure(value, prefix):
    if isinstance(value, list):
        return [rebase_structure(v, prefix) for v in value]
    if isinstance(value, dict):
        return {k: (prefix + '/' + v if k in {'image_path', 'img_path'} and isinstance(v, str) and v.startswith('images/')
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
    workers = _render_worker_count()
    pending = deque()
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(source))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            try:
                for page in pages:
                    check_cancelled()
                    visual = [b for b in page['blocks'] if b['type'] in ('equation', 'table') and b.get('bbox')]
                    if not visual:
                        continue
                    with _PDFIUM_LOCK:
                        pdfpage = doc[page['page_idx']]
                        try:
                            bitmap = pdfpage.render(scale=2.5)
                            try:
                                raster = bitmap.to_pil()
                            finally:
                                bitmap.close()
                        finally:
                            pdfpage.close()
                    try:
                        for n, block in enumerate(visual):
                            x0, y0, x1, y1 = block['bbox']
                            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                                continue
                            box = (max(0, int(x0 * raster.width) - 3), max(0, int(y0 * raster.height) - 3),
                                   min(raster.width, int(x1 * raster.width) + 4), min(raster.height, int(y1 * raster.height) + 4))
                            relative = f"evidence/page-{page['page_idx']+1:04}-{block['type']}-{n:03}.png"
                            _drain_futures(pending, workers * 2 - 1)
                            crop = raster.crop(box)
                            pending.append((pool.submit(_save_image, crop, root / relative), crop))
                            evidence.append({'page': page['page_idx'] + 1, 'type': block['type'],
                                             'block_index': block.get('index'), 'bbox': block['bbox'], 'image': relative,
                                             'recognized_content': flatten_text(block.get('content', ''))})
                    finally:
                        raster.close()
                _drain_futures(pending)
            except BaseException:
                _cancel_futures(pending)
                raise
    finally:
        with _PDFIUM_LOCK:
            doc.close()
    write_json(root / 'evidence.json', evidence)
    return evidence


def convert(source, output, *, tier="standard", pages="all", chunk_size=20,
            timeout=1800, ocr="auto", progress=print, on_event=None, retries=1, engine=None):
    configure()
    check_cancelled()
    if tier not in {'basic', 'standard', 'advanced'} or ocr not in {'auto', 'txt', 'ocr'}:
        raise ValueError('Unsupported tier or OCR mode')
    source = Path(source).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF file: {source}")
    count = pdf_details(source)
    selected = select_pages(pages, count)
    if chunk_size < 1 or timeout <= 0:
        raise ValueError("chunk-size and timeout must be positive")
    if type(retries) is not int or not 0 <= retries <= 5:
        raise ValueError('retries must be an integer between 0 and 5')
    config = {
        "source_sha256": digest_file(source), "pages": selected, "tier": tier,
        "ocr": ocr, "chunk_size": chunk_size, "mineru": importlib.metadata.version("mineru"),
        "formula_device": os.environ.get('PAPERBRIDGE_FORMULA_DEVICE', 'cpu'),
        "paperbridge_schema": 1,
    }
    job_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
    stem = re.sub(r"[^\w.-]", "_", source.stem)[:65] or "paper"
    root = Path(output).expanduser().resolve() / f"{stem}-{job_hash}"
    root.mkdir(parents=True, exist_ok=True)
    from .engine import EngineSession
    with EngineSession() if engine is None else nullcontext(engine) as session:
        with job_lock(root):
            return _convert_locked(source, root, config, count, selected, timeout, progress, on_event, retries, session)


def _convert_locked(source, root, config, count, selected, timeout, progress, on_event=None, retries=1, engine=None):
    start = time.monotonic()
    report = {
        "schema_version": 1, "status": "running", "config": config,
        "source_name": source.name, "source_pages": count,
        "selected_pages": [i + 1 for i in selected],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "chunks": [], "issues": [], "accuracy_verified": False,
        "stage_seconds": {}, "retry_limit": retries,
        "engine_mode": 'persistent' if engine is not None and engine.reuse else 'isolated',
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
    current_stage, stage_started = None, time.monotonic()
    def record_stage():
        nonlocal stage_started
        now = time.monotonic()
        if current_stage:
            report['stage_seconds'][current_stage] = round(
                report['stage_seconds'].get(current_stage, 0) + now - stage_started, 4)
        stage_started = now

    def notify(stage, **extra):
        nonlocal current_stage
        _check_background_render()
        if current_stage != stage:
            record_stage()
            current_stage = stage
        report.update(stage=stage, **extra)
        report['elapsed_seconds'] = round(time.monotonic() - start, 2)
        write_json(root / "report.json", report)
        if on_event:
            on_event({"stage": stage, "output": str(root),
                      "owner_pid": os.getpid(),
                      "total_pages": len(selected), "completed_pages": report.get("completed_pages", 0),
                      "elapsed_seconds": report['elapsed_seconds'],
                      **extra})

    render_abort = threading.Event()
    render_pool = None
    render_future = None

    def _check_background_render():
        if render_future is not None and render_future.done():
            render_future.result()

    def _start_background_render():
        nonlocal render_pool, render_future
        if render_future is not None:
            return
        def _task():
            _RENDER_ABORT.event = render_abort
            try:
                render_pages(snapshot, selected, root / "pages")
            finally:
                _RENDER_ABORT.event = None
        render_pool = ThreadPoolExecutor(max_workers=1)
        render_future = render_pool.submit(_task)

    try:
        notify("preparing", completed_pages=0)
        if not snapshot.exists():
            shutil.copyfile(source, snapshot)
        if digest_file(snapshot) != config['source_sha256']:
            raise RuntimeError(f'Source changed during conversion or snapshot was modified: {snapshot}')
        _start_background_render()
        for number, indices in enumerate(chunks):
            check_cancelled()
            _check_background_render()
            notify("parsing", current_pages=[i + 1 for i in indices],
                   chunk=number + 1, total_chunks=len(chunks))
            chunk = root / "chunks" / f"{number:04}"
            chunk.mkdir(parents=True, exist_ok=True)
            page_spec = ",".join(str(i + 1) for i in indices)
            progress(f"[{number + 1}/{len(chunks)}] pages {page_spec}", flush=True)
            chunk_started = time.monotonic()
            cached = cache_valid(chunk, expected_pages=indices)
            chunk_report = {"index": number, "pages": indices, "cached": cached, "attempts": []}
            report['chunks'].append(chunk_report)
            if not cached:
                cmd = module_command('paperbridge.worker', snapshot, chunk,
                       '--tier', config['tier'], '--pages', page_spec, '--ocr', config['ocr'])
                for attempt in range(retries + 1):
                    check_cancelled()
                    _check_background_render()
                    # Archive failed/invalid output so stale images cannot satisfy a new attempt.
                    if any(chunk.iterdir()):
                        archive = root / 'history' / f'chunk-{number:04}-{time.time_ns()}'
                        archive.parent.mkdir(exist_ok=True)
                        if not (chunk.resolve().is_relative_to(root.resolve()) and
                                archive.resolve().is_relative_to(root.resolve())):
                            raise RuntimeError('Checkpoint archive must stay inside the result folder')
                        shutil.move(str(chunk), str(archive))
                        chunk.mkdir()
                        if len(chunk_report['attempts']):
                            chunk_report['attempts'][-1]['archive'] = archive.relative_to(root).as_posix()
                    attempt_started = time.monotonic()
                    attempt_report = {'attempt': attempt + 1}
                    chunk_report['attempts'].append(attempt_report)
                    notify('parsing', attempt=attempt + 1, max_attempts=retries + 1,
                           worker_step='loading_engine', step_completed=0, step_total=0)
                    try:
                        with (chunk / "engine.log").open("w", encoding="utf-8") as log:
                            result = run_worker(cmd, log, timeout,
                                on_update=lambda event: notify('parsing', **event), engine=engine)
                        if result.returncode:
                            raise RuntimeError(f"Parser failed ({result.returncode}). See {chunk / 'engine.log'}")
                        if not cache_valid(chunk, expected_pages=indices):
                            raise IntegrityError(chunk_issues(chunk, indices) or [
                                {'code': 'invalid_checkpoint', 'detail': str(chunk)}])
                        attempt_report['status'] = 'complete'
                        break
                    except (RuntimeError, subprocess.TimeoutExpired) as exc:
                        # A failed renderer cannot be recovered by retrying the parser.
                        _check_background_render()
                        if engine is not None:
                            engine.reset()
                        attempt_report.update(status='failed', error=str(exc))
                        if isinstance(exc, IntegrityError):
                            attempt_report['issues'] = exc.issues
                        if attempt == retries:
                            raise
                        notify('retrying', attempt=attempt + 2, max_attempts=retries + 1,
                               retry_reason=str(exc))
                        progress(f'Retrying chunk {number + 1}: {exc}', flush=True)
                    finally:
                        attempt_report['elapsed_seconds'] = round(time.monotonic() - attempt_started, 3)
            chunk_report['elapsed_seconds'] = round(time.monotonic() - chunk_started, 3)
            timing = chunk / 'engine-timing.json'
            if timing.exists():
                try:
                    chunk_report['engine_timing'] = json.loads(timing.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    pass
            if (chunk / 'engine.log').exists():
                chunk_report['engine_log_timing'] = summarize_log((chunk / 'engine.log').read_text(encoding='utf-8', errors='replace'))
            notify("parsing", completed_pages=sum(len(c['pages']) for c in report['chunks']))
        notify("assembling")
        check_cancelled()
        assemble(root, selected, report)
        notify("rendering")
        if render_future is not None:
            render_pool.shutdown(wait=True)
            render_pool = None
            render_future.result()
        else:
            render_pages(snapshot, selected, root / "pages")
        check_cancelled()
        notify('validating')
        report['integrity'] = validate_bundle(root, report)
        write_json(root / 'integrity.json', report['integrity'])
        if not report['integrity']['assets_passed']:
            raise IntegrityError(report['integrity']['issues'])
        for issue in report['integrity']['issues']:
            if issue not in report['issues']:
                report['issues'].append(issue)
        report["status"] = "needs_review" if report["issues"] else "complete"
        notify("finished", status=report["status"])
    except (Exception, KeyboardInterrupt) as exc:
        render_abort.set()
        if render_pool is not None:
            render_pool.shutdown(wait=True, cancel_futures=True)
            render_pool = None
        report["status"] = 'stopped' if isinstance(exc, ConversionCancelled) else 'failed'
        report['stage'] = report['status']
        report["issues"].append({"code": "conversion_failed", "detail": str(exc) or type(exc).__name__})
        raise
    finally:
        render_abort.set()
        if render_pool is not None:
            render_pool.shutdown(wait=True, cancel_futures=True)
            render_pool = None
        record_stage()
        report["elapsed_seconds"] = round(time.monotonic() - start, 2)
        write_json(root / "report.json", report)
    return root, report


def assemble(root, selected, report):
    from mineru.parser import ParseResult
    sections, all_pages = [], []
    for item in report["chunks"]:
        check_cancelled()
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
