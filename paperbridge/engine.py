"""One lazy, sequential inference process owned by a conversion batch."""
import codecs
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from .environment import ROOT, configure
from .performance import log_progress
from .processes import check_cancelled, hidden_options, module_command, terminate_tree


def worker_update(log, on_update):
    if not on_update:
        return
    try:
        event = json.loads(Path(log.name).with_name('worker-status.json').read_text(encoding='utf-8'))
        if event.get('worker_step') == 'inference':
            with Path(log.name).open('rb') as reader:
                reader.seek(max(0, reader.seek(0, 2) - 8192))
                event.update(log_progress(reader.read().decode('utf-8', errors='replace')))
        on_update(event)
    except (OSError, ValueError):
        pass


class EngineSession:
    def __init__(self, *, reuse=True, max_jobs=32, max_rss_bytes=4 * 1024**3):
        self.reuse = reuse
        self.max_jobs = max_jobs
        self.max_rss_bytes = max_rss_bytes
        self.jobs = 0
        self.rss_bytes = 0
        self.process = None
        self.directory = None
        self.log_offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.reset()

    def reset(self):
        if self.process:
            if self.directory:
                try:
                    ready = json.loads((Path(self.directory.name) / 'ready.json').read_text(encoding='utf-8'))
                    self.process._paperbridge_pid = ready['pid']
                except (OSError, ValueError, KeyError):
                    pass
            try:
                self.process.stdin.close()
            except OSError:
                pass
            terminate_tree(self.process)
            self.process = None
        if self.directory:
            self.directory.cleanup()
            self.directory = None
        self.jobs = self.rss_bytes = 0
        self.log_offset = 0

    def run(self, command, log, timeout, on_update=None):
        from .core import write_json
        check_cancelled()
        try:
            if self.jobs >= self.max_jobs or self.rss_bytes >= self.max_rss_bytes:
                self.reset()
            started = time.monotonic()
            if self.process is None:
                cache = configure()
                self.directory = tempfile.TemporaryDirectory(prefix='engine-', dir=cache / 'tmp')
                with (Path(self.directory.name) / 'engine.log').open('ab') as stream:
                    self.process = subprocess.Popen(
                        module_command('paperbridge.worker', '--serve', self.directory.name),
                        cwd=ROOT, stdin=subprocess.PIPE, stdout=stream, stderr=subprocess.STDOUT,
                        text=True, encoding='utf-8',
                        env={**os.environ, 'PAPERBRIDGE_ENGINE_OWNER_PID': str(os.getpid())}, **hidden_options())
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')

            def copy_log(final=False):
                with (Path(self.directory.name) / 'engine.log').open('rb') as reader:
                    reader.seek(self.log_offset)
                    data = reader.read()
                    self.log_offset = reader.tell()
                log.write(decoder.decode(data, final=final))
                log.flush()
            # Establish the real worker identity before dispatch. Cancelling
            # during startup must not leave an untracked native inference job.
            ready_path = Path(self.directory.name) / 'ready.json'
            while not ready_path.exists():
                check_cancelled()
                if self.process.poll() is not None:
                    raise RuntimeError(f'Parser failed during startup ({self.process.returncode})')
                if time.monotonic() - started >= timeout:
                    raise subprocess.TimeoutExpired(command, timeout)
                time.sleep(.05)
            self.process._paperbridge_pid = json.loads(ready_path.read_text(encoding='utf-8'))['pid']
            request_id = uuid.uuid4().hex
            response_path = Path(self.directory.name) / 'response.json'
            offset = command.index('--tier')
            request = {'id': request_id, 'pdf': command[offset - 2], 'output': command[offset - 1],
                       'tier': command[offset + 1], 'pages': command[command.index('--pages') + 1],
                       'ocr': command[command.index('--ocr') + 1]}
            try:
                self.process.stdin.write(json.dumps(request) + '\n')
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise RuntimeError('Inference engine disconnected before accepting the chunk') from exc
            next_update = 0
            while True:
                check_cancelled()
                if response_path.exists():
                    response = json.loads(response_path.read_text(encoding='utf-8'))
                    if response['id'] == request_id:
                        copy_log(final=True)
                        if response.get('error'):
                            raise RuntimeError(f"Parser failed: {response['error']}. See {log.name}")
                        timing_path = Path(log.name).with_name('engine-timing.json')
                        timing = json.loads(timing_path.read_text(encoding='utf-8'))
                        timing['dispatch_seconds'] = round(time.monotonic() - started, 3)
                        self.jobs = timing['worker_job_index']
                        self.rss_bytes = timing.get('worker_rss_bytes') or 0
                        write_json(timing_path, timing)
                        return subprocess.CompletedProcess(command, 0)
                if self.process.poll() is not None:
                    copy_log(final=True)
                    raise RuntimeError(f'Parser failed ({self.process.returncode}). See {log.name}')
                if time.monotonic() - started >= timeout:
                    raise subprocess.TimeoutExpired(command, timeout)
                if time.monotonic() >= next_update:
                    next_update = time.monotonic() + 1
                    copy_log()
                    worker_update(log, on_update)
                time.sleep(0.1)
        except BaseException:
            if self.directory and 'copy_log' in locals():
                try:
                    copy_log(final=True)
                except (OSError, ValueError):
                    pass
            self.reset()
            raise
