import importlib

import pytest

import paperbridge.accelerators as accelerators


@pytest.fixture
def fresh(monkeypatch):
    module = importlib.reload(accelerators)
    monkeypatch.delenv('PAPERBRIDGE_FORMULA_DEVICE', raising=False)
    return module


def test_formula_device_defaults_to_cpu_and_rejects_unknown(fresh, monkeypatch):
    assert fresh.requested_formula_device() == 'cpu'
    monkeypatch.setenv('PAPERBRIDGE_FORMULA_DEVICE', 'other')
    with pytest.raises(ValueError):
        fresh.requested_formula_device()


def test_auto_falls_back_when_cuda_provider_is_unavailable(fresh, monkeypatch):
    import onnxruntime
    monkeypatch.setattr(onnxruntime, 'get_available_providers', lambda: ['CPUExecutionProvider'])
    status = fresh.configure_formula_backend('auto')
    assert status['actual'] == 'cpu'
    assert 'unavailable' in status['reason']


def test_forced_cuda_reports_missing_provider(fresh, monkeypatch):
    import onnxruntime
    monkeypatch.setattr(onnxruntime, 'get_available_providers', lambda: ['CPUExecutionProvider'])
    with pytest.raises(RuntimeError, match='unavailable'):
        fresh.configure_formula_backend('cuda')


def test_backend_cannot_change_after_configuration(fresh):
    fresh.configure_formula_backend('cpu')
    with pytest.raises(RuntimeError, match='already configured'):
        fresh.configure_formula_backend('auto')
