"""Build the complete Windows x64 folder and ZIP; never download models."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    from paperbridge import __version__
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', type=Path, required=True, help='Existing MinerU models directory')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--package-only', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'win32' or __import__('struct').calcsize('P') != 8:
        parser.error('Build on Windows x64 with Python 3.12.')
    from paperbridge.environment import configure
    configure()
    from mineru.config import config
    from mineru.model.download import verify_model_repo
    from mineru.model.registry import model_repos_for_tier
    source = args.models.resolve()
    config.model.base_dir = str(source)
    repos = model_repos_for_tier('standard', small_backend='onnx', vlm_engine='llama-cpp')
    for repo in repos:
        result = verify_model_repo(repo)
        if not result.ready:
            parser.error(f'Missing local models: {repo.name}: {result.missing_paths}')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not args.package_only:
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--workpath',
                        str(ROOT / '.cache' / 'pyinstaller'), '--distpath', str(output),
                        str(ROOT / 'PaperBridge.spec')], cwd=ROOT, check=True)
    bundle = output / 'PaperBridge'
    for name in ('PaperBridge.exe', 'PaperBridge-CLI.exe', '_internal/python312.dll'):
        if not (bundle / name).is_file():
            raise RuntimeError(f'Incomplete frozen runtime: {name}')
    model_manifest = []
    model_target = bundle / 'models'
    if model_target.exists():
        # Only replace generated model copies inside this exact build folder.
        if not model_target.resolve().is_relative_to(bundle.resolve()) or source.is_relative_to(model_target.resolve()):
            raise ValueError('Build model target must be separate from the source models.')
        shutil.rmtree(model_target)
    for repo in repos:
        print(f'Copying models: {repo.name}', flush=True)
        target = bundle / 'models' / repo.local_name
        shutil.copytree(source / repo.local_name, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('.cache', '._____temp', '.msc', '.mdl', '.mv'))
        for path in sorted(target.rglob('*')):
            if path.is_file():
                model_manifest.append({'path': path.relative_to(bundle).as_posix(),
                                       'bytes': path.stat().st_size, 'sha256': sha256(path)})
    shutil.copy2(ROOT / 'docs' / '桌面版使用说明.md', bundle / '使用说明.md')
    shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', bundle / 'THIRD_PARTY_NOTICES.md')
    licenses = bundle / 'licenses'
    licenses.mkdir(exist_ok=True)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.exists():
        shutil.copy2(python_license, licenses / 'Python-LICENSE.txt')
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get('Name', 'unknown')
        for item in distribution.files or []:
            if '.dist-info/' in str(item).replace('\\', '/') and any(
                    part.lower().startswith(('license', 'copying', 'notice')) for part in item.parts):
                path = Path(distribution.locate_file(item))
                if path.is_file():
                    destination = licenses / name / Path(*item.parts[1:])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    metadata = {'version': __version__, 'git_commit': commit, 'source_dirty': dirty,
                'python': sys.version, 'models': model_manifest,
                'executables': {name: sha256(bundle / name) for name in ('PaperBridge.exe', 'PaperBridge-CLI.exe')}}
    (bundle / 'build-info.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = output / f'PaperBridge-{__version__}-windows-x64.zip'
    temporary = archive.with_suffix('.zip.tmp')
    print(f'Writing {archive.name}', flush=True)
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as zip_file:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                zip_file.write(path, 'PaperBridge/' + path.relative_to(bundle).as_posix())
    temporary.replace(archive)
    checksum = sha256(archive)
    archive.with_suffix('.zip.sha256').write_text(f'{checksum}  {archive.name}\n', encoding='ascii')
    print(json.dumps({'folder': str(bundle), 'zip': str(archive), 'bytes': archive.stat().st_size,
                      'sha256': checksum}, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
