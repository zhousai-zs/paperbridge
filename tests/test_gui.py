import queue
import sys
import tkinter as tk

import pytest

from paperbridge.gui import DesktopApp


class Runner:
    def __init__(self):
        self.events = queue.Queue()
        self.started = None
        self.stopped = False

    def start(self, files, output, options):
        self.started = (files, output, options)

    def stop(self):
        self.stopped = True


@pytest.fixture(scope='module')
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        if sys.platform == 'win32':
            raise
        pytest.skip(f'A display is required for native Tk checks; run with xvfb-run on Linux: {exc}')
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(tmp_path, monkeypatch, tk_root):
    monkeypatch.setenv('PAPERBRIDGE_STATE', str(tmp_path / 'state'))
    window = tk.Toplevel(tk_root)
    window.withdraw()
    instance = DesktopApp(window, tmp_path / '输出', runner=Runner())
    yield instance
    try:
        for timer in tk_root.tk.splitlist(tk_root.tk.call('after', 'info')):
            tk_root.after_cancel(timer)
        window.destroy()
    except tk.TclError:
        pass


def test_minimal_defaults_selection_and_advanced_options(app, tmp_path):
    assert not app.advanced_open
    assert app.read_options().tier == 'standard'
    app.add_files([tmp_path / '甲.pdf', tmp_path / '乙.pdf', tmp_path / '甲.pdf'])
    assert len(app.tree.get_children()) == 2
    app.toggle_advanced()
    app.pages.set('1-3,5')
    assert app.read_options().pages == '1-3,5'
    app.tree.selection_set('0')
    app.remove_files()
    assert app.files == [tmp_path / '乙.pdf']


def test_start_progress_failure_retry_and_stop(app, tmp_path):
    source = tmp_path / 'paper.pdf'
    app.add_files([source])
    app.start_or_stop()
    assert app.busy and app.runner.started
    assert str(app.choose['state']) == 'disabled'
    app.handle_event({'kind': 'started', 'index': 1, 'total': 1, 'source': str(source)})
    app.handle_event({'kind': 'progress', 'stage': 'parsing', 'total_pages': 3, 'completed_pages': 1})
    assert 33 < app.progress['value'] < 34
    app.handle_event({'kind': 'progress', 'stage': 'rendering'})
    assert str(app.progress['mode']) == 'indeterminate'
    app.handle_event({'kind': 'result', 'source': str(source), 'status': 'failed', 'error': '错误'})
    app.handle_event({'kind': 'done', 'stopped': False})
    assert not app.busy and '失败 1' in app.status.get()
    app.retry_failed()
    assert app.runner.started[0] == [source]
    app.start_or_stop()
    assert app.runner.stopped


def test_close_can_be_declined_then_stops_before_exiting(app, tmp_path, monkeypatch):
    app.add_files([tmp_path / 'a.pdf'])
    app.start_or_stop()
    monkeypatch.setattr('paperbridge.gui.messagebox.askyesno', lambda *a, **k: False)
    app.close()
    assert not app.runner.stopped
    monkeypatch.setattr('paperbridge.gui.messagebox.askyesno', lambda *a, **k: True)
    app.close()
    assert app.runner.stopped and app.closing


def test_batch_level_error_stays_visible_after_done(app):
    app.handle_event({'kind': 'error', 'error': '输出磁盘已不可用'})
    app.handle_event({'kind': 'done', 'stopped': False})
    assert '运行失败' in app.status.get()
    assert '输出磁盘' in app.phase.get()
    assert app.progress['value'] == 0
