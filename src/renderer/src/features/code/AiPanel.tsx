import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, ChevronDown, ChevronRight, Square, Trash2, X } from 'lucide-react'
import { Button, IconButton, Segmented, Spinner, Tag } from '@/components/ui'
import { formatRelativeTime } from '@/lib/format'
import type { CodeCard, CodeEvidence, PaperCodeLink, VerificationStatus } from '@/lib/types.code'
import { CodeMarkdown } from './CodeMarkdown'
import { CERTAINTY_LABEL, CONFIDENCE_LABEL, deriveTarget, isUncertain, LINK_STATUS_LABEL, lineRange, METHOD_LABEL, targetLabel } from './codeUtils'
import { openPaperAt, useCodeStore, type ExplainRun, type SideTab } from './codeStore'

const VERIFY_TEXT: Record<VerificationStatus, string> = {
  verified: '证据已逐条核实：引用的文件与行号都真实存在。',
  partial: '部分证据已核实，其余没有通过核实，已划线标注。',
  no_evidence: '没有可核实的代码证据，下面的内容请谨慎对待。',
  unverified: '尚未核实。'
}

/* ---------------- 证据 ---------------- */
function EvidenceItem({ ev, open, onToggle }: { ev: CodeEvidence; open: boolean; onToggle: () => void }) {
  const goToLines = useCodeStore((s) => s.goToLines)
  return (
    <li className="pd-ev" data-invalid={!ev.verified || undefined} id={`pd-ev-${ev.ref}`}>
      <div className="pd-ev-head">
        <span className="pd-lref pd-lref--static">L{ev.ref}</span>
        <button type="button" className="pd-ev-loc pd-ellipsis" title="在代码中查看" onClick={() => void goToLines(ev.path, ev.line_start, ev.line_end)}>
          {ev.path}:{lineRange(ev.line_start, ev.line_end)}
          {ev.symbol ? ` · ${ev.symbol}` : ''}
        </button>
        <span className="pd-aux pd-muted">{ev.verified ? '已核实' : `未核实${ev.reason ? `：${ev.reason}` : ''}`}</span>
        {ev.snippet && (
          <IconButton size="sm" icon={open ? <ChevronDown size={14} /> : <ChevronRight size={14} />} label={open ? '收起代码' : '展开代码'} onClick={onToggle} />
        )}
      </div>
      {open && ev.snippet && <pre className="pd-ev-snippet">{ev.snippet}</pre>}
    </li>
  )
}

/* ---------------- 回答卡片 ---------------- */
function CardView({ card }: { card: CodeCard }) {
  const goToLines = useCodeStore((s) => s.goToLines)
  const explain = useCodeStore((s) => s.explain)
  const removeCard = useCodeStore((s) => s.removeCard)
  const [showDetail, setShowDetail] = useState(false)
  const [showEvidence, setShowEvidence] = useState(true)
  const [openRefs, setOpenRefs] = useState<Set<number>>(new Set())
  const toggleRef = (r: number): void =>
    setOpenRefs((prev) => {
      const n = new Set(prev)
      if (n.has(r)) n.delete(r)
      else n.add(r)
      return n
    })
  const byRef = new Map(card.evidence.map((e) => [e.ref, e]))
  const onCite = (r: number): void => {
    const ev = byRef.get(r)
    if (!ev) return
    setShowEvidence(true)
    setOpenRefs((prev) => new Set(prev).add(r))
    void goToLines(ev.path, ev.line_start, ev.line_end)
    window.setTimeout(() => document.getElementById(`pd-ev-${r}`)?.scrollIntoView({ block: 'nearest' }), 30)
  }
  const a = card.answer
  const invalid = card.verification.invalid_citations
  const status = card.verification.status

  return (
    <article className="pd-ccard">
      <header className="pd-ccard-head">
        <span className="pd-ccard-target pd-ellipsis" title={targetLabel(card.target)}>
          {targetLabel(card.target)}
        </span>
        <span className="pd-aux pd-muted pd-ellipsis">
          {card.model} · {formatRelativeTime(card.created_at)}
        </span>
        <IconButton size="sm" icon={<Trash2 size={14} />} label="删除这条解释" onClick={() => void removeCard(card.id)} />
      </header>
      {card.question && <p className="pd-ccard-q">问：{card.question}</p>}
      <p className="pd-ccard-verify pd-aux" data-status={status}>
        {VERIFY_TEXT[status]}
        {card.verification.notes.length > 0 && ` ${card.verification.notes.join('；')}`}
      </p>

      <CodeMarkdown text={a.direct} onCite={onCite} invalid={invalid} />
      {a.detail && (
        <>
          <button type="button" className="pd-link pd-ccard-toggle" onClick={() => setShowDetail(!showDetail)}>
            {showDetail ? '收起详细说明' : '展开详细说明'}
          </button>
          {showDetail && <CodeMarkdown text={a.detail} onCite={onCite} invalid={invalid} />}
        </>
      )}

      {a.inferences.length > 0 && (
        <div className="pd-ccard-sec">
          <div className="pd-ccard-sec-title" title="不是代码里直接写明的">推断</div>
          <ul className="pd-ccard-list">
            {a.inferences.map((inf, i) => (
              <li key={i}>
                {inf.text}
                {inf.basis_refs.length > 0 && (
                  <span className="pd-lrefs">
                    {inf.basis_refs.map((r) => (
                      <button key={r} type="button" className="pd-lref" onClick={() => onCite(r)}>
                        L{r}
                      </button>
                    ))}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {card.evidence.length > 0 && (
        <div className="pd-ccard-sec">
          <button type="button" className="pd-ccard-sec-title pd-ccard-sec-btn" aria-expanded={showEvidence} onClick={() => setShowEvidence(!showEvidence)}>
            {showEvidence ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            代码证据 {card.evidence.length}
          </button>
          {showEvidence && (
            <ul className="pd-ev-list">
              {card.evidence.map((ev) => (
                <EvidenceItem key={ev.ref} ev={ev} open={openRefs.has(ev.ref)} onToggle={() => toggleRef(ev.ref)} />
              ))}
            </ul>
          )}
        </div>
      )}

      {a.follow_ups.length > 0 && (
        <div className="pd-ccard-follow">
          {a.follow_ups.slice(0, 3).map((f) => (
            <button key={f} type="button" className="pd-follow" onClick={() => void explain(card.target, targetLabel(card.target), f)}>
              {f}
            </button>
          ))}
        </div>
      )}
    </article>
  )
}

/* ---------------- 进行中的解释 ---------------- */
function RunView({ run }: { run: ExplainRun }) {
  const stop = useCodeStore((s) => s.stopExplain)
  const dismiss = useCodeStore((s) => s.dismissExplain)
  const explain = useCodeStore((s) => s.explain)
  const ctx = run.meta?.context
  return (
    <article className="pd-ccard pd-ccard--run">
      <header className="pd-ccard-head">
        <span className="pd-ccard-target pd-ellipsis">{run.label}</span>
        {run.status === 'streaming' ? (
          <Button size="sm" variant="ghost" icon={<Square size={12} />} onClick={stop}>
            停止
          </Button>
        ) : (
          <IconButton size="sm" icon={<X size={14} />} label="关闭" onClick={dismiss} />
        )}
      </header>
      {run.question && <p className="pd-ccard-q">问：{run.question}</p>}
      {ctx && (
        <p className="pd-aux pd-muted">
          已发送 {ctx.snippet_count} 段代码，共 {ctx.chars_sent} 字符，给 {run.escalatedTo ?? run.meta?.model}，不含密钥与论文以外的文件。
        </p>
      )}
      {run.escalatedTo && <p className="pd-aux pd-muted">第一次回答没有通过证据核实，已换用 {run.escalatedTo} 重新生成。</p>}
      {run.status === 'streaming' && !run.text && (
        <p className="pd-aux pd-muted pd-row" style={{ gap: 8 }}>
          <Spinner size={13} />
          {ctx ? '正在生成…' : '正在读取相关代码…'}
        </p>
      )}
      {run.text && <CodeMarkdown text={run.text} streaming />}
      {run.status === 'streaming' && run.text && <span className="pd-cursor" aria-hidden />}
      {run.status === 'stopped' && <p className="pd-aux pd-muted">已停止，未保存。</p>}
      {run.status === 'failed' && run.error && (
        <div className="pd-ccard-error" role="alert">
          <p>{run.error.message}</p>
          <Button size="sm" onClick={() => void explain(run.target, run.label, run.question)}>
            重试
          </Button>
        </div>
      )}
    </article>
  )
}

/* ---------------- 标签页 ---------------- */
function ExplainTab() {
  const repoId = useCodeStore((s) => s.repoId)
  const openPath = useCodeStore((s) => s.openPath)
  const lineSel = useCodeStore((s) => s.lineSel)
  const symbol = useCodeStore((s) => s.selectedSymbol)
  const run = useCodeStore((s) => s.explainRun)
  const cards = useCodeStore((s) => s.cards)
  const explain = useCodeStore((s) => s.explain)
  const [q, setQ] = useState('')
  const target = deriveTarget(openPath, lineSel, symbol)
  const busy = run?.status === 'streaming'
  const send = (): void => {
    if (!target || !repoId || busy) return
    void explain(target, targetLabel(target), q.trim())
    setQ('')
  }
  const sym = symbol && symbol.path === openPath ? symbol : null
  const quick = (kind: 'algorithm' | 'dataflow' | 'calls'): void => {
    if (!sym || busy) return
    void explain({ kind, path: sym.path, symbol_id: sym.id, symbol: sym.qualname }, sym.qualname)
  }

  return (
    <div className="pd-explain">
      <div className="pd-ask">
        <div className="pd-ask-target pd-aux">
          <span className="pd-muted">对象</span> {target ? targetLabel(target) : '先在左侧打开一个文件'}
        </div>
        <textarea
          className="pd-textarea pd-ask-input"
          rows={2}
          value={q}
          disabled={!target}
          placeholder="可以留空直接解释；也可以问，例如：这段代码的输入和输出是什么？"
          aria-label="向 AI 提问"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              send()
            }
          }}
        />
        <div className="pd-ask-actions">
          {sym && (
            <>
              <button type="button" className="pd-link" onClick={() => quick('algorithm')} disabled={busy}>
                算法步骤
              </button>
              <button type="button" className="pd-link" onClick={() => quick('dataflow')} disabled={busy}>
                数据流
              </button>
              <button type="button" className="pd-link" onClick={() => quick('calls')} disabled={busy}>
                调用关系
              </button>
            </>
          )}
          <div className="pd-spacer" />
          <Button size="sm" variant="primary" disabled={!target || busy} loading={busy} onClick={send}>
            {q.trim() ? '提问' : '解释'}
          </Button>
        </div>
        <p className="pd-aux pd-muted">只会发送相关的代码片段给模型。回答里的每条证据都会回查真实的文件与行号。</p>
      </div>

      {run && <RunView run={run} />}
      {[...cards].reverse().map((c) => (
        <CardView key={c.id} card={c} />
      ))}
      {!run && cards.length === 0 && <p className="pd-aux pd-muted pd-panel-hint">还没有解释。在左侧选择文件、函数或代码行，再点“解释”。</p>}
    </div>
  )
}

function CallsTab() {
  const graph = useCodeStore((s) => s.graph)
  const symbol = useCodeStore((s) => s.selectedSymbol)
  const goToSymbolId = useCodeStore((s) => s.goToSymbolId)
  const goToLines = useCodeStore((s) => s.goToLines)
  const loadFlow = useCodeStore((s) => s.loadFlow)
  const setTab = useCodeStore((s) => s.setTab)

  if (!symbol) return <p className="pd-aux pd-muted pd-panel-hint">在左侧“大纲”里选一个函数或类，这里会显示它调用了谁、被谁调用。</p>
  if (!graph) return <p className="pd-aux pd-muted pd-panel-hint pd-row" style={{ gap: 8 }}><Spinner size={13} />正在读取调用关系…</p>

  return (
    <div className="pd-calls">
      <div className="pd-calls-head">
        <div className="pd-calls-name">{symbol.qualname}</div>
        <div className="pd-aux pd-muted">
          {symbol.path}:{lineRange(symbol.line_start, symbol.line_end)}
        </div>
        <Button size="sm" onClick={() => { setTab('flow'); void loadFlow(symbol.id) }}>
          以它为起点看运行流程
        </Button>
      </div>
      <p className="pd-aux pd-muted">静态分析得到的调用关系：“确定”表示通过 import 或变量类型精确定位；“不确定”表示只按名称匹配，可能指向别处。</p>

      <h4 className="pd-calls-h">它调用了 {graph.callees.length}</h4>
      {graph.callees.length === 0 ? (
        <p className="pd-aux pd-muted">没有发现调用。</p>
      ) : (
        <ul className="pd-calls-list">
          {graph.callees.map((c, i) => (
            <li key={`${c.callee_name}-${c.line}-${i}`}>
              <button
                type="button"
                className="pd-calls-row"
                disabled={c.callee_id === null}
                onClick={() => (c.callee_id !== null ? void goToSymbolId(c.callee_id) : void goToLines(symbol.path, c.line, c.line))}
                title={c.callee_id !== null ? '跳到定义' : '在调用处查看'}
              >
                <span className="pd-calls-name2 pd-ellipsis">{c.callee_name}</span>
                <span className="pd-calls-cert" data-uncertain={isUncertain(c.certainty) || undefined}>
                  {CERTAINTY_LABEL[c.certainty]}
                  {isUncertain(c.certainty) ? ' · 不确定' : ''}
                </span>
                <span className="pd-aux pd-muted pd-ellipsis pd-calls-where">
                  {c.qualname ? `${c.qualname} · ${c.path}` : '未找到定义'} · 调用处 L{c.line}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <h4 className="pd-calls-h">被谁调用 {graph.callers.length}</h4>
      {graph.callers.length === 0 ? (
        <p className="pd-aux pd-muted">没有发现调用者，可能是入口，或只被动态方式调用。</p>
      ) : (
        <ul className="pd-calls-list">
          {graph.callers.map((c, i) => (
            <li key={`${c.caller_id}-${c.line}-${i}`}>
              <button type="button" className="pd-calls-row" onClick={() => void goToSymbolId(c.caller_id)} title="跳到调用者">
                <span className="pd-calls-name2 pd-ellipsis">{c.qualname}</span>
                <span className="pd-calls-cert" data-uncertain={isUncertain(c.certainty) || undefined}>
                  {CERTAINTY_LABEL[c.certainty]}
                  {isUncertain(c.certainty) ? ' · 不确定' : ''}
                </span>
                <span className="pd-aux pd-muted pd-ellipsis pd-calls-where">
                  {c.path} · 调用处 L{c.line}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function LinkLine({ link }: { link: PaperCodeLink }) {
  const setLinkStatus = useCodeStore((s) => s.setLinkStatus)
  const navigate = useNavigate()
  const paperId = useCodeStore((s) => s.paperId)
  return (
    <div className="pd-link-item" data-status={link.status}>
      <div className="pd-link-top">
        <span className="pd-link-name">{link.module.name}</span>
        {link.module.pages.length > 0 && paperId && (
          <button type="button" className="pd-link" onClick={() => void openPaperAt(navigate, paperId, { page: link.module.pages[0], ord: link.module.passage_ords[0] })}>
            论文第 {link.module.pages.join('、')} 页
          </button>
        )}
      </div>
      <div className="pd-aux pd-muted">
        {CONFIDENCE_LABEL[link.confidence]} · {METHOD_LABEL[link.method]} · {LINK_STATUS_LABEL[link.status]}
      </div>
      {link.basis.text && <p className="pd-aux pd-link-basis">{link.basis.text}</p>}
      <div className="pd-link-actions">
        {link.status !== 'confirmed' && (
          <Button size="sm" icon={<Check size={13} />} onClick={() => void setLinkStatus(link.id, 'confirmed')}>
            确认
          </Button>
        )}
        {link.status !== 'rejected' && (
          <Button size="sm" variant="ghost" onClick={() => void setLinkStatus(link.id, 'rejected')}>
            不对应
          </Button>
        )}
        {link.status === 'rejected' && (
          <Button size="sm" variant="ghost" onClick={() => void setLinkStatus(link.id, 'suggested')}>
            恢复
          </Button>
        )}
      </div>
    </div>
  )
}

function PaperTab() {
  const symbol = useCodeStore((s) => s.selectedSymbol)
  const sp = useCodeStore((s) => s.symbolPaper)
  const paperId = useCodeStore((s) => s.paperId)
  const setTab = useCodeStore((s) => s.setTab)
  const navigate = useNavigate()
  if (!symbol) return <p className="pd-aux pd-muted pd-panel-hint">在左侧“大纲”里选一个函数或类，这里会显示它对应论文里的哪一部分。</p>
  if (!sp) return <p className="pd-aux pd-muted pd-panel-hint pd-row" style={{ gap: 8 }}><Spinner size={13} />正在查找对应的论文内容…</p>
  const visible = sp.links.filter((l) => l.status !== 'rejected')
  return (
    <div className="pd-paper-tab">
      <div className="pd-calls-head">
        <div className="pd-calls-name">{symbol.qualname}</div>
        <p className="pd-aux pd-muted">{sp.note}</p>
      </div>

      <h4 className="pd-calls-h">已关联的论文方法 {visible.length}</h4>
      {visible.length === 0 ? (
        <p className="pd-aux pd-muted">
          还没有论文方法与它关联。
          <button type="button" className="pd-link" onClick={() => setTab('mapping')}>
            去“论文 ↔ 代码”生成关联
          </button>
        </p>
      ) : (
        visible.map((l) => <LinkLine key={l.id} link={l} />)
      )}

      <h4 className="pd-calls-h">论文里可能相关的段落 {sp.related_passages.length}</h4>
      {sp.related_passages.length === 0 ? (
        <p className="pd-aux pd-muted">没有找到词汇上相关的论文段落。</p>
      ) : (
        <ul className="pd-calls-list">
          {sp.related_passages.map((p) => (
            <li key={p.ord}>
              <button type="button" className="pd-calls-row pd-passage" onClick={() => paperId && void openPaperAt(navigate, paperId, { page: p.page, ord: p.ord })}>
                <span className="pd-calls-cert">
                  第 {p.page} 页{p.section_title ? ` · ${p.section_title}` : ''} · 可能相关
                </span>
                <span className="pd-passage-text">{p.snippet}</span>
                <span className="pd-aux pd-muted">匹配词：{p.matched_terms.slice(0, 6).join('、')}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <Tag tone="outline">词法匹配只表示用词相近，不代表这段代码实现了该段落</Tag>
    </div>
  )
}

export function AiPanel() {
  const sideTab = useCodeStore((s) => s.sideTab)
  const setSideTab = useCodeStore((s) => s.setSideTab)
  return (
    <aside className="pd-code-right" aria-label="AI 面板">
      <div className="pd-code-right-head">
        <Segmented<SideTab>
          variant="text"
          aria-label="面板"
          value={sideTab}
          onChange={setSideTab}
          options={[
            { value: 'explain', label: '解释' },
            { value: 'calls', label: '调用' },
            { value: 'paper', label: '对应论文' }
          ]}
        />
      </div>
      <div className="pd-code-right-body">{sideTab === 'explain' ? <ExplainTab /> : sideTab === 'calls' ? <CallsTab /> : <PaperTab />}</div>
    </aside>
  )
}
