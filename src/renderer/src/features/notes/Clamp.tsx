import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'

/** 内容超过 `lines` 行就折起来（底部渐隐），带“展开 / 收起”；没超过就原样显示、不出按钮。笔记再多、再长也不会把页面越撑越长。 */
export function Clamp({ lines, children, className = '' }: { lines: number; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [overflow, setOverflow] = useState(false)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el || open) return
    setOverflow(el.scrollHeight > el.clientHeight + 2)
  })

  return (
    <div className={className}>
      <div ref={ref} className={`pd-clamp${open ? '' : ' is-clamped'}${overflow && !open ? ' is-fade' : ''}`} style={{ ['--clamp-lines' as string]: lines }}>
        {children}
      </div>
      {(overflow || open) && (
        <button type="button" className="pd-clamp-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? '收起' : '展开'}
        </button>
      )}
    </div>
  )
}
