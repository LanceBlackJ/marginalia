import { Network } from 'lucide-react'
import { Button, EmptyState, ProgressBar, Tag } from '@/components/ui'
import { MIND_VIEW_HINTS, type MindStatus, type MindView } from '@/lib/types.mindmap'
import type { JobProgress } from './mindmapStore'

/** 还没有思维导图（或正在生成/生成失败）时的空状态。明确说明会调用云端模型及大致代价。 */
export function GenerateCard({
  view,
  status,
  error,
  progress,
  onGenerate,
  compact
}: {
  view: MindView
  status: MindStatus
  error?: string | null
  progress?: JobProgress
  onGenerate: (view: MindView | 'all') => void
  compact?: boolean
}) {
  if (status === 'pending' || status === 'running') {
    return (
      <EmptyState
        icon={<Network size={20} />}
        title="正在生成思维导图…"
        description={progress?.message || '正在检索相关段落并整理结构，通常需要 1~3 分钟。可以先去做别的事，完成后会提示你。'}
        action={
          <div style={{ width: 220 }}>
            <ProgressBar value={status === 'running' && progress ? progress.progress : undefined} />
          </div>
        }
      />
    )
  }
  return (
    <EmptyState
      icon={<Network size={20} />}
      title={status === 'failed' ? '思维导图没有生成成功' : '还没有思维导图'}
      description={
        <>
          {status === 'failed' ? (
            (error ?? '生成过程中出现错误，可以重试。')
          ) : (
            <>
              {MIND_VIEW_HINTS[view]}。每个节点都附有可跳转的原文依据；没有依据的内容不会出现在图里。
            </>
          )}
          <div style={{ marginTop: 8 }}>
            <Tag tone="outline">会调用云端模型</Tag>
            <div className="pd-aux pd-weak" style={{ marginTop: 6 }}>
              只发送约 2.6 万字以内的相关原文片段，不上传整篇 PDF；约一次较大的模型调用。
            </div>
          </div>
        </>
      }
      action={
        <div className="pd-row" style={{ gap: 8, flexWrap: 'wrap', justifyContent: 'center' }}>
          <Button variant="primary" size={compact ? 'sm' : 'md'} onClick={() => onGenerate(view)}>
            {status === 'failed' ? '重新生成' : '生成思维导图'}
          </Button>
        </div>
      }
    />
  )
}
