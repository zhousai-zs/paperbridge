"""Create scanned benchmark PDFs plus deterministic layout regression fixtures."""
from pathlib import Path
import json
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.utils import ImageReader

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/corpus"


def main():
    manifest = json.loads((CORPUS / "benchmark_manifest.json").read_text(encoding="utf-8"))
    for entry in manifest:
        image = Image.open(CORPUS / entry["file"]).convert("RGB")
        # Raster source is intentional: this exercises OCR rather than a hidden text layer.
        pdf = CORPUS / f"{entry['id']}.pdf"
        if not pdf.exists():
            c = canvas.Canvas(str(pdf), pagesize=(image.width * 72 / 200, image.height * 72 / 200), invariant=1)
            c.drawImage(ImageReader(image), 0, 0, width=image.width * 72 / 200, height=image.height * 72 / 200)
            c.save()
        entry["pdf"] = pdf.name
    (CORPUS / "benchmark_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    for language in ("en", "zh"):
        for columns in (1, 2):
            name = f"fixture_{language}_{columns}col"
            if (CORPUS / f'{name}.pdf').exists():
                continue
            c = canvas.Canvas(str(CORPUS / f"{name}.pdf"), pagesize=(595, 842), invariant=1)
            font = "Helvetica" if language == "en" else "STSong-Light"
            c.setFont(font, 18)
            c.drawString(50, 790, "Document Layout Regression" if language == "en" else "论文版面转换测试")
            for col in range(columns):
                x = 50 + col * 255
                c.setFont(font, 11)
                for i in range(12):
                    tag = f"{'LEFT' if col == 0 else 'RIGHT'}{i:02}"
                    line = f"{tag} The reading order must stay correct." if language == "en" else f"{tag} 中文内容应当保持正确阅读顺序。"
                    c.drawString(x, 740 - 23 * i, line)
            c.setFont("Times-Italic", 15)
            c.drawString(70, 400, "E = mc")
            c.setFont("Times-Italic", 9)
            c.drawString(116, 408, "2")
            c.setFont("Helvetica", 10)
            c.drawString(270, 400, "(1)")
            c.rect(60, 240, 190, 110)
            c.line(75, 255, 220, 325)
            c.setFont(font, 10)
            c.drawString(60, 225, "Figure 1: Vector line chart" if language == "en" else "图1：矢量折线示意图")
            for y in [180, 160, 140, 120]:
                c.line(320, y, 520, y)
            c.drawString(330, 166, "Method          Score")
            c.drawString(330, 146, "Baseline        0.85")
            c.drawString(330, 126, "Proposed       0.95")
            c.showPage()
            c.save()
    print("Created 7 scanned benchmark PDFs and 4 deterministic fixtures.")


if __name__ == "__main__":
    main()
