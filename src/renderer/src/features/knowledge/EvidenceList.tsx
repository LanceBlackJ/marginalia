import { useNavigate } from 'react-router-dom'
import type { KbEvidence } from '@/lib/types.discovery'
import { openEvidenceInReader } from '@/features/shared/evidenceNav'

/** 知识条目 / 候选的原文证据：引文 + 位置 + 跳回阅读器。证据由后端从数据库重新读取，不是模型自己写的。 */
export function EvidenceList({ evidence, empty }: { evidence: KbEvidence[]; empty?: string }) {
  const navigate = useNavigate()
  if (evidence.length === 0) return empty ? <p className="pd-dc-hint">{empty}</p> : null
  return (
    <ul className="pd-kb-ev">
      {evidence.map((ev, i) => (
        <li key={`${ev.paper_id}-${ev.ord ?? ev.note_id ?? i}`}>
          <blockquote className="pd-cmp-quote">{ev.quote}</blockquote>
          <div className="pd-dc-sub">
            <span>{ev.kind === 'note' ? '我的笔记' : '论文原文'}</span>
            <span>{ev.paper_title.slice(0, 60)}</span>
            {ev.page && <span>第 {ev.page} 页</span>}
            {ev.section_title && <span>{ev.section_title}</span>}
            <button type="button" className="pd-dc-more" onClick={() => openEvidenceInReader(navigate, ev.paper_id, { page: ev.page, bbox: ev.bbox })}>
              在原文中查看
            </button>
          </div>
        </li>
      ))}
    </ul>
  )
}
