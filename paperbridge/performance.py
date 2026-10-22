"""Extract measured engine costs and approximate progress-bar timings from local logs."""
import re


STEPS = {'Layout Predict': 'layout', 'VLM Predict': 'visual_recognition',
         'MFR Predict': 'formula_recognition', 'OCR-det': 'text_detection',
         'OCR-rec Predict': 'text_recognition'}
BAR = re.compile(r'(Layout Predict|VLM Predict|MFR Predict|OCR-det|OCR-rec Predict):\s*(\d+)%[^\r\n]*?\|\s*(\d+)/(\d+)')


def log_progress(text):
    matches = list(BAR.finditer(text))
    if not matches:
        return {}
    match = matches[-1]
    return {'worker_step': STEPS[match[1]], 'step_completed': int(match[3]), 'step_total': int(match[4])}


def summarize_log(text):
    stages, active, last = {}, None, None
    for line in re.split(r'[\r\n]+', text):
        match = BAR.search(line)
        if not match:
            continue
        label = match[1]
        if label != active or int(match[2]) < 100:
            last = None
        active = label
        elapsed = re.search(r'\[(\d+:\d+(?::\d+)?)<', line)
        if int(match[2]) == 100 and elapsed:
            parts = [int(x) for x in elapsed[1].split(':')]
            seconds = 0
            for part in parts:
                seconds = seconds * 60 + part
            if last is None:
                stages[STEPS[label]] = stages.get(STEPS[label], 0) + seconds
            last = seconds
    return {'inference_seconds': round(sum(float(x) for x in re.findall(
                r'infer finished[^\r\n]*cost=([\d.]+)s', text)), 3),
            'model_loading_seconds': round(sum(float(x) for x in re.findall(
                r'get .*? predictor cost: ([\d.]+)s', text)), 3),
            'progress_seconds_approximate': stages}
