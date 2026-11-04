# 文库：文件夹、多特征筛选、智能视图与建议归类

前端 ↔ 后端契约。基础约定（鉴权、错误格式）见 `docs/API.md`。

## 文件夹与标签的分工

- **文件夹**管“放在哪里”：一篇论文**只能放在一个文件夹里**（像资料柜的一格），文件夹可嵌套（最多 6 层）。
- **标签**管“是什么”：一篇论文可以有**多个标签**（见 `POST /papers/{id}/tags`，标签就是知识库主题）。
- 补充材料子论文（`role='supplement'`）跟随主论文，不单独出现在列表和文件夹里；移动请求里带上它们会被跳过并计数。
- 删除文件夹**不会删除论文**：文件夹和它的全部子文件夹一起被移除，里面的论文回到“未分类”。

## 文件夹

| 方法与路径 | 说明 |
|---|---|
| `GET /api/folders` | `{items:[{id,name,parent_id,sort,paper_count,total_count}], counts:{all,unfiled,favorite,recent}}`。平铺列表，前端按 `parent_id` 组树；`paper_count` 是直接放在这里的篇数，`total_count` 含子文件夹 |
| `POST /api/folders` | `{name, parent_id?}` → 新文件夹。名称去首尾空白、≤60 字符；同级重名（不分大小写）409；父级不存在 404；超过 6 层 400 |
| `PATCH /api/folders/{id}` | `{name?, parent_id?, sort?}`。**只有请求里带了 `parent_id` 字段才会移动**（`null` = 移到最上层）；不能移进自己或自己的子孙里（400） |
| `POST /api/folders/reorder` | `{parent_id, ids}`：`ids` 必须恰好是该级下的全部文件夹，按顺序重新编号 |
| `DELETE /api/folders/{id}` | `{ok, deleted_folders, moved_papers}` |
| `POST /api/papers/move` | `{paper_ids, folder_id}`（`folder_id=null` 移回未分类）→ `{ok, moved, skipped_supplements}`。论文不存在 404、文件夹不存在 404、空列表 400 |

数据库：`folders(id, name, parent_id, sort, created_at)`、`papers.folder_id`。删除论文时论文行连同它在 `paper_vectors`、`folder_suggestion_ignored` 里的记录一并清理（外键级联），文件夹本身保留，计数随之更新。

## 列表与多特征筛选：`GET /api/papers`

旧参数不变：`q`、`tag`、`view`（all/recent/favorite）、`sort`、`order`、`limit`、`offset`。新增（全部可选，彼此 **AND**）：

| 参数 | 含义 |
|---|---|
| `folder` | 文件夹 id；`none` = 未分类。默认含子文件夹，`subfolders=false` 只看这一级 |
| `tags`、`tag_mode` | 多个标签 id（逗号分隔）；`any`（默认）含任一，`all` 同时含有全部 |
| `note_q` | 在**笔记**（笔记正文和它引用的原文）里搜索 |
| `annotation_q` | 在**批注**与高亮文字里搜索 |
| `text_q` | 在**论文正文**里搜索（英文按词前缀走 FTS5；含中文时退回子串匹配） |
| `card_q` | 在 **AI 研究卡片**里搜索（AI 生成的内容，不是论文原文） |
| `year_from`、`year_to` | 年份范围 |
| `author`、`venue`、`language` | 作者/期刊或会议名的一部分（不分大小写）；语言代码 |
| `read` | `unread`（从没打开过）、`reading`、`done`（进度 ≥95% 或读到最后一页） |
| `parse` | `ready`、`parsing`、`failed`、`metadata_only`、`ocr`（含 OCR 识别的段落） |
| `has_notes`、`has_annotations`、`has_supplement`、`has_code`、`favorite` | 布尔；`has_code` = 已获取代码仓库 |
| `imported_from`、`imported_to` | 导入时间（`imported_to` 是“早于”，含当天要传次日） |
| `sort=relevance` | 有 `q` 时按相关度：标题命中 > 作者 > 摘要；没有关键词时退回导入时间 |

文字类条件里多个词用空格隔开，**每个词都必须出现**；不分大小写。非法的 `read`/`parse` 返回 400。

每篇论文新增字段：`folder_id`、`folder_path`（如“图神经网络 / 图注意力”）、`tags`（既有）。有 `note_q`/`annotation_q`/`text_q`/`card_q` 时还带 `matches`：

```json
"matches": {
  "notes":       [{"id": 12, "page": 3, "from": "笔记", "text": "…这里的 Attention 机制…", "hit": [5, 14]}],
  "annotations": [{"id": 4,  "page": 3, "from": "批注", "text": "…", "hit": [0, 2]}],
  "fulltext":    [{"ord": 87, "page": 5, "from": "正文", "text": "…", "hit": [10, 19]}],
  "cards":       [{"id": "…", "page": null, "from": "AI 卡片（AI 生成）", "text": "…", "hit": [3, 8]}]
}
```

`hit` 是 `text` 里的 `[start, end)` 下标，界面据此高亮；每类每篇论文最多 3 条。

## 筛选项：`GET /api/papers/facets`

`{total, years:[{year,count}], authors:[{author,count}] (前 60), venues:[…] (前 60), tags:[{id,name,count}], languages:[…], parse:{ready,parsing,failed,metadata_only,ocr}, read:{unread,reading,done}, flags:{has_notes,has_annotations,has_code,has_supplement,favorite}}`。筛选面板据此显示可选值与计数。

## 智能视图（保存下来的筛选条件）

| 方法与路径 | 说明 |
|---|---|
| `GET /api/smart-views` | `{items:[{id,name,query,sort}]}` |
| `POST /api/smart-views` | `{name, query}`；`query` 用的是筛选条件的 JSON 形式（`note_q`、`tags`、`year_from`、`has_notes`…），空条件 400、同名 409、非法枚举 400。只保存条件，不保存论文 |
| `DELETE /api/smart-views/{id}` | 删除 |

## 建议归类

`GET /api/folders-suggestions` → `{method: "embedding"|"keywords"|"none", items:[{paper_id, paper_title, folder_id, folder_name, folder_path, score, confidence: "high"|"medium"|"low", basis}], reason}`。

- 只对**未分类**且已解析好的论文给建议，**从不自动移动**；接受 = 普通的 `POST /papers/move`。
- `method=embedding`：有语义索引（`passage_embeddings`）时，论文向量 = 前 60 个段落向量的均值（缓存在 `paper_vectors`，段落嵌入数量变化时重算），与文件夹（含子文件夹）里最相似的论文比较；`basis` 写明最相似的是哪一篇和相似度。
- `method=keywords`：没有语义索引时，用标题和摘要的关键词重合（词频加逆文档频率的余弦；中文用相邻两字作词）；`basis` 写明共享的关键词，并注明“没有语义索引时的粗略判断”。**跨语言（中文论文对英文文件夹）关键词方法帮不上忙**，需要语义索引。
- `method=none`：还没有已归类的论文，或没有未分类的论文（`reason` 说明）。
- `POST /api/folders-suggestions/ignore` `{paper_id, folder_id}`：不再建议这一对；同一篇论文被忽略 3 次后不再出现。

## 性能（`backend/tests/test_folders_filters.py::test_list_filters_stay_fast_…`）

造数据：2000 篇论文、24000 段正文、5000 条笔记；列表每次取 50 条，三次取中位数（本机、SQLite WAL）：

| 查询 | 耗时 | 命中 |
|---|---|---|
| 无筛选 | ≈ 36 ms | 2000 |
| `note_q` | ≈ 97 ms | 1785 |
| `text_q`（词几乎出现在每篇里的最坏情况） | ≈ 150 ms | 2000 |
| 笔记 + 年份 + 有笔记 | ≈ 82 ms | 1200 |
| 文件夹 + 关键词 + 相关度排序 | ≈ 39 ms | 768 |
| `text_q` + `note_q` | ≈ 200 ms | 1803 |
| `GET /papers/facets` | ≈ 178 ms | — |

实现要点：笔记/批注/卡片/正文命中的论文 id 只算一次（计数和取列表共用，经 `json_each` 传入）；命中片段只在当前页那几十篇论文的段落里找词，不再整库做 FTS 排序。测试断言每项 < 600 ms 以免偶发波动。
