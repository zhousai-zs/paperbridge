"""Assert that the wheel contains desktop modules and no PaperBridge web server."""
from pathlib import Path
import os
import zipfile
from setuptools.build_meta import build_wheel

root = Path(__file__).resolve().parents[1]
target = root / '.cache' / 'wheel-check'
target.mkdir(parents=True, exist_ok=True)
os.chdir(root)
wheel = target / build_wheel(str(target))
with zipfile.ZipFile(wheel) as file:
    files = set(file.namelist())
    assert {'paperbridge/gui.py', 'paperbridge/desktop.py', 'paperbridge/processes.py',
            'paperbridge/bundles.py', 'paperbridge/worker.py', 'paperbridge/engine.py',
            'paperbridge/accelerators.py'} <= files
    assert not any(name.startswith('paperbridge/web/') or name == 'paperbridge/workbench.py' for name in files)
print('PASS: desktop wheel, no PaperBridge Web implementation')
