import json
import os
from pathlib import Path
import sys
import time

import pytest

from paperbridge.desktop import BatchRunner, Options, load_output, save_output, unique_files, validate_output


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERBRIDGE_STATE', str(tmp_path / 'state'))


def test_remember_output_and_recover_missing_or_corrupt_setting(tmp_path):
    folder = validate_output(tmp_path / '中文 输出')
    save_output(folder)
    assert load_output() == (folder, '')
    folder.rmdir()
    assert load_output()[1]
    settings = tmp_path / 'state' / 'settings.json'
    settings.write_text('broken', encoding='utf-8')
    assert load_output()[1]


def test_bad_output_does_not_replace_settings(tmp_path):
    folder = validate_output(tmp_path / 'good')
    save_output(folder)
    with pytest.raises(ValueError):
        validate_output('')
    file = tmp_path / 'file'
    file.touch()
    with pytest.raises(ValueError):
        validate_output(file)
    assert load_output()[0] == folder


def test_selection_preserves_order_and_deduplicates(tmp_path):
    a, b = tmp_path / '甲.pdf', tmp_path / 'b.pdf'
    assert unique_files([b, a, b]) == [b, a]


@pytest.mark.parametrize('options', [Options(chunk_size=0), Options(timeout=float('nan')),
                                          Options(timeout=0), Options(pages='1;exit')])
def test_options_reject_invalid_values(options):
    with pytest.raises(ValueError):
        options.validate()


def wait_done(runner, timeout=40):
    deadline = time.monotonic() + timeout
    events = []
    while time.monotonic() < deadline:
        event = runner.events.get(timeout=max(0.1, deadline - time.monotonic()))
        events.append(event)
        if event['kind'] == 'done':
            runner.thread.join(timeout=1)
            assert not runner.running
            return events
    pytest.fail('desktop runner did not finish')


def test_batch_continues_after_failure_and_keeps_review_status(tmp_path, monkeypatch):
    calls = []
    script = '''import json,sys,pathlib
files=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
def emit(e): print('PAPERBRIDGE_EVENT '+json.dumps(e),flush=True)
for index,source in enumerate(files,1):
 emit({'event':'document_started','source':source,'index':index,'total':len(files)})
 emit({'stage':'parsing','total_pages':3,'completed_pages':2})
 status={'bad.pdf':'failed','review.pdf':'needs_review','good.pdf':'complete'}[pathlib.Path(source).name]
 emit({'event':'document_result','source':source,'status':status})
'''
    def command(module, *args):
        calls.append(args)
        return [sys.executable, '-u', '-c', script, str(args[args.index('--batch-manifest') + 1])]
    monkeypatch.setattr('paperbridge.desktop.module_command', command)
    output = validate_output(tmp_path / 'outputs')
    runner = BatchRunner()
    runner.start([tmp_path / p for p in ('bad.pdf', 'review.pdf', 'good.pdf')], output, Options())
    events = wait_done(runner)
    assert [r['status'] for r in runner.results] == ['failed', 'needs_review', 'complete']
    assert any(e.get('completed_pages') == 2 for e in events)
    assert len(json.loads((output / 'batch-report.json').read_text(encoding='utf-8'))) == 3
    assert all(Path(r['log']).is_file() for r in runner.results)
    assert len(calls) == 1
    assert [e['source'] for e in events if e['kind'] == 'started'] == [str(tmp_path / p) for p in ('bad.pdf', 'review.pdf', 'good.pdf')]


def test_stop_current_file_skips_remaining_and_cleans_signal(tmp_path, monkeypatch):
    script = ("import os,time,pathlib,json; print('PAPERBRIDGE_EVENT '+json.dumps({'event':'document_started','source':'a.pdf','index':1,'total':2}),flush=True); "
              "exec(\"while not pathlib.Path(os.environ['PAPERBRIDGE_CANCEL_FILE']).exists(): time.sleep(.02)\"); "
              "raise SystemExit(130)")
    monkeypatch.setattr('paperbridge.desktop.module_command', lambda *args: [sys.executable, '-u', '-c', script])
    runner = BatchRunner()
    runner.start([tmp_path / 'a.pdf', tmp_path / 'b.pdf'], validate_output(tmp_path / 'out'), Options())
    deadline = time.monotonic() + 10
    while runner.process is None and time.monotonic() < deadline:
        time.sleep(.01)
    runner.stop()
    events = wait_done(runner)
    assert len(runner.results) == 1
    assert runner.results[0]['status'] == 'stopped'
    assert events[-1]['stopped']
    assert not runner.cancel_file.exists()
    assert runner.process is None


def test_coordinator_exiting_without_results_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr('paperbridge.desktop.module_command',
                        lambda *args: [sys.executable, '-c', 'raise SystemExit(1)'])
    runner = BatchRunner()
    runner.start([tmp_path / 'a.pdf'], validate_output(tmp_path / 'out'), Options())
    events = wait_done(runner)
    assert any(e['kind'] == 'error' for e in events)
    assert events[-1]['kind'] == 'done'


def test_forced_stop_cleans_only_active_coordinator_lock(tmp_path, monkeypatch):
    output = validate_output(tmp_path / 'out')
    target = output / 'active'
    target.mkdir()
    other = output / 'other'
    other.mkdir()
    (other / '.lock').write_text('123456')
    script = '''import json,os,pathlib,sys,time
root=pathlib.Path(sys.argv[1])
(root/'.lock').write_text(str(os.getpid()))
(root/'report.json').write_text(json.dumps({'status':'running'}))
def emit(e): print('PAPERBRIDGE_EVENT '+json.dumps(e),flush=True)
emit({'event':'document_started','source':'a.pdf','index':1,'total':2})
emit({'stage':'preparing','output':str(root),'owner_pid':os.getpid()})
time.sleep(60)
'''
    monkeypatch.setattr('paperbridge.desktop.module_command',
                        lambda *args: [sys.executable, '-u', '-c', script, str(target)])
    from paperbridge.processes import terminate_tree
    runner = BatchRunner()
    monkeypatch.setattr(runner, '_stop_after_grace', terminate_tree)
    runner.start([tmp_path / 'a.pdf', tmp_path / 'b.pdf'], output, Options())
    while runner.events.get(timeout=10)['kind'] != 'progress':
        pass
    runner.stop()
    wait_done(runner)
    assert [r['status'] for r in runner.results] == ['stopped']
    assert not (target / '.lock').exists()
    assert (other / '.lock').read_text() == '123456'
    assert json.loads((target / 'report.json').read_text())['status'] == 'stopped'
    assert not runner.cancel_file.with_suffix('.inputs.json').exists()
