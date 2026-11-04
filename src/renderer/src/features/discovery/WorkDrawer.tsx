import { useEffect, useState } from 'react'
import { Code2, ExternalLink, Link2, X } from 'lucide-react'
import { Button, IconButton, Skeleton, Tag } from '@/components/ui'
import { SelectTranslate } from '@/components/SelectTranslate'
import { errorMessage } from '@/lib/api'
import { discoveryApi } from '@/lib/api.discovery'
import { openExternal } from '@/lib/native'
import type { CodeResponse, Work } from '@/lib/types.discovery'
import { WorkItem } from './WorkItem'
import { useWorkActions } from './useWorkActions'
import { useWorkZh } from './workZh'
import { VenueIntro } from './VenueIntro'
import { authorsLine, DIMENSION_LABELS, impactText, originalUrl, versionLabel, venueLine } from './workUtils'

function ExternalA({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      onClick={(e) => {
        e.preventDefault()
        void openExternal(href)
      }}
    >
      {children}
    </a>
  )
}

function Breakdown({ work }: { work: Work }) {
  const keys = Object.keys(DIMENSION_LABELS).filter((k) => work.breakdown[k] !== undefined)
  if (keys.length === 0) return null
  return (
    <div className="pd-dc-breakdown">
      {keys.map((k) => (
        <div key={k} className="pd-dc-bar">
          <span>{DIMENSION_LABELS[k]}</span>
          <span className="pd-dc-bar-track" aria-hidden>
            <span style={{ width: `${Math.round(Math.max(0, Math.min(1, work.breakdown[k])) * 100)}%` }} />
          </span>
          <span className="pd-dc-bar-val">{work.breakdown[k].toFixed(2)}</span>
        </div>
      ))}
    </div>
  )
}

const NO_WORK = { key: '', title: '', abstract: null } as const

export function WorkDrawer({ work, onClose, onOpenOther }: { work: Work | null; onClose: () => void; onOpenOther?: (w: Work) => void }) {
  const [code, setCode] = useState<{ loading: boolean; data?: CodeResponse; error?: string } | null>(null)
  const zh = useWorkZh(work ?? NO_WORK)
  const [similar, setSimilar] = useState<{ loading: boolean; items?: Work[]; error?: string } | null>(null)

  useEffect(() => {
    setCode(null)
    setSimilar(null)
  }, [work?.key])

  useEffect(() => {
    if (!work) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [work, onClose])

  if (!work) return null
  const url = originalUrl(work)
  const impact = impactText(work)

  const findCode = async (): Promise<void> => {
    setCode({ loading: true })
    try {
      setCode({ loading: false, data: await discoveryApi.findCode({ title: work.title, doi: work.doi, arxiv_id: work.arxiv_id }) })
    } catch (err) {
      setCode({ loading: false, error: errorMessage(err) })
    }
  }
  const findSimilar = async (): Promise<void> => {
    setSimilar({ loading: true })
    try {
      const r = await discoveryApi.similar({ doi: work.doi, title: work.title })
      setSimilar({ loading: false, items: r.results })
    } catch (err) {
      setSimilar({ loading: false, error: errorMessage(err) })
    }
  }

  return (
    <>
      <div className="pd-dc-scrim" onClick={onClose} aria-hidden />
      <aside className="pd-dc-drawer" role="dialog" aria-label="论文详情">
        <SelectTranslate paperId={null} standalone contextTitle={work.title} />
        <header className="pd-dc-drawer-head">
          <span className="pd-section-title">论文详情</span>
          <IconButton icon={<X size={16} />} label="关闭 Esc" onClick={onClose} />
        </header>
        <div className="pd-dc-drawer-body">
          <h2 className="pd-dc-drawer-title">{work.title}</h2>
          {zh.title && <p className="pd-dc-title-zh is-open">{zh.title}</p>}
          <p className="pd-dc-meta">
            <span>{authorsLine(work.authors, 8)}</span>
          </p>
          <p className="pd-dc-meta">
            {venueLine(work) && <span>{venueLine(work)}</span>}
            <Tag title={work.status_evidence}>{work.status_label}</Tag>
            {work.peer_reviewed === true && <Tag>同行评审</Tag>}
            {work.core_venue && <Tag>核心来源</Tag>}
            {work.open_access && <Tag>开放获取</Tag>}
          </p>
          <VenueIntro name={work.venue} type={work.venue_type} />
          {work.status_evidence && <p className="pd-dc-sub">状态判定依据：{work.status_evidence}</p>}
          {impact && <p className="pd-dc-sub">{impact}</p>}
          {(work.field || work.topics.length > 0) && <p className="pd-dc-sub">{[work.field, ...work.topics].filter(Boolean).join(' · ')}</p>}

          <div className="pd-dc-drawer-actions">
            <DrawerPrimary work={work} />
            {url && (
              <Button size="sm" variant="secondary" icon={<ExternalLink size={14} />} onClick={() => void openExternal(url)}>
                打开原文
              </Button>
            )}
          </div>

          {work.abstract && (
            <section>
              <h3 className="pd-dc-h">摘要</h3>
              {zh.abstract && <p className="pd-dc-abstract is-open">{zh.abstract}</p>}
              <p className={`pd-dc-abstract is-open${zh.abstract ? ' pd-dc-abstract-en' : ''}`} data-translatable>{work.abstract}</p>
              {/(?:…|\.\.\.)\s*$/.test(work.abstract) && <p className="pd-dc-sub">数据源只给了摘要的开头，完整摘要请打开原文。</p>}
            </section>
          )}
          {work.reason && (
            <section>
              <h3 className="pd-dc-h">{work.reason_source === 'ai' ? '推荐理由 · AI 生成' : '入选依据'}</h3>
              <p className="pd-dc-reason" style={{ margin: 0 }}>{work.reason}</p>
            </section>
          )}
          {work.summary && (
            <section>
              <h3 className="pd-dc-h">导读 · {work.summary.source}</h3>
              <p className="pd-dc-sub">研究问题：{work.summary.research_question}</p>
              <p className="pd-dc-sub">核心贡献：{work.summary.contribution}</p>
              <p className="pd-dc-sub">为什么值得读：{work.summary.why_read}</p>
            </section>
          )}

          {work.score > 0 && (
            <section>
              <h3 className="pd-dc-h">排序依据</h3>
              <Breakdown work={work} />
              {work.rank_note && <p className="pd-dc-sub">{work.rank_note}</p>}
            </section>
          )}

          <section>
            <h3 className="pd-dc-h">标识与来源</h3>
            <dl className="pd-dc-kv">
              {work.doi && (<><dt>DOI</dt><dd><ExternalA href={`https://doi.org/${work.doi}`}>{work.doi}</ExternalA></dd></>)}
              {work.arxiv_id && (<><dt>arXiv</dt><dd><ExternalA href={`https://arxiv.org/abs/${work.arxiv_id}`}>{work.arxiv_id}</ExternalA></dd></>)}
              {work.pmid && (<><dt>PMID</dt><dd>{work.pmid}</dd></>)}
              <dt>数据来源</dt>
              <dd>
                {work.sources.map((s, i) => (
                  <span key={s.name}>
                    {i > 0 && ' · '}
                    {s.url ? <ExternalA href={s.url}>{s.name}</ExternalA> : s.name}
                  </span>
                ))}
              </dd>
            </dl>
          </section>

          {work.versions.length > 0 && (
            <section>
              <h3 className="pd-dc-h">其它版本</h3>
              <ul className="pd-dc-list">
                {work.versions.map((v, i) => {
                  const href = v.url || (v.doi ? `https://doi.org/${v.doi}` : v.arxiv_id ? `https://arxiv.org/abs/${v.arxiv_id}` : null)
                  return (
                    <li key={i}>
                      <Tag>{versionLabel(v)}</Tag>{' '}
                      {href ? <ExternalA href={href}>{v.venue || v.doi || v.arxiv_id || href}</ExternalA> : <span>{v.venue || '未提供链接'}</span>}
                    </li>
                  )
                })}
              </ul>
            </section>
          )}

          <section>
            <h3 className="pd-dc-h">查找代码</h3>
            {!code && (
              <Button size="sm" variant="secondary" icon={<Code2 size={14} />} onClick={() => void findCode()}>
                在 GitHub 上查找
              </Button>
            )}
            {code?.loading && <Skeleton height={48} />}
            {code?.error && <p className="pd-status-text pd-status-text--danger">{code.error}</p>}
            {code?.data && (
              <>
                {code.data.warnings.map((w) => (
                  <p key={w} className="pd-status-text pd-status-text--warning">{w}</p>
                ))}
                {code.data.candidates.length === 0 && <p className="pd-dc-sub">没有找到候选仓库。</p>}
                <ul className="pd-dc-list">
                  {code.data.candidates.map((c) => (
                    <li key={c.full_name}>
                      <div>
                        <ExternalA href={c.url}>{c.full_name}</ExternalA>{' '}
                        <Tag tone={c.match === 'high' ? 'success' : 'neutral'}>{c.match === 'high' ? '高匹配' : c.match === 'mention' ? '仅提及' : '候选'}</Tag>
                        {c.stars !== null && <span className="pd-dc-sub"> ★ {c.stars}</span>}
                      </div>
                      {c.description && <div className="pd-dc-sub">{c.description}</div>}
                      <div className="pd-dc-sub">匹配依据：{c.evidence.join('；')}</div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>

          <section>
            <h3 className="pd-dc-h">相似论文</h3>
            {!similar && (
              <Button size="sm" variant="secondary" icon={<Link2 size={14} />} onClick={() => void findSimilar()}>
                查看相关工作
              </Button>
            )}
            {similar?.loading && <Skeleton height={64} />}
            {similar?.error && <p className="pd-status-text pd-status-text--danger">{similar.error}</p>}
            {similar?.items && (
              <div className="pd-dc-compact">
                {similar.items.map((w) => (
                  <WorkItem key={w.key} work={w} showAbstract={false} onOpen={(x) => onOpenOther?.(x)} />
                ))}
              </div>
            )}
          </section>
        </div>
      </aside>
    </>
  )
}

function DrawerPrimary({ work }: { work: Work }) {
  const a = useWorkActions(work)
  return work.in_library ? (
    <Button size="sm" variant="primary" onClick={a.openLibrary}>
      在文献库中打开
    </Button>
  ) : (
    <Button size="sm" variant="primary" loading={a.importing} disabled={!a.canImport} title={a.importHint || undefined} onClick={() => void a.addToLibrary()}>
      {a.importing ? '导入中…' : '加入文献库'}
    </Button>
  )
}
