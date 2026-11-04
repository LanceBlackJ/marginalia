import { useState, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'

/** 设置项：左侧名称 + 说明，右侧控件；窄容器自动上下堆叠。 */
export function SettingRow({ title, description, children }: { title: ReactNode; description?: ReactNode; children: ReactNode }) {
  return (
    <section className="pd-setting-row">
      <div className="pd-setting-row-info">
        <h3 className="pd-setting-row-title">{title}</h3>
        {description && <p className="pd-setting-row-desc">{description}</p>}
      </div>
      <div className="pd-setting-row-body">{children}</div>
    </section>
  )
}

/**
 * 设置页里的一段：标题和当前状态摘要，下面直接是内容，顺着往下排，不折叠。
 * 一个标签里的几段之间用一条细线分开。
 */
export function SettingsPanel({
  id,
  title,
  summary,
  warn,
  children
}: {
  id: string
  title: ReactNode
  summary?: ReactNode
  /** 以前用来决定默认展开；现在内容总是展开，保留参数只为调用处不用改。 */
  defaultOpen?: boolean
  warn?: boolean
  children: ReactNode
}) {
  return (
    <section id={id} className="pd-panel" data-warn={warn ? 'true' : undefined}>
      <h2 className="pd-panel-h">
        <span id={`${id}-title`} className="pd-panel-title">
          {title}
        </span>
        {summary ? <span className="pd-panel-sum">{summary}</span> : null}
      </h2>
      <div id={`${id}-body`} className="pd-panel-body">
        {children}
      </div>
    </section>
  )
}

/**
 * 大块里的一组相关设置项，有小标题和一行说明。
 * `advanced` 的块默认折叠（低频选项）；`wide` 的块（表格、记录）独占一整行，其余块在宽屏上两列并排。
 */
export function SettingsBlock({
  id,
  title,
  description,
  advanced,
  wide,
  children
}: {
  id?: string
  title: ReactNode
  description?: ReactNode
  advanced?: boolean
  wide?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(!advanced)
  return (
    <section id={id} className="pd-sblock" data-wide={wide ? 'true' : undefined} data-advanced={advanced ? 'true' : undefined}>
      <header className="pd-sblock-head">
        {advanced ? (
          <button type="button" className="pd-sblock-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
            <ChevronRight size={14} className="pd-sblock-chev" data-open={open} />
            <h3 className="pd-sblock-title">{title}</h3>
          </button>
        ) : (
          <h3 className="pd-sblock-title">{title}</h3>
        )}
        {description && <p className="pd-sblock-desc">{description}</p>}
      </header>
      {open && <div className="pd-sblock-body">{children}</div>}
    </section>
  )
}
