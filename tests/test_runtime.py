import json
from pathlib import Path
import subprocess
import sys
import threading
import zipfile

import pytest

from paperbridge import environment
from paperbridge.bundles import effective_markdown, export_bundle
from paperbridge.core import run_worker
from paperbridge.processes import ConversionCancelled, module_command


def test_frozen_paths_are_relative_to_exe_and_state_is_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'portable' / 'PaperBridge.exe'))
    monkeypatch.setenv('PAPERBRIDGE_STATE', str(tmp_path / 'user'))
    environment.configure()
    assert environment.model_home() == tmp_path / 'portable' / 'models'
    assert Path(__import__('os').environ['MINERU_MODEL_BASE_DIR']) == environment.model_home()
    assert module_command('paperbridge.worker', 'a.pdf')[0:2] == [
        str(tmp_path / 'portable' / 'PaperBridge-CLI.exe'), '--internal-worker']
    assert not (tmp_path / 'portable').exists()


def test_cancel_stops_running_worker(tmp_path, monkeypatch):
    cancel = tmp_path / 'cancel'
    monkeypatch.setenv('PAPERBRIDGE_CANCEL_FILE', str(cancel))
    timer = threading.Timer(0.3, lambda: cancel.touch())
    timer.start()
    try:
        with (tmp_path / 'log').open('w') as log, pytest.raises(ConversionCancelled):
            run_worker([sys.executable, '-c', 'import time; time.sleep(60)'], log, 30)
    finally:
        timer.join()


def test_worker_timeout_is_bounded(tmp_path):
    with (tmp_path / 'log').open('w') as log, pytest.raises(subprocess.TimeoutExpired):
        run_worker([sys.executable, '-c', 'import time; time.sleep(60)'], log, 0.2)


def test_export_keeps_legacy_revisions_and_assets(tmp_path):
    root = tmp_path / 'result'
    (root / 'pages').mkdir(parents=True)
    (root / 'review' / 'history').mkdir(parents=True)
    (root / 'pages' / 'p.png').write_bytes(b'asset')
    (root / 'document.md').write_text('Title\n<!-- source_page: 1 -->\nOriginal\n', encoding='utf-8')
    revisions = {'pages': {'1': {'markdown': '修订 ![](pages/p.png)'}}}
    (root / 'review' / 'revisions.json').write_text(json.dumps(revisions), encoding='utf-8')
    (root / 'review' / 'history' / 'old.json').write_text('{}')
    target = tmp_path / 'reading.zip'
    export_bundle(root, target)
    with zipfile.ZipFile(target) as archive:
        assert '修订' in archive.read('document.md').decode('utf-8')
        assert 'Original' in archive.read('original-document.md').decode('utf-8')
        assert 'review/history/old.json' in archive.namelist()
        assert archive.read('pages/p.png') == b'asset'
    assert 'Original' in (root / 'document.md').read_text()


def test_export_rejects_missing_and_escaping_assets(tmp_path):
    (tmp_path / 'document.md').write_text('![](../secret.png)', encoding='utf-8')
    with pytest.raises(ValueError):
        export_bundle(tmp_path)
