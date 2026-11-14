import { useEffect, type RefObject } from 'react'

/**
 * 右侧面板各栏的滚动位置记忆：切到别的栏、或者离开阅读页再回来，应该还在刚才看到的位置。
 * 键是“论文 + 栏目 + 滚动容器的类名”；只存在内存里（应用重开不保留）。
 */
const memory = new Map<string, number>()

const containerKey = (paperId: string, tab: string, el: HTMLElement): string => `${paperId}|${tab}|${el.className.toString().split(/\s+/).slice(0, 2).join('.')}`

export function rememberedScroll(paperId: string, tab: string, el: HTMLElement): number | undefined {
  return memory.get(containerKey(paperId, tab, el))
}

/** root 里任何元素滚动都记下来；tab / paperId 变了（或内容晚到）就把记下的位置放回去。 */
export function usePanelScrollMemory(root: RefObject<HTMLElement | null>, paperId: string | null, tab: string): void {
  useEffect(() => {
    const el = root.current
    if (!el || !paperId) return
    const touched = new Set<string>() // 用户这次自己动过的容器：不再替他恢复
    const onScroll = (e: Event): void => {
      const t = e.target
      if (!(t instanceof HTMLElement) || !el.contains(t)) return
      const k = containerKey(paperId, tab, t)
      memory.set(k, t.scrollTop)
      touched.add(k)
    }
    el.addEventListener('scroll', onScroll, true)
    const restore = (): void => {
      for (const t of el.querySelectorAll<HTMLElement>('*')) {
        if (t.scrollHeight <= t.clientHeight + 4) continue
        const k = containerKey(paperId, tab, t)
        const want = memory.get(k)
        if (want !== undefined && !touched.has(k) && Math.abs(t.scrollTop - want) > 2) t.scrollTop = want
      }
    }
    // 内容是异步加载出来的（卡片、导图…）：滚动高度够了才放得回去，所以隔一会儿再试几次
    const timers = [0, 120, 400, 1000, 2200].map((ms) => window.setTimeout(restore, ms))
    return () => {
      el.removeEventListener('scroll', onScroll, true)
      timers.forEach((t) => window.clearTimeout(t))
    }
  }, [root, paperId, tab])
}
