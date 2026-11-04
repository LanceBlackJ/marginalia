import { useEffect, useMemo, useRef, useState } from 'react'
import { RefreshCw, Search } from 'lucide-react'
import { useReaderStore } from '@/features/reader/readerStore'
import { useGlossaryStore } from './glossaryStore'
import './glossary.css'

/**
 * 术语表：读这篇论文之前该先弄懂的概念（导入时在后台做好）。
 * “论文里定义的”附原话出处（后端核对过，点一下跳到原文）；“论文默认你已懂的”是 AI 自己的解释，不是论文内容，单独成组并标明。
 */
export function GlossaryPanel() {
  const { terms, status, error, focus } = useGlossaryStore()
  const jumpToOrd = useReaderStore((s) => s.jumpToOrd)
  const [q, setQ] = useState('')
  const refs = useRef(new Map<string, HTMLElement>())

  useEffect(() => {
    if (!focus) return
    setQ('')
    const t = window.setTimeout(() => refs.current.get(focus)?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 60)
    return () => window.clearTimeout(t)
  }, [focus])

  const shown = useMemo(() => {
    const k = q.trim().toLowerCase()
    return k ? terms.filter((t) => `${t.term} ${t.zh} ${t.plain}`.toLowerCase().includes(k)) : terms
  }, [terms, q])
  const own = shown.filter((t) => t.in_paper)
  const assumed = shown.filter((t) => !t.in_paper)

  const card = (t: (typeof terms)[number]) => (
    <article key={t.term} className="pd-gl-item" data-focus={focus === t.term} ref={(el) => void (el ? refs.current.set(t.term, el) : refs.current.delete(t.term))}>
      <header>
        <b>{t.term}</b>
        {t.zh && <span className="pd-gl-zh">{t.zh}</span>}
        <span className="pd-gl-kind">{t.kind}</span>
      </header>
      <p>{t.plain}</p>
      {t.why && <p className="pd-gl-why">在这篇里：{t.why}</p>}
      {t.in_paper && t.quote && t.ord !== null && (
        <button type="button" className="pd-gl-quote" onClick={() => void jumpToOrd(t.ord as number)} title="跳到原文这一段">
          <q>{t.quote}</q>
          <span className="pd-cite">P{t.ord}</span>
        </button>
      )}
    </article>
  )

  return (
    <div className="pd-gl">
      {status && terms.length === 0 && (
        <div className="pd-gl-state">
          {status.state === 'unavailable' && <p>这篇论文的正文太少（或还没有识别出文字），没有生成术语表。</p>}
          {(status.state === 'running' || status.state === 'pending') && <p>正在后台整理这篇论文的术语表（通常几十秒），完成后这里会自动出现。</p>}
          {status.state === 'failed' && (
            <p>
              术语表没生成成功{status.error ? `：${status.error}` : ''}
              <button type="button" className="pd-para-link" onClick={() => void useGlossaryStore.getState().retry()}>
                <RefreshCw size={12} /> 重试
              </button>
            </p>
          )}
        </div>
      )}
      {!status && !error && <p className="pd-aux pd-muted pd-gl-state">正在读取……</p>}
      {error && <p className="pd-field-error pd-gl-state">{error}</p>}

      {terms.length > 0 && (
        <>
          <label className="pd-gl-search">
            <Search size={13} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`在 ${terms.length} 个词条里找`} />
          </label>
          {own.length > 0 && (
            <section>
              <h4>论文里定义的 · {own.length}</h4>
              {own.map(card)}
            </section>
          )}
          {assumed.length > 0 && (
            <section>
              <h4>论文默认你已经懂的 · {assumed.length}</h4>
              <p className="pd-gl-note">这一组论文里没有解释，下面是 AI 自己的解释，不是论文内容。</p>
              {assumed.map(card)}
            </section>
          )}
          {shown.length === 0 && <p className="pd-aux pd-muted pd-gl-state">没有匹配的词条。</p>}
        </>
      )}
    </div>
  )
}
