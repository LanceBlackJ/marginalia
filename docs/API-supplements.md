# 补充材料与数据集接口（`api/supplements.py`）

通用约定见 `API.md`（Bearer 令牌、错误格式、任务 `/api/jobs/{id}`）。

## 核心约定
- **补充材料 PDF = 子论文**：导入后作为主论文的 `child_paper`（`role="supplement"`），在文库列表里隐藏，只在主论文的“补充材料”面板出现；有 PDF 的补充材料可像论文一样阅读、检索、截图公式。
- **同一篇论文的补充材料不会被当作重复**：普通导入时若 DOI/标题与已有论文相同，仍返回 `status="duplicate"`，但带 `can_attach_as_supplement=true`、`target_paper_id`（以及首页含 Supplementary 字样时 `supplement_hint=true`），界面据此提供“作为补充材料添加”；完全相同的文件仍是普通重复。用户确认后走 `supplements/files`（或 `attach-paper`）。从主论文的“补充材料”面板添加则直接附加，不经过查重。
- **授权**：AI 使用补充材料内容前必须有授权。`GET /papers/{id}/supplements/authorization` → `{"granted":false,"decision":null,"decided_at":null,"ttl_seconds":28800}`；每个会话默认未授权，授权只在当前会话/TTL 内有效，可随时撤销。未授权时问答不会带上补充材料文字，SSE 会发 `permission` 事件让界面询问；补充材料引用格式 `[S序号P序号]`，编造的引用会被标记。
- 下载大文件/未知大小文件需要用户批准（`approve` / `reject`，`reject` 之后 `retry` 会重新请求批准，不能绕过）。自动发现只给“候选”，需要用户确认；遇到登录页/403 只给手动下载提示，不绕过。
- 压缩包只列目录，解压是显式操作，防 zip-slip 与压缩炸弹；任何路径不得越出数据目录；永不删除用户原文件。

## 接口
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/papers/{id}/supplements` | `{"items":[asset…],"authorization":{…},"categories":[…]}`；asset 含 `category`/`category_source`(rule/ai/user)、`match_status`、`download_status`、`analysis_status`、`child_paper_id`、`child` |
| GET/POST | `/api/papers/{id}/supplements/authorization` | 查询/设置授权。POST 体：`{"granted": true\|false\|null, "ttl_seconds"?: 60–604800}`；`allow` 是 `granted` 的等价旧写法（两者同时给且不一致返回 400）；`granted` 为 null 或不给任何字段 = 重置（视为本次尚未询问）；`ttl_seconds` 缺省 28800（8 小时）；**未知字段返回 400**，不会被静默忽略。返回 `{"granted","decision","decided_at","ttl_seconds"}` |
| POST | `/api/papers/{id}/supplements/files` | `{"paths":[…]}` 添加本地文件（PDF 变子论文；表格/文档/压缩包为资产），返回 `{"job_id"}` |
| POST | `/api/papers/{id}/supplements/url` | 添加链接，进入下载流程（需确认/批准） |
| POST | `/api/papers/{id}/supplements/link-remote` | 仅登记远程文件，不下载 |
| POST | `/api/papers/{id}/supplements/attach-paper` | 把文库中已有论文转为本论文的补充材料 |
| POST | `/api/papers/{id}/supplements/discover` | 从论文自己的页面/参考信息找补充材料与代码候选（仅候选，需用户确认） |
| POST | `/api/papers/{id}/supplements/recommend` | AI 推荐阅读顺序（需授权；编造的编号会被丢弃） |
| PATCH/DELETE | `/api/supplements/{asset_id}` | 改分类/备注；删除（连带子论文，不动用户原文件） |
| POST | `/api/supplements/{asset_id}/approve` `/reject` `/retry` | 大文件批准/拒绝/重试 |
| GET | `/api/supplements/{asset_id}/file` | 取文件 |
| GET | `/api/supplements/{asset_id}/analysis` | 数据集画像/文档文本等分析结果 |
| GET | `/api/supplements/{asset_id}/table?sheet=&offset=&limit=` | 表格分页预览 |
| POST | `/api/supplements/{asset_id}/analyze` | 重新做本地画像；AI 分类需授权 |
| POST | `/api/supplements/{asset_id}/explain` | AI 解释数据集（需授权；带证据；发给云端的内容记入隐私日志）。**这是后台任务，不是 SSE**：返回 `{"job_id"}`，进度与结果走 `/api/jobs/{id}`，完成后结果保存在该资产分析画像的 `explanation` 字段（`GET /api/supplements/{asset_id}/analysis`） |
| POST | `/api/supplements/{asset_id}/extract` | 显式解压压缩包（安全检查） |

## 数据集画像（`materials/datasets.py`）
- 支持 CSV/TSV/XLSX/XLSM/JSON；编码与分隔符自动检测；整表流式扫描（上限 `SCAN_ROW_LIMIT`，超过标记 `partial`）。
- 每列：类型（整数/浮点/布尔/日期时间/字符串/混合）、缺失率、唯一值、统计量、直方图、高频取值。
- 分位数与直方图用**均匀蓄水池抽样**（每个值带随机键，始终保留键最小的 100,000 个），在已排序数据上也无偏。
- 形如 `2021-03-29`、`2021-03-29 10:00:00`、带时区的 ISO 日期字符串按日期时间处理（字典序即时间序）。
- 画像是本地计算，不上传；只有用户点“AI 解释”且已授权时，才把画像摘要（不含整表）发给模型。

## 测试
`backend/tests/test_materials.py`（43 项）：同 DOI 的补充材料提示而非判重复；子论文隐藏；授权的会话/TTL/撤销；未授权不发送补充材料文字；引用规范化与编造引用标记；主文与补充材料同 ord 不混淆；`Table S5` 检索；下载批准流程；zip-slip/炸弹；画像（含已排序数据的分位数、ISO 日期）。
