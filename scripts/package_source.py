"""Archive source and small reports, without PDFs, models or virtualenv."""
from pathlib import Path
import zipfile
import subprocess

ROOT=Path(__file__).resolve().parents[1]
target=ROOT/'paperbridge-source.zip'
files=[ROOT / name for name in subprocess.check_output(
    ['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0') if name]
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path,'paperbridge/'+path.relative_to(ROOT).as_posix())
print(target, target.stat().st_size)
