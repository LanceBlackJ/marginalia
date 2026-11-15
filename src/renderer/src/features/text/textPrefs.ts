import { create } from 'zustand'

const KEY = 'pd-text-prefs'

interface Persisted {
  raw: boolean
  boldFirst: boolean
  pages: boolean
}

const DEFAULTS: Persisted = { raw: false, boldFirst: false, pages: false }

function load(): Persisted {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<Persisted>
    return { raw: v.raw === true, boldFirst: v.boldFirst === true, pages: v.pages === true }
  } catch {
    return DEFAULTS
  }
}

function save(p: Persisted): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p))
  } catch {
    /* 存储不可用时只在本次会话里生效 */
  }
}

interface PrefsState extends Persisted {
  setRaw: (v: boolean) => void
  setBoldFirst: (v: boolean) => void
  setPages: (v: boolean) => void
}

/** 文字阅读器的显示偏好：显示原始提取文字、首句加粗、边距页码。只影响显示，存在本机浏览器存储里。 */
export const useTextPrefs = create<PrefsState>((set, get) => {
  const patch = (p: Partial<Persisted>): void => {
    set(p)
    const { raw, boldFirst, pages } = get()
    save({ raw, boldFirst, pages })
  }
  return {
    ...load(),
    setRaw: (raw) => patch({ raw }),
    setBoldFirst: (boldFirst) => patch({ boldFirst }),
    setPages: (pages) => patch({ pages })
  }
})
