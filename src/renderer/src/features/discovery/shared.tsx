import type { ReactNode } from 'react'
import { EmptyState, Skeleton } from '@/components/ui'

/** 发现页三种方式共用的加载态：一行说明加几行骨架。 */
export function DcLoading({ text, rows = 3 }: { text?: string; rows?: 2 | 3 | 5 }) {
  return (
    <div role="status" aria-live="polite" style={{ marginTop: 'var(--sp-2xl)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-md)' }}>
      {text && <p className="pd-dc-hint">{text}</p>}
      <Skeleton height={20} width="60%" />
      <Skeleton height={14} width="90%" />
      {rows >= 3 && <Skeleton height={14} width="80%" />}
      {rows >= 5 && (
        <>
          <Skeleton height={20} width="55%" style={{ marginTop: 16 }} />
          <Skeleton height={14} width="85%" />
        </>
      )}
    </div>
  )
}

/** 空状态：标题加一句说明（可带一个操作）。 */
export function DcEmpty({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <EmptyState title={title} description={description} action={action} />
}
