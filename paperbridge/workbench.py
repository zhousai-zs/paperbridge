"""Loopback-only workbench; inference runs in a separate, sequential process."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import queue
import secrets
import shutil
import subprocess
import sys
import threading
from urllib.parse import quote
import uuid
import webbrowser

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import __version__
from .bundles import export_bundle, page_sections, read_revisions, revision_version, save_revision
from .core import ROOT, write_json
from .preview import render_markdown


STATIC = Path(__file__).with_name('web')
MAX_UPLOAD = 200 * 1024 * 1024


def friendly_error(message):
    if 'Encrypted PDF' in message:
        return 'PDF 已加密，请先提供未加密的副本。'
    if 'outside 1-' in message or 'Invalid page range' in message:
        return '页码超出范围或格式有误。请使用 1-3,5 这样的格式。'
    if 'lock needs inspection' in message:
        return '该结果正在转换，或上次异常退出留下了锁文件。请先确认没有其他转换进程，再检查结果目录中的 .lock。'
    if 'timed out' in message or 'TimeoutExpired' in message:
        return '当前分段转换超时。重试会复用已完成的检查点；也可用命令行调整超时。'
    if 'model' in message.lower() and any(x in message.lower() for x in ('not found', 'missing', 'local', 'download')):
        return '本地模型尚未准备好，请运行 setup.ps1 安装或补齐模型后重试。'
    return '转换未完成。请查看下方运行记录及结果目录中的 chunks/*/engine.log；重试会复用完整的检查点。'


class TaskQueue:
    def __init__(self, output, state, start_worker=True):
        self.output = Path(output).resolve()
        self.state = Path(state).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.revision_lock = threading.Lock()
        self.pending = queue.Queue()
        self.stopping = threading.Event()
        self.process = None
        self.jobs = []
        state_file = self.state / 'tasks.json'
        if state_file.exists():
            self.jobs = json.loads(state_file.read_text(encoding='utf-8'))
        for job in self.jobs:
            if job['status'] == 'running':
                job.update(status='failed', error='上次工作台已退出。点击重试以恢复已完成的检查点。')
            if job['status'] == 'queued':
                self.pending.put(job['id'])
        self.persist()
        if start_worker:
            threading.Thread(target=self.run, daemon=True, name='paperbridge-queue').start()

    def persist(self):
        write_json(self.state / 'tasks.json', self.jobs)

    def add(self, source, filename, tier, pages):
        with self.lock:
            job = {'id': uuid.uuid4().hex, 'source': str(source), 'filename': filename,
                   'tier': tier, 'pages': pages, 'status': 'queued', 'stage': 'queued',
                   'created_at': datetime.now(timezone.utc).isoformat(), 'logs': [],
                   'completed_pages': 0, 'total_pages': 0}
            self.jobs.append(job)
            self.persist()
            self.pending.put(job['id'])
            return dict(job)

    def retry(self, job_id):
        with self.lock:
            job = next((j for j in self.jobs if j['id'] == job_id), None)
            if not job:
                raise HTTPException(404, '任务不存在。')
            if job['status'] != 'failed':
                raise HTTPException(409, '仅失败的任务可以重试。')
            if not Path(job['source']).is_file():
                raise HTTPException(409, '原始上传文件已被清理，请重新添加 PDF。')
            job.update(status='queued', stage='queued', logs=[], error='', completed_pages=0)
            self.persist()
            self.pending.put(job_id)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.jobs))

    def run(self):
        while not self.stopping.is_set():
            try:
                job_id = self.pending.get(timeout=0.3)
            except queue.Empty:
                continue
            with self.lock:
                job = next(j for j in self.jobs if j['id'] == job_id)
                job.update(status='running', stage='preparing', error='')
                job.pop('owner_pid', None)
                self.persist()
            try:
                command = [sys.executable, '-u', '-m', 'paperbridge.cli', 'convert', job['source'],
                           '-o', str(self.output), '--tier', job['tier'], '--pages', job['pages'],
                           '--offline', '--progress-json']
                env = {**os.environ, 'PYTHONUTF8': '1', 'MINERU_MODEL_SOURCE': 'local'}
                options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
                with self.lock:
                    if self.stopping.is_set():
                        raise RuntimeError('工作台已退出，任务可重试。')
                    self.process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                                    errors='replace', **options)
                    process = self.process
                for line in process.stdout:
                    line = line.strip()
                    with self.lock:
                        if line.startswith('PAPERBRIDGE_EVENT '):
                            event = json.loads(line[len('PAPERBRIDGE_EVENT '):])
                            # Only trusted conversion events can update task state.
                            for key in ('stage', 'output', 'owner_pid', 'total_pages', 'completed_pages', 'current_pages', 'chunk', 'total_chunks'):
                                if key in event:
                                    job[key] = event[key]
                        elif line:
                            job['logs'] = (job['logs'] + [line])[-30:]
                        self.persist()
                code = process.wait()
                with self.lock:
                    job['status'] = {0: 'complete', 2: 'needs_review'}.get(code, 'failed')
                    if code not in (0, 2):
                        job['error'] = ('工作台已停止。点击重试以恢复已完成的检查点。' if self.stopping.is_set()
                                        else friendly_error('\n'.join(job['logs'])))
                    job['stage'] = 'finished' if code in (0, 2) else 'failed'
                    job['finished_at'] = datetime.now(timezone.utc).isoformat()
                    self.persist()
            except Exception as exc:
                with self.lock:
                    job.update(status='failed', stage='failed', error=friendly_error(str(exc)))
                    job['logs'] = (job['logs'] + [str(exc)])[-30:]
                    self.persist()
            finally:
                with self.lock:
                    self.process = None
                self.pending.task_done()

    def close(self):
        self.stopping.set()
        with self.lock:
            process = self.process
            if process and process.poll() is None:
                job = next((j for j in self.jobs if j['status'] == 'running'), None)
                owner = job.get('owner_pid', process.pid) if job else process.pid
                if os.name == 'nt':
                    result = subprocess.run(['taskkill', '/PID', str(owner), '/T', '/F'],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                    tree_stopped = result.returncode == 0
                else:
                    import signal
                    try:
                        # The CLI handles KeyboardInterrupt and stops its isolated inference worker.
                        os.killpg(process.pid, signal.SIGINT)
                        tree_stopped = False
                    except ProcessLookupError:
                        tree_stopped = False
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    tree_stopped = False
                if tree_stopped:
                    # Only release a lock written by this exact owned CLI process after its tree stopped.
                    for job in self.jobs:
                        if job['status'] == 'running' and job.get('output'):
                            root = Path(job['output']).resolve()
                            path = root / '.lock'
                            if root.is_relative_to(self.output) and path.is_file():
                                if path.read_text(encoding='utf-8').strip() == str(owner):
                                    path.unlink(missing_ok=True)
                if job:
                    job.update(status='failed', stage='failed', error='工作台已停止。点击重试以恢复已完成的检查点。')
                    self.persist()


def documents(output):
    found = {}
    for report_file in output.rglob('report.json'):
        root = report_file.parent.resolve()
        if not root.is_relative_to(output) or not (root / 'document.md').is_file():
            continue
        try:
            report = json.loads(report_file.read_text(encoding='utf-8'))
            if report.get('status') not in {'complete', 'needs_review'}:
                continue
            relative = root.relative_to(output).as_posix()
            key = hashlib.sha256(relative.encode()).hexdigest()[:20]
            found[key] = {'id': key, 'name': report.get('source_name', root.name),
                          'status': report['status'], 'pages': report.get('selected_pages', []),
                          'tier': report.get('config', {}).get('tier', ''),
                          'issues': report.get('issues', []), 'path': str(root),
                          'modified': report_file.stat().st_mtime,
                          'revision_count': len(read_revisions(root)['pages'])}
        except (OSError, ValueError, KeyError):
            continue
    return found


def create_app(output=None, state=None, start_worker=True):
    output = Path(output or ROOT / 'outputs').resolve()
    output_key = hashlib.sha256(str(output).encode()).hexdigest()[:12]
    tasks = TaskQueue(output, state or ROOT / '.cache' / 'workbench' / output_key, start_worker)
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        yield
        tasks.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.tasks = tasks
    app.state.token = token

    @app.middleware('http')
    async def local_access(request: Request, call_next):
        host = request.headers.get('host', '')
        hostname = host.split(':')[0]
        if hostname not in {'127.0.0.1', 'localhost'}:
            return JSONResponse({'detail': '工作台仅允许本机访问。'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != f'http://{host}':
            return JSONResponse({'detail': '请求来源不匹配。'}, status_code=403)
        if request.url.path.startswith('/api/'):
            if not secrets.compare_digest(request.headers.get('x-paperbridge-token', ''), token):
                return JSONResponse({'detail': '工作台会话已更新，请刷新页面。'}, status_code=403)
            if request.method == 'POST':
                length = request.headers.get('content-length', '')
                limit = MAX_UPLOAD + 1024 * 1024 if request.url.path == '/api/tasks' else 4 * 1024 * 1024
                if not length.isdigit() or int(length) > limit:
                    return JSONResponse({'detail': '请求内容过大或缺少长度。'}, status_code=413)
        if request.url.path.startswith('/files/'):
            if not secrets.compare_digest(request.cookies.get('paperbridge-session', ''), token):
                return JSONResponse({'detail': '请先打开工作台。'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self'; font-src 'self'; connect-src 'self'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    def document(doc_id):
        item = documents(output).get(doc_id)
        if not item:
            raise HTTPException(404, '结果不存在或转换尚未完成。')
        return Path(item['path']), item

    @app.get('/', response_class=HTMLResponse)
    def index():
        html = (STATIC / 'index.html').read_text(encoding='utf-8').replace('__API_TOKEN__', token)
        response = HTMLResponse(html)
        response.set_cookie('paperbridge-session', token, httponly=True, samesite='strict')
        return response

    @app.get('/api/state')
    def get_state():
        docs = sorted(documents(output).values(), key=lambda d: d['modified'], reverse=True)
        jobs = tasks.snapshot()
        for job in jobs:
            job['document_id'] = next((d['id'] for d in docs if d['path'] == job.get('output')), None)
            job.pop('source', None)
        return {'version': __version__, 'tasks': jobs, 'documents': docs, 'output': str(output),
                'free_gb': round(shutil.disk_usage(output).free / 1024**3, 1)}

    @app.post('/api/tasks')
    def upload(tier: str = 'standard', pages: str = 'all', file: UploadFile = File(...)):
        if tier not in {'basic', 'standard'}:
            raise HTTPException(400, '请选择标准模式或快速模式。')
        import re
        pages = pages.strip() or 'all'
        if pages != 'all' and (len(pages) > 300 or not re.fullmatch(r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*', pages)):
            raise HTTPException(400, '页码格式有误，请使用 1-3,5。')
        name = (file.filename or 'paper.pdf').replace('\\', '/').rsplit('/', 1)[-1]
        if not name.lower().endswith('.pdf') or len(name) > 180:
            raise HTTPException(400, '请添加 PDF 文件，文件名应少于 180 个字符。')
        import re
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')
        if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
            name = '_' + name
        directory = tasks.state / 'uploads' / uuid.uuid4().hex
        directory.mkdir(parents=True)
        source = directory / name
        size = 0
        try:
            with source.open('wb') as destination:
                while block := file.file.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, '单份 PDF 最大支持 200 MB。')
                    destination.write(block)
            with source.open('rb') as uploaded:
                if b'%PDF-' not in uploaded.read(1024):
                    raise HTTPException(400, '文件不是有效的 PDF。')
            from .core import pdf_details, select_pages
            try:
                select_pages(pages, pdf_details(source))
            except Exception as exc:
                raise HTTPException(400, friendly_error(str(exc)) if 'Encrypted' in str(exc) or 'range' in str(exc)
                                    else 'PDF 无法读取，请检查文件是否损坏。') from exc
            return tasks.add(source, name, tier, pages)
        except Exception:
            source.unlink(missing_ok=True)
            directory.rmdir()
            raise
        finally:
            file.file.close()

    @app.post('/api/tasks/{job_id}/retry')
    def retry(job_id: str):
        tasks.retry(job_id)
        return {'ok': True}

    @app.get('/api/documents/{doc_id}/pages/{page}')
    def get_page(doc_id: str, page: int):
        root, item = document(doc_id)
        if page not in item['pages']:
            raise HTTPException(404, '页面不存在。')
        original = page_sections((root / 'document.md').read_text(encoding='utf-8')).get(page, '')
        revision = read_revisions(root)['pages'].get(str(page))
        text = revision['markdown'] if revision else original
        base = f'/files/{doc_id}/'
        evidence_file = root / 'evidence.json'
        evidence = json.loads(evidence_file.read_text(encoding='utf-8')) if evidence_file.exists() else []
        return {'page': page, 'markdown': text, 'html': render_markdown(text, base),
                'original': original, 'revised': bool(revision), 'version': revision_version(root),
                'revision_stale': bool(revision and revision.get('original_sha256') != hashlib.sha256(original.encode()).hexdigest()),
                'source': base + f'pages/page-{page:04}.png',
                'evidence': [{**e, 'url': base + quote(e['image'])} for e in evidence if e['page'] == page],
                'issues': [i for i in item['issues'] if i.get('page') in (page, None)]}

    @app.post('/api/documents/{doc_id}/pages/{page}')
    async def put_page(doc_id: str, page: int, request: Request):
        root, _ = document(doc_id)
        if int(request.headers.get('content-length', 0)) > 4 * 1024 * 1024:
            raise HTTPException(413, '修订内容过大。')
        data = await request.json()
        if not isinstance(data.get('markdown'), str) or len(data['markdown']) > 1_000_000:
            raise HTTPException(400, '修订内容无效或过长。')
        with tasks.revision_lock:
            try:
                version = save_revision(root, page, data['markdown'], data.get('version', ''))
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
        return {'ok': True, 'version': version}

    @app.post('/api/preview')
    async def preview(request: Request):
        data = await request.json()
        text = data.get('markdown', '')
        if not isinstance(text, str) or len(text) > 1_000_000:
            raise HTTPException(400, '预览内容无效或过长。')
        document(data.get('document_id', ''))
        return {'html': render_markdown(text, f'/files/{data["document_id"]}/')}

    @app.get('/api/documents/{doc_id}/export')
    def export(doc_id: str):
        root, _ = document(doc_id)
        import tempfile
        temporary = tempfile.TemporaryDirectory(prefix='export-', dir=tasks.state)
        try:
            with tasks.revision_lock:
                archive = export_bundle(root, Path(temporary.name) / 'reading.zip')
        except ValueError as exc:
            temporary.cleanup()
            raise HTTPException(409, str(exc)) from exc
        except Exception:
            temporary.cleanup()
            raise
        return FileResponse(archive, media_type='application/zip', filename=root.name + '-agent.zip',
                            background=BackgroundTask(temporary.cleanup))

    @app.post('/api/documents/{doc_id}/open')
    def open_folder(doc_id: str):
        root, _ = document(doc_id)
        if os.name == 'nt':
            os.startfile(root)
        else:
            raise HTTPException(400, '请使用结果路径在文件管理器中打开。')
        return {'ok': True}

    @app.get('/files/{doc_id}/{relative:path}')
    def file(doc_id: str, relative: str):
        root, _ = document(doc_id)
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, '文件不存在。')
        if path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.pdf', '.md'}:
            raise HTTPException(403, '不支持预览此文件。')
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')

    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app


def serve(output, port=8765, open_browser=True):
    import socket
    import uvicorn
    if not 1 <= port <= 65535:
        print('端口应在 1–65535 之间。', file=sys.stderr)
        return 1
    # Bind before opening a browser: an occupied port must never open another service.
    sock = socket.socket()
    try:
        sock.bind(('127.0.0.1', port))
        sock.listen(128)
    except OSError:
        sock.close()
        print(f'端口 {port} 已被占用。请关闭已有工作台，或使用 ui --port {port + 1}。', file=sys.stderr)
        return 1
    app = create_app(output)
    url = f'http://127.0.0.1:{port}'
    print(f'PaperBridge {__version__} · 本地工作台 {url}\n关闭此窗口将停止工作台；已完成的检查点会保留。', flush=True)
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning')).run(sockets=[sock])
    finally:
        sock.close()
        app.state.tasks.close()
    return 0
