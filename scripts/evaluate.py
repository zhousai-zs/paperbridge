"""Small transparent smoke benchmark, NOT the official OmniDocBench score."""
from pathlib import Path
from collections import Counter
from difflib import SequenceMatcher
import json
import re
import sys
import unicodedata

from bs4 import BeautifulSoup
from pylatexenc.latex2text import LatexNodes2Text

ROOT = Path(__file__).resolve().parents[1]


def norm(text):
    text = re.sub(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)',
                  lambda m: LatexNodes2Text().latex_to_text(m[1]), text, flags=re.S)
    text = unicodedata.normalize('NFKC', text).lower()
    return ''.join(c for c in text if c.isalnum())


def mathnorm(text):
    text = re.sub(r'\\(?:left|right|displaystyle|textstyle)\b', '', text)
    return re.sub(r'\s+', '', text).strip('$')


def content(block):
    value = block.get('content', '')
    if isinstance(value, str):
        return value
    return ' '.join(content(x) for x in value if isinstance(x, dict))


def evaluate(entry, job):
    md = (job / 'document.md').read_text(encoding='utf-8')
    md = md.split('<!-- source_page:', 1)[-1]
    text_only = re.sub(r'\$\$.*?\$\$|\$[^$]*\$', '', md, flags=re.S)
    normalized = norm(BeautifulSoup(text_only, 'html.parser').get_text(' '))
    all_normalized = norm(BeautifulSoup(md, 'html.parser').get_text(' '))
    gt = entry['annotation']['layout_dets']
    pages = json.loads((job / 'document.json').read_text(encoding='utf-8'))['pages']
    blocks = [b for p in pages for b in p['blocks']]
    anchors, positions = [], []
    for block in sorted(gt, key=lambda x: x['order'] if x.get('order') is not None else 10000):
        if block.get('ignore') or block['category_type'] not in ('text_block', 'title', 'figure_caption', 'table_caption'):
            continue
        text = re.sub(r'\$.*?\$', '', block.get('text', ''))
        text = norm(text)
        if len(text) < 16:
            continue
        for start in sorted(set([0, max(0, len(text)//2 - 8), max(0, len(text)-16)])):
            needle = text[start:start+16]
            position = normalized.find(needle)
            anchors.append({'text': needle, 'found': position >= 0})
            if position >= 0 and normalized.count(needle) == 1:
                positions.append(position)
    ordered = sum(a <= b for i,a in enumerate(positions) for b in positions[i+1:])
    pairs = len(positions) * (len(positions)-1) // 2
    formulas = [b.get('latex', b.get('text', '')) for b in gt
                if b['category_type'] == 'equation_isolated' and not b.get('ignore')]
    predicted = re.findall(r'\$\$(.*?)\$\$', md, flags=re.S)
    math_scores = []
    for formula in formulas:
        best = max((SequenceMatcher(None, mathnorm(formula), mathnorm(p), autojunk=False).ratio()
                    for p in predicted), default=0)
        math_scores.append({'reference': formula, 'best_normalized_similarity': round(best, 4)})
    cells = []
    reference_numeric = Counter()
    predicted_numeric = Counter()
    def numbers(html):
        result = Counter()
        for cell in BeautifulSoup(html, 'html.parser').find_all(['td','th']):
            text = cell.get_text(' ')
            text = re.sub(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)',lambda m:LatexNodes2Text().latex_to_text(m[1]),text,flags=re.S)
            text = re.sub(r'(?<=\d)\s+(?=\d)','',text)
            result.update(re.findall(r'[-−]?\d+(?:\.\d+)?', text.replace('−','-')))
        return result
    for block in blocks:
        if block['type'] == 'table':
            predicted_numeric.update(numbers(content(block)))
    for block in gt:
        if block['category_type'] != 'table' or block.get('ignore'):
            continue
        soup = BeautifulSoup(block.get('html', ''), 'html.parser')
        reference_numeric.update(numbers(block.get('html','')))
        for cell in soup.find_all(['td', 'th']):
            text = norm(cell.get_text())
            if text:
                cells.append({'value': text, 'found': text in all_normalized})
    figures_gt = sum(b['category_type'] == 'figure' and not b.get('ignore') for b in gt)
    figures_pred = sum(b['type'] in ('image', 'chart') for b in blocks)
    equation_regions = [b['bbox'] for b in blocks if b['type'] == 'equation' and b.get('bbox')]
    region_coverages = []
    info = entry['annotation']['page_info']
    for block in gt:
        if block['category_type'] != 'equation_isolated' or block.get('ignore'):
            continue
        poly = block['poly']
        box = [min(poly[0::2])/info['width'], min(poly[1::2])/info['height'],
               max(poly[0::2])/info['width'], max(poly[1::2])/info['height']]
        def coverage(pred):
            area = max(0,min(box[2],pred[2])-max(box[0],pred[0])) * max(0,min(box[3],pred[3])-max(box[1],pred[1]))
            return area / max(1e-12,(box[2]-box[0])*(box[3]-box[1]))
        region_coverages.append(max((coverage(b) for b in equation_regions), default=0))
    result = {
        'id': entry['id'], 'job': str(job.relative_to(ROOT)),
        'attributes': entry['annotation']['page_info']['page_attribute'],
        'text_anchor_recall': round(sum(x['found'] for x in anchors)/len(anchors),4) if anchors else None,
        'text_anchors_checked': len(anchors), 'text_anchors_found': sum(x['found'] for x in anchors),
        'reading_order_pair_accuracy': round(ordered/pairs,4) if pairs else None,
        'formula_count_reference': len(formulas), 'formula_count_output': len(predicted),
        'formula_region_recall_at_80pct_coverage': round(sum(x>=0.8 for x in region_coverages)/len(region_coverages),4) if region_coverages else None,
        'formula_latex_similarity_mean': round(sum(x['best_normalized_similarity'] for x in math_scores)/len(math_scores),4) if math_scores else None,
        'table_cell_value_recall': round(sum(x['found'] for x in cells)/len(cells),4) if cells else None,
        'table_cells_checked': len(cells), 'figure_count_reference': figures_gt,
        'table_numeric_token_recall': round(sum((reference_numeric & predicted_numeric).values())/sum(reference_numeric.values()),4) if reference_numeric else None,
        'figure_count_output': figures_pred,
        'missed_text_anchors': [a['text'] for a in anchors if not a['found']],
        'missed_table_cells': [c['value'] for c in cells if not c['found']],
        'formulas': math_scores,
    }
    return result


def main():
    output = ROOT / (sys.argv[1] if len(sys.argv) > 1 else 'outputs/validation')
    manifest = json.loads((ROOT / 'tests/corpus/benchmark_manifest.json').read_text(encoding='utf-8'))
    jobs = {}
    summary = []
    for path in output.glob('*/report.json'):
        report = json.loads(path.read_text(encoding='utf-8'))
        summary.append({k:report.get(k) for k in ('source_name', 'status', 'elapsed_seconds', 'counts', 'issues')})
        if report['status'] in ('complete', 'needs_review'):
            jobs[report['source_name']] = path.parent
    scores = [evaluate(x, jobs[x['pdf']]) for x in manifest if x['pdf'] in jobs]
    result = {'method': 'Exact 16-character alphanumeric anchors; pair order among found unique anchors; whitespace-normalized LaTeX string similarity (sensitive to split/merged equations); formula region coverage; table cell value substring recall (sensitive to split/merged cells); table numeric multiset recall ignoring within-cell digit spacing (does not validate row association). These are smoke checks, not CER/TEDS/CDM or a full official benchmark.', 'conversions': summary, 'annotated_pages': scores}
    (output / 'evaluation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([{k:v for k,v in s.items() if k not in ('attributes','formulas','missed_text_anchors','missed_table_cells')} for s in scores],ensure_ascii=False,indent=2))
    for source,job in jobs.items():
        if source.startswith('fixture_'):
            md = (job/'document.md').read_text(encoding='utf-8')
            tags = [f'LEFT{i:02}' for i in range(12)]
            if '2col' in source:
                tags += [f'RIGHT{i:02}' for i in range(12)]
            found = [md.find(t) for t in tags]
            print(source, 'anchors',sum(x>=0 for x in found),'/',len(tags),'in_order',all(a<b for a,b in zip(found,found[1:])))


if __name__ == '__main__':
    main()
