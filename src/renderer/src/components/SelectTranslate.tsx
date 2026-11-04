import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Copy, Languages, X } from 'lucide-react'
import { Skeleton, toast } from '@/components/ui'
import { friendlyTranslateError, streamTranslate } from '@/lib/translateText'
import { MAX_TRANSLATE_CHARS, placeBar, placePanel, selectionContainer, shouldOfferTranslate, type Box, type PanelPlacement } from './selectTranslateModel'
import './selectTranslate.css'

/**
 * 划词翻译：给任意容器加属性 `data-translatable`，用户在里面选中英文文字，就在选区上方出现一条很小的浮条（翻译、复制）；
 * 点“翻译”（或选中后按 T）在选区下方弹出小浮层，流式显示译文。译文由后端按文本哈希缓存，同一段不会重复发送；发送的文字记入隐私日志。
 *
 * 用法：在页面里挂一次 `<SelectTranslate paperId={当前论文 id} />`，再给需要的容器加 `data-translatable`：
 *   <p data-translatable>An English abstract …</p>
 * - 选中的内容中文为主、太短或太长（>6000 字）时不显示“翻译”；复制仍可用。
 * - 选区跨出容器不处理；选区变化、点击别处、按 Esc 都会关闭。
 * - 组件自己的浮条/浮层带 `data-selecttranslate-ui`，其它浮层（如点击即关的弹层）判断“点在外面”时应排除它。
 */
export interface SelectTranslateProps {
  /** 当前论文 id：/ai/translate 需要它来缓存译文和记录隐私日志；没有时不工作 */
  paperId: string | null | undefined
  /** 关掉整个功能（默认开启） */
  disabled?: boolean
  /** 论文不在文献库里（如发现页里的摘要）：没有 paperId 也能翻译，只是译文不缓存 */
  standalone?: boolean
  /** standalone 时用来帮助翻译的论文标题 */
  contextTitle?: string
}

interface Sel {
  text: string
  rect: Box
  offer: boolean
}

type Status = 'idle' | 'streaming' | 'done' | 'error'

function rectOf(sel: Selection): Box | null {
  try {
    const r = sel.getRangeAt(0).getBoundingClientRect?.()
    if (r && (r.width > 0 || r.height > 0)) return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }
  } catch {
    /* 退回用容器的位置 */
  }
  const el = sel.anchorNode instanceof Element ? sel.anchorNode : sel.anchorNode?.parentElement
  const b = el?.getBoundingClientRect()
  return b ? { left: b.left, right: b.right, top: b.top, bottom: b.bottom } : null
}

export function SelectTranslate({ paperId, disabled, standalone, contextTitle }: SelectTranslateProps) {
  const enabled = Boolean(paperId) || Boolean(standalone)
  const [sel, setSel] = useState<Sel | null>(null)
  const [panel, setPanel] = useState<{ source: string; rect: Box } | null>(null)
  const [status, setStatus] = useState<Status>('idle')
  const [text, setText] = useState('')
  const [meta, setMeta] = useState<{ model: string; cached: boolean } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [barPos, setBarPos] = useState<{ left: number; top: number } | null>(null)
  const [panelPos, setPanelPos] = useState<PanelPlacement | null>(null)
  const barRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const ctrl = useRef<AbortController | null>(null)

  const closeAll = useCallback((): void => {
    ctrl.current?.abort()
    setSel(null)
    setPanel(null)
    setStatus('idle')
  }, [])

  const run = useCallback(
    async (source: string, rect: Box, force = false): Promise<void> => {
      if (!enabled) return
      ctrl.current?.abort()
      const c = new AbortController()
      ctrl.current = c
      setPanel({ source, rect })
      setSel(null)
      setStatus('streaming')
      setText('')
      setMeta(null)
      setError(null)
      await streamTranslate(
        paperId ?? null,
        source.slice(0, MAX_TRANSLATE_CHARS),
        {
          onMeta: (m) => setMeta(m),
          onDelta: (d) => setText((t) => t + d),
          onDone: (r) => {
            setText(r.translation)
            setMeta({ model: r.model, cached: r.cached })
            setStatus('done')
          },
          onError: (e) => {
            setError(friendlyTranslateError(e))
            setStatus('error')
          }
        },
        c.signal,
        force,
        contextTitle
      )
    },
    [paperId, enabled, contextTitle]
  )

  // 选区变化：只在“带 data-translatable 的容器”里选中文字时出现浮条
  useEffect(() => {
    if (disabled || !enabled) return
    const onChange = (): void => {
      const s = window.getSelection()
      const container = selectionContainer(s)
      const t = s?.toString().trim() ?? ''
      if (!s || !container || !t) {
        setSel(null)
        return
      }
      const rect = rectOf(s)
      if (rect) setSel({ text: t, rect, offer: shouldOfferTranslate(t) })
    }
    document.addEventListener('selectionchange', onChange)
    return () => document.removeEventListener('selectionchange', onChange)
  }, [disabled, enabled])

  // 点击别处、Esc 关闭；选中后按 T 翻译
  useEffect(() => {
    if (!sel && !panel) return
    const down = (e: PointerEvent): void => {
      const t = e.target as Element | null
      if (t?.closest?.('[data-selecttranslate-ui]')) return
      if (panel) closeAll()
    }
    const key = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.stopPropagation() // 捕获阶段先关译文，不让同一次 Esc 再关掉下面的菜单/浮层/抽屉
        closeAll()
      } else if ((e.key === 't' || e.key === 'T') && sel?.offer && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const target = e.target as HTMLElement | null
        if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
        e.preventDefault()
        void run(sel.text, sel.rect)
      }
    }
    document.addEventListener('pointerdown', down, true)
    document.addEventListener('keydown', key, true)
    return () => {
      document.removeEventListener('pointerdown', down, true)
      document.removeEventListener('keydown', key, true)
    }
  }, [sel, panel, closeAll, run])

  useLayoutEffect(() => {
    if (!sel) return setBarPos(null)
    const b = barRef.current
    setBarPos(placeBar(sel.rect, { w: b?.offsetWidth ?? 150, h: b?.offsetHeight ?? 34 }, { w: window.innerWidth, h: window.innerHeight }))
  }, [sel])

  useLayoutEffect(() => {
    if (!panel) return setPanelPos(null)
    setPanelPos(placePanel(panel.rect, 360, { w: window.innerWidth, h: window.innerHeight }, panelRef.current?.offsetHeight ?? 200))
  }, [panel, text, status])

  useEffect(() => () => ctrl.current?.abort(), [])

  const copy = async (s: string): Promise<void> => {
    try {
      await navigator.clipboard.writeText(s)
      toast.success('已复制')
    } catch {
      toast.error('复制失败', '没有剪贴板权限')
    }
  }

  return (
    <>
      {sel && (
        <div
          ref={barRef}
          className="pd-seltr-bar"
          data-selecttranslate-ui
          role="toolbar"
          aria-label="选中文字"
          style={{ left: barPos?.left ?? 0, top: barPos?.top ?? 0, visibility: barPos ? 'visible' : 'hidden' }}
          onMouseDown={(e) => e.preventDefault() /* 点按钮不要清掉选区 */}
        >
          {sel.offer && (
            <button type="button" title="翻译成中文（选中后按 T）" onClick={() => void run(sel.text, sel.rect)}>
              <Languages size={13} />
              翻译
            </button>
          )}
          <button type="button" title="复制选中的文字" onClick={() => void copy(sel.text)}>
            <Copy size={13} />
            复制
          </button>
        </div>
      )}
      {panel && (
        <div
          ref={panelRef}
          className="pd-seltr-panel"
          data-selecttranslate-ui
          role="dialog"
          aria-label="译文"
          style={{ left: panelPos?.left ?? 0, top: panelPos?.top, bottom: panelPos?.bottom, maxHeight: panelPos?.maxHeight, visibility: panelPos ? 'visible' : 'hidden' }}
        >
          <div className="pd-seltr-head">
            <strong>译文</strong>
            <span className="pd-seltr-sub">AI 翻译{meta?.model ? ` · ${meta.model}` : ''}{meta?.cached ? ' · 已缓存' : ''}</span>
            <button type="button" aria-label="关闭" onClick={closeAll}>
              <X size={14} />
            </button>
          </div>
          <p className="pd-seltr-src">{panel.source.length > 160 ? `${panel.source.slice(0, 160)}…` : panel.source}</p>
          {status === 'streaming' && !text && <Skeleton height={12} />}
          {text && <p className="pd-seltr-text">{text}</p>}
          {status === 'error' && (
            <p className="pd-seltr-err">
              {error}{' '}
              {error && !error.includes('API Key') && (
                <button type="button" className="pd-tp-link" onClick={() => void run(panel.source, panel.rect, true)}>
                  重试
                </button>
              )}
            </p>
          )}
          {status === 'done' && (
            <div className="pd-seltr-foot">
              <button type="button" className="pd-tp-link" onClick={() => void copy(text)}>
                复制译文
              </button>
            </div>
          )}
        </div>
      )}
    </>
  )
}
