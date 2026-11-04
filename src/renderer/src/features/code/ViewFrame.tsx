import { useState, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'

/**
 * 代码工作区各视图共用的页面骨架：
 * 页面头（22px/700 标题 + 一行 13px 说明 + 右侧最多一个主按钮和一个“更多”）→ 内容；
 * 列表/表格类视图用满宽，文字类视图（width="narrow"）内容区最大 880px 居中。
 */
export function ViewFrame({ width = 'full', children }: { width?: 'full' | 'narrow'; children: ReactNode }) {
  return (
    <div className="pd-vf" data-width={width}>
      <div className="pd-vf-inner">{children}</div>
    </div>
  )
}

export function ViewHead({ title, desc, actions }: { title: string; desc?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="pd-vf-head">
      <div className="pd-vf-head-text">
        <h2 className="pd-vf-title">{title}</h2>
        {desc && <p className="pd-vf-desc">{desc}</p>}
      </div>
      {actions && <div className="pd-vf-actions">{actions}</div>}
    </header>
  )
}

/** 区块：15px/600 标题 + 可选一行说明，区块之间 1px 线分隔；strong 的顶部用 2px 黑线。 */
export function ViewSection({ title, hint, aside, strong, children }: { title: string; hint?: ReactNode; aside?: ReactNode; strong?: boolean; children: ReactNode }) {
  return (
    <section className="pd-vf-section" data-strong={strong ? 'true' : undefined}>
      <header className="pd-vf-sec-head">
        <h3 className="pd-vf-sec-title">{title}</h3>
        {hint && <span className="pd-vf-sec-hint">{hint}</span>}
        {aside && <span className="pd-vf-sec-aside">{aside}</span>}
      </header>
      <div className="pd-vf-sec-body">{children}</div>
    </section>
  )
}

/**
 * 步骤式分区：当前步骤展开，其余只留一行标题（序号 + 名称 + 摘要），点击标题切换。
 * 用于“评估 → 计划 → 结果对比”这类有先后顺序的流程。
 */
export function StepSection({
  index,
  title,
  summary,
  open,
  onToggle,
  children
}: {
  index: number
  title: string
  summary?: ReactNode
  open: boolean
  onToggle: () => void
  children: ReactNode
}) {
  return (
    <section className="pd-step" data-open={open ? 'true' : 'false'}>
      <button type="button" className="pd-step-head" aria-expanded={open} onClick={onToggle}>
        <span className="pd-step-index" aria-hidden="true">
          {index}
        </span>
        <span className="pd-step-title">{title}</span>
        {summary && <span className="pd-step-summary">{summary}</span>}
        <ChevronRight size={16} className="pd-step-chev" aria-hidden="true" />
      </button>
      {open && <div className="pd-step-body">{children}</div>}
    </section>
  )
}

/** 一行可展开的“更多/高级”折叠，用于低频选项。 */
export function Disclosure({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="pd-disc">
      <button type="button" className="pd-disc-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <ChevronRight size={14} className="pd-disc-chev" data-open={open ? 'true' : 'false'} aria-hidden="true" />
        {label}
      </button>
      {open && <div className="pd-disc-body">{children}</div>}
    </div>
  )
}
