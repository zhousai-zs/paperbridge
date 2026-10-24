import json
from pathlib import Path

from paperbridge.cli import main
from paperbridge.processes import ConversionCancelled


def test_batch_uses_one_session_and_continues_in_selection_order(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('PAPERBRIDGE_CACHE', str(tmp_path / 'cache'))
    files = [str(tmp_path / name) for name in ('bad.pdf', 'review.pdf', 'good.pdf')]
    manifest = tmp_path / 'inputs.json'
    manifest.write_text(json.dumps(files), encoding='utf-8')
    sessions, sources = [], []

    def convert(source, output, *, engine, on_event, **kwargs):
        sessions.append(engine)
        sources.append(str(source))
        if source.name == 'bad.pdf':
            raise RuntimeError('injected failure')
        status = 'needs_review' if source.name == 'review.pdf' else 'complete'
        return Path(output) / source.stem, {'status': status}

    monkeypatch.setattr('paperbridge.core.convert', convert)
    output = tmp_path / 'out'
    assert main(['convert', '--batch-manifest', str(manifest), '-o', str(output), '--progress-json']) == 1
    assert sources == files
    assert len({id(s) for s in sessions}) == 1
    results = json.loads((output / 'batch-report.json').read_text(encoding='utf-8'))
    assert [r['status'] for r in results] == ['failed', 'needs_review', 'complete']
    events = [json.loads(line.split(' ', 1)[1]) for line in capsys.readouterr().out.splitlines()
              if line.startswith('PAPERBRIDGE_EVENT ')]
    assert [e['event'] for e in events] == ['document_started', 'document_result'] * 3


def test_cancel_emits_stopped_result_and_does_not_start_next_document(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('PAPERBRIDGE_CACHE', str(tmp_path / 'cache'))
    calls = []
    def convert(source, output, **kwargs):
        calls.append(source)
        raise ConversionCancelled('stopped')
    monkeypatch.setattr('paperbridge.core.convert', convert)
    output = tmp_path / 'out'
    assert main(['convert', str(tmp_path / 'a.pdf'), str(tmp_path / 'b.pdf'), '-o', str(output), '--progress-json']) == 130
    assert len(calls) == 1
    assert json.loads((output / 'batch-report.json').read_text())[-1]['status'] == 'stopped'


def test_new_batch_keeps_previous_batch_report_in_history(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERBRIDGE_CACHE', str(tmp_path / 'cache'))
    monkeypatch.setattr('paperbridge.core.convert',
                        lambda source, output, **kwargs: (Path(output) / source.stem, {'status': 'complete'}))
    output = tmp_path / 'out'
    assert main(['convert', str(tmp_path / 'first.pdf'), '-o', str(output)]) == 0
    assert main(['convert', str(tmp_path / 'second.pdf'), '-o', str(output)]) == 0
    current = json.loads((output / 'batch-report.json').read_text(encoding='utf-8'))
    assert [Path(r['source']).name for r in current] == ['second.pdf']
    archived = list((output / 'history').glob('batch-report-*.json'))
    assert len(archived) == 1
    assert Path(json.loads(archived[0].read_text(encoding='utf-8'))[0]['source']).name == 'first.pdf'
