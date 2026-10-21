"""Exercise the actual cmd.exe entry point, including startup from another directory."""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def check():
    if os.name != 'nt':
        print('SKIP: cmd.exe launcher is Windows-only')
        return
    launcher = ROOT / '论文工作台.cmd'
    for path in ROOT.glob('*.cmd'):
        raw = path.read_bytes()
        assert b'\n' in raw and b'\n' not in raw.replace(b'\r\n', b''), f'Non-Windows line endings: {path}'
        raw.decode('ascii')  # Batch syntax must not depend on the active Windows code page.
    python = ROOT / '.venv' / 'Scripts' / 'python.exe'
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', '--system-site-packages',
                        str(ROOT / '.venv')], check=True)
    with tempfile.TemporaryDirectory(prefix='launcher-', dir=ROOT / '.cache') as temporary:
        temporary = Path(temporary)
        # Pass a raw Windows command line: list2cmdline would escape cmd's quotes as literal backslashes.
        comspec = os.environ.get('COMSPEC', 'cmd.exe')
        command = f'"{comspec}" /d /s /c ""{launcher}" --help"'
        result = subprocess.run(command,
                                cwd=temporary, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=15)
        help_text = result.stdout.decode('utf-8', errors='replace')
        assert result.returncode == 0 and '--no-browser' in help_text and '--port' in help_text, help_text
        with closing(socket.socket()) as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        output = temporary / '文档 library'
        command = f'"{comspec}" /d /s /c ""{launcher}" --no-browser --port {port} -o "{output}""'
        with (temporary / 'launcher.log').open('wb') as log:
            process = subprocess.Popen(command,
                                       cwd=temporary, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            try:
                url = f'http://127.0.0.1:{port}'
                for _ in range(80):
                    assert process.poll() is None, (temporary / 'launcher.log').read_text(encoding='utf-8', errors='replace')
                    try:
                        with urlopen(url, timeout=1) as response:
                            html = response.read().decode('utf-8')
                        break
                    except (URLError, TimeoutError, ConnectionError):
                        time.sleep(.25)
                else:
                    raise AssertionError('Launcher did not start the local workbench')
                token = re.search(r'name="paperbridge-token" content="([^"]+)"', html)[1]
                request = Request(url + '/api/state', headers={'X-PaperBridge-Token': token})
                with urlopen(request, timeout=5) as response:
                    state = json.load(response)
                assert Path(state['output']) == output, state
                from paperbridge import __version__
                assert state['version'] == __version__, state
                print('PASS: actual .cmd help and HTTP startup, unrelated working directory, Chinese/spaced output path, argument forwarding')
            finally:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                process.wait(timeout=10)


if __name__ == '__main__':
    (ROOT / '.cache').mkdir(exist_ok=True)
    check()
