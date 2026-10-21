"""Download a fixed, attributed subset; no user documents leave this machine."""
from pathlib import Path
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/corpus"
BASE = "https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/main"


def download(url, path):
    if path.exists():
        return
    print(f"Download {path.name}", flush=True)
    with urllib.request.urlopen(url, timeout=90) as response:
        payload = response.read()
    path.write_bytes(payload)


def main():
    CORPUS.mkdir(parents=True, exist_ok=True)
    download(f"{BASE}/OmniDocBench.json", CORPUS / "OmniDocBench.json")
    data = json.loads((CORPUS / "OmniDocBench.json").read_text(encoding="utf-8"))
    chosen = [(247, "zh_single"), (252, "zh_mixed_layout"), (282, "zh_math"),
              (272, "bilingual_table")]
    for layout, name in [("single_column", "en_single_math"), ("double_column", "en_double_math"),
                         ("1andmore_column", "en_mixed_figure")]:
        candidates = [
            (i, x) for i, x in enumerate(data)
            if x["page_info"]["page_attribute"]["data_source"] == "academic_literature"
            and x["page_info"]["page_attribute"]["language"] == "english"
            and x["page_info"]["page_attribute"]["layout"] == layout
        ]
        if "figure" in name:
            candidates = [(i, x) for i, x in candidates if any(b["category_type"] == "figure" for b in x["layout_dets"])]
        else:
            candidates = [(i, x) for i, x in candidates if sum(b["category_type"] == "equation_isolated" for b in x["layout_dets"]) >= 2]
        chosen.append((candidates[0][0], name))
    manifest = []
    for index, name in chosen:
        entry = data[index]
        original = entry["page_info"]["image_path"]
        path = CORPUS / (name + Path(original).suffix)
        url = f"{BASE}/images/{urllib.parse.quote(original)}"
        download(url, path)
        manifest.append({
            "id": name, "file": path.name, "url": url, "dataset_index": index,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "annotation": entry,
        })
    (CORPUS / "benchmark_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Ready: {len(manifest)} annotated pages.")


if __name__ == "__main__":
    import urllib.parse
    main()
