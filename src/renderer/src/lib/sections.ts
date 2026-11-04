/**
 * 把模型流式输出的 Markdown 按约定小节拆开，用于生成过程中的实时展示。
 * 最终展示使用后端返回的结构化 InsightCard.answer。
 */

export interface RawSections {
  direct: string
  detail: string | null
  evidence: string | null
  inference: string | null
  followUps: string | null
  outside: string | null
}

type Key = 'direct' | 'detail' | 'evidence' | 'inference' | 'followUps' | 'outside'

function headingKey(title: string): Key | null {
  const t = title.replace(/\s+/g, '').toLowerCase()
  if (t.includes('论文之外') || t.includes('论文以外') || t.includes('补充知识')) return 'outside'
  if (t.includes('直接回答') || t === '回答' || t === '结论') return 'direct'
  if (t.includes('详细') || t.includes('深入') || t.includes('解释')) return 'detail'
  if (t.includes('依据') || t.includes('证据')) return 'evidence'
  if (t.includes('推断')) return 'inference'
  if (t.includes('后续') || t.includes('延伸') || t.includes('追问')) return 'followUps'
  return null
}

export function parseAnswerSections(raw: string): RawSections {
  const out: Record<Key, string | null> = { direct: '', detail: null, evidence: null, inference: null, followUps: null, outside: null }
  let current: Key = 'direct'
  const buf: Record<Key, string[]> = { direct: [], detail: [], evidence: [], inference: [], followUps: [], outside: [] }
  const seen = new Set<Key>(['direct'])
  for (const line of raw.split(/\r?\n/)) {
    const m = /^#{1,3}\s+(.+?)\s*$/.exec(line)
    if (m) {
      const key = headingKey(m[1])
      if (key) {
        current = key
        seen.add(key)
        continue
      }
    }
    buf[current].push(line)
  }
  for (const key of seen) out[key] = buf[key].join('\n').trim()
  return {
    direct: out.direct ?? '',
    detail: out.detail,
    evidence: out.evidence,
    inference: out.inference,
    followUps: out.followUps,
    outside: out.outside
  }
}
