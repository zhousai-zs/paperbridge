"""Exercise the real process/IPC boundary without loading inference models."""
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from paperbridge.engine import EngineSession
from paperbridge.processes import ConversionCancelled, module_command


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERBRIDGE_CACHE', str(tmp_path / 'cache'))
    script = tmp_path / 'fake_engine.py'
    script.write_text('''import os,time
from pathlib import Path
from paperbridge import worker
from paperbridge.core import write_json
def execute(pdf, output, **kw):
    if pdf == 'hang':
        (Path(output)/'pid').write_text(str(os.getpid()))
        (Path(output)/'entered').touch()
        time.sleep(60)
    if pdf == 'crash': os._exit(7)
    print('python log: '+pdf,flush=True)
    os.write(2, ('native log: '+pdf+'\\n').encode())
    if pdf == 'fail': raise RuntimeError('injected failure')
    write_json(Path(output)/'engine-timing.json', {'worker_pid':os.getpid(),
        'worker_job_index':kw['job_index'], 'engine_reused':kw['job_index']>1})
worker.execute=execute
raise SystemExit(worker.main(['--serve', __import__('sys').argv[1]]))
''', encoding='utf-8')
    monkeypatch.setattr('paperbridge.engine.module_command',
                        lambda module, flag, directory: [sys.executable, '-u', str(script), directory])

    def run(session, name, timeout=10, on_update=None):
        output = tmp_path / name
        output.mkdir(exist_ok=True)
        cmd = module_command('paperbridge.worker', name, output, '--tier', 'standard', '--pages', '1', '--ocr', 'auto')
        with (output / 'engine.log').open('w', encoding='utf-8') as log:
            return session.run(cmd, log, timeout, on_update)
    return run


def test_reuses_process_and_keeps_native_logs_separate(fake_engine, tmp_path):
    with EngineSession() as session:
        fake_engine(session, 'first')
        first = json.loads((tmp_path / 'first/engine-timing.json').read_text())
        fake_engine(session, 'second')
        second = json.loads((tmp_path / 'second/engine-timing.json').read_text())
        process, directory = session.process, Path(session.directory.name)
        assert first['worker_pid'] == second['worker_pid']
        assert second['worker_job_index'] == 2
        assert second['engine_reused']
    assert process.poll() is not None
    assert not directory.exists()
    assert 'native log: first' in (tmp_path / 'first/engine.log').read_text()
    assert 'second' not in (tmp_path / 'first/engine.log').read_text()
    assert 'first' not in (tmp_path / 'second/engine.log').read_text()


@pytest.mark.parametrize('failure', ['fail', 'crash', 'hang'])
def test_failed_process_is_discarded_and_next_job_recovers(fake_engine, tmp_path, failure):
    with EngineSession() as session:
        with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
            fake_engine(session, failure, timeout=.5 if failure == 'hang' else 10)
        assert session.process is None
        assert session.directory is None
        fake_engine(session, 'recovered')
        result = json.loads((tmp_path / 'recovered/engine-timing.json').read_text())
        assert result['worker_job_index'] == 1


def test_cancel_terminates_process_and_cleans_ipc(fake_engine, tmp_path, monkeypatch):
    signal = tmp_path / 'cancel'
    monkeypatch.setenv('PAPERBRIDGE_CANCEL_FILE', str(signal))
    def update(event):
        signal.touch()
    # A status file makes the real polling loop invoke the progress callback.
    output = tmp_path / 'hang'
    output.mkdir()
    (output / 'worker-status.json').write_text('{"worker_step":"loading_engine"}')
    with EngineSession() as session:
        with pytest.raises(ConversionCancelled):
            fake_engine(session, 'hang', on_update=update)
        assert session.process is None
        assert session.directory is None


@pytest.mark.parametrize('error_type', [OSError, ValueError, RuntimeError])
def test_progress_error_terminates_engine_without_status(fake_engine, error_type):
    processes = []

    with EngineSession() as session:
        def update(event):
            processes.append(session.process)
            raise error_type('background render failed')

        with pytest.raises(error_type, match='background render failed'):
            fake_engine(session, 'hang', on_update=update)
        assert processes[0].poll() is not None
        assert session.process is None
        assert session.directory is None


def test_recycles_at_completed_job_boundary(fake_engine, tmp_path):
    with EngineSession(max_jobs=1) as session:
        fake_engine(session, 'first')
        first_process = session.process
        fake_engine(session, 'second')
        assert first_process.poll() is not None
        assert session.process.pid != first_process.pid
        result = json.loads((tmp_path / 'second/engine-timing.json').read_text())
        assert result['worker_job_index'] == 1


def test_rss_limit_recycles_only_before_next_job(fake_engine, tmp_path):
    with EngineSession(max_rss_bytes=10) as session:
        fake_engine(session, 'first')
        first_process = session.process
        session.rss_bytes = 10
        fake_engine(session, 'second')
        assert first_process.poll() is not None
        assert session.process.pid != first_process.pid


def test_owner_death_stops_active_native_job(fake_engine, tmp_path):
    import os
    import paperbridge.engine as engine_module
    ipc, output = tmp_path / 'ipc', tmp_path / 'output'
    ipc.mkdir()
    output.mkdir()
    command = engine_module.module_command('paperbridge.worker', '--serve', str(ipc))
    coordinator = tmp_path / 'coordinator.py'
    coordinator.write_text("""import os,sys,json,subprocess,time,pathlib
os.environ['PAPERBRIDGE_ENGINE_OWNER_PID']=str(os.getpid())
p=subprocess.Popen(json.loads(sys.argv[1]),stdin=subprocess.PIPE,text=True,encoding='utf-8')
p.stdin.write(json.dumps({'id':'test','pdf':'hang','output':sys.argv[2],
                         'tier':'standard','pages':'1','ocr':'auto'})+'\\n')
p.stdin.flush()
time.sleep(60)
""", encoding='utf-8')
    process = subprocess.Popen([sys.executable, str(coordinator), json.dumps(command), str(output)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while not (output / 'entered').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (output / 'entered').exists()
        # The fake job records its true PID, bypassing Windows venv launchers.
        child_pid = int((output / 'pid').read_text())
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, child_pid)
            assert handle
            try:
                process.kill()
                process.wait(timeout=5)
                assert kernel.WaitForSingleObject(handle, 5000) == 0
            finally:
                kernel.CloseHandle(handle)
        else:
            process.kill()
            process.wait(timeout=5)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    state = Path(f'/proc/{child_pid}/stat').read_text().split()[2]
                    if state == 'Z':
                        break
                except FileNotFoundError:
                    break
                time.sleep(.05)
            else:
                pytest.fail('orphaned inference survived its owner')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
