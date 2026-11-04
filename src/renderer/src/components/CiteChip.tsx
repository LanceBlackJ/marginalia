import { createContext, useContext } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/lib/api'
import { truncate } from '@/lib/format'
import { refTag, type CiteRef } from '@/lib/citations'
import type { BBox, Evidence } from '@/lib/types'
import { openSupplementEvidence, setPendingJump } from '@/features/materials/crossJump'
import { useReaderStore } from '@/features/reader/readerStore'

/** 引用标记 → 证据：正文 "P12"，补充材料 "S1P12"。 */
export type EvidenceMap = Map<string, Evidence>

export function evidenceTag(ev: Pick<Evidence, 'ord' | 'ref' | 'source'>): string {
  if (ev.ref) return ev.ref
  return ev.source ? `S${ev.source.doc}P${ev.ord}` : `P${ev.ord}`
}

export function buildEvidenceMap(evidence: Evidence[] | undefined): EvidenceMap {
  return new Map((evidence ?? []).map((e) => [evidenceTag(e), e]))
}

/** 无法核实的引用集合（正文的整数序号 + 全部字符串标记，如 "S1P99"）。 */
export function buildInvalidSet(invalidCitations: number[] | undefined, invalidRefs?: string[]): Set<string> {
  return new Set([...(invalidCitations ?? []).map((o) => `P${o}`), ...(invalidRefs ?? [])])
}

/** "S1P12" / "P12" 标记 → CiteRef（无法解析时返回 null）。 */
export function refFromTag(tag: string): CiteRef | null {
  const m = /^(?:S(\d+)P|P)(\d+)$/.exec(tag)
  return m ? { doc: m[1] ? Number(m[1]) : 0, ord: Number(m[2]) } : null
}

/**
 * 当前内容所属的论文。阅读器内为 null（正文引用直接在已打开的 PDF 中定位）；
 * 在阅读器之外（如数据集详情页）提供论文 id，点击正文引用会先打开那篇论文再定位。
 */
export const CitePaperContext = createContext<string | null>(null)

type KnownPos = { page: number; bbox?: BBox | null }

/** 正文引用跳转：阅读器内直接定位；阅读器外先导航到论文再定位。 */
function useMainJump(): (ord: number, known?: KnownPos) => void {
  const navigate = useNavigate()
  const ctxPaper = useContext(CitePaperContext)
  const jumpToOrd = useReaderStore((s) => s.jumpToOrd)
  return (ord, known) => {
    if (!ctxPaper) {
      void jumpToOrd(ord, known ? { page: known.page, bbox: known.bbox ?? [0, 0, 0, 0] } : undefined)
      return
    }
    void (async () => {
      let pos: KnownPos | undefined = known
      if (!pos) {
        try {
          const p = await api.papers.passage(ctxPaper, ord)
          pos = { page: p.page, bbox: p.bbox }
        } catch {
          return
        }
      }
      setPendingJump({ paperId: ctxPaper, page: pos.page, bbox: pos.bbox })
      navigate(`/reader/${ctxPaper}`)
    })()
  }
}

/** 证据跳转：正文 → 当前阅读器定位；补充材料 → 打开子论文的阅读器并定位。 */
export function useEvidenceJump(): (ev: Evidence) => void {
  const navigate = useNavigate()
  const mainJump = useMainJump()
  return (ev) => {
    if (ev.source) openSupplementEvidence(navigate, ev)
    else mainJump(ev.ord, { page: ev.page, bbox: ev.bbox })
  }
}

interface CiteChipProps {
  cite: CiteRef
  evidence: EvidenceMap
  invalid: Set<string>
}

/** 单个引用 chip：已核实 → 点击跳转并高亮原文；无法核实 → 灰色虚线样式；未知（流式过程中）→ 按需查询段落后跳转。 */
export function CiteChip({ cite, evidence, invalid }: CiteChipProps) {
  const mainJump = useMainJump()
  const jump = useEvidenceJump()
  const tag = refTag(cite)
  const ev = evidence.get(tag)

  if (invalid.has(tag) && !ev) {
    return (
      <span className="pd-cite pd-cite--invalid" title={`模型引用了 [${tag}]，但${cite.doc ? '补充材料' : '论文'}中没有这一段，无法核实。`}>
        无法核实
      </span>
    )
  }

  if (cite.doc > 0) {
    // 补充材料引用：只有后端核实过的证据才知道它属于哪个子论文
    if (!ev) {
      return (
        <span className="pd-cite pd-cite--pending" title={`补充材料 ${cite.doc} 的段落 ${cite.ord}，回答完成后可点击定位`}>
          {tag}
        </span>
      )
    }
    return (
      <button
        type="button"
        className="pd-cite"
        title={`${ev.source?.label ?? `补充材料 ${cite.doc}`} · 第 ${ev.page} 页${ev.section_title ? ` · ${ev.section_title}` : ''}\n${truncate(ev.quote, 160)}`}
        onClick={() => jump(ev)}
      >
        S{cite.doc} · p.{ev.page}
      </button>
    )
  }

  const title = ev ? `第 ${ev.page} 页${ev.section_title ? ` · ${ev.section_title}` : ''}\n${truncate(ev.quote, 160)}` : `段落 P${cite.ord}`
  return (
    <button type="button" className="pd-cite" title={title} onClick={() => mainJump(cite.ord, ev ? { page: ev.page, bbox: ev.bbox } : undefined)}>
      {ev ? `p.${ev.page}` : `P${cite.ord}`}
    </button>
  )
}

interface CiteGroupProps {
  /** 正文段落序号（兼容旧用法） */
  ords?: number[]
  /** 含补充材料的引用 */
  refs?: CiteRef[]
  evidence: EvidenceMap
  invalid: Set<string>
}

export function CiteGroup({ ords, refs, evidence, invalid }: CiteGroupProps) {
  const raw: CiteRef[] = refs ?? (ords ?? []).map((ord) => ({ doc: 0, ord }))
  const list = raw.filter((r, i) => raw.findIndex((x) => x.doc === r.doc && x.ord === r.ord) === i)
  // 相邻的多个引用合并成一串小标记，整体算一处引用
  return (
    <span className="pd-cites" data-count={list.length}>
      {list.map((c) => (
        <CiteChip key={refTag(c)} cite={c} evidence={evidence} invalid={invalid} />
      ))}
    </span>
  )
}
