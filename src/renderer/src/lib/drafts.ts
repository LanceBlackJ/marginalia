/**
 * 没写完的东西（对话输入框、新笔记、正在改的笔记）存在本机：切到别的页面、关掉论文、甚至重启应用都还在。
 * 只存文字草稿；发送/保存/取消后立刻清掉。读写失败（隐私模式、存储被禁）时静默退化成“不保存”。
 */
export type DraftKind = 'composer' | 'note' | 'profile' | 'edit'

const KEY = 'pd.drafts'
const MAX_PER_KIND = 60

type Bag = Partial<Record<DraftKind, Record<string, unknown>>>

function readBag(): Bag {
  try {
    const raw = localStorage.getItem(KEY)
    const v = raw ? (JSON.parse(raw) as unknown) : null
    return v && typeof v === 'object' ? (v as Bag) : {}
  } catch {
    return {}
  }
}

export function getDraft<T>(kind: DraftKind, id: string | null | undefined): T | undefined {
  if (!id) return undefined
  return readBag()[kind]?.[id] as T | undefined
}

/** value 为 null、空串时删除这条草稿。 */
export function setDraft(kind: DraftKind, id: string | null | undefined, value: unknown | null): void {
  if (!id) return
  try {
    const bag = readBag()
    const m = { ...(bag[kind] ?? {}) }
    if (value === null || value === undefined || value === '') delete m[id]
    else {
      delete m[id] // 重新写入放到最后：超过上限时先丢最久没动的
      m[id] = value
    }
    const keys = Object.keys(m)
    for (const k of keys.slice(0, Math.max(0, keys.length - MAX_PER_KIND))) delete m[k]
    bag[kind] = m
    localStorage.setItem(KEY, JSON.stringify(bag))
  } catch {
    /* 存不下就算了：草稿只是锦上添花 */
  }
}
