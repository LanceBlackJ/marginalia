import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'

/**
 * 模型的思考过程：和回答分开显示，默认折叠。
 * 生成中、回答正文还没出来时自动展开（能看见它在想什么，而不是干等）；回答开始后收起，随时可以点开。
 */
export function ThinkingBlock({ text, live = false, answering = false }: { text: string; live?: boolean; answering?: boolean }) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null)
  const open = userOpen ?? (live && !answering)
  return (
    <div className="pd-think">
      <button type="button" className="pd-sent-toggle" onClick={() => setUserOpen(!open)} aria-expanded={open}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        {live && !answering ? '思考中…' : '思考过程'}
        <span className="pd-aux pd-weak"> · 模型自己的推理，不是论文原文，也没有核实</span>
      </button>
      {open && <div className="pd-think-body">{text}</div>}
    </div>
  )
}
