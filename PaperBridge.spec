# Build on Windows/Python 3.12 using the locked inference environment.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH)
datas = []
for package in ('mineru', 'docvortex', 'magika', 'jieba', 'mineru_llama_cpp'):
    datas += collect_data_files(package, excludes=['kit/**', 'doclib/**', 'router/**', 'server/**'])
for package in ('mineru', 'mineru-llama-cpp', 'docvortex', 'magika', 'pypdfium2'):
    datas += copy_metadata(package)
# These backends use dynamically selected modules and native ggml DLLs.
hidden = collect_submodules('mineru', filter=lambda name: not any(
    name == part or name.startswith(part + '.') for part in
    ('mineru.kit', 'mineru.cli', 'mineru.doclib', 'mineru.server', 'mineru.router')))
hidden += collect_submodules('docvortex')
hidden += ['paperbridge.worker', 'paperbridge.bundles', 'mineru_llama_cpp']
from PyInstaller.utils.hooks import collect_dynamic_libs
binaries = collect_dynamic_libs('mineru_llama_cpp')

a = Analysis([str(root / 'scripts' / 'desktop_entry.py')], pathex=[str(root)],
             binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=['gradio', 'gradio_client', 'torch', 'torchvision', 'transformers',
                       'vllm', 'lmdeploy', 'mlx', 'mineru.kit', 'mineru.doclib', 'mineru.router'],
             noarchive=False)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PaperBridge',
          console=False, disable_windowed_traceback=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PaperBridge-CLI', console=True)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name='PaperBridge')
