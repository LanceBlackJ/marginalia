import { useState } from 'react'
import { ROLE_LABEL, fmtBytes, shortSha } from './codeUtils'
import { useCodeStore } from './codeStore'
import type { AuthoredBy, CodeRepo } from '@/lib/types.code'
import { ViewFrame, ViewHead, ViewSection } from './ViewFrame'

const AUTHORED: Record<AuthoredBy, string> = {
  author: '论文作者提供，来自论文里声明的链接',
  user: '用户自行提供，不是论文声明的，可能与论文不一致',
  self: '用户自己的实现'
}
const ORIGIN: Record<CodeRepo['origin'], string> = { github: 'GitHub', zip: '压缩包', folder: '本地文件夹', self_implemented: '自行实现' }

/** 列表默认只显示前 limit 条，其余折叠；只多出一两条时不折叠（折叠它们没有意义）。 */
function useFold<T>(items: T[], limit: number) {
  const [all, setAll] = useState(false)
  const foldable = items.length - limit > 2
  const expanded = all || !foldable
  return {
    shown: expanded ? items : items.slice(0, limit),
    rest: expanded ? [] : items.slice(limit),
    hidden: expanded ? 0 : items.length - limit,
    all: all && foldable,
    toggle: () => setAll((a) => !a)
  }
}

function FoldButton({ hidden, all, toggle, noun }: { hidden: number; all: boolean; toggle: () => void; noun: string }) {
  if (!hidden && !all) return null // noun 形如 "个" 或 "个（其中 3 个在第三方拷贝里）"
  return (
    <button type="button" className="pd-link pd-ov-more" onClick={toggle}>
      {all ? '收起' : `显示其余 ${hidden} ${noun}`}
    </button>
  )
}

function FileLink({ path }: { path: string }) {
  const openFile = useCodeStore((s) => s.openFile)
  return (
    <button type="button" className="pd-link pd-ov-mono" onClick={() => void openFile(path)}>
      {path}
    </button>
  )
}

export function OverviewView() {
  const repo = useCodeStore((s) => s.repos.find((r) => r.id === s.repoId) ?? null)
  const openFile = useCodeStore((s) => s.openFile)
  const ov = repo?.overview
  const vendDirs = ov?.vendored ?? []
  const isVend = (p: string) => vendDirs.some((d) => p.startsWith(`${d.path}/`))
  const entries = ov ? [...ov.entry_points].sort((a, b) => Number(a.vendored ?? isVend(a.path)) - Number(b.vendored ?? isVend(b.path))) : []
  const deps = ov ? [...ov.dependency_files, ...ov.config_files] : []
  const ownDeps = deps.filter((p) => !isVend(p))
  const entryFold = useFold(entries, 8)
  const depFold = useFold(ownDeps, 10)
  const ownWarns = (ov?.parse_warnings ?? []).filter((w) => !isVend(w.path))
  const warnFold = useFold(ownWarns, 5)
  if (!repo || !ov) return null
  const langs = Object.entries(ov.languages).sort((a, b) => b[1].lines - a[1].lines)
  const maxLines = Math.max(1, ...langs.map(([, v]) => v.lines))
  const roles = Object.entries(ov.roles).sort((a, b) => b[1] - a[1])
  const vendInRest = entryFold.rest.filter((e) => e.vendored ?? isVend(e.path)).length
  const vendDeps = deps.length - ownDeps.length
  const vendWarns = ov.parse_warnings.length - ownWarns.length

  return (
    <ViewFrame width="narrow">
      <div className="pd-ov-inner">
        <ViewHead title="项目概览" desc={`${repo.name ?? repo.origin} 的来源、语言、依赖和入口点，只来自静态分析，不会运行任何代码。`} />
        {repo.warnings.length > 0 && (
          <ul className="pd-ov-warns">
            {repo.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        )}

        {vendDirs.length > 0 && (
          <ViewSection title="拷贝来的第三方代码" strong>
            <p className="pd-ov-lead">
              {vendDirs.map((d) => d.path).join('、')} 文件很多，又自带依赖文件，多半是整个拷进来的第三方项目，不是论文的核心实现。下面的入口、依赖和框架先列作者自己的部分，AI 讲解也会降低它的权重。
            </p>
            <ul className="pd-ov-dirs">
              {vendDirs.map((d) => (
                <li key={d.path}>
                  <span className="pd-ov-mono">{d.path}/</span>
                  <span className="pd-aux pd-muted">
                    {d.files} 个文件 · {d.lines.toLocaleString()} 行
                  </span>
                </li>
              ))}
            </ul>
          </ViewSection>
        )}

        <ViewSection title="入口点" hint="主程序入口、训练与评估脚本。运行命令来自 README，仅供参考，不会被执行。">
          {entries.length === 0 ? (
            <p className="pd-muted">没有识别到入口。</p>
          ) : (
            <>
              <ul className="pd-ov-entries">
                {entryFold.shown.map((e, i) => (
                  <li key={`${e.path}-${i}`} data-vendored={(e.vendored ?? isVend(e.path)) || undefined}>
                    <button type="button" className="pd-ov-entry" onClick={() => void openFile(e.path)}>
                      <span className="pd-ov-mono pd-ellipsis">{e.path}</span>
                      <span className="pd-aux pd-muted">
                        {e.role ? `${ROLE_LABEL[e.role]} · ` : ''}
                        {e.reason}
                        {(e.vendored ?? isVend(e.path)) ? ' · 第三方拷贝' : ''}
                      </span>
                    </button>
                    {e.command && <code className="pd-ov-cmd">{e.command}</code>}
                  </li>
                ))}
              </ul>
              <FoldButton {...entryFold} noun={vendInRest && vendInRest < entryFold.hidden ? `个（其中 ${vendInRest} 个在第三方拷贝里）` : vendInRest ? '个，都在第三方拷贝里' : '个'} />
            </>
          )}
        </ViewSection>

        <ViewSection title="来源与版本">
          <dl className="pd-kv pd-ov-kv">
            <dt>来源</dt>
            <dd>{AUTHORED[repo.authored_by]}</dd>
            <dt>获取方式</dt>
            <dd>{ORIGIN[repo.origin]}</dd>
            {repo.url && (
              <>
                <dt>地址</dt>
                <dd className="pd-ov-mono">{repo.url}</dd>
              </>
            )}
            {repo.branch && (
              <>
                <dt>分支</dt>
                <dd>{repo.branch}</dd>
              </>
            )}
            {repo.commit_sha && (
              <>
                <dt>commit</dt>
                <dd className="pd-ov-mono" title={repo.commit_sha}>
                  {shortSha(repo.commit_sha)}
                  {repo.commit_date ? ` · ${repo.commit_date.slice(0, 10)}` : ''}
                </dd>
              </>
            )}
            {repo.downloaded_at && (
              <>
                <dt>获取时间</dt>
                <dd>{repo.downloaded_at.slice(0, 16).replace('T', ' ')}</dd>
              </>
            )}
            <dt>大小</dt>
            <dd>
              {fmtBytes(repo.size_bytes)} · {ov.files} 个文件 · {ov.source_files} 个源码文件
            </dd>
            <dt>静态分析</dt>
            <dd>
              {ov.symbols} 个符号 · {ov.calls} 处调用，从不运行代码
            </dd>
          </dl>
        </ViewSection>

        <ViewSection title="语言">
          <ul className="pd-ov-langs">
            {langs.map(([name, v]) => (
              <li key={name}>
                <span className="pd-ov-lang">{name}</span>
                <span className="pd-ov-bar" aria-hidden>
                  <span style={{ width: `${Math.max(2, (v.lines / maxLines) * 100)}%` }} />
                </span>
                <span className="pd-aux pd-muted">
                  {v.files} 个文件 · {v.lines.toLocaleString()} 行
                </span>
              </li>
            ))}
          </ul>
        </ViewSection>

        {ov.frameworks.length > 0 && (
          <ViewSection title="框架与主要依赖" hint={vendDirs.length > 0 ? '不含拷贝来的第三方代码' : undefined}>
            <p>{ov.frameworks.join('、')}</p>
          </ViewSection>
        )}

        {deps.length > 0 && (
          <ViewSection title="依赖与配置文件">
            {ownDeps.length > 0 && (
              <>
                <ul className="pd-ov-files">
                  {depFold.shown.map((p) => (
                    <li key={p}>
                      <FileLink path={p} />
                    </li>
                  ))}
                </ul>
                <FoldButton {...depFold} noun="个" />
              </>
            )}
            {vendDeps > 0 && <p className="pd-aux pd-muted">另有 {vendDeps} 个在第三方拷贝里，不列出。</p>}
          </ViewSection>
        )}

        <ViewSection title="文件角色">
          <p className="pd-ov-roles">
            {roles.map(([r, n]) => (
              <span key={r}>
                {ROLE_LABEL[r as keyof typeof ROLE_LABEL] ?? r} <b>{n}</b>
              </span>
            ))}
          </p>
          {(ov.skipped.binary > 0 || ov.skipped.too_large > 0) && (
            <p className="pd-aux pd-muted">
              已跳过：二进制文件 {ov.skipped.binary} 个、过大文件 {ov.skipped.too_large} 个，只在文件树里标注，不显示内容。
            </p>
          )}
        </ViewSection>

        {ov.top_dirs.length > 0 && (
          <ViewSection title="主要目录">
            <ul className="pd-ov-dirs">
              {ov.top_dirs.slice(0, 12).map((d) => (
                <li key={d.path}>
                  <span className="pd-ov-mono">{d.path === '.' ? '(根目录)' : `${d.path}/`}</span>
                  <span className="pd-aux pd-muted">
                    {d.files} 个文件{isVend(`${d.path}/x`) ? ' · 第三方拷贝' : ''}
                  </span>
                </li>
              ))}
            </ul>
          </ViewSection>
        )}

        {ov.parse_warnings.length > 0 && (
          <ViewSection title="解析提示" hint="这些文件没能用标准语法树解析，用了容错解析，符号和调用关系可能不完整。">
            {ownWarns.length > 0 && (
              <>
                <ul className="pd-ov-parse">
                  {warnFold.shown.map((w) => (
                    <li key={w.path}>
                      <span className="pd-ov-mono">{w.path}</span>
                      <span className="pd-aux pd-muted">{w.message}</span>
                    </li>
                  ))}
                </ul>
                <FoldButton {...warnFold} noun="条" />
              </>
            )}
            {vendWarns > 0 && <p className="pd-aux pd-muted">另有 {vendWarns} 条在第三方拷贝里，不列出。</p>}
          </ViewSection>
        )}
      </div>
    </ViewFrame>
  )
}
