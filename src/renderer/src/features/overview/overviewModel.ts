import type { AnalysisKey } from '@/lib/types'

/** 去掉 Markdown 符号和 [P12] / 【P3、P7】 / [S1P2] 之类的依据标记，只留可读文字。 */
export function plainText(s: string): string {
  return s
    .replace(/[[【]\s*(?:S\d+)?P\d+[^\]】]*[\]】]/g, '')
    .replace(/(\*\*|__|`|^#+\s*|^>\s*|^\s*[-*]\s+)/gm, '')
    .replace(/\s+/g, ' ')
    .replace(/\s+([。，、；：！？）】”])/g, '$1') // 去掉标记后留下的“字 。”
    .trim()
}

/** 取第一句（中文句号/问号/叹号，或英文句点后跟空格）；过长时截断并加省略号。 */
export function firstSentence(text: string, max = 90): string {
  const t = plainText(text)
  if (!t) return ''
  const m = /^(.+?[。！？!?]|.+?\.(?=\s|$))/.exec(t)
  const s = (m ? m[1] : t).trim()
  return s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s
}

/** “一句话结论”：优先取研究结论，其次实验结果、方法设计；都没有就返回空串。 */
export function leadSentence(content: Partial<Record<AnalysisKey, { text: string } | null | undefined>>): string {
  for (const k of ['conclusion', 'results', 'method'] as const) {
    const s = firstSentence(content[k]?.text ?? '', 120)
    if (s) return s
  }
  return ''
}
