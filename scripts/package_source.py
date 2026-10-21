"""Archive source and small reports, without PDFs, models or virtualenv."""
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
target=ROOT/'paperbridge-source.zip'
files=[ROOT/x for x in ('README.md','CHANGELOG.md','pyproject.toml','requirements.lock.txt','requirements-test.txt','setup.ps1','convert.ps1','.gitignore')]
for folder in ('paperbridge','tests','scripts'):
    files.extend((ROOT/folder).glob('*.py'))
files.extend((ROOT/'research').glob('*.md'))
files.extend((ROOT/'paperbridge/web').glob('*'))
files.extend(p for p in (ROOT/'.github').rglob('*') if p.is_file())
files.extend((ROOT/'scripts').glob('*.cjs'))
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path,'paperbridge/'+path.relative_to(ROOT).as_posix())
    archive.write(ROOT/'PDF转Markdown.cmd','paperbridge/PDF转Markdown.cmd')
    archive.write(ROOT/'论文工作台.cmd','paperbridge/论文工作台.cmd')
print(target, target.stat().st_size)
