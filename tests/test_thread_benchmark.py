"""Guard against misleading experiment comparisons; no inference is loaded."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def benchmark(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('thread_benchmark_test', scripts / 'benchmark_threads.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def trial(root, name, threads, seconds, docs=None):
    folder = root / name
    folder.mkdir()
    record = {'run': name, 'threads': threads, 'mode': 'persistent', 'measured_seconds': seconds,
              'documents': docs if docs is not None else [{'name': 'case', 'manifest': {'pages/p.png': 'same'}}]}
    for doc in record['documents']:
        doc.setdefault('output', str(folder / doc['name']))
    (folder / 'run.json').write_text(json.dumps(record), encoding='utf-8')


def test_duplicate_names_cannot_hide_a_case(benchmark, tmp_path):
    pdf = tmp_path / 'example.pdf'
    pdf.touch()
    path = tmp_path / 'cases.json'
    path.write_text(json.dumps([{'name': 'same', 'source': 'example.pdf', 'pages': '1'},
                                {'name': 'same', 'source': 'example.pdf', 'pages': '2'}]))
    with pytest.raises(ValueError, match='unique'):
        benchmark.load_cases(path)


def test_missing_case_prevents_a_false_equal_report(benchmark, tmp_path):
    trial(tmp_path, '01', 4, 10)
    trial(tmp_path, '02', 6, 8, docs=[])
    trial(tmp_path, '03', 8, 9)
    with pytest.raises(RuntimeError, match='coverage'):
        benchmark.summarize(tmp_path)


def test_quality_differences_are_retained_separately_from_speed(benchmark, tmp_path):
    trial(tmp_path, '01', 4, 12)
    trial(tmp_path, '02', 4, 10)
    trial(tmp_path, '03', 6, 8, docs=[{'name': 'case', 'manifest': {'pages/p.png': 'changed'}}])
    trial(tmp_path, '04', 8, 9)
    result = benchmark.summarize(tmp_path)
    assert result['persistent_median_seconds'] == {'4': 11, '6': 8, '8': 9}
    assert result['saved_content_identical'] is False
    assert result['differences'][0]['path'] == 'pages/p.png'


def test_profile_costs_keep_nested_initialization_separate(benchmark):
    text = ('MinerU stage: name=model.initialize, elapsed_s=1.000001, pid=1\n'
            'MinerU stage: name=pdf.formulas, elapsed_s=3.123456, pid=1\n'
            'MinerU stage: name=pdf.formulas, elapsed_s=2.100000, pid=1')
    assert benchmark.profile_log(text) == {'model.initialize': 1.000001, 'pdf.formulas': 5.223456}
