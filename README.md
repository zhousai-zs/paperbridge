# PaperBridge：论文 PDF → Agent Markdown

本地论文转换工具。支持中文、英文、单栏、双栏、扫描版、图片、公式和表格。基于 MinerU 4.0.12，增加可追溯输出、批处理、断点续跑和完整性检查。

## 现在就用

首次使用先按下方“安装到 Windows 机器”准备环境与模型。已经安装的机器直接双击 **论文工作台.cmd**，浏览器会打开本机中文工作台。

1. 拖入或选择一份、多份 PDF，选择标准/快速模式及页码，点击“开始转换”。
2. 在队列中查看分段进度；失败后可查看运行记录并重试，自动复用完整检查点。
3. 在文档库打开论文，逐页对照原图、公式与表格裁片；可切换到 Markdown 编辑，保存本页修订。
4. 点击“导出阅读包”，将 ZIP 解压后的整个目录交给 Agent。阅读包已合入保存的修订，并保留原始识别稿。

工作台只监听 `127.0.0.1`，不上传论文，不使用外部脚本、字体或公式服务。运行时保留启动窗口；关闭工作台服务会中断当前任务，重新打开后可重试。关闭浏览器标签不会停止转换。默认地址为 `http://127.0.0.1:8765`，端口占用时可运行 `论文工作台.cmd --port 8766`。

人工修订存放在结果目录的 `review/`，每次覆盖保存前都会留存历史。转换重跑不会覆盖修订；若识别稿发生变化，阅读器会提示重新核对。导出包中的 `document.md` 合入修订，`original-document.md` 为原始识别稿，`START_HERE.md` 为交接入口。导出会校验 Markdown 图片引用；缺图时先修订再导出。导出包省略运行日志和断点文件，不用于续跑。

原有 **PDF转Markdown.cmd** 和命令行继续可用。转换完成后，可在 `outputs/论文名-哈希/` 找到 **document.md**。

把整个结果文件夹交给 agent，告诉它：**先读 AGENT_README.md，再读 document.md；关键公式和表格请结合对应原图核对。**

默认 Standard 高质量模式。转换在本机完成，不需要 API Key。第一次安装要联网下载依赖与模型；安装后可以完全离线运行。

## 命令行

在本项目目录打开 PowerShell，不需要激活环境：

```powershell
# 单篇，默认高质量模式；禁止模型联网下载
.\.venv\Scripts\paperbridge.exe convert "D:\论文\示例.pdf" --offline

# 整个文件夹；每个文件独立报告，某份失败后继续处理其余文件
.\.venv\Scripts\paperbridge.exe convert "D:\论文" --offline

# 只转换指定页码（从 1 开始），适合先试几页
.\.venv\Scripts\paperbridge.exe convert "D:\论文\示例.pdf" --pages 1-3,5 --offline

# 快速模式，数学和中文文字公式需特别核对
.\.venv\Scripts\paperbridge.exe convert "D:\论文\示例.pdf" --tier basic --offline

# 自定义输出目录，递归搜索子目录
.\.venv\Scripts\paperbridge.exe convert "D:\论文" -o "D:\阅读材料" --recursive --offline

# 检查环境与模型
.\.venv\Scripts\paperbridge.exe doctor --models

# 启动本地工作台；可用 -o 指定另一份文档库
.\.venv\Scripts\python.exe -m paperbridge.cli ui
```

`--tier` 支持 basic、standard、advanced。已实测 basic 和 standard；advanced 接口可用但本项目未做质量验证，不作为推荐默认值。

长论文默认每 20 页保存一个检查点，单段超时 1800 秒，可用 `--chunk-size` 和 `--timeout` 调整。使用完全相同的参数重跑，会校验已完成段的文件哈希并复用。换页码或模式会产生独立结果目录。修改过的缓存会重新解析；不要在生成目录中保存唯一一份手工修改稿。

退出码：`0` = 转换和机械检查通过；`2` = 已输出但有明确复核项；`1` = 失败。目录中的 `batch-report.json` 汇总每份结果。空白页、损坏页或未识别公式等问题不会被当作无问题成功。

## 输出包含什么

| 文件 | 用途 |
| --- | --- |
| document.md | Agent 主阅读文件，UTF-8；保留页码标记、图片相对链接、LaTeX 公式、Markdown/HTML 表格 |
| AGENT_README.md | 阅读约定与已知限制 |
| source.pdf | 原文件快照；输入不会被修改 |
| pages/ | 每页原图，用于查看遗漏内容、核对符号和阅读顺序 |
| evidence/ + evidence.json | 检测到的行间公式、表格裁片，以及对应页码、位置、识别内容 |
| document.json | 按页有序的块结构，图片路径相对于结果目录 |
| chunks/ | 引擎原始输出、素材、日志、带哈希的检查点 |
| report.json | 页面覆盖、图片引用、状态、模式和运行时间；`accuracy_verified` 默认为 false |
| history/ | 重跑前的报告，避免缓存运行时间覆盖首次记录 |
| review/ | 工作台人工修订及修订历史，独立于原始识别稿保存 |

Markdown 图片文件在 `chunks/0000/images/` 等目录中。**移动或分享时保留整个结果文件夹**。合并单元格表格可能使用内嵌 HTML，这是为了保留结构。

## 实测范围与限制

在 Windows、Python 3.12.14 上完成 **13 份 PDF、44 页**的两档转换，包括 Attention 全文、Raft 全文、7 页有人工标注的真实中英文扫描论文页、4 份可控单双栏回归样本。标准档的最终包通过完整性检查，并保留了 38 个公式/表格裁片。

详细指标、例子和未覆盖内容见 [测试报告](research/测试报告.md)，选型依据见 [工具调研](research/工具调研.md)。仓库不包含模型、论文、原始基准标注和转换结果；复现时由脚本下载或自行提供输入。

本工具不承诺任意论文零误差。复杂矩阵、上下标、扫描噪声、跨页表格、特殊字体仍可能误识别。页间段落按页保留，跨页表格不自动合并。行间公式有独立原图，行内公式通过整页图核对。纯文本 agent 无法仅凭图片链接理解图中全部内容，需使用支持图像的 agent。高度复杂、手写、极低分辨率、竖排、加密和损坏 PDF 不在当前质量保证范围；加密文件会明确拒绝。

## Python 调用

```python
from paperbridge.core import convert

folder, report = convert("paper.pdf", "outputs", tier="standard")
print(folder / "document.md", report["status"])
```

## 安装到 Windows 机器

安装 Python 3.12，将仓库克隆或下载到本机，在仓库目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Python "C:\Python312\python.exe"
```

脚本创建独立环境、安装 `requirements.lock.txt` 中的实测版本，并从 MinerU 官方支持的 ModelScope 源下载模型。模型和临时文件放在项目 `.cache/`，可用 `PAPERBRIDGE_CACHE` 指定另一磁盘。项目缓存是数 GB 级，预留 6 GB 以上空间更合适。锁文件面向本次 Windows/Python 环境；其他系统需另做安装验证。

从旧版本更新后再次运行安装脚本即可更新包元数据与工作台依赖。已安装的模型可以复用。工作台任务记录和上传副本位于 `.cache/workbench/`，按输出目录隔离。手动清理这里的上传文件后，旧任务不能直接重试，需要重新添加原始 PDF。

如需手动下载或验证：

```powershell
.\.venv\Scripts\python.exe -c "from paperbridge.environment import configure; configure(); import subprocess; subprocess.run(['.venv/Scripts/mineru-kit.exe','models','verify','--tier','standard'],check=True)"
```

引擎和模型遵循各自上游许可；具体见安装包元数据和模型仓库。公开测试论文和基准数据仍属各自权利人，不包含在源码归档中。

## 复现测试

```powershell
# 不启动识别模型的程序测试
.\.venv\Scripts\python.exe -m pytest -q --basetemp .cache\pytest

# 下载基准图像并生成扫描 PDF
.\.venv\Scripts\python.exe scripts\fetch_benchmark.py
.\.venv\Scripts\python.exe scripts\make_fixtures.py

# 两篇原始论文也需放到 tests/corpus/，下载链接见调研文档
.\.venv\Scripts\paperbridge.exe convert tests\corpus --tier standard --offline -o outputs\standard
.\.venv\Scripts\python.exe scripts\validate_outputs.py outputs/standard
.\.venv\Scripts\python.exe scripts\evaluate.py outputs/standard
```

评估脚本是透明的小规模核对，不是官方 OmniDocBench 全量评测，也不把字符串相似度称为公式语义准确率。

## GitHub 协作与自动检查

需求和问题通过 Issue 记录；每次改动在 `codex/` 分支上开发，通过 PR 检查后合入 `main`。版本变化见 [CHANGELOG.md](CHANGELOG.md)。

本次工作台的验证范围与真实模型检查结果见 [工作台验收](research/工作台验收.md)。

GitHub Actions 分别执行 Windows/Linux Python 3.12 程序测试，以及离线浏览器流程测试；它们不下载模型，不证明真实论文识别质量。涉及引擎、分段或文档组装的修改仍需按上面的真实论文流程回归，并记录结果。

本地浏览器检查需要 Node 和 Playwright：`node scripts/check_workbench.cjs`；设置 `PAPERBRIDGE_PYTHON` 可指定 Python 路径。该脚本使用 `.cache/ui-checks/` 中的独立测试文档，不修改日常文档库。源码归档使用 `scripts/package_source.py`。
