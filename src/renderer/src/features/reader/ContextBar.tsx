import type { ReactNode } from 'react'
import { useReaderStore } from './readerStore'

/** 当前所在章节：取目录里页码不超过当前页的最后一项。 */
export function sectionAt(outline: Array<{ page: number; title: string }>, page: number): string | null {
  let section: string | null = null
  for (const it of outline) {
    if (it.page <= page) section = it.title
    else break
  }
  return section
}

/**
 * 右侧面板顶部的一行：位置（第 N 页 · 章节），右侧可放当前标签的筛选。
 * 选中的文字不在这里显示，只在提问框上方出现一次。
 */
export function ContextBar({ children }: { children?: ReactNode }) {
  const paper = useReaderStore((s) => s.paper)
  const currentPage = useReaderStore((s) => s.currentPage)
  const outline = useReaderStore((s) => s.outline)
  // 文字阅读器里按标题的实际位置算出的当前小节（和左边目录的高亮同一个来源）；没有（PDF 视图等）才按页码估
  const activeTitle = useReaderStore((s) => s.activeSectionTitle)
  const active = activeTitle ? (activeTitle.startsWith('#') ? outline.find((o) => `#${o.id}` === activeTitle)?.title : activeTitle) : null
  const section = active ?? sectionAt(outline, currentPage)
  return (
    <div className="pd-context" aria-label="当前位置" title={paper?.title}>
      <div className="pd-context-line">
        <span className="pd-context-pos">第 {currentPage} 页</span>
        {section && <span className="pd-ellipsis pd-context-sec">{section}</span>}
        {children && <span className="pd-context-right">{children}</span>}
      </div>
    </div>
  )
}
