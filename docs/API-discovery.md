# 学术发现、论文比较、知识库接口（`api/discovery.py`、`api/compare.py`、`api/knowledge.py`）

通用约定见 `API.md`（Bearer 令牌、错误格式、任务 `/api/jobs/{id}`）。参数校验失败统一返回 400。

## 通用原则
- **不做假功能**：联网来源被限流/不可用时，在响应的 `warnings` 里如实说明并降级；全部来源都不可用时返回明确错误，而不是空结果。
- **排序不是引用数排序**：质量（发表状态、领域归一化影响力 FWCI/百分位）、相关性、时效按模式加权；`reason` 里只陈述可核对的事实。AI 重排/理由（可关）带 `reason_source`（`rule`/`ai`）。
- **版本关联**：同一工作的预印本与正式版按 DOI / 来源声明的版本链接 / 标题+作者去重合并；系列论文（标题仅差一个数字/Part）不合并。
- 发给云端的内容记入隐私日志（`/api/privacy/requests`）；嵌入向量缓存在数据库里。

## 学术发现 `/api/discovery`
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/search` | `{query, mode, year_from?, year_to?, limit(1–50), seed?, ai}`；`mode`：`comprehensive` `recent` `impact` `related` `cross`。`related` 以 `seed{doi/title/paper_id}` 为基准取 OpenAlex 的相关工作/参考文献/施引文献，结果带 `relation`；`cross` 排除主导学科以找跨学科工作；`recent` 有相关性闸门，不把“新但无关”的论文排前面。返回 `{results[], warnings[], sources[], plan?, seed?}`（结果字段叫 `results`，不是 `items`；相关性闸门把全部候选都过滤掉时，退回按综合评分的前几条并在 `warnings` 里写明，不返回空）。`/discovery/recommend/direction` 与 `/discovery/recommend/personal` 同样返回 `results`；随机探索 `/discovery/random` 返回 `items` |
| POST | `/recommend/direction` | `{topic, ai}`：按“综述 / 经典 / 近期”分组 |
| POST | `/recommend/personal` | `{limit(1–30), ai}`：依据文库提取兴趣与检索词；已在库的不再推荐；空库返回 400；每条带 `related_library` 与“共享术语”理由 |
| POST | `/random` | `{field_id?, exclude[], count(1–3), seed?, ai}`（`seen_fields` 已废弃，仅兼容旧客户端）：**只在用户点击按钮后才调用**。随机探索只返回发表在白名单顶刊/顶会上的论文：先从收录名单里随机取一批来源，按 OpenAlex source id 直接检索，再逐篇核对来源名称（精确匹配白名单别名；预印本、workshop、findings、来源缺失一律丢弃，丢弃数量如实报告在 `filter.dropped_unconfirmed`）。每条结果带 `top_venue{id,name,short,kind,category,label}`（label 为“已确认顶刊/顶会”）。门槛不足时先放宽 FWCI 再换一批来源；来源在 OpenAlex 里解析不到返回 502，没有可确认的论文返回 404。返回 `filter{field?, venues[], venue_pool, venue_policy, pool_total, pool_passed, sampled, dropped_unconfirmed, after_exclusions, thresholds}` |
| GET | `/venues` | 随机探索收录的顶刊顶会名单（可审阅）：`{count, policy, items:[{id,name,short,kind,category,fields}]}`；名单定义在 `backend/paperdesk/discovery/venues.py` |
| GET | `/fields` | 可选领域列表（含每个领域适用的收录来源数 `venue_count`） |
| POST | `/similar` | `{doi?/title?, limit(1–25)}`：相似论文，带 `relation`、`in_library`；查不到基准论文返回 404 |
| POST | `/find-code` | `{title?, doi?, arxiv_id?}`：GitHub 仓库搜索。只有 README 里出现论文标题/arXiv 号/DOI 才标“高匹配”，论文清单类仓库标“仅提及”，其余只是“候选”；被限流时返回提示 |
| GET/POST/DELETE | `/saved`、`/saved/{key}` | 收藏的搜索结果（本地） |
| POST | `/library-match` | `{items:[{key,doi?,arxiv_id?,title?}]}` → `{matches:{key:true}}`，导入后刷新“已在文库” |
| GET/PUT | `/settings` | `{use_embeddings, embedding_model}`：发现结果的语义重排开关（关闭或失败自动回到关键词+规则） |
| GET | `/sources` | 数据来源说明（OpenAlex、Crossref、Semantic Scholar、arXiv、Europe PMC、GitHub）及各自限制 |

## 论文比较 `/api/compare`
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/preflight?paper_ids=a,b&mode=` | 比较前检查：缺全文（仅摘要/扫描件）的论文会被指出；深度比较缺全文时阻止并说明 |
| POST | `` | `{paper_ids(2–8), mode: quick|method|lineage, title?}` → 创建比较并提交后台任务 `paper_compare` |
| GET | `` / `/{cid}` | 列表 / 详情；结果含对比维度表、相同点/差异、引用关系图（`known_edges` 来自正文参考文献文本与 OpenAlex，AI 边需有依据）、`warnings`、`verification` |
| POST | `/{cid}/rerun` | 重新运行 |
| DELETE | `/{cid}` | 删除 |

- 三种模式：`quick`（4 个维度，不联网）、`method`（方法维度、证据逐条核实）、`lineage`（加上引用谱系；OpenAlex 不可用时降级为仅正文证据并提示）。
- 引用格式 `[A:P12]`、`[B:ABS]`（仅基于摘要）；后端用数据库原文核实，编造的引用被标记；未配置 AI 时明确报错。

## 主题知识库、画像、导出（`api/knowledge.py`）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST/PATCH/DELETE | `/kb/topics`、`/kb/topics/{tid}` | 主题 CRUD（名称唯一） |
| GET/POST/DELETE | `/kb/topics/{tid}/papers[/{pid}]` | 论文归入/移出主题 |
| POST/DELETE | `/papers/{id}/tags`、`/papers/{id}/tags/{tid}` | 论文标签（= 主题）：POST `{name}` 返回 `{id,name}`；名字去首尾空白、≤30 字符、不区分大小写不重复，不存在就自动创建；DELETE 只解除关联，不删除标签。文库列表与详情的每篇论文带 `tags:[{id,name}]`（一次批量查询），`GET /papers?tag=<id>` 按标签过滤 |
| POST | `/kb/topics/suggest`；GET `/kb/topic-suggestions`；POST `…/{tid}/accept` `…/reject` | AI 建议的主题归类（需确认） |
| GET/POST/PATCH/DELETE | `/kb/items`、`/kb/items/{iid}` | 知识条目；证据由后端按数据库重建，不信任客户端传来的文字 |
| POST | `/kb/extract` `{paper_id}` | 从论文提取候选条目 → `GET /kb/candidates`、`POST …/{cid}/accept` `…/reject` |
| GET/POST/DELETE | `/kb/relations`、`/kb/relations/{rid}` | 条目/论文间关系；**必须带可核对的证据**（`evidence` 至少一条） |
| POST | `/kb/relations/suggest`、`/kb/relations/{rid}/confirm` | AI 建议的关系标记为 AI，需用户确认 |
| GET | `/profile` | 研究画像 |
| PUT | `/profile/meta` | `{interests?, explanation_depth?, use_in_prompts?}`；默认**不**注入提示词，也不会覆盖引用规则 |
| POST/PATCH/DELETE | `/profile/concepts[/{cid}]` | 概念掌握状态：**只能由用户设定**，AI 不会自动标“已掌握” |
| POST | `/profile/suggest`；`/profile/suggestions/{sid}/accept` `/dismiss` | AI 只能“建议”，由用户接受 |
| GET | `/notes/all` | 全局阅读笔记（跨论文） |
| POST | `/export/markdown` | `{kind: topic|viewpoints|notes|cards, topic_id?, paper_ids?, include_evidence?}` → `{filename, content}`；`topic` 缺 `topic_id` 返回 400，未知 id 返回 404 |

导出的内容把作者原文与 AI 推断分开标注，证据带页码。

## 补充：随机探索按研究方向
`POST /api/discovery/random` 新增可选字段 `topic`（自由文本，≤120 字）：来自研究画像里的研究方向。给了 `topic` 时按 `title_and_abstract.search:<清洗后的方向>` 在白名单来源里检索，不再限定一级学科（`field_id` 被忽略），来源核对不放松；返回的 `filter.topic` 是实际使用的方向（清洗后）。界面只在研究画像里有方向时才显示“我的方向：…”选项。
