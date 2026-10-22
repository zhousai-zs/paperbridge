import argparse
import json
from pathlib import Path
import sys
import os

from .environment import ROOT, configure, default_output, prepare_engine, model_home


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == '--internal-worker':
        from .worker import main as worker_main
        worker_main(argv[1:])
        return 0
    parser = argparse.ArgumentParser(description="PaperBridge: PDF -> Markdown + images + audit report")
    commands = parser.add_subparsers(dest="command", required=True)
    conv = commands.add_parser("convert", help="Convert one or more PDFs / folders")
    conv.add_argument("inputs", nargs="+")
    conv.add_argument("-o", "--output", default=str(default_output()))
    conv.add_argument("--tier", choices=["basic", "standard", "advanced"], default="standard")
    conv.add_argument("--pages", default="all", help="1-based page range, e.g. 1-3,5")
    conv.add_argument("--ocr", choices=["auto", "txt", "ocr"], default="auto")
    conv.add_argument("--chunk-size", type=int, default=20)
    conv.add_argument("--timeout", type=float, default=1800)
    conv.add_argument("--recursive", action="store_true")
    conv.add_argument("--offline", action="store_true", help="Require local models; never download them")
    conv.add_argument('--progress-json', action='store_true', help=argparse.SUPPRESS)
    gui = commands.add_parser('gui', aliases=['ui'], help='Open the minimal desktop client')
    gui.add_argument('-o', '--output')
    export = commands.add_parser('export', help='Export an Agent reading ZIP, including legacy revisions')
    export.add_argument('result')
    export.add_argument('-o', '--output', required=True)
    doctor = commands.add_parser("doctor", help="Inspect runtime/model readiness")
    doctor.add_argument("--models", action="store_true")
    args = parser.parse_args(argv)
    if args.command in {'gui', 'ui'}:
        from .gui import launch
        return launch(args.output)
    if args.command == 'export':
        from .bundles import export_bundle
        try:
            source = Path(args.result).expanduser().resolve()
            destination = Path(args.output).expanduser().resolve()
            if destination.suffix.lower() != '.zip' or destination.is_relative_to(source):
                raise ValueError('请将阅读包保存为结果目录以外的 .zip 文件。')
            destination.parent.mkdir(parents=True, exist_ok=True)
            import uuid
            temporary = destination.with_name(destination.name + f'.{uuid.uuid4().hex}.tmp')
            try:
                export_bundle(source, temporary)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
            print(destination)
            return 0
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if getattr(args,'offline',False):
        os.environ['MINERU_MODEL_SOURCE']='local'
    configure()
    if args.command == "doctor":
        import importlib.metadata
        print(json.dumps({"python": sys.version, "executable": sys.executable,
                          "mineru": importlib.metadata.version("mineru"), "root": str(ROOT)}, indent=2))
        if args.models:
            prepare_engine()
            from mineru.model.download import verify_model_repo
            from mineru.model.registry import model_repos_for_tier
            checks = {repo.name: verify_model_repo(repo).ready for repo in model_repos_for_tier(
                'standard', small_backend='onnx', vlm_engine='llama-cpp')}
            print(json.dumps({'models': str(model_home()), 'ready': checks}, ensure_ascii=False, indent=2))
            return 0 if all(checks.values()) else 1
        return 0
    from .core import convert
    files = []
    for raw in args.inputs:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            files.extend(p for p in (path.rglob("*") if args.recursive else path.iterdir())
                         if p.is_file() and p.suffix.lower() == ".pdf"
                         and not p.resolve().is_relative_to(Path(args.output).expanduser().resolve()))
        else:
            files.append(path)
    files = sorted(set(files))
    if not files:
        print("No PDFs found.", file=sys.stderr)
        return 1
    results, failures, review, cancelled = [], 0, 0, False
    for source in files:
        try:
            target, report = convert(
                source, args.output, tier=args.tier, pages=args.pages, chunk_size=args.chunk_size,
                timeout=args.timeout, ocr=args.ocr,
                on_event=(lambda event: print('PAPERBRIDGE_EVENT ' + json.dumps(event), flush=True))
                if args.progress_json else None,
            )
            print(f"{report['status']}: {target / 'document.md'}", flush=True)
            review += report["status"] == "needs_review"
            results.append({"source": str(source), "output": str(target), "status": report["status"]})
        except (Exception, KeyboardInterrupt) as exc:
            from .processes import ConversionCancelled
            failures += 1
            print(f"FAILED: {source}: {exc}", file=sys.stderr, flush=True)
            results.append({"source": str(source), "status": "failed", "error": str(exc)})
            if isinstance(exc, (KeyboardInterrupt, ConversionCancelled)):
                cancelled = True
                break
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    from .core import write_json
    write_json(output / "batch-report.json", results)
    return 130 if cancelled else 1 if failures else 2 if review else 0


if __name__ == "__main__":
    sys.exit(main())
