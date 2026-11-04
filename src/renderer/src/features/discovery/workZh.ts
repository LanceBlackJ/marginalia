import { useEffect } from 'react'
import { create } from 'zustand'
import { apiRequest } from '@/lib/api'
import { usePrefs } from '@/stores/prefsStore'
import type { Work } from '@/lib/types.discovery'

/** 找到的论文自动配的中文：标题（一批一批译）和摘要（少量几篇一组译）。译不出来或没配置模型就没有，界面只显示英文。 */
export interface WorkZh {
  title?: string
  abstract?: string
}

const CJK = /[一-鿿]/
const TITLE_BATCH = 12
const ABSTRACT_BATCH = 4
const DELAY_MS = 250
const COOLDOWN_MS = 60_000

interface ZhState {
  byKey: Record<string, WorkZh>
  /** 请求失败（断网、网关暂时不可用等）之后先歇一会儿，不反复请求；歇过了会自动再试 */
  disabled: boolean
}

export const useWorkZhStore = create<ZhState>(() => ({ byKey: {}, disabled: false }))

const queued = { title: new Map<string, string>(), abstract: new Map<string, string>() }
const asked = new Set<string>()
let timer: ReturnType<typeof setTimeout> | null = null
let running = false

function put(kind: 'title' | 'abstract', got: Record<string, string>): void {
  useWorkZhStore.setState((s) => {
    const byKey = { ...s.byKey }
    for (const [k, zh] of Object.entries(got)) byKey[k] = { ...byKey[k], [kind]: zh }
    return { byKey }
  })
}

async function flush(): Promise<void> {
  if (running) return
  running = true
  try {
    // 标题永远排在摘要前面：摘要一批要十几秒，不能让新出现的卡片干等着
    while (!useWorkZhStore.getState().disabled) {
      const kind = queued.title.size > 0 ? 'title' : queued.abstract.size > 0 ? 'abstract' : null
      if (!kind) break
      const size = kind === 'title' ? TITLE_BATCH : ABSTRACT_BATCH
      const batch = [...queued[kind].entries()].slice(0, size)
      for (const [k] of batch) queued[kind].delete(k)
      try {
        const res = await apiRequest<{ items: Record<string, string> }>('POST', '/api/discovery/translate', {
          body: { kind, items: batch.map(([id, text]) => ({ id, text })) }
        })
        put(kind, res.items)
      } catch {
        // 断网、网关暂时不可用等：这一批放回待办（歇 1 分钟再试），不弹错
        for (const [k, text] of batch) {
          queued[kind].set(k, text)
        }
        useWorkZhStore.setState({ disabled: true })
        setTimeout(() => {
          useWorkZhStore.setState({ disabled: false })
          schedule()
        }, COOLDOWN_MS)
      }
    }
  } finally {
    running = false
    if (queued.title.size + queued.abstract.size > 0 && !useWorkZhStore.getState().disabled) schedule()
  }
}

function schedule(): void {
  if (timer) return
  timer = setTimeout(() => {
    timer = null
    void flush()
  }, DELAY_MS)
}

/** 登记一篇论文要配中文（已经是中文、已请求过的不重复）。 */
export function requestZh(work: Pick<Work, 'key' | 'title' | 'abstract'>, opts: { abstract: boolean }): void {
  if (usePrefs.getState().autoTranslateWorks === false) return // 设置里关掉了自动翻译
  const t = `t:${work.key}`
  if (work.title && !CJK.test(work.title) && !asked.has(t)) {
    asked.add(t)
    queued.title.set(work.key, work.title)
  }
  const a = `a:${work.key}`
  if (opts.abstract && work.abstract && work.abstract.length >= 40 && !CJK.test(work.abstract) && !asked.has(a)) {
    asked.add(a)
    queued.abstract.set(work.key, work.abstract)
  }
  schedule()
}

/** 这篇论文的中文标题/摘要（有才有）；同时登记自动翻译。 */
export function useWorkZh(work: Pick<Work, 'key' | 'title' | 'abstract'>, opts: { abstract?: boolean } = {}): WorkZh {
  const zh = useWorkZhStore((s) => s.byKey[work.key])
  const wantAbstract = opts.abstract ?? true
  useEffect(() => {
    requestZh(work, { abstract: wantAbstract })
  }, [work.key, work.title, work.abstract, wantAbstract]) // eslint-disable-line react-hooks/exhaustive-deps
  return zh ?? {}
}

/** 测试用：清空缓存与队列。 */
export function resetWorkZh(): void {
  queued.title.clear()
  queued.abstract.clear()
  asked.clear()
  useWorkZhStore.setState({ byKey: {}, disabled: false })
}
