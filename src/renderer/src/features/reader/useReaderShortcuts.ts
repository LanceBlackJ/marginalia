import { useEffect } from 'react'
import { useNotesStore } from '@/features/notes/notesStore'
import type { TextView } from '@/features/text/textModel'
import { useReaderStore } from './readerStore'

const TEXT_CYCLE: TextView[] = ['text', 'both', 'target']

/** 在输入框、文本域、可编辑区域里打字时不触发快捷键。 */
export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false
  return t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable
}

/** 当前正在滚动的阅读区域：并排视图里优先文字栏。 */
function scroller(): HTMLElement | null {
  return document.querySelector<HTMLElement>('.pd-textreader') ?? document.querySelector<HTMLElement>('.pd-viewer')
}

/** 上一节 / 下一节：按目录里的章节顺序，从“当前页所在章节”往前或往后一个。 */
export function neighbourSection<T extends { title: string; page: number; id?: string }>(outline: T[], currentPage: number, current: string | null, dir: 1 | -1): T | null {
  if (outline.length === 0) return null
  // current 可以是章节键（#id，同名小节也唯一）或标题
  let idx = current?.startsWith('#') ? outline.findIndex((o) => o.id !== undefined && `#${o.id}` === current) : -1
  if (idx < 0) idx = outline.findIndex((o) => o.title === current)
  if (idx < 0) {
    idx = -1
    for (let i = 0; i < outline.length; i++) if (outline[i].page <= currentPage) idx = i
  }
  const next = outline[Math.max(0, Math.min(outline.length - 1, idx + dir))]
  return next ?? null
}

/** 阅读器快捷键（J/K 翻屏、[ ] 切章节、/ 搜索、T/S 切换界面、F 专注、A 外观、N 笔记、? 帮助、Esc 退出）。 */
export function useReaderShortcuts(enabled: boolean): void {
  useEffect(() => {
    if (!enabled) return
    const onKey = (e: KeyboardEvent): void => {
      if (isTypingTarget(e.target)) return
      // Alt+← ：回到跳转之前的位置（点了目录、引用、证据、书签之后）
      if (e.altKey && !e.ctrlKey && !e.metaKey && e.key === 'ArrowLeft') {
        const s = useReaderStore.getState()
        if (s.backStack.length > 0) {
          e.preventDefault()
          s.goBack()
        }
        return
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const st = useReaderStore.getState()
      const k = e.key
      if (k === 'Escape') {
        if (st.appearanceOpen) st.setAppearanceOpen(false)
        else if (st.focus) st.setFocus(false)
        return
      }
      const lower = k.length === 1 ? k.toLowerCase() : k
      switch (lower) {
        case 'j':
        case 'k': {
          const el = scroller()
          if (!el) return
          e.preventDefault()
          el.scrollBy({ top: (lower === 'j' ? 1 : -1) * el.clientHeight * 0.8, behavior: 'smooth' })
          return
        }
        case '[':
        case ']': {
          const dir = lower === ']' ? 1 : -1
          const target = neighbourSection(st.outline, st.currentPage, st.currentSectionKey(), dir)
          if (target) {
            e.preventDefault()
            st.jumpToSection(target.page, target.title, target.id)
          }
          return
        }
        case '/':
          e.preventDefault()
          st.setSearchOpen(true)
          return
        case 't': {
          if (st.noText) return
          const cur = st.readView === 'split' ? st.splitTextView : st.readView === 'pdf' ? 'target' : (st.readView as TextView)
          const next = TEXT_CYCLE[(TEXT_CYCLE.indexOf(cur) + 1) % TEXT_CYCLE.length]
          if (st.readView === 'split') st.setSplitTextView(next)
          else st.setReadView(next)
          return
        }
        case 's': {
          if (st.noText) return
          const order = ['text', 'split', 'pdf'] as const
          const i = order.indexOf(st.readView as (typeof order)[number])
          st.setReadView(order[(i + 1) % order.length])
          return
        }
        case 'f':
          st.setFocus(!st.focus)
          return
        case 'a':
          st.setAppearanceOpen(!st.appearanceOpen)
          return
        case 'n':
          st.setRight(true, 'notes')
          if (!useNotesStore.getState().draft) useNotesStore.getState().startDraft({ page: st.currentPage })
          return
        case '?':
          st.setHelpOpen(true)
          return
        default:
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled])
}
