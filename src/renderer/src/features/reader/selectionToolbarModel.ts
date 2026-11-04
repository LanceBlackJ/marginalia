import type { HighlightColor } from '@/lib/types'

/**
 * 划词工具条的纯逻辑：根据选中的文字决定主条上有哪些按钮、顺序如何，以及“更多”菜单里有哪些项。
 * 主条只放最常用的动作，其余都收进“更多”，一个功能也不删。
 */

export type PrimaryKey = 'translate' | 'explain' | 'equation' | 'cite' | 'role' | 'note'
export type MoreKey = 'translate' | 'explain' | 'summarize' | 'term' | 'motivation' | 'ask' | 'highlight' | 'copy' | 'search'

export interface SelectionTraits {
  /** 中文为主：翻译没有意义，把“解释”放第一位 */
  chinese: boolean
  /** 很短（一个词或一个短语）：翻译放第一位，工具条更紧凑 */
  short: boolean
}

export function selectionTraits(text: string): SelectionTraits {
  const t = text.trim()
  const han = (t.match(/[一-鿿]/g) ?? []).length
  const latin = (t.match(/[A-Za-z]/g) ?? []).length
  const letters = han + latin
  const chinese = letters > 0 && han / letters > 0.5
  const words = t.split(/\s+/).filter(Boolean).length
  const short = chinese ? t.length <= 8 : words <= 3 && t.length <= 40
  return { chinese, short }
}

/** “12”“[12]”“1,2”“[3–5]”“1-3” 这类纯引用编号 → 编号数组（范围展开，最多 30 个）；不是引用写法返回 null。 */
export function citationIndexes(text: string): number[] | null {
  const m = text.trim().match(/^\[?\s*(\d{1,3}(?:\s*[,，、]\s*\d{1,3}|\s*[–—-]\s*\d{1,3})*)\s*\]?$/)
  if (!m) return null
  const out: number[] = []
  const parts = m[1].split(/[,，、]/)
  for (const p of parts) {
    const r = p.trim().match(/^(\d{1,3})\s*[–—-]\s*(\d{1,3})$/)
    if (r) {
      const a = Number(r[1])
      const b = Number(r[2])
      if (b < a || b - a > 29) return null
      for (let i = a; i <= b; i += 1) out.push(i)
    } else {
      const n = Number(p.trim())
      if (!Number.isFinite(n)) return null
      out.push(n)
    }
  }
  return out.length > 0 && out.length <= 30 ? out : null
}

export interface PrimaryContext {
  /** 选区落在已定位的公式上 */
  equation: boolean
  /** 选区是引用编号，且正好对应本文参考文献里存在的条目 */
  cite: boolean
  /** 选的是一整段话（够长）：多一个“这一段怎么读” */
  long?: boolean
}

/** 主条上的按钮，按显示顺序。常态三个：翻译、解释、笔记。 */
export function primaryKeys(traits: SelectionTraits, ctx: PrimaryContext): PrimaryKey[] {
  if (ctx.equation) return ['equation', 'note']
  if (ctx.cite) return ['cite', 'explain', 'note']
  const base: PrimaryKey[] = traits.chinese ? ['explain', 'note'] : ['translate', 'explain', 'note']
  if (!ctx.long) return base
  base.splice(base.length - 1, 0, 'role') // 笔记之前
  return base
}

/** “更多”菜单里的项。主条没有放出来的翻译、解释会补在最前面，保证每个功能都能用到。 */
export function moreKeys(primary: PrimaryKey[], opts: { fromText: boolean }): MoreKey[] {
  const out: MoreKey[] = []
  if (!primary.includes('translate')) out.push('translate')
  if (!primary.includes('explain')) out.push('explain')
  out.push('summarize', 'term', 'motivation', 'ask')
  // PDF 高亮需要页面坐标：文字阅读器里的选区没有坐标，不提供，也不放做不到的菜单项
  if (!opts.fromText) out.push('highlight')
  out.push('copy', 'search')
  return out
}

export const HIGHLIGHT_COLORS: Array<[HighlightColor, string]> = [
  ['yellow', '黄色'],
  ['blue', '蓝色'],
  ['green', '绿色'],
  ['pink', '粉色']
]

const STORAGE_KEY = 'pd.lastHighlightColor'

interface StorageLike {
  getItem(k: string): string | null
  setItem(k: string, v: string): void
}

function defaultStorage(): StorageLike | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

/** 上次用的高亮颜色，没有或存了无效值就是黄色。 */
export function getLastHighlightColor(storage: StorageLike | null = defaultStorage()): HighlightColor {
  try {
    const v = storage?.getItem(STORAGE_KEY)
    if (v && HIGHLIGHT_COLORS.some(([c]) => c === v)) return v as HighlightColor
  } catch {
    /* 读不到就用默认值 */
  }
  return 'yellow'
}

export function setLastHighlightColor(color: HighlightColor, storage: StorageLike | null = defaultStorage()): void {
  try {
    storage?.setItem(STORAGE_KEY, color)
  } catch {
    /* 存不了不影响使用 */
  }
}
