import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Tag } from '@/components/ui'
import { openPaperBrowser } from '@/lib/native'
import type { CompareResult } from '@/lib/types.discovery'
import { openEvidenceInReader } from '@/features/shared/evidenceNav'
import { CompareMarkdown, EvidenceChip } from './CompareMarkdown'
import { CompareGraph } from './CompareGraph'
import { buildEvidenceIndex, evidenceKey } from './evidenceIndex'
import { BASIS_LABEL, groupEdges } from './graphLayout'

const BASIS_TAG: Record<string, string> = { fulltext: '全文', abstract: '仅摘要', none: '无内容' }
const VERIFY_LABEL: Record<CompareResult['verification']['status'], string> = {
  verified: '全部引用已核实',
  partial: '部分引用无法核实',
  no_evidence: '没有附带原文依据',
  unverified: '未核验'
}

export function CompareResultView({ result }: { result: CompareResult }) {
  const navigate = useNavigate()
  const [edgeSel, setEdgeSel] = useState<string | null>(null)
  const index = useMemo(() => buildEvidenceIndex(result.evidence), [result.evidence])
  const invalid = useMemo(() => new Set(result.verification.invalid_citations), [result.verification])
  const groups = useMemo(() => groupEdges(result.graph.edges), [result.graph.edges])
  const labelOf = (l: string): string => result.papers.find((p) => p.label === l)?.title ?? l
  const shownEdges = edgeSel ? groups.filter((g) => g.id === edgeSel) : groups

  return (
    <div className="pd-cmp-result">
      {result.warnings.map((w) => (
        <p key={w} className="pd-status-text pd-status-text--warning pd-cmp-warn">
          {w}
        </p>
      ))}

      <section className="pd-cmp-sec">
        <h2 className="pd-cmp-h">参与比较的论文</h2>
        <ul className="pd-cmp-papers">
          {result.papers.map((p) => (
            <li key={p.label}>
              <span className="pd-cmp-letter">{p.label}</span>
              <button type="button" onClick={() => navigate(`/paper/${p.paper_id}`)} title="打开这篇论文">
                {p.title}
              </button>
              {p.year && <span className="pd-dc-sub" style={{ display: 'inline' }}> {p.year}</span>}{' '}
              <Tag>{BASIS_TAG[p.basis] ?? p.basis}</Tag>
            </li>
          ))}
        </ul>
      </section>

      <section className="pd-cmp-sec">
        <h2 className="pd-cmp-h">总体结论</h2>
        <CompareMarkdown text={result.overall || '没有生成总体结论'} index={index} invalid={invalid} />
      </section>

      {(result.similarities.length > 0 || result.differences.length > 0) && (
        <section className="pd-cmp-sec pd-cmp-two">
          <div>
            <h2 className="pd-cmp-h">相同点</h2>
            <ul className="pd-cmp-bullets">
              {result.similarities.map((t, i) => (
                <li key={i}>
                  <CompareMarkdown text={t} index={index} invalid={invalid} inline />
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h2 className="pd-cmp-h">不同点</h2>
            <ul className="pd-cmp-bullets">
              {result.differences.map((t, i) => (
                <li key={i}>
                  <CompareMarkdown text={t} index={index} invalid={invalid} inline />
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}

      <section className="pd-cmp-sec">
        <h2 className="pd-cmp-h">结构化对比</h2>
        <div className="pd-cmp-table-wrap">
          <table className="pd-cmp-table">
            <thead>
              <tr>
                <th scope="col">维度</th>
                {result.papers.map((p) => (
                  <th key={p.label} scope="col">
                    <span className="pd-cmp-letter">{p.label}</span> {p.title}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((r) => (
                <tr key={r.dimension}>
                  <th scope="row">{r.dimension}</th>
                  {result.papers.map((p) => (
                    <td key={p.label}>
                      <CompareMarkdown text={r.cells[p.label]?.text ?? ''} index={index} invalid={invalid} inline />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {result.detail && (
        <section className="pd-cmp-sec">
          <h2 className="pd-cmp-h">详细比较</h2>
          <CompareMarkdown text={result.detail} index={index} invalid={invalid} />
        </section>
      )}

      <section className="pd-cmp-sec">
        <h2 className="pd-cmp-h">论文关系</h2>
        {result.graph.edges.length === 0 ? (
          <p className="pd-dc-hint">
            没有找到有依据的论文间关系：这些论文互相之间没有在 OpenAlex 参考文献数据或论文参考文献列表中出现引用，模型也没有给出带原文证据的关系。
            这里不会凭空画线。
          </p>
        ) : (
          <>
            <p className="pd-dc-hint">实线来自引用数据，如 OpenAlex 或论文自己的参考文献列表；虚线是 AI 推断，必须附原文证据。箭头方向为“引用/改进 → 被引用/被改进”。点击连线查看依据。</p>
            <CompareGraph graph={result.graph} selectedEdge={edgeSel} onSelect={setEdgeSel} />
            <ul className="pd-cmp-edges">
              {shownEdges.flatMap((g) =>
                g.edges.map((e, i) => (
                  <li key={`${g.id}-${i}`}>
                    <div>
                      <span className="pd-cmp-letter">{e.from}</span> → <span className="pd-cmp-letter">{e.to}</span> <strong>{e.relation_label}</strong>{' '}
                      <Tag title={e.ai_inferred ? '由模型根据原文推断，已核对原文证据，但仍需你自己判断' : undefined}>{BASIS_LABEL[e.basis]}</Tag>
                    </div>
                    <div className="pd-dc-sub">
                      {labelOf(e.from)} → {labelOf(e.to)}
                    </div>
                    {e.note && <div className="pd-cmp-note">{e.note}</div>}
                    {e.evidence.length > 0 && (
                      <div className="pd-cmp-evrow">
                        {e.evidence.map((ev) => (
                          <EvidenceChip key={evidenceKey(ev.label, ev.ord)} code={evidenceKey(ev.label, ev.ord)} index={index} invalid={invalid} />
                        ))}
                      </div>
                    )}
                  </li>
                ))
              )}
            </ul>
            {edgeSel && (
              <button type="button" className="pd-dc-more" onClick={() => setEdgeSel(null)}>
                显示全部关系
              </button>
            )}
          </>
        )}
      </section>

      {result.shared_references.length > 0 && (
        <section className="pd-cmp-sec">
          <h2 className="pd-cmp-h">共同参考文献 {result.shared_references.length}</h2>
          <ul className="pd-dc-list">
            {result.shared_references.slice(0, 20).map((r, i) => (
              <li key={i}>
                <div>
                  {r.doi ? (
                    <a
                      href={`https://doi.org/${r.doi}`}
                      onClick={(e) => {
                        e.preventDefault()
                        void openPaperBrowser(`https://doi.org/${r.doi}`)
                      }}
                    >
                      {r.title}
                    </a>
                  ) : (
                    r.title
                  )}
                </div>
                <div className="pd-dc-sub">
                  <span>{[r.venue, r.year].filter(Boolean).join(' · ')}</span>
                  <span>被 {r.cited_by.join('、')} 引用</span>
                  {r.citations !== null && <span>被引 {r.citations.toLocaleString()}</span>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="pd-cmp-sec">
        <h2 className="pd-cmp-h">证据核验</h2>
        <p className="pd-dc-sub" style={{ marginTop: 0 }}>
          <strong style={{ color: 'var(--text)' }}>{VERIFY_LABEL[result.verification.status]}</strong>
          {result.verification.notes.map((n) => (
            <span key={n}>{n}</span>
          ))}
          {result.verification.invalid_citations.length > 0 && <span>无法核实的引用：{result.verification.invalid_citations.join('、')}，已在正文中用删除线标出</span>}
        </p>
        <ul className="pd-cmp-evidence">
          {result.evidence.map((ev) => (
            <li key={evidenceKey(ev.label, ev.ord)}>
              <div className="pd-cmp-evhead">
                <EvidenceChip code={evidenceKey(ev.label, ev.ord)} index={index} />
                <span className="pd-dc-sub" style={{ display: 'inline' }}>
                  {[ev.paper_title.slice(0, 60), ev.page ? `第 ${ev.page} 页` : ev.kind === 'abstract' ? '摘要' : null, ev.section_title].filter(Boolean).join(' · ')}
                </span>
                {ev.kind === 'paper_text' && ev.page && (
                  <button type="button" className="pd-dc-more" onClick={() => openEvidenceInReader(navigate, ev.paper_id, { page: ev.page, bbox: ev.bbox })}>
                    在原文中查看
                  </button>
                )}
              </div>
              <blockquote className="pd-cmp-quote">{ev.quote}</blockquote>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
