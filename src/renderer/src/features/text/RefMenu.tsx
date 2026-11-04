import { useEffect, useRef, type ReactElement } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { referencesApi } from '@/lib/api.references'
import { openExternal } from '@/lib/native'
import type { RefItem, RefResolution } from '@/lib/types.references'
import { bibtex, plainCitation } from './refModel'
import { useRefStore } from './refStore'

export async function copyText(text: string, what: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    toast.success('已复制', what)
  } catch {
    toast.error('复制失败', '没有剪贴板权限')
  }
}

/** 加入文库：只保存元数据，不下载 PDF。 */
export async function addToLibrary(doi: string): Promise<void> {
  try {
    await referencesApi.importMetadata(doi)
    toast.success('已开始导入元数据', '只保存题录，不下载 PDF；进度在任务页。')
  } catch (err) {
    toast.error('无法加入文库', errorMessage(err))
  }
}

export function linkFor(ref: RefItem, res?: RefResolution | null): string {
  return (res?.found && (res.url || res.open_access_url)) || ref.url || ''
}

interface Item {
  key: string
  label: string
  disabled?: boolean
  title?: string
  run: () => void
}

/** 右键（或键盘菜单键）打开的菜单：详情、摘要、AI 概括、打开链接、加入文库/打开、复制引文。 */
export function RefMenu() {
  const menu = useRefStore((s) => s.menu)
  const byIndex = useRefStore((s) => s.byIndex)
  const resolutions = useRefStore((s) => s.resolutions)
  const openDrawer = useRefStore((s) => s.openDrawer)
  const summarize = useRefStore((s) => s.summarize)
  const ref = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

  useEffect(() => {
    if (!menu) return
    ref.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
    const close = (e: PointerEvent): void => {
      const t = e.target as Element | null
      if (t?.closest?.('[data-selecttranslate-ui]')) return // 点划词翻译的浮条/浮层不算点在外面
      if (!ref.current?.contains(t as Node)) useRefStore.getState().closeAll()
    }
    const key = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        if (!document.querySelector('[data-selecttranslate-ui]')) useRefStore.getState().closeAll()
        return
      }
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
      e.preventDefault()
      const btns = [...(ref.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])]
      if (btns.length === 0) return
      const cur = btns.indexOf(document.activeElement as HTMLButtonElement)
      btns[(cur + (e.key === 'ArrowDown' ? 1 : -1) + btns.length) % btns.length]?.focus()
    }
    document.addEventListener('pointerdown', close, true)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('pointerdown', close, true)
      document.removeEventListener('keydown', key)
    }
  }, [menu])

  if (!menu) return null
  const index = menu.refs[menu.pos]
  const item = byIndex.get(index)
  if (!item) return null
  const res = resolutions[index]?.data
  const doi = (res?.found && res.doi) || item.doi
  const link = linkFor(item, res)
  const lib = res?.in_library
  const items: Item[] = [
    { key: 'detail', label: '查看参考文献详情', run: () => openDrawer(index) },
    { key: 'abstract', label: '读摘要', title: '联网查询 OpenAlex / Crossref，只发送这条参考文献的标题、作者或 DOI', run: () => openDrawer(index) },
    {
      key: 'ai',
      label: '让 AI 概括它做了什么',
      run: () => {
        openDrawer(index)
        void summarize(index)
      }
    },
    { key: 'open', label: '在浏览器打开 DOI / 链接', disabled: !link, title: link ? link : '这条参考文献没有可用的链接', run: () => void openExternal(link) },
    lib
      ? { key: 'lib', label: '在文库中打开', run: () => navigate(`/paper/${lib.paper_id}`) }
      : { key: 'lib', label: '仅把元数据加入文库', disabled: !doi, title: doi ? undefined : '没有 DOI，无法加入文库', run: () => void addToLibrary(doi) },
    { key: 'cite', label: '复制引文', run: () => void copyText(plainCitation(item, res), '引文已复制') },
    { key: 'bib', label: '复制 BibTeX', run: () => void copyText(bibtex(item, res), 'BibTeX 已复制') }
  ]
  const x = Math.min(menu.x, window.innerWidth - 250)
  const y = Math.min(menu.y, window.innerHeight - items.length * 34 - 24)
  const render = (it: Item): ReactElement => (
    <button
      key={it.key}
      type="button"
      role="menuitem"
      className="pd-refmenu-item"
      disabled={it.disabled}
      title={it.title}
      onClick={() => {
        useRefStore.getState().closeAll()
        it.run()
      }}
    >
      {it.label}
    </button>
  )
  return (
    <div ref={ref} className="pd-refmenu" role="menu" aria-label={`参考文献 ${index}`} style={{ left: Math.max(8, x), top: Math.max(8, y) }}>
      <div className="pd-refmenu-head">参考文献 [{index}]</div>
      {items.map(render)}
    </div>
  )
}
