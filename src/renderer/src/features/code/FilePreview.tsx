import { codeApi } from '@/lib/api.code'
import { useBlobUrl } from '@/features/figures/useBlobUrl'
import { EmptyState, Skeleton } from '@/components/ui'
import type { FileData } from '@/lib/types.code'
import { pathBase } from './codeUtils'

/** 图片 / PDF / 音视频的预览（不是文字的文件，原来只显示“二进制”）。 */
export function FilePreview({ repoId, file }: { repoId: string; file: FileData }) {
  const kind = file.preview
  const { url, error } = useBlobUrl(kind ? () => codeApi.raw(repoId, file.path) : null, [repoId, file.path, kind])
  if (!kind) return null
  if (error) return <div className="pd-code-empty"><EmptyState tone="danger" title="无法预览这个文件" description="读取内容失败，文件可能已被移除。" /></div>
  if (!url) return <div className="pd-code-empty" style={{ alignItems: 'stretch', padding: 24 }}><Skeleton height={240} /></div>
  const name = pathBase(file.path)
  return (
    <div className="pd-code-preview" data-kind={kind}>
      {kind === 'image' && <img src={url} alt={name} draggable={false} />}
      {kind === 'pdf' && <iframe src={url} title={name} />}
      {kind === 'video' && <video src={url} controls preload="metadata" />}
      {kind === 'audio' && <audio src={url} controls preload="metadata" />}
    </div>
  )
}
