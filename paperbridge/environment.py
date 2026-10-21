from pathlib import Path
import os


ROOT = Path(__file__).resolve().parents[1]


def configure():
    """Keep model downloads and state in the project, not global user config."""
    cache = Path(os.environ.get("PAPERBRIDGE_CACHE", ROOT / ".cache")).resolve()
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
    }
    for key, value in defaults.items():
        if key in {"MINERU_HOME", "HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "MODELSCOPE_CACHE"}:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)
    cache.mkdir(parents=True, exist_ok=True)
    return cache
