"""Desktop state and sequential process runner, independent of Tk."""
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

from .core import write_json
from .environment import ROOT, documents_dir, state_dir
from .processes import hidden_options, module_command, terminate_tree


@dataclass(frozen=True)
class Options:
    tier: str = 'standard'
    pages: str = 'all'
    ocr: str = 'auto'
    chunk_size: int = 20
    timeout: float = 1800

    def validate(self):
        import math
        import re
        if self.tier not in {'standard', 'basic'} or self.ocr not in {'auto', 'txt', 'ocr'}:
            raise ValueError('请选择有效的转换模式和 OCR 模式。')
        if self.chunk_size < 1 or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError('分段页数和超时必须大于 0。')
        if self.pages != 'all' and not re.fullmatch(r'\d+(?:-\d+)?(?:\s*,\s*\d+(?:-\d+)?)*', self.pages):
            raise ValueError('页码请填写 all 或 1-3,5 这样的格式。')


def unique_files(paths):
    result, seen = [], set()
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def load_output():
    fallback = documents_dir() / 'PaperBridge' / 'outputs'
    try:
        value = json.loads((state_dir() / 'settings.json').read_text(encoding='utf-8'))
        saved = value.get('output')
        if saved:
            path = Path(saved).expanduser()
            if path.is_absolute() and path.is_dir():
                return path, ''
            return fallback, '上次输出目录已失效，请重新选择。'
    except FileNotFoundError:
        pass
    except (ValueError, OSError, TypeError, AttributeError):
        return fallback, '输出目录设置无法读取，已恢复默认位置。'
    return fallback, ''


def validate_output(raw):
    if not str(raw).strip():
        raise ValueError('请选择输出文件夹。')
    path = Path(raw).expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f'.paperbridge-write-{uuid.uuid4().hex}'
        try:
            with probe.open('xb'):
                pass
        finally:
            probe.unlink(missing_ok=True)
    except OSError as exc:
        raise ValueError(f'输出目录无法写入，请重新选择：{path}') from exc
    return path


def save_output(path):
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / 'settings.json', {'output': str(path)})


def friendly_error(message):
    lower = message.lower()
    if 'encrypted' in lower:
        return 'PDF 已加密，请使用未加密的副本。'
    if any(s in lower for s in ('invalid page', 'outside 1-', 'page range')):
        return '页码格式有误或超出该 PDF 的范围。'
    if 'timed out' in lower or 'timeoutexpired' in lower:
        return '当前分段超时，可增加超时后重试；已完成的检查点会复用。'
    if 'lock needs inspection' in lower or 'job already running' in lower:
        return '该结果正在处理或有异常退出留下的锁；请查看错误详情。'
    if ('model' in lower or '模型' in lower) and any(s in lower for s in ('missing', 'not ready', 'not found')):
        return '本地模型不完整，请重新解压完整便携包后重试。'
    if 'not a pdf' in lower or 'invalid pdf' in lower or 'stream has ended' in lower:
        return '文件不存在、已损坏或不是有效的 PDF。'
    return '转换未完成，可查看错误详情后重试。'


class BatchRunner:
    def __init__(self):
        self.events = queue.Queue()
        self.thread = None
        self.process = None
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.results = []
        self.cancel_file = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, files, output, options):
        if self.running:
            raise RuntimeError('转换仍在运行。')
        options.validate()
        self.cancelled.clear()
        self.results = []
        directory = state_dir() / 'tmp'
        directory.mkdir(parents=True, exist_ok=True)
        self.cancel_file = directory / f'cancel-{uuid.uuid4().hex}'
        self.thread = threading.Thread(target=self._run, args=(list(files), Path(output), options), daemon=True)
        self.thread.start()

    def stop(self):
        self.cancelled.set()
        if self.cancel_file:
            self.cancel_file.touch()
        with self.lock:
            process = self.process
        if process is not None:
            threading.Thread(target=self._stop_after_grace, args=(process,), daemon=True).start()

    def _stop_after_grace(self, process):
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            terminate_tree(process)

    def _run(self, files, output, options):
        try:
            logs = state_dir() / 'logs'
            logs.mkdir(parents=True, exist_ok=True)
            for index, source in enumerate(files, 1):
                if self.cancelled.is_set():
                    break
                result = {'source': str(source), 'status': 'failed', 'output': ''}
                log_path = logs / f'{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.log'
                result['log'] = str(log_path)
                self.events.put({'kind': 'started', 'index': index, 'total': len(files), 'source': str(source)})
                command = module_command('paperbridge.cli', 'convert', source, '-o', output,
                                         '--offline', '--progress-json', '--tier', options.tier,
                                         '--pages', options.pages, '--ocr', options.ocr,
                                         '--chunk-size', options.chunk_size, '--timeout', options.timeout)
                process, tail = None, []
                try:
                    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
                           'MINERU_MODEL_SOURCE': 'local', 'PAPERBRIDGE_CANCEL_FILE': str(self.cancel_file)}
                    with self.lock:
                        if self.cancelled.is_set():
                            break
                        process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                   text=True, encoding='utf-8', errors='replace', **hidden_options())
                        self.process = process
                    with log_path.open('w', encoding='utf-8') as log:
                        for line in process.stdout:
                            log.write(line)
                            log.flush()
                            if line.startswith('PAPERBRIDGE_EVENT '):
                                try:
                                    event = json.loads(line[len('PAPERBRIDGE_EVENT '):])
                                    if event.get('output'):
                                        result['output'] = event['output']
                                    self.events.put({'kind': 'progress', **event})
                                except (ValueError, TypeError, AttributeError):
                                    tail.append(line.strip())
                            elif line.strip():
                                tail = (tail + [line.strip()])[-30:]
                    code = process.wait()
                    result['status'] = {0: 'complete', 2: 'needs_review', 130: 'stopped'}.get(code, 'failed')
                    if self.cancelled.is_set() and code not in (0, 2):
                        result['status'] = 'stopped'
                    if result['status'] == 'failed':
                        result['error'] = friendly_error('\n'.join(tail))
                        result['details'] = '\n'.join(tail)
                except Exception as exc:
                    result.update(error=friendly_error(str(exc)), details=str(exc))
                finally:
                    if process:
                        terminate_tree(process)
                        process.stdout.close()
                        # Only reclaim a lock from the terminated process we own.
                        target = Path(result['output']).resolve() if result['output'] else None
                        if target and target.is_relative_to(output.resolve()):
                            lock_file = target / '.lock'
                            if lock_file.is_file() and lock_file.read_text(encoding='utf-8').strip() == str(process.pid):
                                lock_file.unlink()
                                report_file = target / 'report.json'
                                if report_file.exists():
                                    report = json.loads(report_file.read_text(encoding='utf-8'))
                                    report.update(status=result['status'], stage=result['status'])
                                    write_json(report_file, report)
                    with self.lock:
                        self.process = None
                self.results.append(result)
                self.events.put({'kind': 'result', **result})
            write_json(output / 'batch-report.json', self.results)
        except Exception as exc:
            self.events.put({'kind': 'error', 'error': str(exc)})
        finally:
            self.cancel_file.unlink(missing_ok=True)
            self.events.put({'kind': 'done', 'results': list(self.results), 'stopped': self.cancelled.is_set()})
