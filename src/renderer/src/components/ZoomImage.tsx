import { useState } from 'react'
import { Lightbox } from '@/components/ui/Lightbox'
import { useBlobUrl } from '@/features/figures/useBlobUrl'

interface Props {
  /** 缩略图地址（已经加载好的 object URL 或普通地址） */
  src: string
  alt: string
  /** 作用在 <img> 上的类名 */
  className?: string
  loading?: 'lazy' | 'eager'
  title?: string
  /** 放大时再加载更清晰的版本：key 变化会重新加载；加载期间先显示缩略图 */
  large?: { key: string; load: () => Promise<Blob> }
}

/**
 * 可点击放大的图片：显示缩略图，点击后用统一的 Lightbox 在当前窗口正中放大。
 * 全站的“点击放大图片”都用它，行为一致。
 */
export function ZoomImage({ src, alt, className, loading, title = '点击放大', large }: Props) {
  const [open, setOpen] = useState(false)
  const big = useBlobUrl(open && large ? large.load : null, [large?.key, open])
  return (
    <>
      <button type="button" className="pd-zoomimg" onClick={() => setOpen(true)} title={title} aria-label={`放大${alt}`}>
        <img src={src} alt={alt} className={className} loading={loading} />
      </button>
      <Lightbox open={open} onClose={() => setOpen(false)} src={big.url ?? src} alt={alt} loadingLarge={Boolean(large) && !big.url && !big.error} />
    </>
  )
}
