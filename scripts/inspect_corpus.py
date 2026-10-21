import collections
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
data = json.loads((root / "tests/corpus/OmniDocBench.json").read_text(encoding="utf-8"))
for i, entry in enumerate(data):
    info = entry["page_info"]
    attr = info["page_attribute"]
    if attr["data_source"] == "academic_literature" and attr["language"] != "english":
        print(i, info["image_path"], attr, dict(collections.Counter(x["category_type"] for x in entry["layout_dets"])))
        print(" ".join(x.get("text", "")[:80] for x in entry["layout_dets"] if x["category_type"] == "text_block")[:300])
