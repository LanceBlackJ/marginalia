# Marginalia 架构说明（阶段一）

## 总览

```
┌────────────────────────── Electron ───────────────────────────┐
│  主进程 (src/main)                  渲染进程 (src/renderer)   │
│  · 创建窗口、拉起/监控后端           · React + TypeScript      │
│  · safeStorage 加密保存 API Key      · PDF.js 阅读器           │
│  · 原生文件/文件夹对话框             · Insight Flow 卡片流     │
│  · preload：受控的 window.paperdesk  · 只通过 HTTP+SSE 访问后端│
└──────────────┬──────────────────────────────┬─────────────────┘
               │ spawn + env(token)           │ Bearer token
               ▼                              ▼
      ┌─────────────────── Python 本地服务 (backend/paperdesk) ──────────────────┐
      │ FastAPI（仅 127.0.0.1）                                                  │
      │  api/        路由：文献库、阅读器查询、导入、任务、AI、卡片、设置        │
      │  library/    导入流水线、在线题录（Crossref/OpenAlex/arXiv）              │
      │  parsing/    PyMuPDF 全文解析：分栏、页眉页脚、章节、段落、元数据         │
      │  ai/         提供商适配 · 路由 · 检索 · 提示词 · 证据核实 · 卡片 · 分析    │
      │  jobs.py     持久化后台任务（SQLite），重启恢复                            │
      │  db.py       SQLite + FTS5（WAL）                                         │
      └───────────────────────────────┬──────────────────────────────────────────┘
                                      ▼ HTTPS（用户配置的模型服务）
                           OpenAI 兼容接口 / Anthropic Messages
```

接口契约见 [API.md](API.md)。前端说明见 [FRONTEND.md](FRONTEND.md)。

## 数据目录

默认（开发）`<repo>/.devdata`，安装版 `%APPDATA%/…/data`，可用环境变量 `PAPERDESK_DATA_DIR` 覆盖。

```
database/paperdesk.db      SQLite（元数据、段落、FTS 索引、卡片、任务、用量…）
papers/<paper_id>/original.pdf, metadata.json   Marginalia 自己的副本；用户原文件只读不动
cache/  logs/（backend.log＝Electron 捕获的后端进程输出；server.log＝后端自身的滚动日志）  settings/
```

## 论文解析（parsing/）

1. PyMuPDF 取每页文本块（含字体、字号、粗体、坐标），坐标按页面旋转归一化为“左上角原点、PDF point”。
2. 页眉/页脚：出现在多数页面顶部/底部边缘的重复文字（数字归一）与页码被剔除。
3. 版面：整宽块作分隔，段内先左栏后右栏；单栏页面按 y 排序。
4. 章节：按字号/加粗/编号/常见章节名检测；检测不足时回退到 PDF 自带目录。第一个章节前的内容归“首部信息”。
5. 段落：块级切分；跨块的未完句子（同页同栏）合并；超长段落按句切成 ≤1000 字的块；图注/表注单独标记 `caption`；参考文献标记 `reference`（检索时默认排除）。
6. 元数据：版面最大字号块 → 标题；标题后到首个章节前的短文本 → 作者候选；DOI/arXiv/年份正则；摘要取 Abstract 章节。可用 Crossref 补全（要求标题相近才采纳，避免把引用文献的 DOI 当成自身）。
7. 没有文字层（扫描版）：如实标记“无文字层”，阅读可用，检索/AI 不可用（OCR 属后续阶段）。
8. PyMuPDF 不支持多线程并发，解析由全局锁串行化。

## AI 调度中心（ai/）

一次研究问答 `POST /api/ai/ask` 的流水线：

```
意图识别(router.classify)  ──▶  模型路由(choose_model)
        │                              │ auto：简单任务→快速模型，复杂任务→强模型
        ▼
中文问题 + 英文论文？ ──▶ 快速模型生成英文检索词（失败不影响主流程）
        ▼
上下文检索(retrieval.build_context)
   选中段落(+邻近) ＋ 全文 BM25(FTS5，章节加权) ＋ 图/表/公式引用解析 ＋ 摘要/章节大纲
   在字符预算内按优先级装配，保留 [P序号]/页码/章节
        ▼
记录“发送内容”(ai_requests，可在 设置→数据与隐私 查看) ──▶ 流式生成(providers.stream)
        ▼
证据核实(evidence.parse_answer)
   · 解析固定小节：直接回答/详细解释/依据/AI 推断/后续问题
   · [P序号] → 真实段落；evidence.quote 取自数据库，不信任模型“引文”
   · 模型编造的编号 → verification.invalid_citations，前端标“无法核实”
   · 快速模型结果缺格式或缺依据 → 自动升级强模型（escalate 事件，两次用量都计入）
        ▼
研究卡片落库(insight_cards) + 按研究对象归入分析链(insight_chains)
```

- **不是把全文塞给模型**：每次只发送完成任务所需的段落，并记录发送了什么。
- **没有向量检索**：阶段一使用 FTS5/BM25 + 章节加权 + 查询扩展；语义向量索引留待后续（接口位置在 `retrieval.build_context`）。
- 提供商适配：`OpenAICompatibleProvider`、`AnthropicProvider`；429/5xx/超时指数退避重试；兼容 `max_completion_tokens`/不支持 `temperature` 的新模型；错误信息不含 API Key。
  - `complete()` 内部走流式再聚合（部分网关的非流式路由会挂住）；SSE 只按换行符（LF）切行；无法解析的帧计数并告知调用方，不静默丢弃。
  - 过滤推理模型混在正文里的 `<think>…</think>`（标签可能被拆在多个数据块里），忽略 `reasoning_content` 字段。
  - 输出完整性检查（`evidence.integrity_issues`）：发现疑似缺词（公式定界符不成对等）时在核实信息里提示重新生成。
- **整体分析**（`ai/analysis.py`）：按“背景/动机/挑战/方法/实验/结果/结论”分别检索，合并到 ~28k 字预算，强模型一次综合输出 JSON，解析失败会让模型重输出一次，引用同样核实。以后台任务运行，状态在 `paper_analysis`。

## 后台任务（jobs.py）

状态：`pending / running / waiting_approval / paused / completed / failed / cancelled`。任务与日志写入 SQLite；启动时 `running` → `failed(应用被中断，可重试)`，`pending` 继续执行；支持取消（协作式检查点）与重试（创建新任务）。当前类型：`import` `import_doi` `import_url` `attach_pdf` `paper_analysis`。`waiting_approval/paused` 状态已在模型中保留，阶段三/四（下载大文件、复现）会用到。

## 安全

- 后端只监听回环地址，所有 `/api/*` 需 Bearer token（主进程随机生成，经环境变量传给子进程）。
- API Key：渲染进程 → IPC → 主进程 `safeStorage`（Windows DPAPI）加密落盘；后端只在内存持有，日志与数据库中都没有。
- 导入只复制不移动；删除论文只删 Marginalia 内的副本；下载校验 `%PDF` 头与大小上限，不绕过付费墙/机构认证（只使用开放获取链接）。
- 作者代码执行属于阶段三/四，默认静态分析、隔离环境执行，尚未实现。
