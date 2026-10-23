"""Compare fresh isolated/persistent batches, including saved content and assets."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from paperbridge.checkpoint import sha256


def content_manifest(root):
    paths = [root / name for name in ('document.md', 'document.json', 'evidence.json', 'source.pdf')]
    paths.extend(p for directory in ('pages', 'evidence') for p in (root / directory).rglob('*') if p.is_file())
    for chunk in (root / 'chunks').iterdir():
        marker = json.loads((chunk / 'DONE').read_text(encoding='utf-8'))
        paths.extend(chunk / name for name in marker['files'])
    return {p.relative_to(root).as_posix(): sha256(p) for p in paths}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', nargs='+', type=Path)
    parser.add_argument('-o', '--output', required=True, type=Path, help='A new directory; existing results are refused')
    parser.add_argument('--pages', default='1')
    parser.add_argument('--chunk-size', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error('repeats must be positive')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    files = [str(p.resolve()) for p in args.inputs]
    runs, reference, differences = [], None, []
    for repeat in range(args.repeats):
        modes = ('isolated', 'persistent') if repeat % 2 == 0 else ('persistent', 'isolated')
        for mode in modes:
            target = args.output / f'{repeat + 1}-{mode}'
            log = args.output / f'{repeat + 1}-{mode}.log'
            started = time.monotonic()
            with log.open('w', encoding='utf-8') as stream:
                process = subprocess.run([sys.executable, '-m', 'paperbridge.cli', 'convert', *files,
                    '-o', str(target), '--offline', '--engine-mode', mode, '--pages', args.pages,
                    '--chunk-size', str(args.chunk_size)], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            run = {'mode': mode, 'repeat': repeat + 1, 'wall_seconds': round(time.monotonic() - started, 3),
                   'exit_code': process.returncode, 'documents': []}
            if process.returncode not in (0, 2):
                raise RuntimeError(f'Benchmark conversion failed; inspect {log}')
            manifests = {}
            for report_path in sorted(target.glob('*/report.json')):
                report = json.loads(report_path.read_text(encoding='utf-8'))
                if not report['integrity']['assets_passed'] or any(c['cached'] for c in report['chunks']):
                    raise RuntimeError('Benchmark requires fresh conversions with valid assets')
                manifests[report_path.parent.name] = content_manifest(report_path.parent)
                run['documents'].append({key: report[key] for key in ('source_name', 'elapsed_seconds', 'status', 'counts')})
                run['documents'][-1]['chunks'] = [c.get('engine_timing', {}) for c in report['chunks']]
            if not manifests:
                raise RuntimeError('No benchmark results found')
            if reference is None:
                reference = manifests
            else:
                for doc in sorted(reference.keys() | manifests.keys()):
                    before, after = reference.get(doc, {}), manifests.get(doc, {})
                    for path in sorted(before.keys() | after.keys()):
                        if before.get(path) != after.get(path):
                            differences.append({'run': target.name, 'document': doc, 'path': path})
            runs.append(run)
            print(json.dumps({k: v for k, v in run.items() if k != 'documents'}), flush=True)
    medians = {mode: statistics.median(r['wall_seconds'] for r in runs if r['mode'] == mode)
               for mode in ('isolated', 'persistent')}
    summary = {'scope': 'Fresh local batches; same models, quality settings and inputs. Not a semantic accuracy certification.',
               'inputs': files, 'pages': args.pages, 'chunk_size': args.chunk_size,
               'runs': runs, 'median_wall_seconds': medians,
               'reduction_percent': round((1 - medians['persistent'] / medians['isolated']) * 100, 2),
               'saved_content_identical': not differences, 'differences': differences}
    (args.output / 'benchmark.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k not in ('runs', 'inputs', 'differences')}, ensure_ascii=False, indent=2))
    return 0 if not differences else 2


if __name__ == '__main__':
    sys.exit(main())
