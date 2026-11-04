import { Tag } from '@/components/ui'

/** 尚未开发的模块：只如实说明，不放任何假数据或看似可用的按钮。左对齐在统一 gutter 上。 */
export function PlaceholderPage({ title, stage, description }: { title: string; stage: number; description: string }) {
  return (
    <div className="pd-page">
      <div className="pd-page-inner">
        <h1 className="pd-page-title">{title}</h1>
        <p className="pd-page-subtitle">该功能将在阶段 {stage} 开发。</p>
        <div style={{ marginTop: 'var(--sp-2xl)', paddingTop: 'var(--sp-xl)', borderTop: '1px solid var(--border-strong)', maxWidth: 640 }}>
          <p style={{ lineHeight: 1.75 }}>{description}</p>
          <div style={{ marginTop: 'var(--sp-lg)' }}>
            <Tag>开发中</Tag>
          </div>
        </div>
      </div>
    </div>
  )
}
