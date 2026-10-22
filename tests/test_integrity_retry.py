import json
from pathlib import Path
import subprocess

from PIL import Image
import pytest
from reportlab.pdfgen import canvas

from paperbridge.checkpoint import seal_chunk, sha256
from paperbridge.core import convert, rebase_images, render_pages
from paperbridge.integrity import validate_bundle
from paperbridge.performance import log_progress, summarize_log


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr('paperbridge.core.importlib.metadata.version', lambda name: '4.0.12')
    source = tmp_path / 'paper.pdf'
    c = canvas.Canvas(str(source))
    for _ in range(2):
        c.drawString(20, 700, 'Example'); c.showPage()
    c.save()
    def assemble(root, selected, report):
        # Simulate assembly; keep real validation, rendering, retries and hashes.
        (root / 'document.md').write_text('\n'.join(
            f'<!-- source_page: {p+1} -->\n![](chunks/{i:04}/images/x.png)'
            for i, p in enumerate(selected)))
        (root / 'document.json').write_text(json.dumps({'pages': [{'page_idx': p} for p in selected]}))
        (root / 'evidence.json').write_text('[]')
    monkeypatch.setattr('paperbridge.core.assemble', assemble)
    return source


def save_fake(command, missing=False, corrupt=False):
    chunk = Path(command[5])
    pages = [int(x)-1 for x in command[command.index('--pages')+1].split(',')]
    (chunk / 'middle_json.json').write_text(json.dumps({'pages': [
        {'page_idx': p, 'blocks': [{'image_path': 'images/x.png'}]} for p in pages]}))
    (chunk / 'markdown.md').write_text('![](images/x.png)')
    (chunk / 'structured_content.json').write_text('{}')
    if not missing:
        (chunk / 'images').mkdir(exist_ok=True)
        Image.new('RGB', (10, 10), 'red').save(chunk / 'images/x.png')
    if corrupt:
        (chunk / 'images/x.png').write_bytes(b'broken')
    if missing or corrupt:
        # Reproduce the old success marker which only listed files already on disk.
        files = {p.relative_to(chunk).as_posix(): sha256(p)
                 for p in chunk.rglob('*') if p.is_file() and p.name != 'engine.log'}
        (chunk / 'DONE').write_text(json.dumps({'files': files}))
    else:
        seal_chunk(chunk)
    return subprocess.CompletedProcess(command, 0)


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'timeout', 'exit'])
def test_retry_only_failed_chunk_then_validate_real_bundle(pipeline, tmp_path, monkeypatch, failure):
    calls, events = [], []
    def worker(command, log, timeout, **kwargs):
        page = command[command.index('--pages')+1]
        calls.append(page)
        if page == '2' and calls.count('2') == 1:
            if failure == 'timeout':
                raise subprocess.TimeoutExpired(command, timeout)
            if failure == 'exit':
                return subprocess.CompletedProcess(command, 7)
            return save_fake(command, missing=failure == 'missing', corrupt=failure == 'corrupt')
        return save_fake(command)
    monkeypatch.setattr('paperbridge.core.run_worker', worker)
    root, report = convert(pipeline, tmp_path / 'out', chunk_size=1, on_event=events.append)
    assert calls == ['1', '2', '2']
    assert report['status'] == 'complete'
    assert report['integrity']['passed']
    assert len(report['chunks'][1]['attempts']) == 2
    assert any(e['stage'] == 'retrying' for e in events)
    assert {'preparing', 'parsing', 'assembling', 'rendering', 'validating'} <= report['stage_seconds'].keys()
    convert(pipeline, tmp_path / 'out', chunk_size=1)
    assert calls == ['1', '2', '2']
    assert not (root / '.lock').exists()


def test_retry_exhaustion_and_disabled_retry(pipeline, tmp_path, monkeypatch):
    calls = []
    def worker(command, *args, **kwargs):
        calls.append(command)
        return save_fake(command, missing=True)
    monkeypatch.setattr('paperbridge.core.run_worker', worker)
    with pytest.raises(RuntimeError, match='missing_image'):
        convert(pipeline, tmp_path / 'out', chunk_size=1, retries=1)
    assert len(calls) == 2
    root = next((tmp_path / 'out').iterdir())
    report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    assert report['status'] == 'failed'
    assert len(report['chunks'][0]['attempts']) == 2
    assert not (root / '.lock').exists()
    calls.clear()
    with pytest.raises(RuntimeError):
        convert(pipeline, tmp_path / 'out2', chunk_size=1, retries=0)
    assert len(calls) == 1


def test_final_validation_and_corrupt_page_recovery(pipeline, tmp_path, monkeypatch):
    monkeypatch.setattr('paperbridge.core.run_worker', lambda cmd, *a, **kw: save_fake(cmd))
    root, report = convert(pipeline, tmp_path / 'out', chunk_size=1)
    page = root / 'pages/page-0001.png'
    page.write_bytes(b'truncated')
    assert not validate_bundle(root)['assets_passed']
    render_pages(root / 'source.pdf', [0], root / 'pages')
    assert validate_bundle(root)['passed']
    (root / 'chunks/0000/images/x.png').unlink()
    assert not validate_bundle(root)['assets_passed']


def test_log_summary_and_single_quote_html_paths():
    text = ('MFR Predict: 0%| | 0/20 [00:00<?, ?it/s]\r'
            'MFR Predict: 100%|xxx| 20/20 [00:12<00:00, 1it/s]\r'
            'MFR Predict: 100%|xxx| 20/20 [00:12<00:00, 1it/s]\n'
            'model_list infer finished, pages=1, cost=17.5s\n')
    assert summarize_log(text)['progress_seconds_approximate'] == {'formula_recognition': 12}
    assert summarize_log(text)['inference_seconds'] == 17.5
    assert log_progress(text)['step_completed'] == 20
    assert rebase_images("<img src='images/x.png'>", 'chunks/0000') == "<img src='chunks/0000/images/x.png'>"


def test_verify_command_reads_existing_results_without_rewriting(pipeline, tmp_path, monkeypatch):
    from paperbridge.cli import main
    monkeypatch.setattr('paperbridge.core.run_worker', lambda cmd, *a, **kw: save_fake(cmd))
    root, report = convert(pipeline, tmp_path / 'out', chunk_size=1)
    original = (root / 'report.json').read_bytes()
    destination = tmp_path / 'audit.json'
    assert main(['verify', str(tmp_path / 'out'), '--report', str(destination)]) == 0
    assert json.loads(destination.read_text(encoding='utf-8'))['passed']
    assert (root / 'report.json').read_bytes() == original
    (root / 'chunks/0000/images/x.png').write_bytes(b'bad image')
    assert main(['verify', str(root)]) == 1
    assert main(['verify', str(tmp_path / 'nonexistent')]) == 1


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows file sharing contention')
def test_atomic_report_write_recovers_transient_sharing_error(tmp_path, monkeypatch):
    from paperbridge.core import write_json
    original, attempts = Path.replace, []
    def replace(path, target):
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError('temporarily held by another reader')
        return original(path, target)
    monkeypatch.setattr(Path, 'replace', replace)
    target = tmp_path / 'report.json'
    write_json(target, {'status': 'complete'})
    assert json.loads(target.read_text())['status'] == 'complete'
    assert len(attempts) == 3
    assert not list(tmp_path.glob('*.tmp'))
