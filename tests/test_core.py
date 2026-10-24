from pathlib import Path
import json
import os
import subprocess
import sys

import pytest
from reportlab.pdfgen import canvas

from paperbridge.core import select_pages, job_lock, convert, pdf_details, rebase_images
from paperbridge.quality import check_markdown, image_references


def test_page_ranges():
    assert select_pages('3,1-2,2', 5) == [0, 1, 2]
    assert select_pages('all', 3) == [0, 1, 2]
    for bad in ['0', '4-2', '6', '1-', '../1', '']:
        if bad:
            with pytest.raises(ValueError):
                select_pages(bad, 5)


def test_asset_validation(tmp_path):
    (tmp_path / 'images').mkdir()
    (tmp_path / 'images/x.png').write_bytes(b'png')
    md = '![](images/x.png) <img src="images/x.png">\n$$x^2$$'
    assert check_markdown(md, tmp_path) == []
    assert len(image_references(md)) == 1
    assert {x['code'] for x in check_markdown('![](../a.png) ![](missing.png) $$', tmp_path)} == {
        'image_outside_bundle', 'missing_image', 'unbalanced_display_math'}
    assert check_markdown('![](https://example.com/img.png)', tmp_path)[0]['code'] == 'nonlocal_image'
    assert rebase_images(md, 'chunks/0000').count('chunks/0000/images/x.png') == 2


def test_lock_blocks_concurrent_writer_and_releases(tmp_path):
    with job_lock(tmp_path):
        with pytest.raises(RuntimeError):
            with job_lock(tmp_path):
                pass
    assert not (tmp_path / '.lock').exists()


def test_lock_left_by_dead_owner_is_reclaimed(tmp_path):
    finished = subprocess.Popen([sys.executable, '-c', ''])
    finished.wait()
    (tmp_path / '.lock').write_text(str(finished.pid))
    with job_lock(tmp_path):
        assert (tmp_path / '.lock').read_text() == str(os.getpid())
    assert not (tmp_path / '.lock').exists()


@pytest.mark.parametrize('content', ['', 'not-a-pid'])
def test_unidentifiable_lock_is_kept(tmp_path, content):
    (tmp_path / '.lock').write_text(content)
    with pytest.raises(RuntimeError, match='lock needs inspection'):
        with job_lock(tmp_path):
            pass
    assert (tmp_path / '.lock').read_text() == content


def test_live_owner_keeps_lock(tmp_path):
    (tmp_path / '.lock').write_text(str(os.getpid()))
    with pytest.raises(RuntimeError, match='lock needs inspection'):
        with job_lock(tmp_path):
            pass
    assert (tmp_path / '.lock').exists()


@pytest.fixture
def pdf(tmp_path, monkeypatch):
    import importlib.metadata
    version = importlib.metadata.version
    monkeypatch.setattr('paperbridge.core.importlib.metadata.version',
                        lambda name: '4.0.12' if name == 'mineru' else version(name))
    path = tmp_path / '中文 sample.pdf'
    c = canvas.Canvas(str(path))
    c.drawString(50, 700, 'An original sentence for testing.')
    c.showPage()
    c.save()
    return path


def test_failed_parser_reports_failure_without_document(pdf, tmp_path, monkeypatch):
    monkeypatch.setattr('paperbridge.core.run_worker', lambda *a, **kw: subprocess.CompletedProcess(a, 7))
    with pytest.raises(RuntimeError, match='Parser failed'):
        convert(pdf, tmp_path / 'out')
    result = next((tmp_path / 'out').iterdir())
    assert json.loads((result / 'report.json').read_text(encoding='utf-8'))['status'] == 'failed'
    assert not (result / 'document.md').exists()
    assert not (result / '.lock').exists()


def test_timeout_records_failure(pdf, tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('worker', 1)
    monkeypatch.setattr('paperbridge.core.run_worker', timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        convert(pdf, tmp_path / 'out', timeout=1)
    result = next((tmp_path / 'out').iterdir())
    assert json.loads((result / 'report.json').read_text(encoding='utf-8'))['status'] == 'failed'


def test_encrypted_and_invalid_pdf_rejected(pdf, tmp_path):
    from pypdf import PdfWriter
    writer = PdfWriter(clone_from=pdf)
    writer.encrypt('secret')
    encrypted = tmp_path / 'encrypted.pdf'
    writer.write(encrypted)
    with pytest.raises(ValueError, match='Encrypted'):
        pdf_details(encrypted)
    permission_only = PdfWriter(clone_from=pdf)
    permission_only.encrypt(user_password='', owner_password='secret')
    restricted = tmp_path / 'restricted.pdf'
    permission_only.write(restricted)
    assert pdf_details(restricted) == 1
    invalid = tmp_path / 'bad.pdf'
    invalid.write_text('not a PDF')
    with pytest.raises(Exception):
        pdf_details(invalid)


def test_invalid_pages_create_no_job(pdf, tmp_path):
    with pytest.raises(ValueError):
        convert(pdf, tmp_path / 'out', pages='2')
    assert not (tmp_path / 'out').exists()


def test_progress_and_checkpoint_reuse(pdf, tmp_path, monkeypatch):
    from paperbridge.checkpoint import seal_chunk
    calls, events = [], []
    def worker(command, log, timeout, **kwargs):
        calls.append(command)
        root = Path(command[5])
        (root / 'middle_json.json').write_text('{"pages": [{"page_idx": 0, "blocks": []}]}')
        (root / 'structured_content.json').write_text('{}')
        (root / 'markdown.md').write_text('Example text')
        seal_chunk(root)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr('paperbridge.core.run_worker', worker)
    monkeypatch.setattr('paperbridge.core.assemble', lambda *args: None)
    monkeypatch.setattr('paperbridge.core.render_pages', lambda *args: None)
    monkeypatch.setattr('paperbridge.core.validate_bundle', lambda *args: {'passed': True, 'assets_passed': True, 'issues': []})
    target, report = convert(pdf, tmp_path / 'out', on_event=events.append)
    assert [e['stage'] for e in events] == ['preparing', 'parsing', 'parsing', 'parsing', 'assembling', 'rendering', 'validating', 'finished']
    assert events[-1]['completed_pages'] == events[-1]['total_pages'] == 1
    convert(pdf, tmp_path / 'out')
    assert len(calls) == 1
    assert not (target / '.lock').exists()


def test_cancel_during_parsing_preserves_lock_cleanup(pdf, tmp_path, monkeypatch):
    from paperbridge.processes import ConversionCancelled
    def cancelled(*args, **kwargs):
        raise ConversionCancelled('stopped')
    monkeypatch.setattr('paperbridge.core.run_worker', cancelled)
    with pytest.raises(ConversionCancelled):
        convert(pdf, tmp_path / 'out')
    target = next((tmp_path / 'out').iterdir())
    assert json.loads((target / 'report.json').read_text(encoding='utf-8'))['status'] == 'stopped'
    assert not (target / '.lock').exists()


def test_page_rendering_overlaps_with_parsing(pdf, tmp_path, monkeypatch):
    import threading
    from paperbridge.checkpoint import seal_chunk
    render_started = threading.Event()
    worker_seen_render = threading.Event()

    def slow_render(source, pages, output):
        render_started.set()
        assert worker_seen_render.wait(timeout=5)

    def worker(command, log, timeout, **kwargs):
        assert render_started.wait(timeout=5)
        worker_seen_render.set()
        root = Path(command[5])
        (root / 'middle_json.json').write_text('{"pages": [{"page_idx": 0, "blocks": []}]}')
        (root / 'structured_content.json').write_text('{}')
        (root / 'markdown.md').write_text('Example text')
        seal_chunk(root)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr('paperbridge.core.render_pages', slow_render)
    monkeypatch.setattr('paperbridge.core.run_worker', worker)
    monkeypatch.setattr('paperbridge.core.assemble', lambda *args: None)
    monkeypatch.setattr('paperbridge.core.validate_bundle', lambda *args: {'passed': True, 'assets_passed': True, 'issues': []})
    target, report = convert(pdf, tmp_path / 'out')
    assert report['status'] == 'complete'
    assert not (target / '.lock').exists()


def test_parallel_render_pages_and_save_evidence(tmp_path):
    from paperbridge.core import render_pages, save_evidence
    from paperbridge.integrity import check_images
    source = tmp_path / 'multi.pdf'
    c = canvas.Canvas(str(source))
    for i in range(6):
        c.drawString(50, 700, f'Page {i + 1}')
        c.showPage()
    c.save()
    pages_dir = tmp_path / 'pages'
    render_pages(source, list(range(6)), pages_dir)
    expected = [f'page-{i + 1:04}.png' for i in range(6)]
    assert check_images(expected, pages_dir) == []
    # Re-running when all pages are valid skips re-rendering and keeps files intact.
    render_pages(source, list(range(6)), pages_dir)
    assert check_images(expected, pages_dir) == []
    pages_meta = [
        {'page_idx': i, 'blocks': [
            {'type': 'equation', 'index': 1, 'bbox': [0.1, 0.1, 0.5, 0.2], 'content': f'x_{i}'},
            {'type': 'table', 'index': 2, 'bbox': [0.1, 0.3, 0.8, 0.6], 'content': f't_{i}'},
        ]}
        for i in range(4)
    ]
    evidence = save_evidence(source, pages_meta, tmp_path)
    assert len(evidence) == 8
    assert check_images([item['image'] for item in evidence], tmp_path) == []


@pytest.mark.parametrize('error_type', [OSError, ValueError, RuntimeError])
def test_background_render_failure_interrupts_parser_without_retry(pdf, tmp_path, monkeypatch, error_type):
    import threading
    import time
    started = threading.Event()
    calls = []

    def render(*args):
        assert started.wait(5)
        raise error_type('page render failed')

    def worker(command, log, timeout, on_update, **kwargs):
        calls.append(command)
        started.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            on_update({'worker_step': 'inference'})
            time.sleep(.01)
        pytest.fail('Background render failure did not interrupt parsing')

    assembled = []
    monkeypatch.setattr('paperbridge.core.render_pages', render)
    monkeypatch.setattr('paperbridge.core.run_worker', worker)
    monkeypatch.setattr('paperbridge.core.assemble', lambda *args: assembled.append(True))
    with pytest.raises(error_type, match='page render failed'):
        convert(pdf, tmp_path / 'out')
    assert len(calls) == 1
    assert not assembled
    target = next((tmp_path / 'out').iterdir())
    report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
    assert report['status'] == 'failed'
    assert 'page render failed' in report['issues'][-1]['detail']
    assert not (target / '.lock').exists()


@pytest.mark.parametrize('cancelled', [False, True])
def test_parser_failure_or_cancel_joins_background_renderer(pdf, tmp_path, monkeypatch, cancelled):
    import threading
    import time
    from paperbridge.core import _check_render_cancelled
    from paperbridge.processes import ConversionCancelled
    started, finished = threading.Event(), threading.Event()

    def render(*args):
        started.set()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                _check_render_cancelled()
                time.sleep(.01)
            pytest.fail('Background renderer was not stopped')
        finally:
            finished.set()

    error_type = ConversionCancelled if cancelled else RuntimeError

    def worker(*args, **kwargs):
        assert started.wait(5)
        raise error_type('parser stopped')

    monkeypatch.setattr('paperbridge.core.render_pages', render)
    monkeypatch.setattr('paperbridge.core.run_worker', worker)
    with pytest.raises(error_type, match='parser stopped'):
        convert(pdf, tmp_path / 'out', retries=0)
    assert finished.is_set()
    target = next((tmp_path / 'out').iterdir())
    report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
    assert report['status'] == ('stopped' if cancelled else 'failed')
    assert not (target / '.lock').exists()


@pytest.mark.parametrize('evidence', [False, True])
def test_image_save_failure_closes_images(pdf, tmp_path, monkeypatch, evidence):
    from PIL import Image
    from paperbridge.core import render_pages, save_evidence
    images = []

    def fail_save(image, *args, **kwargs):
        images.append(image)
        raise OSError('image save failed')

    monkeypatch.setattr(Image.Image, 'save', fail_save)
    with pytest.raises(OSError, match='image save failed'):
        if evidence:
            save_evidence(pdf, [{'page_idx': 0, 'blocks': [
                {'type': 'equation', 'bbox': [.1, .1, .5, .2], 'content': 'x'},
            ]}], tmp_path)
        else:
            render_pages(pdf, [0], tmp_path / 'pages')
    assert images
    for image in images:
        with pytest.raises(ValueError, match='closed image'):
            image.getpixel((0, 0))
