import type { PaperTag } from './types'

export const TAG_MAX = 30

/** 规范化用户输入的标签：去首尾空白、合并连续空白；空或超长返回 null。 */
export function normalizeTag(raw: string): string | null {
  const name = raw.trim().replace(/\s+/g, ' ')
  return name && name.length <= TAG_MAX ? name : null
}

export function hasTag(tags: PaperTag[], name: string): boolean {
  const n = name.toLowerCase()
  return tags.some((t) => t.name.toLowerCase() === n)
}

/** 列表行里的标签：最多显示 shown 个，其余折叠成“+N”。 */
export function foldTags(tags: PaperTag[], shown = 3): { visible: PaperTag[]; rest: PaperTag[] } {
  return { visible: tags.slice(0, shown), rest: tags.slice(shown) }
}
