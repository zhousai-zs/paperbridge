"""Source/frozen process entry points and cooperative cancellation."""
import os
from pathlib import Path
import signal
import subprocess
import sys

from .environment import frozen


class ConversionCancelled(Exception):
    pass


def check_cancelled():
    path = os.environ.get('PAPERBRIDGE_CANCEL_FILE')
    if path and Path(path).exists():
        raise ConversionCancelled('用户已停止转换。已完成的检查点可在下次复用。')


def module_command(module, *args):
    if frozen():
        cli = Path(sys.executable).with_name('PaperBridge-CLI.exe')
        prefix = [str(cli), '--internal-worker'] if module == 'paperbridge.worker' else [str(cli)]
    else:
        python = Path(sys.executable)
        if python.name.lower() == 'pythonw.exe':
            python = python.with_name('python.exe')
        prefix = [str(python), '-u', '-m', module]
    return [*prefix, *map(str, args)]


def hidden_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}


def terminate_tree(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        result = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **hidden_options(), check=False)
        if result.returncode and process.poll() is None:
            # Restricted Windows tokens may not enumerate the tree. A venv
            # launcher has a different PID from the Python process it hosts.
            try:
                os.kill(getattr(process, '_paperbridge_pid', process.pid), signal.SIGTERM)
            except OSError:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
