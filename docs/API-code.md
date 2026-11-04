# 代码理解接口（F2）

实现：`backend/paperdesk/code/`（静态分析、获取、AI 解释、论文↔代码关联、一致性）与 `backend/paperdesk/api/code.py`。
前端：`src/renderer/src/features/code/`、`src/renderer/src/pages/CodePage.tsx`（路由 `#/code/:paperId`）。
通用约定（Bearer 令牌、错误体 `{error:{code,message}}`、SSE 事件格式、后台任务）见 [API.md](API.md)。

## 硬性约束

- **只做静态分析，绝不执行用户代码**：解析用 tree-sitter（Python/JS/TS/Java/C/C++/Go/Rust/Bash）与 Python `ast`，Cython `.pyx/.pxd` 用词法级提取；不 import、不运行、不安装依赖。
- 压缩包/文件夹导入走 `safeio`：拒绝路径穿越、绝对路径、符号链接逃逸，限制文件数与解压总大小，跳过二进制与超大文件。
- 从 GitHub 获取：只访问 `api.github.com` / `codeload.github.com`；仓库 >20 MB 先停在“等待确认”，>300 MB 直接拒绝；导入压缩包有文件数（2 万）、解压总大小（600 MB）、单文件（5 MB）上限；仓库改名/转移时按新地址获取并给出提示。其它来源（GitLab/Zenodo/Code Ocean 等）只提示，让用户手动下载后本地导入。
- 论文没有声明代码链接时，`/code/sources` 返回空列表，不猜测链接。
- AI 解释只发送相关代码片段（隐私日志 `ai_requests` 里可见）；回答中的每条证据是真实的 `path:行号`，后端逐条核对（`已核实`/`无效`），区分“代码里直接写明的”与“推断（不是代码里直接写明的）”。

## 获取 / 导入

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/papers/{id}/code/sources` | 论文里声明的代码/数据链接，含页码与来源类型 |
| POST | `/papers/{id}/code/resolve` `{url, branch?}` | 解析 GitHub 仓库信息（大小、分支、commit、是否需确认、警告），不下载 |
| POST | `/papers/{id}/code/fetch` `{url, branch?}` | 创建仓库记录并提交获取任务；`requires_approval=true` 时任务处于 `waiting_approval`，用 `/jobs/{id}/approve` 确认 |
| POST | `/papers/{id}/code/import` `{path, kind: zip\|folder\|auto, authored_by}` | 导入本地 zip/文件夹 |
| GET | `/papers/{id}/code/repos` | 该论文的代码仓库列表（含 `overview`：文件/符号/调用数、语言分布） |
| GET/DELETE | `/code/repos/{repo_id}` | 详情 / 删除（同时删除本地副本与分析结果） |
| POST | `/code/repos/{repo_id}/reanalyze` | 不重新下载，用最新解析器重新分析 |

仓库来源标记 `authored_by`：`author`（论文声明的作者代码）/ `user`（用户自己找的）/ `self`（用户自己的实现），界面上区分显示。

## 浏览与静态结构

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/code/repos/{id}/tree` | 文件树（含角色：训练/评估/模型/数据/配置/文档等） |
| GET | `/code/repos/{id}/file?path=` | 文件内容、语言、行数、符号大纲 |
| GET | `/code/repos/{id}/symbols?q=&kind=&limit=` | 符号搜索 |
| GET | `/code/repos/{id}/symbols/{sid}/graph` | 某符号的调用者/被调用者 |
| GET | `/code/repos/{id}/flow?symbol_id=&max_depth=` | 运行流程：节点（含执行顺序 `order`、阶段 `phase`）与边（`certainty`: `resolved`/`ambiguous`） |

调用图由静态分析得到：动态调用（反射、回调、运行时分派）无法确定，界面如实说明；名称匹配到多个候选的边标为“不确定”（虚线）。

## AI

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/code/explain` `{repo_id, kind, path?, symbol_id?, symbol?, variable?, line_start?, line_end?, question?}` | SSE：`meta` → `delta*` → `escalate?` → `done{card}` \| `error`。`kind`：`file/repo/module/function/class/algorithm/dataflow/variable/calls/line_range`；`repo` 是整个仓库的总讲解（不需要 `path`）：每个顶层目录挑一个最重要的文件开头作为可引用片段，再加 README，完整文件清单只作背景，没有片段的目录只能说“只从文件名看”；`module` 的 `path` 是目录（空或 `.` 表示仓库根），发给模型的是该目录里最重要的几个文件的开头，文件清单和符号索引只作背景，不能当依据引用 |
| GET | `/code/repos/{id}/cards?path=&symbol_id=` | 已保存的解释卡片 |
| DELETE | `/code/cards/{card_id}` | 删除卡片 |
| POST | `/code/repos/{id}/flow/annotate` `{symbol_id?}` | AI 给流程节点命名并写一句话说明（结构来自静态分析，结果标 `ai=true`） |
| GET | `/papers/{id}/code/modules` | 论文里的方法模块（确定性提取，不调用 AI） |
| POST | `/code/repos/{id}/associate` | 任务：论文方法模块 ↔ 代码符号候选关联（仓库须已分析完成，否则 409） |
| GET | `/code/repos/{id}/links?status=` | 关联列表：`suggested/confirmed/rejected`，`confidence`: `likely/possible`，`method`: `llm/lexical/manual` |
| PATCH | `/code/links/{id}` `{status}` | 用户确认/拒绝 |
| POST | `/code/repos/{id}/links` `{module_name, symbol_id, passage_ords?}` | 手动建立关联 |
| GET | `/code/symbols/{sid}/paper` | 反向：函数 → 论文相关段落与已建关联 |
| POST / GET | `/code/repos/{id}/consistency` | 任务 / 报告：论文里的超参数与代码里的取值对照，结论 `match/differs/not_found/to_check`，附两边出处 |

一致性的论文侧抽取：规则抽取总是可用；有 AI 时补充抽取，且每条必须满足：①引文逐字出自段落；②取值数字出现在引文里；③引文及其所在句、上一句里出现与参数相符的词（如回放池容量必须出现 replay/memory/buffer）；④区间值（如 `30–50 nodes`）只能对应节点数范围，会被拆成 `nodes_min/nodes_max`，不能当作“图的数量”等单值参数。没通过核对的 AI 抽取项标 `to_check`（待核查），不给“一致/不一致”结论；`paper` 里带 `context`（引文所在上下文）、`check`（`ok/weak`）、`check_note`。

`GET /code/repos/{id}/flow` 返回 `truncated` 与 `truncated_by`（`depth`=超过展开深度，`nodes`=超过节点数上限 `max_nodes`），界面据此说明被截断的原因与怎么看到更多。

关联与一致性只给“候选 + 证据”，不替用户断言“已复现”或“一致”；用户确认前一律标“待确认”。

## 后台任务类型

`code_fetch`、`code_import`、`code_reanalyze`、`code_associate`、`code_consistency`（均走通用任务系统，可取消、重启后恢复）。

## 已知限制

- 动态调用、元编程、跨语言调用（如 Cython ↔ C++）只能部分识别；
- Cython 为词法级提取，类型/调用解析不如 Python；
- 不执行代码，因此不能给出运行时数据形状，只能指出代码里能静态看到的信息；
- 暂不支持 GitLab/Zenodo/Code Ocean 的直接获取（手动下载后本地导入）。

## 复现（第 4 阶段）

设计与边界见 [REPRODUCTION-PLAN.md](REPRODUCTION-PLAN.md)，用户侧的手动运行说明见 [REPRODUCTION-REMOTE.md](REPRODUCTION-REMOTE.md)。评估、计划、批准、对比都不执行用户代码。只有下面“隔离运行”一节的接口会启动 Docker 容器，代码只在容器里运行，宿主机上只启动 docker 本身。`repo_id` 省略时取这篇论文最新的、已分析完成的仓库。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/system/sandbox` | 隔离环境检测（只探测）：`{docker, wsl2, available, can_run_here, reason}`；Docker 服务在运行时 `can_run_here=true`（WSL2 不用于运行），否则为 `false` 并写明原因 |
| GET | `/api/papers/{id}/repro/assessment?repo_id=` | `{repo_id, status: none\|ready\|failed, report, updated_at, error}` |
| POST | `/api/papers/{id}/repro/assessment` | body `{repo_id?}` → `{job_id, repo_id}`；后台任务 `repro_assess`（有 API Key 时会发送论文里提到数据集的段落，记入隐私日志；抽取的名称必须能在原文里找到引文，否则丢弃） |
| GET | `/api/papers/{id}/repro/plan?repo_id=` | `{repo_id, plan: null \| {id, title, steps[], summary, note}}` |
| POST | `/api/papers/{id}/repro/plan` | body `{repo_id?, force?}`；需要先有评估（否则 409）；已有计划且未 `force` 返回 409 |
| PATCH | `/api/repro/steps/{sid}` | 编辑 `title/detail/command/cwd/note/done`；**命令或目录改动会使之前的批准作废** |
| POST | `/api/repro/steps/{sid}/approve` | body `{decision: approve\|reject\|reset, note?}`；没有命令的步骤返回 409；返回里 `executed=false`（批准只是记录） |
| POST | `/api/repro/plans/{pid}/steps` | 添加自己的步骤 `{title, command?, detail?, cwd?, phase?}`；有命令即“待批准” |
| DELETE | `/api/repro/steps/{sid}` | 删除步骤，返回最新计划 |
| GET | `/api/repro/plans/{pid}/export?format=md\|sh` | `{filename, format, content}`；`sh` 只启用已批准步骤的命令，其余以注释保留 |
| GET | `/api/papers/{id}/repro/target-candidates` | 从论文文字里按规则找出的候选数字（不是结论）：`{ord, page, kind, label, value, std, unit, quote, how}` |
| GET/POST | `/api/papers/{id}/repro/targets` | 论文里报告的目标数值；POST `{name, value, std?, unit?, tol_rel?, tol_abs?, comparable?, page?, ord?, quote?, source?}`；返回带 `quote_verified`（出处原句是否真在该段落里，未填为 `null`） |
| PATCH/DELETE | `/api/papers/{id}/repro/targets/{tid}` | 修改/删除目标 |
| GET/POST | `/api/papers/{id}/repro/runs` | 导入用户自己跑出的结果：`{name?, content? \| path?, format?: csv\|json\|text, repo_id?, env?, notes?}`；`path` 只接受 `.csv .tsv .json .txt .log .md` 且 ≤2MB；解析不出数值返回 400；返回 `warnings`（如“按列取了均值”） |
| GET/PATCH/DELETE | `/api/repro/runs/{rid}` | 读取（含 `metrics`、`mappings`）/ 修改 `name、env、notes、conclusion`（结论由用户自己写）/ 删除 |
| PUT | `/api/repro/runs/{rid}/mapping` | `{target_id, metric}` 手动指定对应关系；`metric=null` 取消，回到自动对应 |
| GET | `/api/repro/runs/{rid}/compare` | `{items[], summary, reasons[], verdict: null, note}`；每项 `status ∈ within_tolerance \| outside_tolerance \| within_reported_range \| outside_reported_range \| no_tolerance \| unmapped \| not_comparable`；`reasons[].kind ∈ fact \| conjecture`（推测明确标注） |
| GET | `/api/repro/runs/{rid}/report` | Markdown 复现报告：环境、已批准的命令、对比表、线索、用户自己的结论 |

评估报告结构：`sections[{key,title,items[{key,status,title,text,evidence[],note}]}]`、`effort{level,basis[],note}`、`facts`、`note`。`status ∈ found | missing | uncertain | risk | info`；证据 `type=code` 带 `path/line_start/line_end/quote`（`line_start=0` 表示整个文件，如二进制权重），`type=paper` 带 `page/ord/quote`。

### 隔离运行（Docker）

docker 一律用 `sandbox.find_docker()` 返回的完整路径，不依赖 PATH。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/repro/exec/options?image=` | 确认框用：`{available, reason, image, image_present, pull: null \| {image, size_hint}, local_images[], defaults, maxima, minima, mounts[]}`；默认镜像 `python:3.11-slim`；`size_hint` 是大致大小，不在内置表里就写“大小未知” |
| POST | `/api/repro/images/pull` | `{image, confirm: true}` → `{job_id, image}`；**只有用户同意后才拉取**，后台任务 `repro_pull`，可取消；没有 `confirm` 返回 400 |
| POST | `/api/repro/steps/{sid}/execute` | `{confirm: true, command, cwd, image?, network?, limits?{memory_mb,cpus,pids,timeout_s}}` → 运行记录。`command`、`cwd` 必须与界面展示的一致，否则 409；本机没有镜像返回 409（不会自动拉取）；已拒绝的步骤 409；同一仓库同时只跑一个；上限只能调小，超过最大值 400 |
| GET | `/api/papers/{id}/repro/exec?repo_id=` | 运行记录列表 |
| GET | `/api/repro/exec/{eid}?tail=400` | 运行记录 + 日志尾部：`{status: queued\|running\|succeeded\|failed\|timeout\|cancelled, reason, exit_code, image, network, limits, files[], imported_run_id, log[]}`；失败、超时、取消都如实写 `reason` |
| POST | `/api/repro/exec/{eid}/cancel` | 真的 `docker kill` 并 `docker rm -f`；已结束返回 409 |
| DELETE | `/api/repro/exec/{eid}` | 删除记录和这次运行的目录（结束后才可删） |
| GET | `/api/repro/exec/{eid}/metrics` | 预览能解析出的数值：只来自输出文件（csv/tsv/json）和日志中单独成行的 `名称: 数值`。每项带 `source{run_id, kind: file\|log, path, line}`；解析不出就是空列表加说明 |
| POST | `/api/repro/exec/{eid}/import` | `{name?, metrics?[]}` → 一条普通的复现结果（`/repro/runs`），指标保留 `source`，对比表和报告都显示来源；只有 `succeeded` 的运行能导入，否则 409 |

容器的固定参数：`--rm`、`--network none`（用户勾选“允许这一步联网”才改为 bridge，并写入日志和记录）、`--user 10001:10001`、`--cap-drop ALL`、`no-new-privileges`、`--read-only` 加 `/tmp` tmpfs、`--memory`/`--memory-swap`/`--cpus`/`--pids-limit`、代码目录只读挂到 `/code`、这次运行自己的目录挂到 `/work`；不用 `--privileged`，不挂 docker.sock 或任何其它宿主机目录；容器环境只有写死的 `HOME` 等几个变量，传给 docker 命令本身的环境也会剔除名字像密钥的变量。默认上限：内存 2048 MB、2 核、256 个进程、600 秒。运行产物在 `papers/<paper_id>/repro/runs/<eid>/`（`run.log`、`work/`）。应用重启时，停在运行中的记录标记为失败并清理残留容器。

后台任务类型新增：`repro_assess`、`repro_exec`、`repro_pull`。论文删除时 `repro_*` 表随之级联清理。
