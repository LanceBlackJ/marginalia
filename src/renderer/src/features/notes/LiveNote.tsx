import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { NoteBody } from './NoteBody'

/**
 * 没有“编辑 / 预览”之分的笔记输入：按段落分块，正在写的那一块显示源码，其余的块都是渲染好的样子；
 * 空一行（或写完一个 `$$公式$$`）当前块就渲染出来，光标自动落到下一块；点任何一块就回到它的源码继续改。
 */

const FENCE = /^\s*```/gm

/** 一个块里的围栏代码或 `$$` 公式还没闭合：块内的空行不算分块。 */
export function isOpenBlock(b: string): boolean {
  return (b.match(FENCE)?.length ?? 0) % 2 === 1 || (b.match(/\$\$/g)?.length ?? 0) % 2 === 1
}

export function splitBlocks(text: string): string[] {
  const out: string[] = []
  for (const p of text.split(/\n{2,}/)) {
    const last = out[out.length - 1]
    if (last !== undefined && isOpenBlock(last)) out[out.length - 1] = `${last}\n\n${p}`
    else out.push(p)
  }
  return out.length ? out : ['']
}

export function joinBlocks(blocks: string[]): string {
  return blocks.join('\n\n')
}

/** 整块就是一个写完的 `$$…$$` 公式：写到这里就该渲染了。 */
export function isCompleteDisplayMath(b: string): boolean {
  return /^\s*\$\$[^$]+\$\$\s*$/.test(b)
}

type Caret = 'start' | 'end'

export function LiveNoteEditor({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  const blocks = useMemo(() => splitBlocks(value), [value])
  const empty = blocks.length === 1 && blocks[0] === ''
  // 新便签从第一块开始写；已有内容的便签，光标落在最后一块的末尾
  const [active, setActive] = useState<number | null>(() => blocks.length - 1)
  const caret = useRef<Caret>('end')
  const ta = useRef<HTMLTextAreaElement>(null)

  const commit = (next: string[], nextActive: number | null, at: Caret = 'end'): void => {
    caret.current = at
    setActive(nextActive)
    onChange(joinBlocks(next))
  }

  // 切到某一块的源码时：聚焦并放好光标
  useEffect(() => {
    const el = ta.current
    if (!el || active === null) return
    el.focus()
    const p = caret.current === 'start' ? 0 : el.value.length
    el.setSelectionRange(p, p)
  }, [active])

  // 输入区跟着内容长高
  useLayoutEffect(() => {
    const el = ta.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.max(el.scrollHeight, 24)}px`
  })

  const edit = (i: number, text: string): void => {
    const next = [...blocks]
    if (/\n{2,}/.test(text) && !isOpenBlock(text)) {
      // 空了一行：这一块写完了，渲染出来，光标去下一块
      const [head, ...rest] = splitBlocks(text)
      next.splice(i, 1, head.replace(/\s+$/, ''), ...(rest.length ? rest : ['']))
      commit(next, i + 1, 'start')
      return
    }
    if (isCompleteDisplayMath(text) && i === blocks.length - 1) {
      // `$$公式$$` 写完：渲染，接着写下一块
      next[i] = text.trim()
      next.push('')
      commit(next, i + 1, 'start')
      return
    }
    next[i] = text
    onChange(joinBlocks(next))
  }

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>, i: number): void => {
    const el = e.currentTarget
    // 在块的最前面按退格：并入上一块
    if (e.key === 'Backspace' && i > 0 && el.selectionStart === 0 && el.selectionEnd === 0) {
      e.preventDefault()
      const next = [...blocks]
      const merged = next[i - 1] ? `${next[i - 1]}\n${next[i]}`.replace(/\n$/, '') : next[i]
      next.splice(i - 1, 2, merged)
      commit(next, i - 1, 'end')
    }
  }

  return (
    <div className="pd-live">
      {blocks.map((b, i) =>
        i === active ? (
          <textarea
            key={i}
            ref={ta}
            className="pd-live-text"
            value={b}
            rows={1}
            aria-label="笔记内容"
            placeholder={empty ? placeholder : i === blocks.length - 1 ? '' : undefined}
            onChange={(e) => edit(i, e.target.value)}
            onKeyDown={(e) => onKey(e, i)}
            onBlur={() => setActive((a) => (a === i ? null : a))}
          />
        ) : b.trim() === '' ? (
          <button
            key={i}
            type="button"
            className="pd-live-gap"
            aria-label="在这里写"
            onMouseDown={(e) => {
              e.preventDefault()
              caret.current = 'end'
              setActive(i)
            }}
          />
        ) : (
          <div
            key={i}
            className="pd-live-block"
            role="button"
            tabIndex={0}
            // 用 mousedown：这时正在写的那块还没失焦、版面还没变，点到的就是想点的这一块
            onMouseDown={(e) => {
              e.preventDefault()
              caret.current = 'end'
              setActive(i)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') setActive(i)
            }}
          >
            <NoteBody text={b} />
          </div>
        )
      )}
      {active === null && !empty && (
        <button type="button" className="pd-live-gap pd-live-gap--end" aria-label="接着写" onMouseDown={(e) => {
            e.preventDefault()
            commit([...blocks, ''], blocks.length, 'start')
          }} />
      )}
    </div>
  )
}
