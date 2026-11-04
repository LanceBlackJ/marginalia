import type { CompareEvidence } from '@/lib/types.discovery'

export type EvidenceIndex = Map<string, CompareEvidence>

/** 证据编号：论文 A 的第 12 号段落 → "A:P12"；摘要 → "A:ABS"。 */
export const evidenceKey = (label: string, ord: number | null): string => `${label}:${ord === null ? 'ABS' : `P${ord}`}`

export function buildEvidenceIndex(evidence: CompareEvidence[]): EvidenceIndex {
  return new Map(evidence.map((e) => [evidenceKey(e.label, e.ord), e]))
}

const CITE_RE = /\[([A-Z]):(P\d+|ABS)\]/g

/** 把文本里的 [A:P12] 转成 Markdown 链接 [A·P12](cite:A:P12)，渲染时再替换为证据 chip。 */
export function citesToLinks(text: string): string {
  return text.replace(CITE_RE, (_m, l: string, r: string) => `[${l}·${r}](cite:${l}:${r})`)
}
