import { useEffect, useState } from 'react'
import { discoveryApi } from '@/lib/api.discovery'
import type { VenueIntro as Info } from '@/lib/types.discovery'

const cache = new Map<string, Info>()
const pending = new Map<string, Promise<Info>>()

export const venueKey = (name: string, type?: string | null): string => `${name}|${type ?? ''}`

/** 查一个期刊/会议的介绍（同一个来源只查一次，卡片悬停和“来源是什么水平”共用）。 */
export function loadVenue(name: string, type?: string | null): Promise<Info> {
  const key = venueKey(name, type)
  const hit = cache.get(key)
  if (hit) return Promise.resolve(hit)
  let p = pending.get(key)
  if (!p) {
    p = discoveryApi
      .venueInfo(name, type)
      .then((r) => {
        cache.set(key, r)
        return r
      })
      .catch((): Info => ({ name, listed: null, stats: null, note: '没能取到这个来源的介绍，请稍后再试。' }))
      .finally(() => pending.delete(key))
    pending.set(key, p)
  }
  return p
}

export function cachedVenue(name: string, type?: string | null): Info | null {
  return cache.get(venueKey(name, type)) ?? null
}

/** 打开时才查；返回介绍和是否还在查。 */
export function useVenueInfo(name: string | null | undefined, type: string | null | undefined, enabled: boolean): { info: Info | null; loading: boolean } {
  const [info, setInfo] = useState<Info | null>(name ? cachedVenue(name, type) : null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!name) {
      setInfo(null)
      return
    }
    const hit = cachedVenue(name, type)
    setInfo(hit)
    if (!enabled || hit) return
    let alive = true
    setLoading(true)
    void loadVenue(name, type).then((r) => {
      if (alive) {
        setInfo(r)
        setLoading(false)
      }
    })
    return () => {
      alive = false
    }
  }, [name, type, enabled])
  return { info, loading }
}
