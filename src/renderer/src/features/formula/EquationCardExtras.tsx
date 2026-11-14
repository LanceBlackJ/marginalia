import { useState } from 'react'
import { AlertTriangle, Copy, Image as ImageIcon } from 'lucide-react'
import { Button, Tag, toast } from '@/components/ui'
import { CiteGroup, buildEvidenceMap, buildInvalidSet } from '@/components/CiteChip'
import { Markdown } from '@/components/Markdown'
import { ZoomImage } from '@/components/ZoomImage'
import { textApi } from '@/lib/api.text'
import { useBlobUrl } from '@/features/figures/useBlobUrl'
import type { EquationCard, SymbolEntry } from '@/lib/types.vision'
import { Tex } from './Tex'
import './formula.css'

const CONF_LABEL = { high: '较高', medium: '中等', low: '较低' } as const

/** 公式卡片顶部：渲染后的公式 + 转写置信度 + 复制 LaTeX + 对照原图。 */
export function EquationHeader({ card }: { card: EquationCard }) {
  const eq = card.answer.equation
  const [showOrig, setShowOrig] = useState(!eq?.latex || eq.status !== 'ok')
  const orig = useBlobUrl(showOrig && eq ? () => textApi.equationVector(card.paper_id, eq.id, 200) : null, [showOrig, card.paper_id, eq?.id])
  if (!eq) return null

  const copy = async (): Promise<void> => {
    if (!eq.latex) return
    try {
      await navigator.clipboard.writeText(eq.latex)
      toast.success('已复制 LaTeX')
    } catch {
      toast.error('复制失败')
    }
  }

  return (
    <div className="pd-eq" aria-label="公式">
      {eq.latex && eq.status === 'ok' ? (
        <div className="pd-eq-view">
          <Tex latex={eq.latex} display />
        </div>
      ) : (
        <p className="pd-eq-missing">公式截图没有识别出可用的 LaTeX，下面是原文截图；解释只依据 PDF 文字层与上下文。</p>
      )}
      {showOrig && (
        <>
          {orig.url ? <ZoomImage className="pd-eq-orig" src={orig.url} alt={`公式 ${eq.number ?? ''} 原文截图`} /> : <p className="pd-eq-missing">{orig.error ? '原文截图加载失败' : '正在加载原文截图…'}</p>}
        </>
      )}
      <div className="pd-eq-meta">
        <span>{eq.number ? `公式 (${eq.number})` : '公式'} · 第 {eq.page} 页</span>
        {eq.latex && eq.status === 'ok' && eq.confidence && <Tag tone={eq.confidence === 'low' ? 'warning' : 'outline'}>转写置信度{CONF_LABEL[eq.confidence]}</Tag>}
        <span className="pd-spacer" />
        {eq.latex && eq.status === 'ok' && (
          <Button size="sm" variant="ghost" icon={<Copy size={12} />} onClick={() => void copy()}>
            复制 LaTeX
          </Button>
        )}
        {eq.latex && (
          <Button size="sm" variant="ghost" icon={<ImageIcon size={12} />} onClick={() => setShowOrig((v) => !v)} aria-pressed={showOrig}>
            {showOrig ? '隐藏原文截图' : '对照原文'}
          </Button>
        )}
      </div>
      {eq.warnings.length > 0 && (
        <div className="pd-card-hint" role="note" style={{ marginTop: 6 }}>
          <AlertTriangle size={13} />
          <span>转写校验：{eq.warnings.join('；')}。请对照原文确认。</span>
        </div>
      )}
    </div>
  )
}

function StateCell({ s, card }: { s: SymbolEntry; card: EquationCard }) {
  const evMap = buildEvidenceMap(card.evidence)
  const invalid = buildInvalidSet(card.verification.invalid_citations, card.verification.invalid_refs)
  if (s.status === 'defined') return <CiteGroup refs={s.ords.map((ord) => ({ doc: 0, ord }))} evidence={evMap} invalid={invalid} />
  if (s.status === 'undefined') return <span className="pd-sym-state" data-s="undefined">论文未定义</span>
  return (
    <span className="pd-sym-state" data-s="unsupported" title="模型给出了含义，但既没有论文依据，也没有说明论文未定义">
      <AlertTriangle size={12} />
      缺少依据
    </span>
  )
}

/** 符号表：每个符号要么有可点击的论文出处，要么明确标注“论文未定义”，否则标为“缺少依据”。 */
export function SymbolTable({ card }: { card: EquationCard }) {
  const symbols = card.answer.symbols
  if (symbols.length === 0) return null
  return (
    <table className="pd-sym">
      <caption className="pd-sr-only">符号表</caption>
      <thead>
        <tr>
          <th scope="col">符号</th>
          <th scope="col">含义</th>
          <th scope="col">出处</th>
        </tr>
      </thead>
      <tbody>
        {symbols.map((s, i) => (
          <tr key={`${s.symbol}-${i}`}>
            <td>
              <Tex latex={s.symbol} />
            </td>
            <td>
              <Markdown text={s.meaning} />
            </td>
            <td>
              <StateCell s={s} card={card} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** 展开区里的“与其他公式的关系 / 推导”。 */
export function EquationSections({ card }: { card: EquationCard }) {
  const { relations, derivation } = card.answer
  const v = card.verification
  return (
    <>
      {relations && (
        <section>
          <h4 className="pd-card-sec">与其他公式的关系</h4>
          <Markdown text={relations} evidence={card.evidence} invalid={v.invalid_citations} invalidRefs={v.invalid_refs} />
        </section>
      )}
      {derivation && (
        <section>
          <h4 className="pd-card-sec">推导</h4>
          <Markdown text={derivation} evidence={card.evidence} invalid={v.invalid_citations} invalidRefs={v.invalid_refs} />
        </section>
      )}
    </>
  )
}
