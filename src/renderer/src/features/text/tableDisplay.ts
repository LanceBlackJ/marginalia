import type { TableAnalysisItem } from '@/lib/api.text'

/**
 * 只在没有可用结构化数据，或明确知道结构化数据缺格时默认显示原图。
 *
 * 文字层/版面坐标的 confidence 只能说明“坐标核对的把握”，不能说明表格
 * 本身不可用。尤其是一个单元格包含两行文字时，低 confidence 很常见，
 * 但把它强制切成原图主视图会造成横向裁剪、表头缺行等更严重的问题。
 * 结构化表格仍然保留“查看原图”入口供人工核对。
 */
export function tablePrefersImage(t: TableAnalysisItem): boolean {
  if (t.status !== 'ready' || !t.data) return true
  if (t.kind === 'figure_data' || t.source === 'vision' || t.source === null) return true

  const d = t.data
  // 没有列或数据行时，结构化结果实际上不能作为表格展示。
  if (d.columns.length === 0 || d.rows.length === 0) return true
  if ((d.unreadable ?? 0) > 0) return true
  if ((d.unverified_cells?.length ?? 0) > 0 || (d.misplaced_cells?.length ?? 0) > 0) return true
  if (d.warnings?.some((w) => /存疑|可能有误|看不清|未能|不一致|缺失|低可信/.test(w))) return true
  return false
}

export function tableDisplayReason(t: TableAnalysisItem): string {
  if (t.kind === 'figure_data') return '图像中的数据表，原图更可靠'
  if (t.source === 'vision') return '视觉识别结果，原图优先'
  if (t.source === 'layout' && t.data?.layout && t.data.layout.confidence < 0.9) return '文字层坐标核对可信度较低，建议对照原图'
  if ((t.data?.unverified_cells?.length ?? 0) > 0 || (t.data?.misplaced_cells?.length ?? 0) > 0) return '部分单元格未能核对，原图优先'
  if ((t.data?.unreadable ?? 0) > 0) return '有单元格无法确认，原图优先'
  return '结构化表格主视图，原图可用于核对'
}
