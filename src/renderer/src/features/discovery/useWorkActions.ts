import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { create } from 'zustand'
import { toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { discoveryApi } from '@/lib/api.discovery'
import { openExternal } from '@/lib/native'
import { importDois, importUrl } from '@/features/importing/importActions'
import { useJobsStore } from '@/stores/jobsStore'
import type { Work } from '@/lib/types.discovery'
import { useBasket, useSaved } from './discoveryStore'
import { importTarget, originalUrl } from './workUtils'

/** 正在导入的论文 key（跨页面共享，导入任务结束后由 useLibraryMatchRefresh 清除）。 */
const useImporting = create<{ keys: Set<string>; add: (k: string) => void; clear: (ks: string[]) => void }>((set) => ({
  keys: new Set(),
  add: (k) => set((s) => ({ keys: new Set(s.keys).add(k) })),
  clear: (ks) =>
    set((s) => {
      const next = new Set(s.keys)
      ks.forEach((k) => next.delete(k))
      return { keys: next }
    })
}))

export function useWorkActions(work: Work) {
  const navigate = useNavigate()
  const importing = useImporting((s) => s.keys.has(work.key))
  const saved = useSaved((s) => s.keys.has(work.key))
  const inBasket = useBasket((s) => (work.in_library ? s.ids.includes(work.in_library) : false))
  const target = importTarget(work)
  const url = originalUrl(work)

  return {
    importing,
    saved,
    inBasket,
    canImport: !!target,
    importHint: target ? '' : '这条记录没有 DOI、arXiv 编号或 PDF 直链，Marginalia 不知道去哪里下载，所以不能一键加入。可以点“打开原文”自己下载 PDF，再用右上角“导入”加进来。',
    addToLibrary: async (): Promise<void> => {
      if (!target) return
      try {
        useImporting.getState().add(work.key)
        if (target.kind === 'doi') await importDois([target.value])
        else await importUrl(target.value)
      } catch (err) {
        useImporting.getState().clear([work.key])
        toast.error('无法开始导入', errorMessage(err))
      }
    },
    /** 只保存题录（不下载全文）：只有能解析出题录的 DOI / arXiv 链接可用。 */
    addMetadataOnly: async (): Promise<void> => {
      if (!target) return
      try {
        useImporting.getState().add(work.key)
        if (target.kind === 'doi') await importDois([target.value], true)
        else await importUrl(target.value, true)
      } catch (err) {
        useImporting.getState().clear([work.key])
        toast.error('无法开始导入', errorMessage(err))
      }
    },
    openLibrary: (): void => {
      if (work.in_library) navigate(`/paper/${work.in_library}`)
    },
    toggleSave: async (): Promise<void> => {
      try {
        await useSaved.getState().toggle(work)
      } catch (err) {
        toast.error('收藏失败', errorMessage(err))
      }
    },
    toggleBasket: (): void => {
      if (!work.in_library) {
        toast.info('需要先加入文献库', '比较要读取论文全文，请先“加入文献库”并等待解析完成，再加入比较篮。')
        return
      }
      useBasket.getState().toggle(work.in_library)
    },
    openOriginal: (): void => {
      if (url) void openExternal(url)
    },
    hasOriginal: !!url
  }
}

/** 导入任务结束后，重新匹配结果是否已在文献库中。 */
export function useLibraryMatchRefresh(works: Work[], onMatches: (matches: Record<string, string>) => void): void {
  const tick = useJobsStore((s) => s.finishedTick)
  const first = useRef(true)
  const latest = useRef(works)
  latest.current = works
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    const pending = latest.current.filter((w) => !w.in_library)
    if (pending.length === 0) return
    void discoveryApi
      .libraryMatch(pending.map((w) => ({ key: w.key, doi: w.doi, arxiv_id: w.arxiv_id, title: w.title })))
      .then(({ matches }) => {
        if (Object.keys(matches).length) {
          onMatches(matches)
          useImporting.getState().clear(Object.keys(matches))
        }
      })
      .catch(() => undefined)
  }, [tick, onMatches])
}
