import { useCallback, useEffect, useState } from 'react'
import { libraryApi } from '@/lib/api.library'
import type { ReviewCategory, ReviewProject } from '@/lib/types.library'

export function useReviews(activeId: string | null) {
  const [projects, setProjects] = useState<ReviewProject[]>([])
  const [categories, setCategories] = useState<ReviewCategory[]>([])
  const [loaded, setLoaded] = useState(false)
  const [key, setKey] = useState(0)
  useEffect(() => {
    let alive = true
    setLoaded(false)
    const categoryRequest = activeId ? libraryApi.reviewCategories(activeId) : Promise.resolve({ items: [] as ReviewCategory[] })
    Promise.all([libraryApi.reviews(), categoryRequest]).then(([p, c]) => {
      if (!alive) return
      setProjects(p.items)
      setCategories(c.items)
      setLoaded(true)
    }).catch(() => alive && setLoaded(true))
    return () => { alive = false }
  }, [activeId, key])
  const reload = useCallback(() => setKey((v) => v + 1), [])
  return { projects, categories, loaded, reload }
}
