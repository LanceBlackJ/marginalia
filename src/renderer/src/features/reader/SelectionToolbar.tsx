import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentType, type KeyboardEvent, type SVGProps } from 'react'
import {
  BookMarked,
  BookOpenText,
  Compass,
  Copy,
  Languages,
  Lightbulb,
  ListCollapse,
  MessageSquarePlus,
  MoreHorizontal,
  NotebookPen,
  Search,
  Sigma,
  Tags
} from 'lucide-react'
import type { HighlightColor } from '@/lib/types'
import { useEquationAtSelection } from '@/features/formula/useEquationAtSelection'
import { useRefStore } from '@/features/text/refStore'
import {
  askFreeform,
  askQuick,
  copySelection,
  explainEquation,
  highlightSelection,
  noteSelection,
  openCitation,
  roleSelection,
  searchSelection,
  translateSelection
} from './selectionActions'
import { useReaderStore } from './readerStore'
import { placeSelectionToolbar } from './selectionToolbarLayout'
import {
  citationIndexes,
  getLastHighlightColor,
  HIGHLIGHT_COLORS,
  moreKeys,
  primaryKeys,
  selectionTraits,
  setLastHighlightColor,
  type MoreKey,
  type PrimaryKey
} from './selectionToolbarModel'
import type { SelectionInfo } from './pdf/selection'

type Icon = ComponentType<SVGProps<SVGSVGElement> & { size?: number | string }>

interface Item {
  label: string
  title: string
  icon: Icon
  run: (sel: SelectionInfo) => void
}

const MORE_ITEMS: Record<Exclude<MoreKey, 'highlight'>, Item> = {
  translate: { label: '翻译', title: '翻译选中内容', icon: Languages, run: translateSelection },
  explain: { label: '解释', title: '解释选中内容', icon: BookOpenText, run: (s) => askQuick(s, 'explain') },
  summarize: { label: '总结', title: '总结选中内容', icon: ListCollapse, run: (s) => askQuick(s, 'summarize') },
  term: { label: '术语解释', title: '解释其中的术语', icon: Tags, run: (s) => askQuick(s, 'term') },
  motivation: { label: '分析设计动机', title: '分析作者为什么这样设计', icon: Lightbulb, run: (s) => askQuick(s, 'motivation') },
  ask: { label: '提问', title: '把选中内容作为上下文提问', icon: MessageSquarePlus, run: askFreeform },
  copy: { label: '复制', title: '复制选中的文字', icon: Copy, run: copySelection },
  search: { label: '在文中搜索', title: '在整篇论文里搜索选中的文字', icon: Search, run: searchSelection }
}

function clearSelection(): void {
  window.getSelection()?.removeAllRanges()
  useReaderStore.getState().setSelection(null)
}

/**
 * 选中文字后出现在选区附近的小浮条：常态只有【翻译】【解释】【笔记】和一个【更多】，
 * 总结、术语、设计动机、提问、高亮、复制、搜索都收在“更多”菜单里。
 * 公式上的选区把“解释”换成“解释公式”，引用编号的选区把“翻译”换成“查看引用”。
 */
export function SelectionToolbar() {
  const sel = useReaderStore((s) => s.selection)
  const equation = useEquationAtSelection(sel)
  const byIndex = useRefStore((s) => s.byIndex)
  const ref = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 190, h: 32 })
  const [more, setMore] = useState(false)
  const [color, setColor] = useState<HighlightColor>(() => getLastHighlightColor())

  const text = sel?.text ?? ''
  const traits = useMemo(() => selectionTraits(text), [text])
  const cite = useMemo(() => {
    const idx = citationIndexes(text)
    const valid = idx?.filter((i) => byIndex.has(i)) ?? []
    return valid.length > 0 ? valid : null
  }, [text, byIndex])
  const primary = useMemo(() => primaryKeys(traits, { equation: Boolean(equation), cite: Boolean(cite), long: text.trim().length >= 60 }), [traits, equation, cite, text])
  const moreList = useMemo(() => moreKeys(primary, { fromText: Boolean(sel?.fromText) }), [primary, sel?.fromText])

  // 选区变了就收起“更多”
  useEffect(() => setMore(false), [text, sel?.page])

  // 量出工具条自己的实际尺寸（按钮数量随选区类型变化）
  useLayoutEffect(() => {
    const el = ref.current
    if (el && (Math.abs(el.offsetWidth - size.w) > 1 || Math.abs(el.offsetHeight - size.h) > 1)) {
      setSize({ w: el.offsetWidth, h: el.offsetHeight })
    }
  })

  // Esc：先收起“更多”，再关闭工具条；点工具条以外的地方也收起“更多”
  useEffect(() => {
    if (!sel) return
    const onKey = (e: globalThis.KeyboardEvent): void => {
      if (e.key !== 'Escape') return
      if (more) setMore(false)
      else clearSelection()
    }
    const onDown = (e: MouseEvent): void => {
      if (more && ref.current && !ref.current.contains(e.target as Node)) setMore(false)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onDown)
    }
  }, [sel, more])

  if (!sel) return null
  const r = sel.clientRect
  // 选区已滚出视口则不显示
  if (r.bottom < 56 || r.top > window.innerHeight - 8) return null

  const pos = placeSelectionToolbar(r, size, { width: window.innerWidth, height: window.innerHeight })
  const style = { left: pos.left, top: pos.top, transform: 'none' as const }

  const primaryItem = (k: PrimaryKey): Item => {
    switch (k) {
      case 'translate':
        return MORE_ITEMS.translate
      case 'explain':
        return MORE_ITEMS.explain
      case 'equation':
        return {
          label: '解释公式',
          title: '识别这个公式并讲解符号和含义',
          icon: Sigma,
          run: (s) => equation && explainEquation(s, equation.id)
        }
      case 'cite':
        return { label: '查看引用', title: '查看这条参考文献的详情和摘要', icon: BookMarked, run: (s) => openCitation(s, cite?.[0] ?? 0) }
      case 'role':
        return { label: '这段怎么读', title: '这一段在论文里起什么作用、该带走什么、读的时候留意什么', icon: Compass, run: roleSelection }
      case 'note':
        return { label: '笔记', title: '给选中内容记一条笔记', icon: NotebookPen, run: noteSelection }
    }
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    const root = ref.current
    if (!root) return
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      const els = Array.from(root.querySelectorAll<HTMLButtonElement>('button[data-seltool="main"]'))
      const i = els.indexOf(document.activeElement as HTMLButtonElement)
      if (els.length === 0) return
      e.preventDefault()
      els[(i + (e.key === 'ArrowRight' ? 1 : -1) + els.length) % els.length].focus()
    } else if (more && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      const els = Array.from(root.querySelectorAll<HTMLButtonElement>('button[data-seltool="item"]'))
      const i = els.indexOf(document.activeElement as HTMLButtonElement)
      if (els.length === 0) return
      e.preventDefault()
      els[(i + (e.key === 'ArrowDown' ? 1 : -1) + els.length) % els.length].focus()
    }
  }

  const compact = traits.short && primary.length >= 2

  return (
    <div
      ref={ref}
      className="pd-seltool"
      data-place={pos.place}
      data-compact={compact}
      style={style}
      role="toolbar"
      aria-label="选中文字操作"
      onMouseDown={(e) => e.preventDefault()}
      onKeyDown={onKeyDown}
    >
      {primary.map((k) => {
        const it = primaryItem(k)
        return (
          <button key={k} type="button" className="pd-seltool-btn" data-seltool="main" title={it.title} aria-label={it.label} onClick={() => it.run(sel)}>
            <it.icon size={14} />
            <span className="pd-seltool-label">{it.label}</span>
          </button>
        )
      })}
      <button
        type="button"
        className="pd-seltool-btn pd-seltool-more"
        data-seltool="main"
        title="更多"
        aria-label="更多"
        aria-haspopup="menu"
        aria-expanded={more}
        onClick={() => setMore((v) => !v)}
      >
        <MoreHorizontal size={15} />
      </button>
      {more && (
        <div className="pd-seltool-menu" role="menu" data-side={pos.place === 'below' ? 'up' : 'down'}>
          {moreList.map((k) => {
            if (k === 'highlight') {
              return (
                <div key={k} className="pd-seltool-item pd-seltool-item--row" role="none">
                  <button
                    type="button"
                    role="menuitem"
                    className="pd-seltool-itembtn"
                    data-seltool="item"
                    title="用上次的颜色高亮选中内容"
                    onClick={() => {
                      setMore(false)
                      highlightSelection(sel, color)
                    }}
                  >
                    <span className="pd-seltool-swatch pd-seltool-swatch--current" style={{ background: `var(--hl-${color})` }} aria-hidden />
                    高亮
                  </button>
                  <span className="pd-seltool-swatches" role="group" aria-label="高亮颜色">
                    {HIGHLIGHT_COLORS.map(([c, label]) => (
                      <button
                        key={c}
                        type="button"
                        className="pd-seltool-swatch"
                        data-selected={c === color}
                        style={{ background: `var(--hl-${c})` }}
                        title={`${label}高亮`}
                        aria-label={`${label}高亮`}
                        onClick={() => {
                          setColor(c)
                          setLastHighlightColor(c)
                          setMore(false)
                          highlightSelection(sel, c)
                        }}
                      />
                    ))}
                  </span>
                </div>
              )
            }
            const it = MORE_ITEMS[k]
            return (
              <button
                key={k}
                type="button"
                role="menuitem"
                className="pd-seltool-item pd-seltool-itembtn"
                data-seltool="item"
                title={it.title}
                onClick={() => {
                  setMore(false)
                  it.run(sel)
                }}
              >
                <it.icon size={14} />
                {it.label}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
