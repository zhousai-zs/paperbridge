"""Fresh representative-page CPU trials; no production defaults are changed."""
import argparse
import collections
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from paperbridge.checkpoint import sha256
from paperbridge.core import write_json
from paperbridge.engine import EngineSession
from paperbridge.environment import configure, model_home
from paperbridge.processes import hidden_options, terminate_tree
from benchmark_engine import content_manifest


def profile_log(text):
    costs = collections.defaultdict(float)
    for name, elapsed in re.findall(r'MinerU stage: name=([\w.]+), elapsed_s=([\d.]+)', text):
        costs[name] += float(elapsed)
    return {name: round(value, 6) for name, value in costs.items()}


def load_cases(path):
    cases = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(cases, list) or not cases:
        raise ValueError('Cases must be a nonempty array')
    names = set()
    for case in cases:
        if not isinstance(case, dict) or not all(isinstance(case.get(k), str) and case[k].strip()
                                                for k in ('name', 'source', 'pages')):
            raise ValueError('Each case requires nonempty name/source/pages strings')
        if case['name'] in names:
            raise ValueError('Case names must be unique')
        names.add(case['name'])
        case['source'] = str((path.resolve().parent / case['source']).resolve())
        if not Path(case['source']).is_file():
            raise ValueError(f"Missing PDF: {case['source']}")
    return cases


def run_cases(cases, output, mode):
    from paperbridge.core import convert
    configure()
    write_json(output / 'runner.json', {'pid': os.getpid()})
    started = time.perf_counter()
    documents = []
    with EngineSession(reuse=mode == 'persistent') as engine:
        # Warm base engines. First-use formula/table models may still load later.
        warm_started = time.perf_counter()
        warm_root, warm_report = convert(ROOT / 'tests/corpus/fixture_en_1col.pdf', output / 'warmup',
                                         pages='1', engine=engine)
        warm_seconds = time.perf_counter() - warm_started
        warm_pid = warm_report['chunks'][0]['engine_timing']['worker_pid']
        measured_started = time.perf_counter()
        for case in cases:
            target, report = convert(case['source'], output / 'results', pages=case['pages'],
                                     chunk_size=2, engine=engine)
            if any(c['cached'] for c in report['chunks']) or not report['integrity']['assets_passed']:
                raise RuntimeError('Every measured chunk must be fresh with valid assets')
            stages = collections.Counter()
            chunks = []
            for chunk in report['chunks']:
                directory = target / 'chunks' / f"{chunk['index']:04}"
                stages.update(profile_log((directory / 'engine.log').read_text(encoding='utf-8', errors='replace')))
                chunks.append({'pages': chunk['pages'], 'timing': chunk['engine_timing'],
                               'engine_log_timing': chunk.get('engine_log_timing', {})})
            if not stages:
                raise RuntimeError('The installed engine did not emit high-resolution stage timings')
            documents.append({'name': case['name'], 'output': str(target), 'pages': report['selected_pages'],
                'seconds': report['elapsed_seconds'], 'stage_seconds': dict(stages), 'chunks': chunks,
                'counts': report['counts'], 'status': report['status'], 'issues': report['issues'],
                'manifest': content_manifest(target)})
        measured_seconds = time.perf_counter() - measured_started
    import onnxruntime as ort
    from mineru.model.runtime.onnx import configure_ort_threads, ort_providers
    options = ort.SessionOptions()
    configure_ort_threads(options)
    result = {'mode': mode, 'threads': options.intra_op_num_threads,
        'warmup_scope': 'Base engines only; first-use model initialization remains in measured stage costs.',
        'inter_op_threads': options.inter_op_num_threads,
        'ort_version': ort.__version__, 'configured_providers': ort_providers(),
        'available_providers': ort.get_available_providers(),
        'warmup_seconds': round(warm_seconds, 3), 'warmup_worker_pid': warm_pid,
        'measured_seconds': round(measured_seconds, 3),
        'wall_seconds': round(time.perf_counter() - started, 3), 'documents': documents}
    write_json(output / 'run.json', result)


def summarize(output):
    runs = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(output.glob('*/run.json'))]
    reference = runs[0]
    before = {d['name']: d for d in reference['documents']}
    if len(before) != len(reference['documents']):
        raise RuntimeError('Duplicate reference case names')
    differences = []
    for run in runs[1:]:
        names = [d['name'] for d in run['documents']]
        if len(names) != len(before) or set(names) != set(before):
            raise RuntimeError('Trial case coverage differs from the reference')
        for doc in run['documents']:
            a, b = before[doc['name']]['manifest'], doc['manifest']
            for path in sorted(a.keys() | b.keys()):
                if a.get(path) != b.get(path):
                    differences.append({'mode': run['mode'], 'threads': run['threads'],
                        'run': run['run'], 'document': doc['name'], 'path': path,
                        'reference_root': before[doc['name']]['output'], 'candidate_root': doc['output']})
    medians = {str(n): statistics.median(r['measured_seconds'] for r in runs
               if r['threads'] == n and r['mode'] == 'persistent') for n in (4, 6, 8)}
    return {'runs': runs, 'persistent_median_seconds': medians, 'differences': differences,
            'saved_content_identical': not differences,
            'scope': 'Base-engine warmup, fresh page groups, fixed 2-page chunks. First-use models may still initialize during measurement. Stage costs include CPU preparation and may nest; do not sum them as exclusive pure compute costs.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases', required=True, type=Path)
    p.add_argument('-o', '--output', required=True, type=Path)
    p.add_argument('--run', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--mode', choices=['persistent', 'isolated'], default='persistent', help=argparse.SUPPRESS)
    args = p.parse_args()
    try:
        cases = load_cases(args.cases)
    except (OSError, ValueError) as exc:
        p.error(str(exc))
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    if args.run:
        run_cases(cases, args.output, args.mode)
        return 0
    configure()
    shutil.copyfile(__file__, args.output / 'benchmark-source.py')
    sources = [{**c, 'sha256': sha256(Path(c['source']))} for c in cases]
    models = {p.relative_to(model_home()).as_posix(): sha256(p)
              for p in model_home().rglob('*')
              if p.is_file() and p.suffix in ('.onnx', '.gguf', '.yml')}
    if not models:
        raise RuntimeError('No local model files found')
    write_json(args.output / 'environment.json', {'cases': sources, 'models': models,
        'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'mineru': importlib.metadata.version('mineru'), 'python': sys.version,
        'profile_timer_sha256': sha256(Path(importlib.util.find_spec('mineru.utils.timing').origin)),
        'onnx_factory_sha256': sha256(Path(importlib.util.find_spec('mineru.model.runtime.onnx').origin)),
        'note': 'Factory-configured CPU providers, not per-operator placement tracing. RSS readings in chunk timings are end-of-chunk working sets, not GPU memory or global peak.'})
    schedule = [('isolated', 4), ('persistent', 4), ('persistent', 6), ('persistent', 8),
                ('persistent', 8), ('persistent', 6), ('persistent', 4)]
    for index, (mode, threads) in enumerate(schedule, 1):
        name = f'{index:02}-{mode}-{threads}'
        env = {**os.environ, 'MINERU_INTRA_OP_NUM_THREADS': str(threads),
               'MINERU_INTER_OP_NUM_THREADS': '1', 'MINERU_PROFILE_STAGES': '1',
               'MINERU_MODEL_SOURCE': 'local', 'HF_HUB_OFFLINE': '1', 'PYTHONUTF8': '1',
               'PAPERBRIDGE_CANCEL_FILE': str(args.output / f'{name}.cancel')}
        with (args.output / f'{name}.log').open('w', encoding='utf-8') as stream:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--run',
                '--cases', str(args.cases.resolve()), '-o', str(args.output / name), '--mode', mode],
                cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, **hidden_options())
            try:
                code = process.wait(timeout=1800)
            except BaseException:
                Path(env['PAPERBRIDGE_CANCEL_FILE']).touch()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    ready = args.output / name / 'runner.json'
                    if ready.exists():
                        process._paperbridge_pid = json.loads(ready.read_text(encoding='utf-8'))['pid']
                    terminate_tree(process)
                raise
        if code:
            raise RuntimeError(f'Trial failed ({code}); inspect {name}.log')
        record_path = args.output / name / 'run.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        record['run'] = name
        write_json(record_path, record)
        print(json.dumps({'run': name, 'warmup': record['warmup_seconds'],
                          'measured': record['measured_seconds']}), flush=True)
        time.sleep(3)
    report = summarize(args.output)
    write_json(args.output / 'benchmark.json', report)
    print(json.dumps({'medians': report['persistent_median_seconds'],
                      'changed_files': len(report['differences'])}), flush=True)
    return 0 if report['saved_content_identical'] else 2


if __name__ == '__main__':
    sys.exit(main())
