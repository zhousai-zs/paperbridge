"""Real frozen-model regression with a sanitized environment, not a clean VM claim."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / '.cache' / 'portable-check')
    args = parser.parse_args()
    target = args.output.resolve()
    target.mkdir(parents=True, exist_ok=True)
    executable = args.bundle.resolve() / 'PaperBridge-CLI.exe'
    env = {key: value for key, value in os.environ.items() if not key.startswith(
        ('PYTHON', 'PAPERBRIDGE', 'MINERU', 'HF_', 'HUGGINGFACE', 'MODELSCOPE', 'VIRTUAL_ENV'))}
    env.update(PATH=os.path.join(os.environ['SystemRoot'], 'System32'),
               PAPERBRIDGE_STATE=str(target / 'user-state'), PYTHONUTF8='1',
               HTTP_PROXY='http://127.0.0.1:9', HTTPS_PROXY='http://127.0.0.1:9', ALL_PROXY='http://127.0.0.1:9')
    runs = []
    def run(label, arguments, allowed=(0, 2)):
        print(label, flush=True)
        start = time.monotonic()
        result = subprocess.run([str(executable), *map(str, arguments)], cwd=target, env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', timeout=1800,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        (target / f'{label}.log').write_text(result.stdout, encoding='utf-8')
        record = {'name': label, 'exit_code': result.returncode, 'seconds': round(time.monotonic()-start, 2)}
        runs.append(record)
        (target / 'runs.json').write_text(json.dumps(runs, indent=2), encoding='utf-8')
        if result.returncode not in allowed:
            raise AssertionError(f'{label}: {result.returncode}\n{result.stdout[-3000:]}')
        return result
    run('01-doctor', ['doctor', '--models'])
    corpus = args.corpus.resolve()
    names = ['fixture_en_1col', 'fixture_en_2col', 'fixture_zh_1col', 'fixture_zh_2col',
             'zh_single', 'zh_mixed_layout', 'zh_math', 'en_single_math',
             'en_mixed_figure', 'en_double_math', 'bilingual_table']
    inputs = [corpus / f'{name}.pdf' for name in names]
    if not all(path.is_file() for path in inputs):
        parser.error('The existing regression corpus is incomplete.')
    output = target / '中文 输出'
    run('02-standard-layouts', ['convert', *inputs, '--offline', '--progress-json', '-o', output])
    run('03-original-papers', ['convert', corpus / 'attention.pdf', corpus / 'raft.pdf',
                              '--pages', '1,3', '--offline', '-o', output])
    from paperbridge.quality import check_markdown
    from paperbridge.checkpoint import cache_valid
    from PIL import Image
    documents = []
    for report_file in sorted(output.glob('*/report.json')):
        root = report_file.parent
        report = json.loads(report_file.read_text(encoding='utf-8'))
        assert report['status'] in {'complete', 'needs_review'}
        markdown = (root / 'document.md').read_text(encoding='utf-8')
        assert not check_markdown(markdown, root)
        pages = [int(page) for page in re.findall(r'<!-- source_page: (\d+) -->', markdown)]
        assert pages == report['selected_pages']
        for page in pages:
            with Image.open(root / 'pages' / f'page-{page:04}.png') as image:
                image.verify()
        evidence = json.loads((root / 'evidence.json').read_text(encoding='utf-8'))
        for item in evidence:
            with Image.open(root / item['image']) as image:
                image.verify()
        assert all(cache_valid(path) for path in (root / 'chunks').iterdir())
        documents.append({'source': report['source_name'], 'pages': len(pages), 'crops': len(evidence),
                          'status': report['status']})
    assert len(documents) == 13
    run('04-checkpoint-reuse', ['convert', *inputs, '--offline', '-o', output])
    for report_file in output.glob('*/report.json'):
        if json.loads(report_file.read_text(encoding='utf-8'))['source_name'] in {path.name for path in inputs}:
            assert all(item['cached'] for item in json.loads(report_file.read_text(encoding='utf-8'))['chunks'])
    bad = target / '损坏.pdf'
    bad.write_bytes(b'not a pdf')
    run('05-failure-continues', ['convert', bad, inputs[0], '--offline', '-o', output], allowed=(1,))
    batch = json.loads((output / 'batch-report.json').read_text(encoding='utf-8'))
    assert {item['status'] for item in batch} >= {'failed', 'complete'}
    from pypdf import PdfWriter
    writer = PdfWriter(clone_from=inputs[0])
    writer.encrypt('test-only')
    encrypted = target / '加密.pdf'
    writer.write(encrypted)
    run('06-encrypted', ['convert', encrypted, '--offline', '-o', target / 'encrypted'], allowed=(1,))
    legacy = target / 'legacy'
    shutil.copytree(next(output.glob('fixture_en_1col-*')), legacy, dirs_exist_ok=True)
    (legacy / 'review' / 'history').mkdir(parents=True, exist_ok=True)
    (legacy / 'review' / 'revisions.json').write_text(json.dumps({'pages': {'1': {'markdown': '旧修订保留'}}}), encoding='utf-8')
    (legacy / 'review' / 'history' / 'old.json').write_text('{}')
    run('07-legacy-export', ['export', legacy, '-o', target / 'reading.zip'])
    with zipfile.ZipFile(target / 'reading.zip') as archive:
        assert '旧修订保留' in archive.read('document.md').decode('utf-8')
        assert 'review/history/old.json' in archive.namelist()
    run('08-basic', ['convert', inputs[0], '--tier', 'basic', '--offline', '-o', target / 'basic'])
    print('09-stop-after-checkpoint', flush=True)
    cancel = target / 'cancel-signal'
    cancel_env = {**env, 'PAPERBRIDGE_CANCEL_FILE': str(cancel)}
    cancel_output = target / 'cancel-output'
    arguments = ['convert', str(corpus / 'attention.pdf'), '--pages', '1,3', '--chunk-size', '1',
                 '--offline', '--progress-json', '-o', str(cancel_output)]
    process = subprocess.Popen([str(executable), *arguments], cwd=target, env=cancel_env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
    lines, requested = [], False
    try:
        for line in process.stdout:
            lines.append(line)
            if line.startswith('PAPERBRIDGE_EVENT '):
                event = json.loads(line[len('PAPERBRIDGE_EVENT '):])
                if event.get('stage') == 'parsing' and event.get('chunk') == 2:
                    cancel.touch()
                    requested = True
        assert requested and process.wait(timeout=30) == 130
    finally:
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        process.stdout.close()
        cancel.unlink(missing_ok=True)
    (target / '09-stop-after-checkpoint.log').write_text(''.join(lines), encoding='utf-8')
    interrupted = next(cancel_output.glob('*/report.json'))
    assert json.loads(interrupted.read_text(encoding='utf-8'))['status'] == 'stopped'
    assert cache_valid(interrupted.parent / 'chunks' / '0000')
    assert not (interrupted.parent / '.lock').exists()
    run('10-resume-after-stop', arguments)
    resumed = json.loads(interrupted.read_text(encoding='utf-8'))
    assert resumed['chunks'][0]['cached'] and not resumed['chunks'][1]['cached']
    run('11-timeout', ['convert', inputs[0], '--timeout', '.05', '--offline', '-o', target / 'timeout'], allowed=(1,))
    timed_out = next((target / 'timeout').glob('*/report.json'))
    assert json.loads(timed_out.read_text(encoding='utf-8'))['status'] == 'failed'
    assert not (timed_out.parent / '.lock').exists()
    result = {'passed': True, 'clean_machine_verified': False, 'sanitized_environment': True,
              'network': 'local-only models, unreachable HTTP/HTTPS proxy; no OS firewall isolation',
              'documents': documents, 'runs': runs, 'accuracy_verified': False,
              'stopped_after_first_checkpoint': True, 'resumed_first_checkpoint': True, 'timeout_cleanup': True}
    (target / 'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
