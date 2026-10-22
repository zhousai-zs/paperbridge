# Third-party components

PaperBridge bundles a Python runtime, Tcl/Tk, MinerU 4.0.12, docvortex,
pypdfium2/PDFium, pypdf, Pillow, ONNX Runtime, mineru-llama-cpp/llama.cpp and their
runtime dependencies. Each component remains under its upstream license.
Available distribution license and notice files are included in `licenses/`.
The build records model file hashes in `build-info.json`.

Models are the existing upstream MinerU ONNX and MinerU2.5-Pro-2605-1.2B GGUF
resources. Model licenses and usage conditions remain those of their authors:

- https://huggingface.co/opendatalab/MinerU-4_models_onnx
- https://huggingface.co/jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF
- https://github.com/opendatalab/MinerU
- https://github.com/ggml-org/llama.cpp
- https://www.python.org/psf/license/

The portable package does not include user papers, conversion results or credentials.
