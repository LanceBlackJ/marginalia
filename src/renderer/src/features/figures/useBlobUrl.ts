import { useEffect, useState } from 'react'

/** 异步取一张需要鉴权的图片（Blob），返回可交给 <img> 的 object URL；组件卸载时释放。 */
export function useBlobUrl(load: (() => Promise<Blob>) | null, deps: unknown[]): { url: string | null; error: boolean; type: string } {
  const [state, setState] = useState<{ url: string | null; error: boolean; type: string }>({ url: null, error: false, type: '' })
  useEffect(() => {
    if (!load) {
      setState({ url: null, error: false, type: '' })
      return
    }
    let cancelled = false
    let objectUrl: string | null = null
    setState({ url: null, error: false, type: '' })
    load()
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setState({ url: objectUrl, error: false, type: blob.type })
      })
      .catch(() => {
        if (!cancelled) setState({ url: null, error: true, type: '' })
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return state
}
