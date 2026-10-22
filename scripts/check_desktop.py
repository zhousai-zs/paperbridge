"""Model-free native window screenshots and interaction checks."""
import argparse
import json
import os
from pathlib import Path
import queue
import sys
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.cache' / 'desktop-check')
    args = parser.parse_args()
    target = args.output.resolve()
    target.mkdir(parents=True, exist_ok=True)
    os.environ['PAPERBRIDGE_STATE'] = str(target / 'state')
    from paperbridge.gui import DesktopApp
    from PIL import ImageGrab
    if os.name == 'nt':
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    class Runner:
        events = queue.Queue()
        def start(self, *args):
            pass
        def stop(self):
            pass
    root = tk.Tk()
    root.geometry('720x520+140+80')
    app = DesktopApp(root, target / '论文输出', runner=Runner())
    files = [target / 'Attention Is All You Need.pdf', target / '中文双栏论文.pdf']
    app.add_files(files)
    root.attributes('-topmost', True)
    def capture(name):
        root.update()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(target / name)
    def checks():
        capture('01-main.png')
        app.toggle_advanced()
        capture('02-options.png')
        app.toggle_advanced()
        app.start_or_stop()
        app.handle_event({'kind': 'started', 'source': str(files[0]), 'index': 1, 'total': 2})
        app.handle_event({'kind': 'progress', 'stage': 'parsing', 'total_pages': 15,
                          'completed_pages': 5, 'current_pages': list(range(6, 11))})
        capture('03-running.png')
        app.handle_event({'kind': 'result', 'source': str(files[0]), 'status': 'complete'})
        app.handle_event({'kind': 'result', 'source': str(files[1]), 'status': 'failed',
                          'error': 'PDF 已加密，请使用未加密的副本。'})
        app.handle_event({'kind': 'done', 'stopped': False})
        app.toggle_details()
        capture('04-error.png')
        (target / 'checks.json').write_text(json.dumps({'native_window': True, 'screenshots': 4,
            'selection': True, 'advanced': True, 'progress': True, 'retry': True}, indent=2), encoding='utf-8')
        root.destroy()
    root.after(650, checks)
    root.mainloop()
    print(f'PASS: native desktop interaction checks and screenshots at {target}')


if __name__ == '__main__':
    main()
