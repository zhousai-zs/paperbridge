from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parents[1]


def frozen():
    return bool(getattr(sys, 'frozen', False))


def state_dir():
    base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share'))
    return Path(os.environ.get('PAPERBRIDGE_STATE', base / 'PaperBridge')).resolve()


def default_output():
    if not frozen():
        return ROOT / 'outputs'
    return documents_dir() / 'PaperBridge' / 'outputs'


def documents_dir():
    if os.name == 'nt':
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        # CSIDL_PERSONAL honours redirected Documents folders, including OneDrive.
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0:
            return Path(buffer.value)
    return Path.home() / 'Documents'


def model_home():
    if frozen():
        return Path(sys.executable).resolve().parent / 'models'
    return Path(os.environ.get('PAPERBRIDGE_CACHE', ROOT / '.cache')).resolve() / 'mineru' / 'models'


def configure():
    """Keep frozen resources read-only and runtime state in a writable user folder."""
    cache = state_dir() if frozen() else Path(os.environ.get("PAPERBRIDGE_CACHE", ROOT / ".cache")).resolve()
    temp = cache / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = str(temp)
    import tempfile
    tempfile.tempdir = str(temp)
    defaults = {
        "MINERU_HOME": str(cache / "mineru"),
        "HF_HOME": str(cache / "huggingface"),
        "HF_HUB_CACHE": str(cache / "huggingface/hub"),
        "HUGGINGFACE_HUB_CACHE": str(cache / "huggingface/hub"),
        "MODELSCOPE_CACHE": str(cache / "modelscope"),
        "HF_HUB_DISABLE_XET": "1",
        "MINERU_MODEL_SMALL_BACKEND": "onnx",
        "MINERU_MODEL_VLM_ENGINE": "llama-cpp",
        "PYTHONUTF8": "1",
        "MINERU_MODEL_BASE_DIR": str(model_home()),
    }
    for key, value in defaults.items():
        if key in {"MINERU_HOME", "HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "MODELSCOPE_CACHE", "MINERU_MODEL_BASE_DIR"}:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)
    cache.mkdir(parents=True, exist_ok=True)
    if frozen():
        os.environ['MINERU_MODEL_SOURCE'] = 'local'
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['MINERU_MODEL_SMALL_BACKEND'] = 'onnx'
        os.environ['MINERU_MODEL_VLM_ENGINE'] = 'llama-cpp'
    return cache


def prepare_engine():
    configure()
    if frozen():
        # MinerU 4 takes download locks even for local, complete models.
        # Redirect only those locks; never copy multi-GB models into user state.
        from mineru.model.download import ModelRepo
        lock_root = state_dir() / 'model-locks'
        ModelRepo.lock_path = lambda repo: lock_root / f'{repo.local_name}.lock'
