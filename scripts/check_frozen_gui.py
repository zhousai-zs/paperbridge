"""Launch and close the actual windowed EXE with an isolated user state."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    target = args.output.resolve()
    target.mkdir(parents=True, exist_ok=True)
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'PAPERBRIDGE', 'MINERU'))}
    env['PAPERBRIDGE_STATE'] = str(target / 'user-state')
    env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    process = subprocess.Popen([str(args.exe.resolve())], cwd=target, env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    found = []
    @callback_type
    def callback(hwnd, lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == process.pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True
    try:
        deadline = time.monotonic() + 20
        while not found and time.monotonic() < deadline and process.poll() is None:
            user32.EnumWindows(callback, 0)
            time.sleep(.2)
        assert found, f'Packaged GUI did not open; exit={process.poll()}'
        hwnd = found[0]
        time.sleep(.5)
        rectangle, point = wintypes.RECT(), wintypes.POINT()
        user32.GetClientRect(hwnd, ctypes.byref(rectangle))
        user32.ClientToScreen(hwnd, ctypes.byref(point))
        from PIL import ImageGrab
        ImageGrab.grab(bbox=(point.x, point.y, point.x + rectangle.right,
                            point.y + rectangle.bottom)).save(target / 'packaged-gui.png')
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        assert process.wait(timeout=10) == 0
        result = {'actual_exe_started': True, 'native_window': True, 'exit_code': 0,
                  'development_python_paths_removed': True}
        (target / 'checks.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result))
    finally:
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)


if __name__ == '__main__':
    main()
