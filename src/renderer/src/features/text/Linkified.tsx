import type { ReactNode } from 'react'
import { openExternal } from '@/lib/native'
import { isSafeHref, splitLinks } from './links'
import './links.css'

/** 一个可点击的链接：用系统浏览器打开（Electron）或新标签页（网页调试），悬停显示真实地址。 */
export function LinkAnchor({ href, children }: { href: string; children: ReactNode }) {
  if (!isSafeHref(href)) return <>{children}</>
  return (
    <a
      className="pd-tp-a"
      href={href}
      title={href}
      rel="noopener noreferrer"
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation() // 参考文献条目、段落本身的点击不要跟着触发
        void openExternal(href)
      }}
      onContextMenu={(e) => e.stopPropagation()}
    >
      {children}
    </a>
  )
}

/**
 * 把文字里的链接渲染成超链接，其余文字交给 wrap 处理（例如行内数学、上标角标）。
 * 复制出去的仍是原文：链接的可见文字就是原文里的那一段，包括被拆开的空格。
 */
export function Linkified({ text, wrap }: { text: string; wrap?: (s: string) => ReactNode }) {
  const pieces = splitLinks(text)
  if (pieces.length === 1 && !('href' in pieces[0])) return <>{wrap ? wrap(text) : text}</>
  return (
    <>
      {pieces.map((p, i) =>
        'href' in p ? (
          <LinkAnchor key={i} href={p.href}>
            {p.text}
          </LinkAnchor>
        ) : (
          <span key={i}>{wrap ? wrap(p.text) : p.text}</span>
        )
      )}
    </>
  )
}
