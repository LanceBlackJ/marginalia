import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { Button, Dialog, EmptyState, Input, Menu, Skeleton, type MenuItem } from '@/components/ui'
import { deriveTarget, fmtBytes, lineRange, pathBase, ROLE_LABEL, targetLabel } from './codeUtils'
import { useCodeStore } from './codeStore'
import { highlightLines, languageForPath } from './highlight'

export const LINE_H = 20
const CHUNK = 100

interface ChunkProps {
  lines: string[]
  start: number // 第一行的行号（从 1 开始）
  hlA: number
  hlB: number
  selA: number
  selB: number
  onGutter: (n: number, shift: boolean) => void
}

/** 一块 100 行。用 content-visibility 让浏览器跳过屏幕外的渲染，同时保留原生文本选择与复制。 */
const Chunk = memo(function Chunk({ lines, start, hlA, hlB, selA, selB, onGutter }: ChunkProps) {
  return (
    <div className="pd-code-chunk" style={{ containIntrinsicSize: `auto ${lines.length * LINE_H}px` }}>
      {lines.map((html, k) => {
        const n = start + k
        return (
          <div key={n} className="pd-code-line" data-ln={n} data-hl={(hlA > 0 && n >= hlA && n <= hlB) || undefined} data-sel={(selA > 0 && n >= selA && n <= selB) || undefined}>
            <span className="pd-code-no" onMouseDown={(e) => { e.preventDefault(); onGutter(n, e.shiftKey) }}>
              {n}
            </span>
            <span className="pd-code-text" dangerouslySetInnerHTML={{ __html: html === '' ? ' ' : html }} />
          </div>
        )
      })}
    </div>
  )
})

function lineOf(node: Node | null): number | null {
  const el = node instanceof Element ? node : node?.parentElement
  const row = el?.closest<HTMLElement>('[data-ln]')
  return row ? Number(row.dataset.ln) : null
}

export function CodeViewer() {
  const file = useCodeStore((s) => s.file)
  const loading = useCodeStore((s) => s.fileLoading)
  const error = useCodeStore((s) => s.fileError)
  const openPath = useCodeStore((s) => s.openPath)
  const highlight = useCodeStore((s) => s.highlight)
  const lineSel = useCodeStore((s) => s.lineSel)
  const setLineSel = useCodeStore((s) => s.setLineSel)
  const symbol = useCodeStore((s) => s.selectedSymbol)
  const explain = useCodeStore((s) => s.explain)
  const scroller = useRef<HTMLDivElement>(null)
  const [varOpen, setVarOpen] = useState(false)
  const [varName, setVarName] = useState('')

  const lang = file ? languageForPath(file.path) : null
  const lines = useMemo(() => (file?.content != null ? highlightLines(file.content, lang) : []), [file?.path, file?.content, lang]) // eslint-disable-line react-hooks/exhaustive-deps

  // 定位并高亮：来自符号、证据引用、论文关联
  useEffect(() => {
    const el = scroller.current
    if (!el || !highlight || !file || highlight.path !== file.path) return
    el.scrollTo({ top: Math.max(0, (highlight.a - 1) * LINE_H - el.clientHeight * 0.3) })
  }, [highlight?.nonce, file?.path]) // eslint-disable-line react-hooks/exhaustive-deps

  const sel = lineSel && file && lineSel.path === file.path ? lineSel : null

  const onGutter = (n: number, shift: boolean): void => {
    if (!file) return
    if (shift && sel) setLineSel({ path: file.path, a: Math.min(sel.a, n), b: Math.max(sel.b, n) })
    else setLineSel(sel && sel.a === n && sel.b === n ? null : { path: file.path, a: n, b: n })
  }

  const onMouseUp = (): void => {
    const s = window.getSelection()
    if (!file || !s || s.isCollapsed || !scroller.current?.contains(s.anchorNode)) return
    const a = lineOf(s.anchorNode)
    const b = lineOf(s.focusNode)
    if (a && b) setLineSel({ path: file.path, a: Math.min(a, b), b: Math.max(a, b) })
  }

  if (!openPath) {
    return (
      <div className="pd-code-empty">
        <EmptyState title="选一个文件开始" description="从左侧文件树打开文件；点击行号或拖选代码后，可以让 AI 解释所选部分。" />
      </div>
    )
  }
  if (loading && !file) {
    return (
      <div className="pd-code-empty" style={{ alignItems: 'stretch', padding: 24 }}>
        {Array.from({ length: 10 }, (_, i) => (
          <Skeleton key={i} height={14} width={`${40 + ((i * 37) % 55)}%`} style={{ marginBottom: 10 }} />
        ))}
      </div>
    )
  }
  if (error || !file) {
    return (
      <div className="pd-code-empty">
        <EmptyState tone="danger" title="无法打开这个文件" description={error ?? '文件不存在'} />
      </div>
    )
  }

  const target = deriveTarget(openPath, lineSel, symbol)
  const hl = highlight && highlight.path === file.path ? highlight : null
  const items: MenuItem[] = [
    ...(sel ? [{ key: 'range', label: `解释所选代码 · ${lineRange(sel.a, sel.b)}`, onSelect: () => void explain({ kind: 'line_range', path: file.path, line_start: sel.a, line_end: sel.b }, `${pathBase(file.path)} ${lineRange(sel.a, sel.b)}`) }] : []),
    ...(symbol && symbol.path === file.path
      ? [
          { key: 'sym', label: `解释${symbol.kind === 'class' ? '类' : '函数'} ${symbol.name}`, onSelect: () => void explain({ kind: symbol.kind === 'class' ? 'class' : 'function', path: file.path, symbol_id: symbol.id, symbol: symbol.qualname }, symbol.qualname) },
          { key: 'algo', label: `提炼 ${symbol.name} 的算法步骤`, onSelect: () => void explain({ kind: 'algorithm', path: file.path, symbol_id: symbol.id, symbol: symbol.qualname }, symbol.qualname) },
          { key: 'flow', label: `${symbol.name} 里的数据怎么流动`, onSelect: () => void explain({ kind: 'dataflow', path: file.path, symbol_id: symbol.id, symbol: symbol.qualname }, symbol.qualname) },
          { key: 'calls', label: `${symbol.name} 调用了谁、被谁调用`, onSelect: () => void explain({ kind: 'calls', path: file.path, symbol_id: symbol.id, symbol: symbol.qualname }, symbol.qualname) }
        ]
      : []),
    { key: 'var', label: '追踪一个变量…', onSelect: () => setVarOpen(true) },
    { key: 'sep', separator: true },
    { key: 'file', label: '解释整个文件', onSelect: () => void explain({ kind: 'file', path: file.path }, pathBase(file.path)) }
  ]

  const startVar = (): void => {
    const v = varName.trim()
    if (!v) return
    setVarOpen(false)
    void explain(
      { kind: 'variable', variable: v, path: file.path, ...(symbol && symbol.path === file.path ? { symbol_id: symbol.id, symbol: symbol.qualname } : {}) },
      v
    )
  }

  const chunks: Array<{ start: number; lines: string[] }> = []
  for (let i = 0; i < lines.length; i += CHUNK) chunks.push({ start: i + 1, lines: lines.slice(i, i + CHUNK) })
  const inChunk = (c: { start: number; lines: string[] }, a: number, b: number): [number, number] => {
    const end = c.start + c.lines.length - 1
    return a <= end && b >= c.start ? [a, b] : [0, 0]
  }

  return (
    <div className="pd-code-viewer">
      <div className="pd-code-head">
        <div className="pd-code-path pd-ellipsis" title={file.path}>
          {file.path.split('/').pop()}
        </div>
        <div className="pd-code-meta pd-aux pd-muted">
          {file.language ?? '纯文本'} · {file.lines} 行 · {fmtBytes(file.size)} · {ROLE_LABEL[file.role]}
        </div>
        <div className="pd-spacer" />
        {sel && (
          <span className="pd-code-sel pd-aux">
            已选 {lineRange(sel.a, sel.b)}
            <button type="button" className="pd-link" onClick={() => setLineSel(null)}>
              取消
            </button>
          </span>
        )}
        <Menu
          align="end"
          width={260}
          trigger={({ toggle, open }) => (
            <Button size="sm" icon={<Sparkles size={14} />} onClick={toggle} aria-haspopup="menu" aria-expanded={open} disabled={file.content == null} disabledReason="这个文件不能被解释，二进制或过大">
              AI 解释{target ? `：${targetLabel(target)}` : ''}
            </Button>
          )}
          items={items}
        />
      </div>

      {file.content == null ? (
        <div className="pd-code-empty">
          <EmptyState title={file.skipped === 'binary' ? '二进制文件，不显示内容' : file.skipped === 'too_large' ? '文件过大，不显示内容' : '无法显示内容'} description={file.note ?? undefined} />
        </div>
      ) : (
        <div ref={scroller} className="pd-code-scroll" onMouseUp={onMouseUp}>
          {file.truncated && <div className="pd-code-note pd-aux">{file.note ?? '文件较大，只显示了前面的部分。'}</div>}
          <div className="pd-code-lines" style={{ minWidth: 'max-content' }}>
            {chunks.map((c) => {
              const [ha, hb] = hl ? inChunk(c, hl.a, hl.b) : [0, 0]
              const [sa, sb] = sel ? inChunk(c, sel.a, sel.b) : [0, 0]
              return <Chunk key={c.start} lines={c.lines} start={c.start} hlA={ha} hlB={hb} selA={sa} selB={sb} onGutter={onGutter} />
            })}
          </div>
        </div>
      )}

      <Dialog
        open={varOpen}
        onClose={() => setVarOpen(false)}
        title="追踪一个变量"
        width={420}
        footer={
          <>
            <Button onClick={() => setVarOpen(false)}>取消</Button>
            <Button variant="primary" disabled={!varName.trim()} onClick={startVar}>
              开始追踪
            </Button>
          </>
        }
      >
        <p className="pd-muted" style={{ marginBottom: 10 }}>
          输入变量名，AI 会说明它在{symbol && symbol.path === file.path ? `函数 ${symbol.name}` : '这个文件'}里从哪里来、被怎样修改、流向哪里。
        </p>
        <Input value={varName} onChange={setVarName} onEnter={startVar} placeholder="例如 batch_size" aria-label="变量名" />
      </Dialog>
    </div>
  )
}
