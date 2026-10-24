# 论文 Markdown 翻译：GitHub 方案调研与接入建议

调研日期：2026-10-22。对象：`D:\KnowledgeBase\papers\图像复原` 中已有的 25 篇论文。目标：中文忠实、自然、易懂，保留公式、图片、表格与原页对应关系，同目录生成 `译文.md`。

## 建议采用的路线

直接翻译现有 `document.md`，继续复用已有图片和原页。增加一个独立的翻译环节：**结构保护 → 术语与上下文准备 → 翻译 → 对照原文审校 → 恢复结构 → 校验与保存**。

最值得组合借鉴的是：

- **格式处理：qxsch/MarkdownTranslator**。按原文位置提取可翻译文本、保护非正文内容，翻译后回填原文件，并校验结构。
- **中文质量：andrewyng/translation-agent**。先翻译，再对照原文指出问题，最后根据问题修订。
- **批量能力参考：bilingual_book_maker**。已有 Markdown、术语表、上下文和续跑机制，但当前 MD 路径需要补足论文公式与表格处理。

这是针对本库输入的适配建议，不是已经通过中文论文翻译质量测试的排行榜。此次完成了仓库文档／关键代码核查和离线结构探测；没有安装这些项目、调用翻译服务、产生正式译文或实测 Obsidian 渲染。

## 仓库比较

| 项目 | 已核实的能力 | 对本任务的限制 | 建议定位 |
| --- | --- | --- | --- |
| [MarkdownTranslator](https://github.com/qxsch/MarkdownTranslator) | 带位置的语法树、文本回填；数学与路径保护；Markdown／HTML 单元格处理；术语和审校；配置中已有简体中文 | 当前服务接入围绕 Azure；主要公开评测集中在德语、法语等资料，不能据此证明中文论文效果 | 格式保护的首选参考；已有 Azure 条件时可优先原样试用 |
| [bilingual_book_maker](https://github.com/yihong0618/bilingual_book_maker) | 直接输入 MD；支持只输出译文、上下文、术语表与恢复中断任务 | 当前 MD 加载器会整块跳过 Markdown 表格；公式与 HTML 缺少与链接相同的硬性保护，需补强 | 多后端与批量翻译基础，不直接对本库全量运行 |
| [translation-agent](https://github.com/andrewyng/translation-agent) | 初译、反思审校、修订三步流程，可定制风格与术语 | 项目明确定位演示；没有本任务需要的完整 Markdown／数学结构保护 | 借鉴审校步骤，不把它单独当论文 MD 翻译器 |
| [GPT Academic](https://github.com/binary-husky/gpt_academic) | 学术翻译、Markdown 项目批量翻译、多模型接口 | 检查到的 MD 插件把文本按长度切分，主要用提示词要求保留格式；没有在这条路径看到本任务要求的结构回填与等价校验 | 现成试译入口之一，需额外验收格式 |
| [Parallel Translator](https://github.com/llbbaagg66/parallel-translator) | Obsidian 科研选区翻译、术语映射、附近上下文；有科学符号占位与恢复校验 | 主要处理选区、侧栏和反向同步，未提供本任务所需的 25 篇整库译文交付流程 | 日常阅读辅助；生成正式译文仍需单独流程 |
| [PDFMathTranslate-next](https://github.com/PDFMathTranslate/PDFMathTranslate-next)／[BabelDOC](https://github.com/funstory-ai/BabelDOC) | 面向论文 PDF 的翻译、双语对照与版式保留；BabelDOC 提供术语能力 | 主产物是 PDF，不直接替代现有 Obsidian Markdown 译文流程 | 后续若要双语 PDF，可作为另一种输出 |

补充查看了 [SuperTranslate](https://github.com/asimfish/super_translate)：其重点是 PDF 翻译、学术术语与版式核验，也属于 PDF 路线。本库已经有 MD，因此不建议为得到 `译文.md` 再走一遍 PDF 解析。

## 关键代码核查

### MarkdownTranslator：结构处理最贴近本库要求

它不是让模型重新输出整篇 Markdown，而是抽取带位置的可翻译片段，把格式与保护内容表示为标记，再回填原始文本。代码覆盖 Markdown 表格单元格、HTML 内容、图片与链接；组装时比较结构，无法通过的块可以回退并报告。

来源：[文本提取](https://github.com/qxsch/MarkdownTranslator/blob/d1d89a93303371340670265c381b6fb20e57ea88/src/markdown/extract.ts)、[组装与结构比较](https://github.com/qxsch/MarkdownTranslator/blob/d1d89a93303371340670265c381b6fb20e57ea88/src/assemble.ts)、[片段校验](https://github.com/qxsch/MarkdownTranslator/blob/d1d89a93303371340670265c381b6fb20e57ea88/src/translate/validate.ts)。

简体中文配置已存在；接入当前围绕 Azure。数学识别有保守规则，实际适配时仍需检查 MinerU 输出中的特殊空格和不规范分隔符，不能只凭仓库“保留数学”的描述承诺所有论文可直接通过。

来源：[中文配置](https://github.com/qxsch/MarkdownTranslator/blob/d1d89a93303371340670265c381b6fb20e57ea88/config/languages.json)、[数学跨度识别](https://github.com/qxsch/MarkdownTranslator/blob/d1d89a93303371340670265c381b6fb20e57ea88/src/markdown/mathSpans.ts)、[服务配置](https://github.com/qxsch/MarkdownTranslator#configuration-reference)。

### bilingual_book_maker：支持 MD，不等于论文结构已经全覆盖

检查的 MD 加载器做了以下处理：

- 标题、段落进入翻译；Markdown 表格、围栏代码、独立图片行与页面注释原样通过。
- `_protect_inline_markdown` 保护 Markdown 链接、图片、行内代码等，没有在该方法中保护 `$...$`、`$$...$$` 或 HTML 标签。
- `_restore_inline_markdown` 直接替换标记，没有在该方法中检查每个标记恰好恢复一次。
- `--single_translate` 可以只输出译文，但保留的表格仍维持原文内容；生成的文件名还需要对接本库的 `译文.md` 约定。

这表示需要补足确定性保护，不表示模型每次都会把公式翻坏。仓库里另有数学跨度模块，但不能把别的输入路径的能力自动视为该 MD 加载器已经接入。

来源：[MD 加载器](https://github.com/yihong0618/bilingual_book_maker/blob/e1027a4823e98f668ef950740b6f805ec30bd3b2/book_maker/loader/md_loader.py#L126)、[保护和恢复函数](https://github.com/yihong0618/bilingual_book_maker/blob/e1027a4823e98f668ef950740b6f805ec30bd3b2/book_maker/loader/md_loader.py#L751)、[Markdown 使用说明](https://github.com/yihong0618/bilingual_book_maker/blob/e1027a4823e98f668ef950740b6f805ec30bd3b2/docs/zh/formats/md.md)。

### 审校与阅读辅助：复用方法，明确交付范围

translation-agent 的代码明确把审校拆成准确性、流畅性、风格、术语等维度，之后依据问题修订。这个工作流适合作为本库的中文质量环节，但其自身没有完成论文结构保护，README 也明确说明软件尚不成熟。

来源：[项目说明](https://github.com/andrewyng/translation-agent)、[审校与修订实现](https://github.com/andrewyng/translation-agent/blob/main/src/translation_agent/utils.py)。

Parallel Translator 有符号掩码、恢复时标记计数校验及相关测试；其主要使用方式是 Obsidian 选区与侧栏。它适合边读边查，但应把正式批量译文和交互式反向修改原文区分开。

来源：[使用范围](https://github.com/llbbaagg66/parallel-translator)、[保护与恢复实现](https://github.com/llbbaagg66/parallel-translator/blob/393a6b7ff9bf52c2a78b034dd72ca49f94b54362/src/main.js#L191)、[科学内容测试](https://github.com/llbbaagg66/parallel-translator/blob/393a6b7ff9bf52c2a78b034dd72ca49f94b54362/tests/scientific.test.js)。

GPT Academic 检查的是 `Markdown_Translate.py`，不是其全部 PDF／LaTeX 插件。该路径采用长度切分与格式提示词，不能据此认定它已经提供字节级公式、表格和页标记保护。

来源：[Markdown 翻译插件](https://github.com/binary-husky/gpt_academic/blob/master/crazy_functions/Markdown_Translate.py)。

## 对现有 25 篇做了什么探测

使用本地实际 `document.md`，只运行从 bilingual_book_maker 当前 MD 加载器抽取的纯解析／保护方法，没有执行其模型初始化或网络请求。

| 项目 | 结果 | 含义 |
| --- | --- | --- |
| 实际扫描论文 | 25 篇 | 使用当前库的 MD |
| 包含作为可翻译文本进入处理的 HTML 表格的论文 | 22 篇 | HTML 单元格与合并属性处理是本库的常见需求 |
| 含 `$$` 的可翻译文本块 | 284 块 | 不是 284 个独立公式；说明公式保护不能只靠提示词 |
| 合成样例中的行内与展示公式 | 没有被当前 MD 保护函数掩码 | 公式原文可能直接进入模型 |
| 合成 HTML 表格标签 | 没有被该保护函数掩码 | 需额外保护标签及属性 |
| 合成 Markdown 表格 | 被识别为整块不翻译 | 保结构，但表头与文本也不会翻成中文 |
| 合成恢复时丢失占位符 | 该恢复函数没有报错 | 需要独立完整性校验 |

这是选定函数的结构探测，**不是完整项目集成测试，不是实际翻译质量测评，也不是上述所有项目的对照实验**。探测记录：项目 `.local/translation-research/structure-probe.json`；仓库版本记录：同目录 `sources.json`。

## 怎样得到自然、易懂而准确的中文

### 先固定一份领域术语表

| 英文 | 建议译法／规则 |
| --- | --- |
| image restoration | 图像复原 |
| degradation / composite degradation | 退化／复合退化 |
| all-in-one image restoration | 统一图像复原；首次保留 All-in-One |
| fidelity | 保真度 |
| state space model | 状态空间模型 |
| prompt | 按语境译为“提示”或“提示表示”；学习到的向量不一律叫“提示词” |
| mixture of experts / expert routing | 专家混合／专家路由 |
| tool calling | 工具调用 |
| AgenticIR、DPMambaIR、DP-SSM、PSNR、SSIM | 保留方法名、模块名和指标标识 |

这些是本库的起始约定，后续允许结合上下文修订。同篇首次出现重要术语时可写“中文（English）”，后文按统一译法；不能给每次出现都堆上英文。

### 用学术中文重组句子，不改变论证

- 允许将冗长英语句子拆成自然中文短句，保留所在段落的顺序和意义。
- 保留否定、条件、可能性、因果、比较对象与限定范围；不能把“可能提升”写成“显著提升”。
- 不把摘要、贡献和结论擅自压缩成总结，也不为了“易懂”添加原文没有的解释或例子。
- 译文负责忠实、通顺；概念讲解和精读解释放到后续独立笔记。
- 跨页被切开的句子需要上下文。可以联合翻译相邻片段，但必须保持原页标记及片段对应，不能为通顺而删除页标记。

### 审校要同时看到原文与译文

建议常规做“初译＋对照审校并修订”两轮；歧义较多的段落再单独进行“指出问题→修订”第三轮。重点检查漏译、术语漂移、指代、否定条件与数值陈述；不要只对中文稿做脱离原文的润色。

多轮会增加时间与用量，因此先用同一批样例比较单轮和审校后的质量，确认收益后再决定全库配置。模型的选择以本库实际样例为依据，不照搬别人的默认模型或欧洲语言评测结论。

## 格式保留的具体规则

| 对象 | 处理方式 |
| --- | --- |
| 标题、段落、列表、强调 | 翻译文字；保留层级、块顺序和对应关系 |
| `$...$`、`$$...$$`、`\(...\)`、`\[...\]` | 数学区域以原文原样恢复，按片段检查内容与出现次数；不只检查分隔符总数 |
| 公式中的 `\text{...}` 与符号 | 首版也保持原样，避免翻译时改变公式内容；周围解释文字译成中文 |
| 公式编号、引用标记、方法名 | 保留原标识、数量和对应关系 |
| `<!-- source_page: N -->` | 原样保留，顺序和数量不变 |
| Markdown 图片、HTML 图片 | 图片文件与路径原样复用；不另存一份附件 |
| 图注与表注 | 翻译文本，保留编号与相对位置 |
| 图片中已栅格化的坐标轴、图例、标签 | 当前仍为原图中的英文；翻译图注不会自动将图片内部汉化 |
| Markdown 表格 | 只翻表头与文本单元格；保护分隔符、行列关系与实验数值 |
| HTML 表格 | 保留标签、属性、`rowspan`／`colspan` 和数值，只翻文本节点；不能粗暴改成普通 Markdown 表格 |
| 图片形式的表格 | 维持原图，翻译其表注；不自动重建表格 |
| 参考文献 | 作者、标题、出处、DOI／URL 等书目信息首版保留原样，章节标题可翻译 |
| 原文已有的识别错误 | 单独标记与复核；翻译成功不能当作纠错成功 |

应使用支持原文位置的解析方式和受保护片段校验，避免把整个 AST 重新排版而改变原有数学或 HTML。受保护内容可提供只读上下文帮助模型理解，最后仍由程序原样恢复；不能依赖模型重写出相同公式。

## 接入现有库

```text
现有论文目录/
├─ source.pdf          保留
├─ document.md         保留原识别稿
├─ 译文.md             检查后新增，主文件以中文为主
├─ pages/              原样复用
├─ evidence/           原样复用
└─ chunks/             原样复用其中的正文图片
```

翻译记录、原文校验值、模型与提示词版本、术语版本、块状态、错误与用量放在 `.paperbridge/` 下，不让断点文件挤入日常阅读入口。先在工作区暂存，完成结构检查后才生成正式 `译文.md`；抽查仍未完成时在记录中明确标记。

全部块处理完成、机械检查通过后，知识地图中该篇才从“待生成”更新为真实译文链接。存在漏译、失败块或源文件变化时保留“部分完成／需复核”，不冒充完成。修订原文会使已有译文的来源校验值过期，应做增量重译。

## 先做两篇试译，再扩展到全库

1. **AgenticIR**：验证摘要、引言、工具编排术语、图片与图注，以及长文上下文。
2. **DPMambaIR**：验证正文公式、符号解释、Markdown 与 HTML 表格、实验数值。
3. 补一个跨页句子样例，例如 **MPerceiver 摘要第 1–2 页**；用 **DiTTo 的异常公式标记**测试是否正确标记或隔离有问题的块，而不是吞掉后续正文。
4. 对试译做两类验收：程序检查格式，人工抽读中文。至少覆盖摘要、方法段、公式解释、图注与复杂表格，不只看摘要。
5. 试译确定术语、风格、模型与调用用量后，再按篇批量处理剩余论文，并支持恢复中断任务。

### 验收要求

- 数学内容、图片路径、页标记、引用标识与表格数值无未说明变化；图片引用可定位。
- 表格行列及合并关系保持，正文和文本单元格实际有译文。
- 无漏段、重复段、占位符残留、无说明的英文回退；参考文献、代码和保留的标识不作为漏译。
- 中文抽查通过：术语一致、语序自然、限定条件准确，不扩写不缩写。
- 在 Obsidian 实际打开检查公式、图片和 HTML 表格；自动结构检查与渲染检查分开记录。
- 原 PDF、原文 MD、附件和来源报告保持不变。

全量翻译前不预报固定费用：当前 25 篇 MD 合计约 207 万字符，含公式、HTML、参考文献等非正文内容，不等于计费 token。费用与时间应根据试译的可翻译文本、上下文、审校次数、缓存及重试统计，再外推范围。

## 本次结论的边界

已经核实代码路径与本库结构需求；尚未证明哪一个模型能给这批论文提供最佳中文。推荐的是可检查、可逐步验证的处理路线。下一项实际工作应是两篇试译与格式验收，不是直接承诺 25 篇一次完成且零损坏。
