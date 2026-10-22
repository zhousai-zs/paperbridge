"""One isolated inference job. Invoked by the CLI with a timeout."""
import argparse
import time
from pathlib import Path
from .environment import prepare_engine


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("pdf")
    p.add_argument("output")
    p.add_argument("--tier", required=True)
    p.add_argument("--pages", required=True)
    p.add_argument("--ocr", default="auto")
    args = p.parse_args(argv)
    from .core import write_json
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    timings = {}
    def stage(name):
        write_json(output / 'worker-status.json', {'worker_step': name})
        return time.monotonic()
    step = stage('loading_engine')
    prepare_engine()
    from mineru.parser import parse
    from mineru.parser.writer import FileBasedDataWriter
    from mineru.config import config
    if config.model.vlm.server_url:
        raise RuntimeError('PaperBridge local mode refuses a remote VLM server. Clear MINERU_MODEL_VLM_SERVER_URL / model.vlm.server_url.')
    timings['engine_import_seconds'] = round(time.monotonic() - step, 3)
    step = stage('inference')
    result = parse(
        args.pdf, tier=args.tier, page_range=args.pages, ocr_mode=args.ocr,
    )
    timings['parse_seconds'] = round(time.monotonic() - step, 3)
    step = stage('saving_assets')
    result.save(FileBasedDataWriter(str(output)))
    timings['save_seconds'] = round(time.monotonic() - step, 3)
    step = stage('checking_assets')
    from .checkpoint import seal_chunk
    seal_chunk(output)
    timings['seal_seconds'] = round(time.monotonic() - step, 3)
    timings['total_seconds'] = round(time.monotonic() - started, 3)
    write_json(output / 'engine-timing.json', timings)
    stage('finished')


if __name__ == "__main__":
    main()
