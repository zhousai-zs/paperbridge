# PaperBridge：极简桌面 PDF → Markdown

本地论文转换工具。支持中文、英文、单栏、双栏、扫描版、图片、公式和表格。基于 MinerU 4.0.12，增加可追溯输出、批处理、断点续跑和完整性检查。

## 现在就用

**[下载 Windows x64 客户端（v0.3.0，约 2.07 GB）](https://github.com/zhousai-zs/paperbridge/releases/download/v0.3.0/PaperBridge-0.3.0-windows-x64.zip)** · [Release 说明与校验文件](https://github.com/zhousai-zs/paperbridge/releases/latest)

下载上面的完整便携 ZIP，解压后双击 **PaperBridge.exe**：选择一份或多份 PDF、选择输出文件夹、点击“开始转换”。输出位置会记住上次选择；更多选项默认折叠。

仓库文件列表和 GitHub 自动生成的 “Source code” 压缩包提供源码；客户端请下载 Release 中的 `PaperBridge-0.3.0-windows-x64.zip`。

便携包包含 Python 运行环境、标准／快速模式模型，使用时无需安装 Python、联网或 API Key。请保留整个程序文件夹，不能单独移动 EXE。操作与错误处理见 [桌面版使用说明](docs/桌面版使用说明.md)。

不提供 Web 服务、文档库、内置阅读和人工修订界面。完整保留核心转换、批量、断点续跑、原页与证据素材。进度按已完成分段更新；停止后保留完整检查点，可重试未完成项。旧结果与 `review/` 修订不被删除，可使用 `export` 导出阅读包。

源码用户按下方步骤安装后，可运行 `.venv\Scripts\pythonw.exe -m paperbridge.cli gui`；原 `论文工作台.cmd` 转向桌面启动器，`PDF转Markdown.cmd` 继续可用。每篇结果仍包含 **document.md**。

把整个结果文件夹交给 agent，告诉它：**先读 AGENT_README.md，再读 document.md；关键公式和表格请结合对应原图核对。**

默认 Standard 高质量模式。只有源码环境的首次安装需要联网下载依赖与模型；完整便携包已包含运行所需文件。

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

# 桌面入口；ui 为兼容别名，不再支持端口参数
.\.venv\Scripts\paperbridge.exe gui

# 导出 Agent 阅读包，合入旧 review/ 修订；ZIP 保存在结果目录之外
.\.venv\Scripts\paperbridge.exe export "D:\结果\论文目录" -o "D:\阅读包\论文.zip"
```

便携包用户将上面的 `.venv\Scripts\paperbridge.exe` 替换为 `PaperBridge-CLI.exe`。便携包默认在用户“文档”目录写结果，源码命令行仍默认项目 `outputs/`。桌面端始终使用自己记住的输出位置。

`--tier` 支持 basic、standard、advanced。已实测 basic 和 standard；advanced 接口可用但本项目未做质量验证，不作为推荐默认值。

长论文默认每 20 页保存一个检查点，单段超时 1800 秒，可用 `--chunk-size` 和 `--timeout` 调整。使用完全相同的参数重跑，会校验已完成段的文件哈希并复用。换页码或模式会产生独立结果目录。修改过的缓存会重新解析；不要在生成目录中保存唯一一份手工修改稿。

源码及本地 0.3.1 构建新增：失败、超时或图片校验不通过的分段，默认额外重试一次（`--retries 0` 禁用，最多 5 次额外重试）。完整分段继续复用，失败现场移入结果的 `history/`。图片引用检查同时覆盖 Markdown 和 JSON，并实际解码图片；校验失败不会显示为“完成”。原页图损坏会在重跑时重新生成。上方已发布的 0.3.0 下载包尚不含这些改进。

检查已有结果无需重新转换，默认只读；可选另存汇总报告：

```powershell
.\.venv\Scripts\python.exe -m paperbridge.cli verify "D:\阅读材料" --report "校验汇总.json"
```

新结果的 `report.json` 包含 `stage_seconds`、每段的尝试记录和引擎细分耗时；`integrity.json` 记录最终校验。`engine_log_timing` 中推理耗时来自引擎日志，`progress_seconds_approximate` 来自进度条的整数秒，不能当作高精度计时。识别准确性仍需人工对照。

退出码：`0` = 转换和机械检查通过；`2` = 已输出但有明确复核项；`1` = 失败；`130` = 用户停止。目录中的 `batch-report.json` 汇总每份结果。空白页、损坏页或未识别公式等问题不会被当作无问题成功。

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
| integrity.json | 最终图片解码、引用、页码、源文件和检查点校验；不代表内容识别准确 |
| history/ | 重跑前的报告，避免缓存运行时间覆盖首次记录 |

Markdown 图片文件在 `chunks/0000/images/` 等目录中。**移动或分享时保留整个结果文件夹**。合并单元格表格可能使用内嵌 HTML，这是为了保留结构。

本地论文、个人知识库和构建产物可归档到 `.local/`，该目录不纳入版本控制。源码转换默认创建根目录 `outputs/`；移动已有结果时保留完整目录。

如需归档到 Obsidian，可按篇保存 PDF、原文 MD 和后续译文 MD，用一份知识地图整理分类与论文关系。

## 实测范围与限制

原转换引擎的历史验证在 Windows、Python 3.12.14 上完成 **13 份 PDF、44 页**的两档转换，包括 Attention 全文、Raft 全文、7 页有人工标注的真实中英文扫描论文页、4 份可控单双栏回归样本。标准档的最终包通过完整性检查，并保留了 38 个公式/表格裁片。桌面便携版的本次验收另见 [桌面版验收](research/桌面版验收.md)，不将历史结果作为本次 EXE 验收。

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

如需手动下载或验证：

```powershell
.\.venv\Scripts\python.exe -c "from paperbridge.environment import configure; configure(); import subprocess; subprocess.run(['.venv/Scripts/mineru-kit.exe','models','verify','--tier','standard'],check=True)"
```

引擎和模型遵循各自上游许可；具体见安装包元数据和模型仓库。公开测试论文和基准数据仍属各自权利人，不包含在源码归档中。

## 复现测试

```powershell
# 不启动识别模型的程序测试
New-Item -ItemType Directory -Force .cache | Out-Null
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

## 桌面开发与便携包构建

在 Windows x64、Python 3.12 的完整推理环境中安装 `requirements-build.txt`，然后运行：

```powershell
# 模型已由 setup.ps1 准备好；构建过程不下载模型
.\.venv\Scripts\python.exe scripts\build_portable.py --models .cache\mineru\models

# 无模型的原生窗口检查及截图
.\.venv\Scripts\python.exe scripts\check_desktop.py
.\.venv\Scripts\python.exe scripts\check_package.py

# 实际 EXE 窗口与真实模型回归
.\.venv\Scripts\python.exe scripts\check_frozen_gui.py --exe dist\PaperBridge\PaperBridge.exe --output .cache\frozen-gui
.\.venv\Scripts\python.exe scripts\check_portable.py --bundle dist\PaperBridge --corpus tests\corpus
```

生成 `dist/PaperBridge/`、版本 ZIP 及 SHA-256 文件，包内 `build-info.json` 记录提交、EXE 和模型哈希。源码归档使用 `scripts/package_source.py`，只包含 Git 已跟踪文件。

CI 在 Windows 与 Linux 虚拟显示环境运行无模型测试及 wheel 检查；不启动 Web 或下载识别模型。真实模型检查清除开发环境变量、收紧 PATH 并使用不可达代理，但**不等同于全新无 Python 机器或操作系统级断网测试**；全新机器验收应单独记录。
