"""Build a wheel and assert that the offline workbench assets ship with it."""
from pathlib import Path
import os
import zipfile
from setuptools.build_meta import build_wheel

root = Path(__file__).resolve().parents[1]
destination = root / '.cache' / 'wheel-check'
destination.mkdir(parents=True, exist_ok=True)
os.chdir(root)
build_wheel(str(destination))
wheel = max(destination.glob('paperbridge-*.whl'), key=lambda p: p.stat().st_mtime)
with zipfile.ZipFile(wheel) as archive:
    assert {'paperbridge/web/index.html', 'paperbridge/web/app.css', 'paperbridge/web/reader.css',
            'paperbridge/web/app.js'} <= set(archive.namelist())
print('PASS: wheel includes offline web assets')
