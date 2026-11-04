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

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <ViewSection title={title}>
      {hint && <p className="pd-aux pd-muted pd-ov-hint">{hint}</p>}
      {children}
    </ViewSection>
  )
}

export function OverviewView() {
  const repo = useCodeStore((s) => s.repos.find((r) => r.id === s.repoId) ?? null)
  const openFile = useCodeStore((s) => s.openFile)
  const [allWarn, setAllWarn] = useState(false)
  if (!repo?.overview) return null
  const ov = repo.overview
  const langs = Object.entries(ov.languages).sort((a, b) => b[1].lines - a[1].lines)
  const maxLines = Math.max(1, ...langs.map(([, v]) => v.lines))
  const roles = Object.entries(ov.roles).sort((a, b) => b[1] - a[1])
  const warns = allWarn ? ov.parse_warnings : ov.parse_warnings.slice(0, 5)

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

        <Section title="来源与版本">
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
        </Section>

        <Section title="语言">
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
        </Section>

        {ov.frameworks.length > 0 && (
          <Section title="框架与主要依赖">
            <p>{ov.frameworks.join('、')}</p>
          </Section>
        )}

        {(ov.dependency_files.length > 0 || ov.config_files.length > 0) && (
          <Section title="依赖与配置文件">
            <ul className="pd-ov-files">
              {[...ov.dependency_files, ...ov.config_files].map((p) => (
                <li key={p}>
                  <button type="button" className="pd-link pd-ov-mono" onClick={() => void openFile(p)}>
                    {p}
                  </button>
                </li>
              ))}
            </ul>
          </Section>
        )}

        <Section title="入口点" hint="主程序入口、训练与评估脚本。运行命令来自 README，仅供参考，不会被执行。">
          {ov.entry_points.length === 0 ? (
            <p className="pd-muted">没有识别到入口。</p>
          ) : (
            <ul className="pd-ov-entries">
              {ov.entry_points.map((e, i) => (
                <li key={`${e.path}-${i}`}>
                  <button type="button" className="pd-ov-entry" onClick={() => void openFile(e.path)}>
                    <span className="pd-ov-mono pd-ellipsis">{e.path}</span>
                    <span className="pd-aux pd-muted">
                      {e.role ? `${ROLE_LABEL[e.role]} · ` : ''}
                      {e.reason}
                    </span>
                  </button>
                  {e.command && <code className="pd-ov-cmd">{e.command}</code>}
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="文件角色">
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
        </Section>

        {ov.top_dirs.length > 0 && (
          <Section title="主要目录">
            <ul className="pd-ov-dirs">
              {ov.top_dirs.slice(0, 12).map((d) => (
                <li key={d.path}>
                  <span className="pd-ov-mono">{d.path || '/'}</span>
                  <span className="pd-aux pd-muted">{d.files} 个文件</span>
                </li>
              ))}
            </ul>
          </Section>
        )}

        {ov.parse_warnings.length > 0 && (
          <Section title="解析提示" hint="这些文件没能用标准语法树解析，用了容错解析，符号和调用关系可能不完整。">
            <ul className="pd-ov-parse">
              {warns.map((w) => (
                <li key={w.path}>
                  <span className="pd-ov-mono">{w.path}</span>
                  <span className="pd-aux pd-muted">{w.message}</span>
                </li>
              ))}
            </ul>
            {ov.parse_warnings.length > 5 && (
              <button type="button" className="pd-link" onClick={() => setAllWarn(!allWarn)}>
                {allWarn ? '收起' : `显示全部 ${ov.parse_warnings.length} 条`}
              </button>
            )}
          </Section>
        )}
      </div>
    </ViewFrame>
  )
}
