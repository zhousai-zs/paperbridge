"""Validate completed bundles and fixed layout fixtures. No inference needed."""
from pathlib import Path
import json
import re
import sys

from PIL import Image
from paperbridge.quality import check_markdown
from paperbridge.checkpoint import cache_valid, seal_chunk

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / (sys.argv[1] if len(sys.argv)>1 else 'outputs/standard')
    seal_legacy = '--seal-legacy' in sys.argv
    passed = []
    for report_file in sorted(output.glob('*/report.json')):
        root = report_file.parent
        report = json.loads(report_file.read_text(encoding='utf-8'))
        assert report['status'] in ('complete','needs_review'), root
        md = (root/'document.md').read_text(encoding='utf-8')
        assert not check_markdown(md, root), root
        page_ids = [int(x) for x in re.findall(r'<!-- source_page: (\d+) -->',md)]
        assert page_ids == report['selected_pages'], root
        for page in page_ids:
            with Image.open(root / f'pages/page-{page:04}.png') as image:
                image.verify()
        evidence = json.loads((root/'evidence.json').read_text(encoding='utf-8'))
        for item in evidence:
            with Image.open(root/item['image']) as image:
                assert image.width > 5 and image.height > 5
                image.verify()
        structure = json.loads((root/'document.json').read_text(encoding='utf-8'))
        def validate_images(value):
            if isinstance(value,dict):
                for k,v in value.items():
                    if k=='image_path' and v:
                        assert (root/v).is_file(), (root,v)
                    else:
                        validate_images(v)
            elif isinstance(value,list):
                for v in value:
                    validate_images(v)
        validate_images(structure)
        for chunk in (root/'chunks').iterdir():
            if seal_legacy and (chunk/'DONE').read_text(encoding='utf-8') == 'ok':
                raw_md=(chunk/'markdown.md').read_text(encoding='utf-8')
                assert not check_markdown(raw_md,chunk)
                json.loads((chunk/'middle_json.json').read_text(encoding='utf-8'))
                seal_chunk(chunk)
            assert cache_valid(chunk), chunk
        if report['source_name'].startswith('fixture_'):
            tags=[f'LEFT{i:02}' for i in range(12)]
            if '2col' in report['source_name']:
                tags += [f'RIGHT{i:02}' for i in range(12)]
            positions=[md.find(t) for t in tags]
            assert min(positions)>=0 and positions==sorted(positions), root
            expressions=re.findall(r'\$\$(.*?)\$\$',md,re.S)
            assert any('E=mc^2' in re.sub(r'[\s{}]','',x) for x in expressions), (root,expressions)
            assert any(e['type']=='table' for e in evidence), root
            assert '![' in md, root
        passed.append({'file':report['source_name'],'pages':len(page_ids),'evidence_crops':len(evidence)})
    assert len(passed)==13, f'Expected 13 validation PDFs; found {len(passed)}'
    (output/'integrity-checks.json').write_text(json.dumps({'passed':True,'documents':passed},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"PASS: {len(passed)} PDFs, {sum(x['pages'] for x in passed)} pages, {sum(x['evidence_crops'] for x in passed)} source crops; page order, image references, JSON assets and cache hashes verified.")


if __name__=='__main__':
    main()
