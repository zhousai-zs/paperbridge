import argparse
import json
from pathlib import Path
import sys
import os

from .environment import ROOT, configure


def main(argv=None):
    parser = argparse.ArgumentParser(description="PaperBridge: PDF -> Markdown + images + audit report")
    commands = parser.add_subparsers(dest="command", required=True)
    conv = commands.add_parser("convert", help="Convert one or more PDFs / folders")
    conv.add_argument("inputs", nargs="+")
    conv.add_argument("-o", "--output", default=str(ROOT / "outputs"))
    conv.add_argument("--tier", choices=["basic", "standard", "advanced"], default="standard")
    conv.add_argument("--pages", default="all", help="1-based page range, e.g. 1-3,5")
    conv.add_argument("--ocr", choices=["auto", "txt", "ocr"], default="auto")
    conv.add_argument("--chunk-size", type=int, default=20)
    conv.add_argument("--timeout", type=float, default=1800)
    conv.add_argument("--recursive", action="store_true")
    conv.add_argument("--offline", action="store_true", help="Require local models; never download them")
    doctor = commands.add_parser("doctor", help="Inspect runtime/model readiness")
    doctor.add_argument("--models", action="store_true")
    args = parser.parse_args(argv)
    if getattr(args,'offline',False):
        os.environ['MINERU_MODEL_SOURCE']='local'
    configure()
    if args.command == "doctor":
        import importlib.metadata
        print(json.dumps({"python": sys.version, "executable": sys.executable,
                          "mineru": importlib.metadata.version("mineru"), "root": str(ROOT)}, indent=2))
        if args.models:
            import subprocess
            return subprocess.call([str(Path(sys.executable).with_name("mineru-kit.exe" if sys.platform == "win32" else "mineru-kit")),
                                    "models", "show"])
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
    results, failures, review = [], 0, 0
    for source in files:
        try:
            target, report = convert(
                source, args.output, tier=args.tier, pages=args.pages, chunk_size=args.chunk_size,
                timeout=args.timeout, ocr=args.ocr,
            )
            print(f"{report['status']}: {target / 'document.md'}", flush=True)
            review += report["status"] == "needs_review"
            results.append({"source": str(source), "output": str(target), "status": report["status"]})
        except (Exception, KeyboardInterrupt) as exc:
            failures += 1
            print(f"FAILED: {source}: {exc}", file=sys.stderr, flush=True)
            results.append({"source": str(source), "status": "failed", "error": str(exc)})
            if isinstance(exc, KeyboardInterrupt):
                break
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    from .core import write_json
    write_json(output / "batch-report.json", results)
    return 1 if failures else 2 if review else 0


if __name__ == "__main__":
    sys.exit(main())
