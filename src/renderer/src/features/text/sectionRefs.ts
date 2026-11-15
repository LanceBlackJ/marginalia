/** 正文里的章节引用（Section 3.3 / Sec. 3.3 / 第 3.3 节）。 */

export interface SectionRef {
  start: number
  end: number
  number: string
}

const EN = /\b(?:Sections?|Secs?\.?|§)\s*(\d+(?:\.\d+){0,5})/gi
const ZH = /第\s*(\d+(?:\.\d+){0,5})\s*节/g

export function findSectionRefs(text: string): SectionRef[] {
  const out: SectionRef[] = []
  for (const m of text.matchAll(EN)) out.push({ start: m.index ?? 0, end: (m.index ?? 0) + m[0].length, number: m[1] })
  for (const m of text.matchAll(ZH)) out.push({ start: m.index ?? 0, end: (m.index ?? 0) + m[0].length, number: m[1] })
  return out.sort((a, b) => a.start - b.start || b.end - a.end)
}

export function splitSectionRefs(text: string, has: (number: string) => boolean): Array<{ text: string } | { text: string; number: string }> {
  const hits = findSectionRefs(text).filter((r) => has(r.number))
  if (!hits.length) return [{ text }]
  const out: Array<{ text: string } | { text: string; number: string }> = []
  let cursor = 0
  for (const hit of hits) {
    if (hit.start < cursor) continue
    if (hit.start > cursor) out.push({ text: text.slice(cursor, hit.start) })
    out.push({ text: text.slice(hit.start, hit.end), number: hit.number })
    cursor = hit.end
  }
  if (cursor < text.length) out.push({ text: text.slice(cursor) })
  return out
}

