/** 表格的纯函数：数字列判断、核对不上的单元格、复制为 TSV / Markdown。 */
import type { TableData } from '@/lib/api.text'

const NUMERIC = /^[\s(（[]*[-+−–]?\s*(?:\d[\d,]*\.?\d*|\.\d+)(?:\s*[±×x]\s*\d[\d.]*)?(?:[eE][-+]?\d+)?\s*(?:%|‰)?[\s)）\]*†‡]*$/

/** 单元格是不是“数值”：整数、小数、百分数、带 ± 或 ×10 的写法。空格、星号、括号等装饰不影响判断。 */
export function isNumericCell(cell: string): boolean {
  const t = cell.trim()
  return t !== '' && NUMERIC.test(t)
}

/** 表示“没有数据”的单元格（NA、N/A、—、-、?）：判断数值列时不计入。 */
const MISSING = /^(?:n\/?a|n\.a\.|none|—|–|-|\?)$/i

/** 每一列是不是数值列：非空、非“缺失”的单元格里至少 70% 是数值（表头不算），且至少有一个数值。数值列右对齐。 */
export function numericColumns(data: Pick<TableData, 'columns' | 'rows'>): boolean[] {
  const n = Math.max(data.columns.length, ...data.rows.map((r) => r.length), 0)
  const out: boolean[] = []
  for (let c = 0; c < n; c++) {
    const cells = data.rows.map((r) => (r[c] ?? '').trim()).filter((x) => x !== '' && !MISSING.test(x))
    const nums = cells.filter(isNumericCell).length
    out.push(nums > 0 && nums / cells.length >= 0.7)
  }
  return out
}

/** 与 PDF 文字层核对不上的单元格，键为 "行,列"。 */
export function unverifiedSet(data: Pick<TableData, 'unverified_cells'>): Set<string> {
  return new Set((data.unverified_cells ?? []).map(([r, c]) => `${r},${c}`))
}

function escapeMd(cell: string): string {
  return cell.replace(/\|/g, '\\|').replace(/\s*\n\s*/g, ' ')
}

/** 复制为 Markdown 表格（核对不上的单元格不加记号：复制出去的是识别出的原样内容）。 */
export function tableToMarkdown(data: Pick<TableData, 'columns' | 'rows'>): string {
  const n = Math.max(data.columns.length, ...data.rows.map((r) => r.length), 1)
  const pad = (r: string[]): string[] => Array.from({ length: n }, (_, i) => escapeMd(r[i] ?? ''))
  const lines = [`| ${pad(data.columns).join(' | ')} |`, `| ${Array.from({ length: n }, () => '---').join(' | ')} |`]
  for (const r of data.rows) lines.push(`| ${pad(r).join(' | ')} |`)
  return lines.join('\n')
}

/** 复制为制表符分隔（可直接粘贴进 Excel）。单元格里的制表符和换行换成空格。 */
export function tableToTsv(data: Pick<TableData, 'columns' | 'rows'>): string {
  const clean = (s: string): string => s.replace(/[\t\r\n]+/g, ' ')
  return [data.columns, ...data.rows].map((r) => r.map(clean).join('\t')).join('\n')
}

/** 位置存疑的单元格（值在文字层里有，但不在对应的行/列），键为 "行,列"。旧结果没有这个字段时为空。 */
export function misplacedSet(data: Pick<TableData, 'misplaced_cells'>): Set<string> {
  return new Set((data.misplaced_cells ?? []).map(([r, c]) => `${r},${c}`))
}

export function misplacedCount(data: Pick<TableData, 'misplaced_cells'>): number {
  return data.misplaced_cells?.length ?? 0
}

/** 表格里有多少个单元格没能与文字层核对（用于提示条）。 */
export function unverifiedCount(data: Pick<TableData, 'unverified_cells'>): number {
  return data.unverified_cells?.length ?? 0
}
