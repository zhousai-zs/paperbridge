"""Isolated or batch-scoped inference; document state stays local to each job."""
import argparse
import gc
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from .environment import prepare_engine


def resident_bytes():
    """Current working set, used only to recycle between completed chunks."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        info = Counters()
        info.cb = ctypes.sizeof(info)
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(info), info.cb):
            return info.WorkingSetSize
    else:
        try:
            return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
        except (OSError, ValueError, IndexError):
            pass
    return None


def execute(pdf, output, *, tier, pages, ocr='auto', job_index=1):
    from .core import write_json
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    timings = {'worker_pid': os.getpid(), 'worker_job_index': job_index,
               'engine_reused': job_index > 1}
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
        pdf, tier=tier, page_range=pages, ocr_mode=ocr,
    )
    timings['parse_seconds'] = round(time.monotonic() - step, 3)
    step = stage('saving_assets')
    result.save(FileBasedDataWriter(str(output)))
    timings['save_seconds'] = round(time.monotonic() - step, 3)
    del result
    step = stage('checking_assets')
    from .checkpoint import seal_chunk
    seal_chunk(output)
    timings['seal_seconds'] = round(time.monotonic() - step, 3)
    step = time.monotonic()
    gc.collect()
    timings['release_document_seconds'] = round(time.monotonic() - step, 3)
    timings['worker_rss_bytes'] = resident_bytes()
    timings['total_seconds'] = round(time.monotonic() - started, 3)
    write_json(output / 'engine-timing.json', timings)
    stage('finished')


def serve(directory):
    from .core import write_json
    parent_pid = int(os.environ.get('PAPERBRIDGE_ENGINE_OWNER_PID', os.getppid()))

    def watch_parent():
        # Avoid reading stdin on another thread: native Windows inference also
        # manipulates CRT descriptors while initializing its log streams.
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, parent_pid)
            if not handle:
                return
            try:
                if kernel.WaitForSingleObject(handle, 0xFFFFFFFF) != 0:
                    return
            finally:
                kernel.CloseHandle(handle)
        else:
            while True:
                try:
                    os.kill(parent_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(.25)
        os._exit(0)

    threading.Thread(target=watch_parent, daemon=True).start()
    write_json(Path(directory) / 'ready.json', {'pid': os.getpid()})
    for job_index, line in enumerate(sys.stdin, 1):
        request = json.loads(line)
        output = Path(request['output'])
        output.mkdir(parents=True, exist_ok=True)
        response = {'id': request['id']}
        try:
            execute(request['pdf'], output, tier=request['tier'], pages=request['pages'],
                    ocr=request['ocr'], job_index=job_index)
        except Exception as exc:
            traceback.print_exc()
            response['error'] = f'{type(exc).__name__}: {exc}'
        sys.stdout.flush()
        sys.stderr.flush()
        write_json(Path(directory) / 'response.json', response)
        if 'error' in response:
            return 1
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == '--serve':
        return serve(args[1])
    p = argparse.ArgumentParser()
    p.add_argument('pdf')
    p.add_argument('output')
    p.add_argument('--tier', required=True)
    p.add_argument('--pages', required=True)
    p.add_argument('--ocr', default='auto')
    options = p.parse_args(args)
    execute(options.pdf, options.output, tier=options.tier, pages=options.pages, ocr=options.ocr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
