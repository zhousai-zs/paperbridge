"""Optional formula CUDA backend with explicit, observable CPU fallback."""
import os
from pathlib import Path
import sys


VALID_FORMULA_DEVICES = {'auto', 'cpu', 'cuda'}
_STATE = {'requested': 'cpu', 'actual': 'cpu', 'reason': '', 'providers': ['CPUExecutionProvider']}
_CONFIGURED = None


def requested_formula_device(value=None):
    requested = (value if value is not None else os.environ.get('PAPERBRIDGE_FORMULA_DEVICE', 'cpu')).strip().lower()
    if requested not in VALID_FORMULA_DEVICES:
        raise ValueError("PAPERBRIDGE_FORMULA_DEVICE must be 'auto', 'cpu' or 'cuda'")
    return requested


def formula_backend_status():
    return dict(_STATE)


def _preload_cuda(ort):
    if not hasattr(ort, 'preload_dlls'):
        return
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        ort.preload_dlls(directory=str(Path(sys._MEIPASS)))
    else:
        ort.preload_dlls(directory='')


def configure_formula_backend(requested=None):
    """Patch only the formula ONNX factory; all other model routes stay unchanged."""
    global _CONFIGURED, _STATE
    requested = requested_formula_device(requested)
    if _CONFIGURED is not None:
        if requested != _CONFIGURED:
            raise RuntimeError(f'Formula backend already configured as {_CONFIGURED}, cannot change to {requested}')
        return formula_backend_status()
    _CONFIGURED = requested
    _STATE = {'requested': requested, 'actual': 'cpu', 'reason': '',
              'providers': ['CPUExecutionProvider']}
    if requested == 'cpu':
        return formula_backend_status()

    import onnxruntime as ort
    available = ort.get_available_providers()
    if 'CUDAExecutionProvider' not in available:
        reason = 'CUDAExecutionProvider is unavailable in this runtime'
        if requested == 'cuda':
            raise RuntimeError(reason)
        _STATE['reason'] = reason
        return formula_backend_status()
    try:
        _preload_cuda(ort)
    except Exception as exc:
        reason = f'CUDA runtime preload failed: {type(exc).__name__}: {exc}'
        if requested == 'cuda':
            raise RuntimeError(reason) from exc
        _STATE['reason'] = reason
        return formula_backend_status()

    from mineru.model.mfr import pp_formulanet_plus_m_onnx as formula_module
    cpu_session = formula_module.ort_session

    class FormulaSession:
        def __init__(self, model_path, device, intra_op_num_threads, cuda_session):
            self.model_path = model_path
            self.device = device
            self.intra_op_num_threads = intra_op_num_threads
            self.cuda = cuda_session
            self.cpu = None

        def __getattr__(self, name):
            return getattr(self.cuda if self.cpu is None else self.cpu, name)

        def run(self, *args, **kwargs):
            global _STATE
            if self.cpu is not None:
                return self.cpu.run(*args, **kwargs)
            try:
                return self.cuda.run(*args, **kwargs)
            except Exception as exc:
                if requested == 'cuda':
                    raise
                self.cpu = cpu_session(self.model_path, self.device, self.intra_op_num_threads)
                _STATE = {'requested': requested, 'actual': 'cpu',
                          'reason': f'CUDA inference failed; using CPU: {type(exc).__name__}: {exc}',
                          'providers': self.cpu.get_providers()}
                return self.cpu.run(*args, **kwargs)

    def cuda_session(model_path, device=None, intra_op_num_threads=0):
        global _STATE
        options = ort.SessionOptions()
        options.log_severity_level = 3
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        providers = [('CUDAExecutionProvider', {
            'device_id': 0,
            'arena_extend_strategy': 'kSameAsRequested',
            'cudnn_conv_algo_search': 'HEURISTIC',
            'do_copy_in_default_stream': True,
            'gpu_mem_limit': 5 * 1024 * 1024 * 1024,
            'use_tf32': 0,
        }), 'CPUExecutionProvider']
        try:
            session = ort.InferenceSession(model_path, sess_options=options, providers=providers)
            actual = session.get_providers()
            if not actual or actual[0] != 'CUDAExecutionProvider':
                raise RuntimeError(f'CUDA provider was not activated: {actual}')
        except Exception as exc:
            reason = f'CUDA initialization failed: {type(exc).__name__}: {exc}'
            if requested == 'cuda':
                raise RuntimeError(reason) from exc
            fallback = cpu_session(model_path, device, intra_op_num_threads)
            _STATE = {'requested': requested, 'actual': 'cpu', 'reason': reason,
                      'providers': fallback.get_providers()}
            return fallback
        _STATE = {'requested': requested, 'actual': 'cuda', 'reason': '', 'providers': actual}
        return FormulaSession(model_path, device, intra_op_num_threads, session)

    formula_module.ort_session = cuda_session
    _STATE = {'requested': requested, 'actual': 'cuda_pending', 'reason': '',
              'providers': available}
    return formula_backend_status()
