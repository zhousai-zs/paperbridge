import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('benchmark_gpu', ROOT / 'scripts' / 'benchmark_gpu.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def run(name, backend, manifest):
    return {'run': name, 'backend': backend,
            'documents': [{'name': 'paper', 'manifest': manifest}]}


def test_compare_runs_keeps_raw_quality_differences():
    differences = MODULE.compare_runs([
        run('01-cpu', 'cpu', {'document.md': 'a'}),
        run('02-cuda', 'cuda', {'document.md': 'b'}),
    ])
    assert differences == [{'run': '02-cuda', 'backend': 'cuda',
                            'document': 'paper', 'path': 'document.md'}]


def test_provider_records_requires_real_cuda_events(tmp_path):
    (tmp_path / 'session-1.json').write_text(
        '{"actual_providers":["CUDAExecutionProvider","CPUExecutionProvider"],'
        '"profile_provider_counts":{"CPUExecutionProvider":12}}', encoding='utf-8')
    try:
        MODULE.provider_records(tmp_path)
    except RuntimeError as exc:
        assert 'no CUDA node events' in str(exc)
    else:
        raise AssertionError('CPU-only profile must not pass as CUDA')


def test_provider_records_accepts_cuda_with_measured_cpu_fallback(tmp_path):
    (tmp_path / 'session-1.json').write_text(
        '{"actual_providers":["CUDAExecutionProvider","CPUExecutionProvider"],'
        '"profile_provider_counts":{"CUDAExecutionProvider":8,"CPUExecutionProvider":3}}', encoding='utf-8')
    records = MODULE.provider_records(tmp_path)
    assert records[0]['profile_provider_counts']['CPUExecutionProvider'] == 3
