# GPU 公式识别可行性与性能验证

2026-10-23。基准代码基线为 `main / a46f907`。实验阶段只新增诊断脚本、测试和本文，没有修改当时的生产转换逻辑、默认四线程、模型权重、识别参数、现有 `.venv` 或已安装客户端；后续产品化实现与发行验收见 [0.4.0发布验收](0.4.0发布验收.md)。

## 结论

在本机 RTX 4070 Laptop GPU 上，只把 PP-FormulaNet-Plus-M 公式 ONNX 模型切到 CUDA，并关闭 TF32，具有明确的继续产品化价值：相同 9 页、四轮 CPU/CUDA 交错实验中，端到端测量中位数由 **132.76 秒降至 58.99 秒，减少 55.57%**；公式阶段中位数由约 **86.94 秒降至 21.78 秒，减少约 75%**。

四轮保存结果的正文、结构 JSON、表格、源文件、正文图片、原页、证据图和分段内容哈希完全一致。全部素材完整性检查通过，没有命中转换缓存。该结果支持进入“可选 GPU 后端”的实现与完整回归阶段。v0.4.0 随后采用桌面端自动尝试、失败回退 CPU 的策略，并单独完成生产路径与冻结 EXE 验收；本节本身仍只陈述基准阶段的证据。

## 环境与边界

- GPU：NVIDIA GeForce RTX 4070 Laptop GPU，8,188 MiB；驱动 610.47，驱动报告 CUDA UMD 13.3，compute capability 8.9。
- CPU 与 CUDA 运行时均为 ONNX Runtime 1.31.0。GPU wheel 为 CUDA 13.0 build；隔离运行库固定为 CUDA runtime/NVRTC 13.4.92、cuDNN 9.27.0.42、cuBLAS 13.8.0.4、cuFFT 12.4.0.43、cuRAND 10.4.4.72。
- GPU 依赖放在被 Git 忽略的 `.local/gpu-ort-overlay/`，约 1.84 GB；没有安装到项目 `.venv` 或系统目录。
- 只替换公式模型的会话工厂。版面、OCR 和表格 ONNX 路径保持原配置；独立 VLM/Vulkan 路径不变。
- CUDA provider 保留 CPU fallback，`use_tf32=0`，显存 arena 上限 5 GiB，cuDNN 算法搜索为 `HEURISTIC`，拷贝使用默认 stream。
- 官方 ONNX Runtime 文档说明 CUDA provider 会把支持的子图交给 CUDA，不支持的节点交给后续 CPU provider；TF32 默认开启，本轮显式关闭以收紧数值边界。兼容性表要求 ORT 的 CUDA/cuDNN 主版本匹配运行库。本轮依照该边界构建隔离环境：<https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html>。

## 对照设计

页组与 CPU 线程实验相同：DiTTo 19～21 页、FAPE-IR 4～6 页、HarnessIR 2～4 页，共 9 个不同页面。每轮固定 Standard、OCR auto、每段 2 页、批次内复用引擎、ONNX CPU intra-op 4 / inter-op 1。先用无关的一页样本启动基础引擎，再测 9 页；首次公式模型加载仍包含在测量区间内。

顺序固定为 CPU → CUDA → CUDA → CPU。每轮使用独立进程、新输出目录和相同输入，不联网取模型。GPU 状态由 `nvidia-smi` 约每 0.5 秒采样；这是全卡数据，不是单进程显存归因。

## 性能结果

| 轮次 | 后端 | 9 页测量 | 公式阶段 | 版面阶段 | 峰值总显存 |
| --- | --- | ---: | ---: | ---: | ---: |
| 01 | CPU | 140.85 秒 | 87.92 秒 | 6.32 秒 | 3,321 MiB |
| 02 | CUDA | 59.95 秒 | 22.23 秒 | 6.18 秒 | 5,425 MiB |
| 03 | CUDA | 58.02 秒 | 21.33 秒 | 6.37 秒 | 5,425 MiB |
| 04 | CPU | 124.67 秒 | 85.96 秒 | 6.21 秒 | 3,316 MiB |

CPU 两轮中位数为 132.7605 秒，CUDA 两轮中位数为 58.985 秒。两轮 CPU 本身相差约 16 秒，说明机器状态仍有波动；但两轮 CUDA 只相差约 1.94 秒，且均显著快于两轮 CPU，因此本样本中的方向和量级较清楚。该比例仍不是跨机器或完整论文的承诺。

公式阶段中位数减少约 65.16 秒，端到端中位数减少约 73.78 秒。阶段计时可能包含准备与惰性初始化，不能把差值解释为纯 GPU 算子时间；它仍表明主要收益来自既有最大瓶颈，而不是版面或文件保存。

## 实际算子分配

两轮 CUDA 都确认公式会话的 provider 顺序为 `CUDAExecutionProvider, CPUExecutionProvider`，不是只看可用 provider 列表。首个真实公式批次的 ONNX profile 在两轮中完全一致地记录：

| CUDA 轮次 | CUDA 节点事件 | CUDA 归属时长 | CPU 节点事件 | CPU 归属时长 |
| --- | ---: | ---: | ---: | ---: |
| 02 | 6,329 | 1.528 秒 | 4,242 | 0.067 秒 |
| 03 | 6,329 | 1.590 秒 | 4,242 | 0.071 秒 |

CUDA 的主要时长来自 Conv、Loop、Add 和 MatMul；CPU fallback 主要是 Unsqueeze、Concat、Gather 等形状与序列操作。事件数包含自回归 Loop 的重复执行，不等于静态图节点数；该 profile 只覆盖每轮第一个公式批次，不能当作整轮时间占比。它足以证明 CUDA 确实执行了核心计算，同时记录了真实 CPU fallback。

## 资源占用

两轮 CUDA 启动前全卡显存分别约 1,333 / 1,361 MiB，峰值均为 5,425 MiB；按 8,188 MiB 总量计算，采样峰值时仍约有 2.7 GiB 余量。两轮 CPU 峰值约 3.32 GiB，说明原有 VLM/Vulkan 等路径本来就在使用 GPU；公式 CUDA 在本样本中额外增加约 2.1 GiB 峰值占用。

GPU 与 CPU 轮次都观察到 100% 瞬时利用率，峰值功耗约 78 W 与 75 W。这里只记录峰值，没有能量积分，也没有隔离桌面和其他进程，因此不据此比较能效。8 GB 显存对当前单任务足够，但多任务并发、其他 GPU 应用和更大 batch 仍可能触发压力。

## 质量与完整性

- 四轮共处理 36 个选定页次，来自相同 9 个页面，另有 4 页基础预热；不能表述为 36 个不同页面。
- 所有分段都为新推理，全部素材完整性检查通过；每轮在同一 worker 中跨篇、跨段复用模型。
- 四轮全部可审计产物的原始 SHA-256 对照为零差异，没有做空白归一化或内容过滤。
- 输入 PDF 的三个哈希和 15 个模型/配置文件哈希在实验后复核不变。
- DiTTo 在四轮中都保持原有 `needs_review` 状态；哈希一致不代表其公式语义已人工认证，也不证明任意 PDF 都会保持相同输出。

## 产品化决策

下一步实现**显式可选的公式 GPU 后端**，继续保持 CPU 为默认值。建议边界如下：

1. 提供 `cpu / cuda` 明确选择；CUDA 启动时校验驱动、DLL、实际 provider 顺序和一次真实推理，不能只检查 `get_available_providers()`。
2. TF32 默认关闭，保持本轮质量边界。CUDA 初始化或运行失败时记录具体原因并回退 CPU，报告中写明实际后端，避免静默伪装成 GPU 成功。
3. GPU 运行库约增加 1.84 GB，优先设计为独立 GPU 构建或可选组件，不直接膨胀现有 CPU 客户端。
4. 先跑完整 24 篇回归、关键公式人工抽查、显存压力、取消/超时和打包测试；通过后再决定本机是否改为自动选择 GPU。

本轮不叠加多论文并行、换模型、FP16/量化、TF32 或降低分辨率，以便将收益和质量变化归因到单一后端切换。

## 复现与证据

诊断入口为 `scripts/benchmark_gpu.py`。它拒绝已有输出目录，固定四轮交错顺序，验证实际 CUDA profile，逐文件比较输出，并在取消或 30 分钟超时时清理子进程。

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_gpu.py `
  --cases .local\cpu-tuning-20261023\cases.json `
  --gpu-overlay .local\gpu-ort-overlay `
  -o .local\gpu-benchmark-rerun
```

本机原始证据保存在被忽略的 `.local/gpu-benchmark-20261023/`：`environment.json` 固定输入、模型、运行库和基线提交；`benchmark.json` 保存四轮结果、哈希对照与资源峰值；每个 CUDA 轮次的 `cuda-profile/` 保存实际 provider、选项和首批公式 profile。最小加载探测保存在 `.local/gpu-probe.json` 与 `.local/gpu-probe-profile/`。
