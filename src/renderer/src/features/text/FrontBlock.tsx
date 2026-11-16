import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { FrontInfo } from './frontMatter'
import { Linkified } from './Linkified'

/**
 * 论文开头的信息块：标题、作者、DOI 与日期、单位（可展开）。
 * 内容来自“首部信息”章节的段落，只在显示层整理；每一行带 data-ord，证据和搜索仍能定位到原来的段落。
 */
export function FrontBlock({ title, front, titleZh, view = 'text' }: { title: string; front: FrontInfo; titleZh?: string; view?: 'text' | 'both' | 'target' }) {
  // 单位是作者信息的必要部分，默认直接展开；仍保留收起按钮，长作者名单/多单位论文可以节省首屏空间。
  const [affOpen, setAffOpen] = useState(true)
  const meta: Array<{ ord: number; text: string }> = [...(front.doi ? [front.doi] : []), ...front.dates]
  return (
    <header className="pd-front" data-page={1}>
      {view === 'target' && titleZh ? (
        <h1 className="pd-front-title" title={`原文：${title}`}>
          {titleZh}
        </h1>
      ) : (
        <>
          <h1 className="pd-front-title">{title}</h1>
          {view === 'both' && titleZh && <h1 className="pd-front-title pd-front-title--zh">{titleZh}</h1>}
        </>
      )}
      {front.authors && (
        <p className="pd-front-authors" data-ord={front.authors.ord}>
          {(front.authors.entries ?? front.authors.names.map((name) => ({ name, markers: [] }))).map((author, i) => (
            <span key={`${author.name}-${i}`}>
              {i > 0 && '，'}
              {author.name}
              {author.markers.length > 0 && <sup className="pd-front-author-markers">{author.markers.join(' ')}</sup>}
            </span>
          ))}
        </p>
      )}
      {meta.length > 0 && (
        <p className="pd-front-meta">
          {meta.map((m) => (
            <span key={m.ord} data-ord={m.ord}>
              <Linkified text={m.text} />
            </span>
          ))}
        </p>
      )}
      {front.affiliations && (
        <div className="pd-front-aff" data-ord={front.affiliations.ord}>
          <button type="button" className="pd-tp-link" onClick={() => setAffOpen((v) => !v)} aria-expanded={affOpen}>
            {affOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            单位 {front.affiliations.lines.length}
          </button>
          {affOpen && (
            <ul>
              {front.affiliations.lines.map((l, i) => (
                <li key={i}>{l}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {front.emails.length > 0 && (
        <p className="pd-front-meta">
          {front.emails.map((e, i) => (
            <span key={`${e.ord}-${i}`} data-ord={e.ord}>
              <Linkified text={e.text} />
            </span>
          ))}
        </p>
      )}
      {front.other.map((o) => (
        <p key={o.ord} className="pd-front-other" data-ord={o.ord}>
          <Linkified text={o.text} />
        </p>
      ))}
    </header>
  )
}
