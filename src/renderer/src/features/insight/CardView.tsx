import { useMemo, useState } from 'react'
import { ChevronDown, ChevronUp, Copy, MoreHorizontal, NotebookPen, Pin, PinOff, Trash2 } from 'lucide-react'
import { IconButton, Menu, type MenuItem, toast } from '@/components/ui'
import { buildEvidenceMap, buildInvalidSet, CiteGroup, evidenceTag, refFromTag, useEvidenceJump } from '@/components/CiteChip'
import { Markdown } from '@/components/Markdown'
import { truncate } from '@/lib/format'
import { openExternal } from '@/lib/native'
import type { Evidence, InsightCard } from '@/lib/types'
import { useNotesStore } from '@/features/notes/notesStore'
import { SupplementPermissionHint } from '@/features/materials/SupplementPermissionHint'
import { EquationHeader, EquationSections, SymbolTable } from '@/features/formula/EquationCardExtras'
import { MathText } from '@/features/formula/Tex'
import { isEquationCard } from '@/lib/types.vision'
import { useInsightStore } from './insightStore'
import { cardMetaParts, questionLine, verifyLabel } from './insightModel'
import { QuestionBubble } from './QuestionBubble'

const VERIFY_HINT: Record<string, string | null> = {
  verified: null,
  partial: '部分引用无法在论文中核实，请对照原文确认。',
  no_evidence: '这条回答没有附上可核对的论文原文依据，请谨慎对待。',
  unverified: '这条回答的依据尚未核实。'
}

function plainAnswer(card: InsightCard): string {
  return [card.answer.direct, card.answer.detail, card.answer.outside].filter(Boolean).join('\n\n')
}

/** 论文之外的补充：模型自己的知识和联网查到的资料，明确标成“不是论文原文”，网络资料能点开原网页。 */
function OutsideBlock({ text, web }: { text: string; web: NonNullable<InsightCard['answer']['web']> }) {
  const linked = text.replace(/\[W(\d+)\]/g, (_m, n: string) => {
    const w = web.find((x) => x.n === Number(n))
    return w ? `[W${n}](${w.url})` : ''
  })
  return (
    <section className="pd-card-outside" data-translatable>
      <h4 className="pd-card-sec">
        论文之外 <span className="pd-card-outside-tag">不是论文原文</span>
      </h4>
      <Markdown text={linked} />
      {web.length > 0 && (
        <ul className="pd-card-web">
          {web.map((w) => (
            <li key={w.n}>
              <button type="button" onClick={() => void openExternal(w.url)} title={w.url}>
                W{w.n} · {w.source} · {w.title}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function EvidenceList({ evidence, notes }: { evidence: Evidence[]; notes: Map<string, string> }) {
  const jumpToEvidence = useEvidenceJump()
  return (
    <div className="pd-ev-list">
      {evidence.map((ev) => (
        <div key={evidenceTag(ev)} className="pd-ev">
          <div className="pd-ev-head">
            <button type="button" className="pd-cite" onClick={() => jumpToEvidence(ev)} title={ev.source ? '打开补充材料并定位' : '跳转到原文'}>
              {ev.source ? `S${ev.source.doc} · p.${ev.page}` : `p.${ev.page}`}
            </button>
            <span className="pd-aux pd-muted pd-ellipsis" style={{ flex: 1, minWidth: 0 }}>
              {ev.section_title ?? '正文'}
              {ev.source ? ` · ${ev.source.label}` : ''}
            </span>
          </div>
          {notes.get(evidenceTag(ev)) && <div className="pd-ev-note">{notes.get(evidenceTag(ev))}</div>}
          <blockquote className="pd-ev-quote">{truncate(ev.quote, 260)}</blockquote>
        </div>
      ))}
    </div>
  )
}

export function CardView({ card }: { card: InsightCard }) {
  const [expanded, setExpanded] = useState(false)
  const setSaved = useInsightStore((s) => s.setSaved)
  const removeCard = useInsightStore((s) => s.removeCard)
  const ask = useInsightStore((s) => s.ask)
  const busy = useInsightStore((s) => s.pending?.status === 'streaming')
  const createNote = useNotesStore((s) => s.create)

  const evMap = useMemo(() => buildEvidenceMap(card.evidence), [card.evidence])
  const evNotes = useMemo(
    () => new Map(card.answer.evidence_notes.map((n) => [n.ref ?? `P${n.ord}`, n.note])),
    [card.answer.evidence_notes]
  )
  const invalidSet = useMemo(
    () => buildInvalidSet(card.verification.invalid_citations, card.verification.invalid_refs),
    [card.verification.invalid_citations, card.verification.invalid_refs]
  )
  // 后端给出的具体原因（缺字、丢帧、模型声明无信息…）优先于按状态给的通用文案
  const hint =
    card.verification.status === 'verified'
      ? null
      : card.verification.notes.length > 0
        ? card.verification.notes.join(' ')
        : (VERIFY_HINT[card.verification.status] ?? null)
  const failed = card.status !== 'complete'

  const meta = cardMetaParts(card)
  const question = questionLine(card.question, card.selection?.text)
  const status = verifyLabel(card.verification.status)
  const eqMore = isEquationCard(card) && !!(card.answer.relations || card.answer.derivation)
  const hasMore = !!card.answer.detail || eqMore || card.evidence.length > 0 || card.answer.inferences.length > 0 || card.answer.follow_ups.length > 0

  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(plainAnswer(card))
      toast.success('已复制回答')
    } catch {
      toast.error('复制失败')
    }
  }

  const addNote = async (): Promise<void> => {
    const first = card.evidence[0]
    const note = await createNote({
      text: plainAnswer(card),
      quote: card.selection?.text ?? null,
      page: card.selection?.page ?? first?.page ?? null,
      passage_ord: card.selection?.passage_ords?.[0] ?? first?.ord ?? null,
      card_id: card.id
    })
    if (note) toast.success('已加入笔记')
  }

  const items: MenuItem[] = [
    { key: 'save', label: card.saved ? '取消保存' : '保存为研究卡片', icon: card.saved ? <PinOff size={14} /> : <Pin size={14} />, onSelect: () => void setSaved(card.id, !card.saved) },
    { key: 'note', label: '加入笔记', icon: <NotebookPen size={14} />, disabled: failed, onSelect: () => void addNote() },
    { key: 'copy', label: '复制回答', icon: <Copy size={14} />, disabled: failed, onSelect: () => void copy() },
    { key: 'sep', separator: true },
    { key: 'delete', label: '删除卡片', icon: <Trash2 size={14} />, danger: true, onSelect: () => void removeCard(card.id) }
  ]

  return (
    <article className="pd-card" data-card-id={card.id} data-temp={!card.saved} data-failed={failed}>
      <div className="pd-card-meta">
        <span className="pd-card-meta-text" title={`${card.model}`}>
          {meta.map((m, i) => (
            <span key={i}>
              {i > 0 && <span className="pd-card-dot"> · </span>}
              {m}
            </span>
          ))}
          {!card.saved && <span className="pd-card-temp"> · 临时</span>}
        </span>
        <span className="pd-card-menu">
          <Menu
            align="end"
            width={180}
            trigger={({ toggle, open }) => <IconButton icon={<MoreHorizontal size={15} />} label="更多操作" size="sm" selected={open} onClick={toggle} />}
            items={items}
          />
        </span>
      </div>

      <QuestionBubble line={question} attachments={card.attachments?.map((a) => a.name)} />

      {failed ? (
        <p className="pd-field-error">{card.status === 'cancelled' ? '生成已取消。' : '生成失败。'}</p>
      ) : (
        <>
          <SupplementPermissionHint cardId={card.id} question={card.question} intent={card.intent} selection={card.selection} />
          {hint && (
            <p className="pd-card-hint" role="note" data-translatable>
              {status ? `${status}：` : ''}
              {hint}
            </p>
          )}

          {isEquationCard(card) && <EquationHeader card={card} />}
          <div className="pd-card-direct" data-translatable>
            <Markdown text={card.answer.direct} evidence={card.evidence} invalid={card.verification.invalid_citations} invalidRefs={card.verification.invalid_refs} />
          </div>

          {isEquationCard(card) && <SymbolTable card={card} />}

          {card.answer.outside && <OutsideBlock text={card.answer.outside} web={card.answer.web ?? []} />}

          {!expanded && (card.evidence.length > 0 || status) && (
            <div className="pd-card-evrow">
              {card.evidence.length > 0 && (
                <>
                  <span className="pd-card-evlabel">依据</span>
                  <CiteGroup refs={card.evidence.map((e) => ({ doc: e.source?.doc ?? 0, ord: e.ord }))} evidence={evMap} invalid={invalidSet} />
                </>
              )}
              {status && !hint && <span className="pd-card-status">{status}</span>}
            </div>
          )}

          {expanded && (
            <div className="pd-card-more" data-translatable>
              {card.answer.detail && (
                <section className="pd-card-secbox">
                  <h4 className="pd-card-sec">详细分析</h4>
                  <Markdown text={card.answer.detail} evidence={card.evidence} invalid={card.verification.invalid_citations} invalidRefs={card.verification.invalid_refs} />
                </section>
              )}

              {isEquationCard(card) && <EquationSections card={card} />}

              {card.evidence.length > 0 && (
                <section className="pd-card-secbox">
                  <h4 className="pd-card-sec">原文依据</h4>
                  <EvidenceList evidence={card.evidence} notes={evNotes} />
                </section>
              )}

              {card.answer.inferences.length > 0 && (
                <section className="pd-card-secbox">
                  <h4 className="pd-card-sec">AI 推断</h4>
                  <ul className="pd-card-inf">
                    {card.answer.inferences.map((inf, i) => (
                      <li key={i}>
                        <MathText text={inf.text} />
                        {(inf.basis_refs?.length ?? inf.basis_ords.length) > 0 && (
                          <span className="pd-card-basis">
                            <span className="pd-aux pd-weak">依据 </span>
                            <CiteGroup
                              refs={
                                inf.basis_refs
                                  ? inf.basis_refs.map(refFromTag).filter((r): r is NonNullable<typeof r> => r !== null)
                                  : inf.basis_ords.map((ord) => ({ doc: 0, ord }))
                              }
                              evidence={evMap}
                              invalid={invalidSet}
                            />
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {card.answer.follow_ups.length > 0 && (
                <section className="pd-card-secbox">
                  <h4 className="pd-card-sec">后续问题</h4>
                  <div className="pd-card-follow">
                    {card.answer.follow_ups.map((q) => (
                      <button
                        key={q}
                        type="button"
                        className="pd-followup"
                        disabled={busy}
                        onClick={() => void ask({ question: q, intent: 'free', selection: card.selection })}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                </section>
              )}
            </div>
          )}

          {hasMore && (
            <button type="button" className="pd-card-toggle" onClick={() => setExpanded((e) => !e)} aria-expanded={expanded}>
              {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
              {expanded ? '收起' : '展开详细分析与依据'}
            </button>
          )}
        </>
      )}
    </article>
  )
}

