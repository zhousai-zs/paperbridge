import io
import json
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from reportlab.pdfgen import canvas

from paperbridge.bundles import effective_markdown, export_bundle, page_sections, save_revision
from paperbridge.preview import render_markdown
from paperbridge.workbench import create_app


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / 'outputs' / 'paper-123'
    root.mkdir(parents=True)
    (root / 'pages').mkdir()
    (root / 'evidence').mkdir()
    (root / 'chunks' / '0000' / 'images').mkdir(parents=True)
    for relative in ('pages/page-0001.png', 'pages/page-0003.png', 'evidence/eq.png', 'chunks/0000/images/fig.png'):
        Image.new('RGB', (20, 20), 'white').save(root / relative)
    pdf = canvas.Canvas(str(root / 'source.pdf'))
    for _ in range(3):
        pdf.drawString(20, 20, 'Hello')
        pdf.showPage()
    pdf.save()
    markdown = ('# Test\n\n<!-- source_page: 1 -->\n\n# First\n\n$$\\frac{a}{b}$$\n\n'
                '![](chunks/0000/images/fig.png)\n\n<!-- source_page: 3 -->\n\n# Third\n')
    (root / 'document.md').write_text(markdown, encoding='utf-8')
    report = {'status': 'needs_review', 'source_name': '测试论文.pdf', 'selected_pages': [1, 3],
              'config': {'tier': 'standard'}, 'issues': [{'code': 'formula_image_only', 'page': 3}]}
    (root / 'report.json').write_text(json.dumps(report), encoding='utf-8')
    (root / 'evidence.json').write_text(json.dumps([{'page': 1, 'type': 'equation', 'image': 'evidence/eq.png'}]), encoding='utf-8')
    (root / 'AGENT_README.md').write_text('Read document.md', encoding='utf-8')
    return root


@pytest.fixture
def client(bundle, tmp_path):
    app = create_app(bundle.parent, tmp_path / 'state', start_worker=False)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        client.get('/')
        client.headers['X-PaperBridge-Token'] = app.state.token
        yield client


def doc_id(client):
    return client.get('/api/state').json()['documents'][0]['id']


def test_preview_sanitizes_html_and_renders_math_and_tables():
    html = render_markdown('# Heading\n\n$x^2$\n\n$$\\frac{a}{b}$$\n\n'
                           '| A | B |\n| --- | --- |\n| 1 | 2 |\n\n'
                           '<script>alert(1)</script><img src="https://evil.test/a" onerror="alert(1)">'
                           '<a href="javascript:alert(1)">bad</a>', '/files/example/')
    assert '<script' not in html and 'onerror' not in html and 'javascript:' not in html
    assert 'https://evil.test/a' not in html
    assert '<math' in html and '<mfrac>' in html and '<msup>' in html
    assert '<table>' in html
    assert '/files/example/pages/p.png' in render_markdown('![](pages/p.png)', '/files/example/')


def test_noncontiguous_page_review_and_revision_conflicts(client, bundle):
    key = doc_id(client)
    page = client.get(f'/api/documents/{key}/pages/1').json()
    assert '<math' in page['html'] and len(page['evidence']) == 1
    assert client.get(f'/api/documents/{key}/pages/2').status_code == 404
    edit = {'markdown': '# Human correction\n\n![](chunks/0000/images/fig.png)', 'version': page['version']}
    saved = client.post(f'/api/documents/{key}/pages/1', json=edit)
    assert saved.status_code == 200
    assert client.post(f'/api/documents/{key}/pages/1', json=edit).status_code == 409
    assert 'Human correction' in effective_markdown(bundle)
    assert '# First' in (bundle / 'document.md').read_text(encoding='utf-8')
    assert client.get(f'/api/documents/{key}/pages/1').json()['revised']
    new_edit = {**edit, 'markdown': '# Second edit', 'version': saved.json()['version']}
    assert client.post(f'/api/documents/{key}/pages/1', json=new_edit).status_code == 200
    assert len(list((bundle / 'review/history').glob('*.json'))) == 1
    assert list(page_sections(effective_markdown(bundle))) == [1, 3]


def test_export_is_self_contained_and_includes_revisions(client, bundle):
    key = doc_id(client)
    save_revision(bundle, 3, '# Changed third page', '')
    response = client.get(f'/api/documents/{key}/export')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert {'document.md', 'original-document.md', 'START_HERE.md', 'source.pdf',
                'chunks/0000/images/fig.png', 'pages/page-0001.png', 'evidence/eq.png',
                'review/revisions.json'} <= set(archive.namelist())
        assert '# Changed third page' in archive.read('document.md').decode('utf-8')
        assert '# Third' in archive.read('original-document.md').decode('utf-8')
        assert not any(name.endswith('engine.log') for name in archive.namelist())
    save_revision(bundle, 1, '![](missing.png)', client.get(f'/api/documents/{key}/pages/1').json()['version'])
    assert client.get(f'/api/documents/{key}/export').status_code == 409


def test_local_access_and_path_boundaries(client, bundle):
    key = doc_id(client)
    assert client.get('/api/state', headers={'X-PaperBridge-Token': ''}).status_code == 403
    assert client.post('/api/tasks/nope/retry', headers={'Origin': 'https://evil.test'}).status_code == 403
    assert client.get('/', headers={'Host': 'evil.test'}).status_code == 403
    assert client.get(f'/files/{key}/pages/page-0001.png').status_code == 200
    assert client.get(f'/files/{key}/..%2F..%2Foutside.png').status_code == 404
    assert client.get(f'/files/{key}/report.json').status_code == 403
    client.cookies.clear()
    assert client.get(f'/files/{key}/source.pdf').status_code == 403


def test_upload_validation_queue_and_retry(client, bundle):
    data = (bundle / 'source.pdf').read_bytes()
    assert client.post('/api/tasks', files={'file': ('a.txt', data)}).status_code == 400
    assert client.post('/api/tasks', files={'file': ('a.pdf', b'broken')}).status_code == 400
    assert client.post('/api/tasks?pages=4', files={'file': ('a.pdf', data)}).status_code == 400
    result = client.post('/api/tasks?pages=1,3', files={'file': ('测试论文.pdf', data)})
    assert result.status_code == 200
    job = result.json()
    assert job['status'] == 'queued' and job['filename'] == '测试论文.pdf'
    assert client.post(f'/api/tasks/{job["id"]}/retry').status_code == 409
    tasks = client.app.state.tasks
    tasks.jobs[0].update(status='failed')
    tasks.persist()
    assert client.post(f'/api/tasks/{job["id"]}/retry').status_code == 200
    assert tasks.jobs[0]['status'] == 'queued'
    assert 'source' not in client.get('/api/state').json()['tasks'][0]


def test_queue_restart_preserves_jobs_and_marks_interruption(tmp_path):
    from paperbridge.workbench import TaskQueue
    state = tmp_path / 'state'
    tasks = TaskQueue(tmp_path / 'output', state, start_worker=False)
    tasks.add(tmp_path / 'paper.pdf', 'paper.pdf', 'standard', 'all')
    tasks.jobs[0]['status'] = 'running'
    tasks.persist()
    recovered = TaskQueue(tmp_path / 'output', state, start_worker=False)
    assert recovered.jobs[0]['status'] == 'failed'
    assert '重试' in recovered.jobs[0]['error']


def test_export_refuses_directory_escape(bundle, tmp_path):
    outside = tmp_path / 'secret.png'
    Image.new('RGB', (1, 1)).save(outside)
    save_revision(bundle, 1, '![](../../secret.png)', '')
    with pytest.raises(ValueError):
        export_bundle(bundle)


def test_revision_rejects_page_marker_injection(bundle):
    with pytest.raises(ValueError, match='source_page'):
        save_revision(bundle, 1, '<!-- source_page: 9 -->\nInjected', '')


@pytest.mark.parametrize('lock_owner', [2222, 3333])
def test_shutdown_stops_actual_cli_pid_and_only_releases_owned_lock(tmp_path, monkeypatch, lock_owner):
    from types import SimpleNamespace
    from paperbridge.workbench import TaskQueue
    tasks = TaskQueue(tmp_path / 'outputs', tmp_path / 'state', start_worker=False)
    result = tasks.output / 'result'
    result.mkdir()
    lock = result / '.lock'
    lock.write_text(str(lock_owner))
    job = tasks.add(tmp_path / 'paper.pdf', 'paper.pdf', 'standard', 'all')
    tasks.jobs[0].update(status='running', output=str(result), owner_pid=2222)
    tasks.process = SimpleNamespace(pid=1111, poll=lambda: None, wait=lambda timeout: 0)
    commands = []
    monkeypatch.setattr('paperbridge.workbench.os', SimpleNamespace(name='nt'))
    monkeypatch.setattr('paperbridge.workbench.subprocess.run',
                        lambda command, **kwargs: commands.append(command) or SimpleNamespace(returncode=0))
    tasks.close()
    assert commands == [['taskkill', '/PID', '2222', '/T', '/F']]
    assert lock.exists() == (lock_owner != 2222)
    assert tasks.jobs[0]['status'] == 'failed'
