import { memo, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { openExternal } from '@/lib/native'
import type { CompareEvidence } from '@/lib/types.discovery'
import { openEvidenceInReader } from '@/features/shared/evidenceNav'
import { citesToLinks, type EvidenceIndex } from './evidenceIndex'

function tooltip(ev: CompareEvidence): string {
  const where = [ev.page ? `第 ${ev.page} 页` : null, ev.section_title].filter(Boolean).join(' · ')
  return `《${ev.paper_title}》${where ? ` ${where}` : ''}\n“${ev.quote.slice(0, 160)}${ev.quote.length > 160 ? '…' : ''}”`
}

/** 一个可核实的引用标记：点击跳到阅读器中的原文位置（摘要类证据没有位置，不可点击）。 */
export function EvidenceChip({ code, index, invalid }: { code: string; index: EvidenceIndex; invalid?: Set<string> }) {
  const navigate = useNavigate()
  const ev = index.get(code)
  const text = code.replace(':', '·').replace('ABS', '摘要')
  if (!ev || invalid?.has(code)) {
    return (
      <span className="pd-cmp-chip is-invalid" title="这个引用编号在所给片段中找不到，无法核实">
        {text}
      </span>
    )
  }
  if (ev.kind !== 'paper_text' || !ev.page) {
    return (
      <span className="pd-cmp-chip" title={`${tooltip(ev)}\n${ev.kind === 'abstract' ? '来自摘要，没有原文位置' : '来自元数据'}`}>
        {text}
      </span>
    )
  }
  return (
    <button type="button" className="pd-cmp-chip" title={tooltip(ev)} onClick={() => openEvidenceInReader(navigate, ev.paper_id, { page: ev.page, bbox: ev.bbox })}>
      {text}
    </button>
  )
}

interface Props {
  text: string
  index: EvidenceIndex
  invalid?: Set<string>
  inline?: boolean
}

/** 渲染带 [A:P12] 引用的 Markdown：引用变成 chip，核验失败的引用以删除线标出。 */
export const CompareMarkdown = memo(function CompareMarkdown({ text, index, invalid, inline }: Props) {
  const source = useMemo(() => citesToLinks(text), [text])
  return (
    <div className={`pd-md${inline ? ' pd-md--inline' : ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => (url.startsWith('cite:') ? url : defaultUrlTransform(url))}
        components={{
          a({ href, children }) {
            if (href?.startsWith('cite:')) return <EvidenceChip code={href.slice(5)} index={index} invalid={invalid} />
            return (
              <a
                href={href}
                onClick={(e) => {
                  e.preventDefault()
                  if (href) void openExternal(href)
                }}
              >
                {children}
              </a>
            )
          }
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
})
