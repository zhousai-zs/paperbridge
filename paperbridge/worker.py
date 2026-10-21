"""One isolated inference job. Invoked by the CLI with a timeout."""
import argparse
from pathlib import Path
from .environment import configure


def main():
    p = argparse.ArgumentParser()
    p.add_argument("pdf")
    p.add_argument("output")
    p.add_argument("--tier", required=True)
    p.add_argument("--pages", required=True)
    p.add_argument("--ocr", default="auto")
    args = p.parse_args()
    configure()
    from mineru.parser import parse
    from mineru.parser.writer import FileBasedDataWriter
    from mineru.config import config
    if config.model.vlm.server_url:
        raise RuntimeError('PaperBridge local mode refuses a remote VLM server. Clear MINERU_MODEL_VLM_SERVER_URL / model.vlm.server_url.')
    result = parse(
        args.pdf, tier=args.tier, page_range=args.pages, ocr_mode=args.ocr,
    )
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    result.save(FileBasedDataWriter(str(output)))
    from .checkpoint import seal_chunk
    seal_chunk(output)


if __name__ == "__main__":
    main()
