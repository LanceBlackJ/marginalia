# 导出、关系图与批量操作 API

所有接口都在 `/api` 下，需要 `Authorization: Bearer <token>`。内容全部由后端从库里生成，不调用任何模型。

## 引文导出

`GET /export/formats` → `{citations:[{id,label,ext}], bundle:[{id,label,ext}]}`

`POST /export/citations`

```json
{"format": "bibtex|ris|csl|gbt7714|apa", "paper_ids": ["…"], "tag_id": "…"}
```

- 范围三选一：`paper_ids`（按给定顺序）、`tag_id`（该标签下已确认的论文）、都不给 = 全库。
- 返回 `{filename, content, count, format, label, mime}`；没有可导出的论文返回 400。
- 补充材料子论文（`papers.parent_id` 非空，或出现在 `supplementary_assets.child_paper_id`）不导出。

### 字段规则

- 字段只来自库里的元数据：标题、作者、年份、来源（期刊/会议）、DOI、arXiv 编号、网址。**缺失的字段一律省略，不编造**；库里没有卷、期、页码，所以输出里也没有。
- 作者规范化为“姓, 名”：支持“名 姓”和“姓, 名”两种输入，小写前缀（van、de）归入姓，Jr./III 保留在姓里；中文名整体作为一个姓，BibTeX 里用 `{…}` 保护。
- 条目类型由来源名称保守判断：含 Conference / Proceedings / Workshop / 常见会议简称 / “Neural Information Processing Systems” 的按会议论文（`inproceedings` / `CONF` / `paper-conference`），其它有来源名的按期刊文章，没有来源但有 arXiv 编号的按预印本，否则按杂项。判断不准时以“期刊文章”为默认，使用者可在文献管理软件里改。
- BibTeX 键：第一作者姓（ASCII）+ 年份 + 标题首个实词；同键依次加 `a`、`b`…；同样的输入重复导出结果一致。特殊字符 `\ { } & % _ # $ ^ ~` 转义，非 ASCII 保持 UTF-8。

### 样例（真实论文，逐字节输出）

BibTeX：

```
@article{Fan2020Finding,
  title = {Finding key players in complex networks through deep reinforcement learning},
  author = {Fan, Changjun and Zeng, Li and Sun, Yizhou and Liu, Yang-Yu},
  year = {2020},
  journal = {Nature Machine Intelligence},
  doi = {10.1038/s42256-020-0177-2}
}
```

RIS：

```
TY  - JOUR
TI  - Finding key players in complex networks through deep reinforcement learning
AU  - Fan, Changjun
AU  - Zeng, Li
AU  - Sun, Yizhou
AU  - Liu, Yang-Yu
PY  - 2020
JO  - Nature Machine Intelligence
DO  - 10.1038/s42256-020-0177-2
UR  - https://doi.org/10.1038/s42256-020-0177-2
ER  - 
```

GB/T 7714（顺序编码制，超过 3 位作者写 “et al”）：

```
[1] FAN C, ZENG L, SUN Y, et al. Finding key players in complex networks through deep reinforcement learning[J]. Nature Machine Intelligence, 2020. DOI:10.1038/s42256-020-0177-2.
```

APA 第 7 版（作者-年份制，带连字符的名字缩写为 `Y.-Y.`）：

```
Fan, C., Zeng, L., Sun, Y., & Liu, Y.-Y. (2020). Finding key players in complex networks through deep reinforcement learning. Nature Machine Intelligence. https://doi.org/10.1038/s42256-020-0177-2
```

## 阅读成果合集

`POST /export/bundle`

```json
{"format": "md|docx", "paper_ids": ["…"], "notes": true, "annotations": true, "cards": true, "items": true}
```

- 汇总所选论文（不给 = 全部）的：我的笔记、我的批注（高亮文字与想法）、研究卡片、知识条目。四类可分别开关；全部为空返回 400。
- `md` 返回 JSON `{filename, content, count, counts}`；`docx` 直接返回 Word 文件字节（`Content-Disposition` 带 UTF-8 文件名，`X-Export-Count` 为条数）。
- 两种格式共用同一份文档模型，内容一致。
- **出处与 AI 标识：** 每条都带页码和原文片段；研究卡片标题下写明“AI 生成 · 模型 · 核实状态”，卡片里的“AI 推断”单独标为“非论文原文”；“我的笔记”“我的批注”“知识条目（我确认保留的）”是用户自己的内容，“原文”是论文摘录。只导出已保存的内容，不重新调用模型。

## 知识库关系图

`GET /kb/graph?topic_id=&kinds=method,finding`

```json
{"nodes":[{"id":"item:…","type":"item|topic|paper","label":"…","kind":"method|…|null","ref":"…"}],
 "edges":[{"id":"…","source":"item:…","target":"…","type":"relation|member|source",
           "label":"采用的方法","status":"confirmed|suggested","ai_inferred":false,"ref":"关系 id","description":"…"}],
 "counts":{"items":4,"relations":2,"suggested":1}}
```

- 节点：知识条目、主题、论文（只含条目的来源论文和关系另一端的论文）。
- 边：`relation` 是学术关系（`confirmed` 已确认，`suggested` 是 AI 建议、待确认）；`member` 是条目归属主题，`source` 是条目来自论文，这两种只是结构线，不是学术关系。
- `topic_id` 只看该主题下的条目及其关系；`kinds` 按条目类型过滤。

## 批量操作

`POST /kb/items/bulk` — `{item_ids:[…≤500], action:"add_topic|remove_topic|delete", topic_id?}` → `{affected, skipped}`

- 不存在的条目计入 `skipped`，不报错；`topic_id` 不存在返回 404；`item_ids` 为空返回 400。
- `delete` 同时删除这些条目的关系；条目引用的论文、笔记、卡片不受影响。

`POST /kb/topics/merge` — `{source_ids:[…], target_id}` → `{topic, merged, moved_items, moved_papers}`

- 把来源主题里的条目和论文并入目标主题（已存在的不重复），再删除来源主题。
- 来源里包含目标本身会被忽略；只剩目标本身返回 400；任一主题不存在返回 404。

## 前端入口

- 文库顶部“导出”：当前标签（若已按标签筛选）或全部文献；每篇论文的行菜单“导出引文与笔记…”。对话框里“引文”页可预览、复制、保存为文件；“笔记与知识”页选择内容后保存为 Markdown 或 Word。
- 知识库：新增“关系图”标签（缩放平移、点击节点看详情、按主题/类型/节点类型过滤、只看学术关系、隐藏孤立节点；实线=已确认，虚线=待确认，浅灰细线=结构线）；“知识条目”里可多选（Shift 选一段、全选）后批量加入/移出主题或删除；主题栏的“合并”可把几个主题合成一个。

## 已知限制

- 库里没有卷、期、页码、出版社字段，所以引文里没有这些；需要时在文献管理软件里补。
- 条目类型是按来源名称做的保守判断，没有来源名称的会议论文会被当成杂项或预印本。
- 关系图只显示条目、主题和条目的来源论文；论文与论文之间的引用关系在“多论文比较”里，不在这张图里。
- 关系图节点很多（几百个）时没有做聚类或分页，只能缩放过滤。
