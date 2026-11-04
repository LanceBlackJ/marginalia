import type { CSSProperties, ReactNode } from 'react'

export function Spinner({ size = 16 }: { size?: number }) {
  return <span className="pd-spinner" role="status" aria-label="加载中" style={{ width: size, height: size }} />
}

export function Skeleton({ width = '100%', height = 14, style }: { width?: number | string; height?: number | string; style?: CSSProperties }) {
  return <div className="pd-skeleton" aria-hidden style={{ width, height, ...style }} />
}

/** value 为 0–1；不传则为不确定进度。 */
export function ProgressBar({ value }: { value?: number }) {
  const indeterminate = value === undefined
  return (
    <div
      className={`pd-progress${indeterminate ? ' pd-progress--indeterminate' : ''}`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(value * 100)}
    >
      <div style={{ width: indeterminate ? undefined : `${Math.min(1, Math.max(0, value)) * 100}%` }} />
    </div>
  )
}

type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'info' | 'outline'

export function Tag({ tone = 'neutral', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`pd-tag${tone === 'neutral' ? '' : ` pd-tag--${tone}`}`} title={title}>
      {children}
    </span>
  )
}

interface EmptyStateProps {
  icon?: ReactNode
  title: string
  description?: ReactNode
  action?: ReactNode
  tone?: 'default' | 'danger'
}

export function EmptyState({ icon, title, description, action, tone = 'default' }: EmptyStateProps) {
  return (
    <div className={`pd-empty${tone === 'danger' ? ' pd-empty--danger' : ''}`}>
      {icon && <div className="pd-empty-icon">{icon}</div>}
      <div className="pd-empty-title">{title}</div>
      {description && <div className="pd-empty-desc">{description}</div>}
      {action && <div className="pd-empty-action">{action}</div>}
    </div>
  )
}
