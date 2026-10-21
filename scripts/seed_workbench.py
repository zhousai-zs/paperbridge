"""Isolated browser-check server; uses generated fixtures and never runs inference."""
import argparse
import json
from pathlib import Path
import tempfile

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]


def seed():
    base = ROOT / '.cache' / 'ui-checks'
    base.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='run-', dir=base))
    output = run / 'outputs'
    root = output / 'sample-123'
    (root / 'pages').mkdir(parents=True)
    (root / 'evidence').mkdir()
    pdf = canvas.Canvas(str(root / 'source.pdf'))
    for page in range(1, 4):
        pdf.setFont('Helvetica', 18)
        pdf.drawString(45, 760, 'PaperBridge sample document')
        pdf.setFont('Helvetica', 12)
        pdf.drawString(45, 720, f'Original source page {page}')
        pdf.drawString(45, 690, 'E = mc^2   |   A = 12   B = 24')
        pdf.showPage()
        image = Image.new('RGB', (650, 860), 'white')
        draw = ImageDraw.Draw(image)
        draw.text((45, 60), f'PaperBridge sample - page {page}', fill='#203a33')
        draw.text((45, 100), 'Source PDF is preserved for comparison.', fill='#33463f')
        for y in range(170, 700, 35):
            draw.line((45, y, 580, y), fill='#d4ddce', width=2)
        image.save(root / 'pages' / f'page-{page:04}.png')
    pdf.save()
    Image.new('RGB', (300, 50), 'white').save(root / 'evidence' / 'equation.png')
    (root / 'document.md').write_text('# Sample\n\n<!-- source_page: 1 -->\n\n'
        '# 从论文到可核对的阅读材料\n\n这是一份用于程序验收的生成样本。\n\n'
        '$$E=mc^2$$\n\n| 指标 | 数值 |\n| --- | --- |\n| A | 12 |\n| B | 24 |\n\n'
        '<!-- source_page: 3 -->\n\n## 第三页\n\n检查非连续页码和公式原图。\n', encoding='utf-8')
    (root / 'report.json').write_text(json.dumps({'status': 'needs_review', 'source_name': '示例论文 · 公式与表格.pdf',
        'selected_pages': [1, 3], 'config': {'tier': 'standard'},
        'issues': [{'page': 3, 'code': 'formula_image_only'}]}, ensure_ascii=False), encoding='utf-8')
    (root / 'evidence.json').write_text(json.dumps([{'page': 1, 'type': 'equation', 'image': 'evidence/equation.png'}]), encoding='utf-8')
    (root / 'AGENT_README.md').write_text('先读 document.md，再核对 pages/。', encoding='utf-8')
    return output, run / 'state', root / 'source.pdf'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    output, state, source = seed()
    print(json.dumps({'source': str(source)}, ensure_ascii=False), flush=True)
    from paperbridge.workbench import create_app
    import uvicorn
    uvicorn.run(create_app(output, state, start_worker=False), host='127.0.0.1', port=args.port, log_level='warning')
