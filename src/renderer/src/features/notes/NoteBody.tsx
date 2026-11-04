import { useState } from 'react'
import { Markdown } from '@/components/Markdown'
import './sticky.css'

/** 笔记的正文：支持 Markdown（标题、列表、加粗、代码、引用、表格、链接）和 LaTeX（行内 $…$，独立成行 $$…$$）。笔记里存的永远是原文，这里只负责显示。 */
export function NoteBody({ text }: { text: string }) {
  return (
    <div className="pd-note-md">
      <Markdown text={blockMath(text)} />
    </div>
  )
}

/** 单独成行的 `$$公式$$` 常常写在同一行里，Markdown 会把它当成行内公式；整理成标准的块公式写法，才会居中显示。 */
export function blockMath(text: string): string {
  return text.replace(/^[ \t]*\$\$([^\n$]+?)\$\$[ \t]*$/gm, (_m, tex: string) => '$$\n' + tex.trim() + '\n$$')
}

export const NOTE_PLACEHOLDER = '写点什么…'

/** 写笔记时的“预览”：点一下看渲染后的样子，再点回去继续写。内容为空时不显示。 */
export function NotePreview({ text }: { text: string }) {
  const [on, setOn] = useState(false)
  if (!text.trim()) return null
  return (
    <div className="pd-note-preview">
      <button type="button" className="pd-dc-more" onClick={() => setOn((v) => !v)}>
        {on ? '收起预览' : '预览'}
      </button>
      {on && (
        <div className="pd-sticky pd-sticky--preview">
          <NoteBody text={text} />
        </div>
      )}
    </div>
  )
}
