/** 论文文字提取、全文翻译与图/公式区域的 API 客户端。 */
import { apiFetch, apiRequest } from './api'
import type { BBox, ProcessingSummary } from './types'

export interface TextItem {
  ord: number
  page: number
  kind: 'paragraph' | 'front' | 'caption' | 'heading' | 'reference' | 'formula' | 'figure_text' | 'table'
  section_title: string | null
  /** 所属章节的 id，和目录项的 id 一致；同名小节靠它区分（旧后端没有时退回按标题） */
  section_id?: string | null
  text: string
  bbox: BBox
  /** 原文里加粗的文字片段，按出现顺序；旧版本解析的论文没有，重新解析后才有 */
  bold?: string[]
  /** 原文里斜体的文字片段，同 bold */
  italic?: string[]
  /** 正文里还原出的行内公式：src 是正文里的文字，tex 是 LaTeX；旧版本解析的论文没有 */
  math?: Array<{ src: string; tex: string }>
}

export interface PaperText {
  count: number
  chars: number
  items: TextItem[]
  /** 这一版段落的版本号；重新解析之后会变。旧后端没有这个字段 */
  revision?: string
}

/** 一张表的译文：和原表同样的行列，翻译不了的格子（数字等）是 null。 */
export interface TableTranslation {
  columns: Array<string | null>
  rows: Array<Array<string | null>>
}

export interface PaperTranslation {
  total: number
  done: number
  complete: boolean
  models: string[]
  items: Array<{ ord: number; translation: string }>
  /** 译文对应的那一版段落的版本号：和 PaperText.revision 对不上，说明译文的序号和手里的段落不是同一版 */
  revision?: string
  /** 表格单元格的翻译进度（和正文段落分开数） */
  cells_total?: number
  cells_done?: number
  /** 已翻译的表格，键是表题序号 */
  tables?: Record<string, TableTranslation>
  /** 已翻译的论文标题和章节标题（键是原文标题） */
  headings?: Record<string, string>
}

/** 解析阶段识别出的图/表区域（和图注段落关联）。 */
export interface FigureRegion {
  id: number
  page: number
  bbox: BBox
  kind: 'figure' | 'table'
  label: string | null
  caption_ord: number | null
}

export interface EquationLatexItem {
  equation_id: number
  latex: string | null
  confidence: 'high' | 'medium' | 'low' | null
  status: 'none' | 'ok' | 'review' | 'failed'
  warnings: string[]
  error?: string | null
}

/** 整篇论文公式识别的进度与结果（只读缓存，不调用模型）。 */
export interface EquationsLatex {
  total: number
  done: number
  failed: number
  complete: boolean
  items: EquationLatexItem[]
}

/** 表格识别结果（和图表理解的 TableData 同一语义：unverified_cells 是与 PDF 文字层核对不上的 [行, 列]）。 */
export interface TableData {
  title: string
  columns: string[]
  rows: string[][]
  notes: string
  unverified_cells: Array<[number, number]>
  /** 位置存疑：值在 PDF 文字层里出现过，但不在对应的行/列位置（模型可能把相邻单元格的数填错了格子）。旧结果没有这个字段。 */
  misplaced_cells?: Array<[number, number]>
  verified_ratio: number | null
  warnings: string[]
  /** 图内数据网格：并排的几块子表（标题、列名、行）。普通表格没有这个字段。 */
  blocks?: Array<{ title: string; columns: string[]; rows: string[][]; confidence: number }>
  /** 视觉模型明确标为看不清的单元格数量。 */
  unreadable?: number
  /** 文字层重建的可信度（0–1）；视觉模型转录的结果没有这个字段。 */
  layout?: { confidence: number; blocks: number }
  /** MinerU 给的表头原结构：每格 {t 文字, rs 跨几行, cs 跨几列}，null 是被别的格子占着的位置 */
  head?: Array<Array<{ t: string; rs: number; cs: number } | null>>
  /** 正文里合并了的单元格 [行, 列, 跨几行, 跨几列]（行号不含表头） */
  spans?: number[][]
}

export interface TableAnalysisItem {
  /** table：带 “Table N:” 图注的表；figure_data：图里的数据网格（挂在图注上，如 Fig.5 子图下方的 Avg score 表） */
  kind?: 'table' | 'figure_data'
  /** 表题所在段落序号（也是这张表的标识） */
  caption_ord: number
  label: string
  page: number
  /** 表格主体的各行文字所在段落序号：识别成功后文字界面不再单独显示它们 */
  body_ords: number[]
  bbox: [number, number, number, number]
  status: 'none' | 'pending' | 'running' | 'ready' | 'failed'
  /** text：行文字自带分隔符、直接建表；layout：文字层词坐标重建；vision：视觉模型转录 */
  source: 'text' | 'layout' | 'vision' | 'mineru' | null
  error: string | null
  model: string | null
  data: TableData | null
}

/** 表格截图/矢量图的渲染规则变更时递增，避免浏览器继续显示旧的横排图片。 */
const TABLE_RENDER_REV = 3

export interface TablesAnalysis {
  total: number
  ready: number
  failed: number
  running: number
  complete: boolean
  items: TableAnalysisItem[]
}

const get = <T>(path: string): Promise<T> => apiRequest<T>('GET', path, {})
const post = <T>(path: string): Promise<T> => apiRequest<T>('POST', path, { body: {} })

export interface RegionKind {
  kind: 'vector' | 'raster' | 'mixed'
  raster_share: number
  image: { width: number; height: number; dpi: number } | null
}

/** 一句话说明这张图放大会不会糊（原文是矢量就不会；原文是位图，再怎么放大也只有那么多像素）。 */
export function regionKindLabel(k: RegionKind): string {
  const px = k.image ? `${k.image.width}×${k.image.height} 像素` : ''
  if (k.kind === 'vector') return '矢量图 · 放大不会糊'
  if (k.kind === 'raster') return `原文是位图（${px}）· 放大会糊，不是显示问题`
  return `含位图（${px}）· 线条和文字是矢量，位图部分放大会糊`
}

export const textApi = {
  text: (paperId: string) => get<PaperText>(`/api/papers/${paperId}/text`),
  translation: (paperId: string) => get<PaperTranslation>(`/api/papers/${paperId}/translation`),
  startTranslation: (paperId: string) => post<{ job_id: string | null }>(`/api/papers/${paperId}/translation`),
  equationsLatex: (paperId: string) => get<EquationsLatex>(`/api/papers/${paperId}/equations-latex`),
  /** 后台识别还没识别的公式（截图发给视觉模型）；没有要识别的返回 job_id=null。 */
  startEquationsLatex: (paperId: string) => post<{ job_id: string | null }>(`/api/papers/${paperId}/equations-latex`),
  /** 整篇论文的表格与识别结果（只读缓存，不调用模型）。 */
  tables: (paperId: string) => get<TablesAnalysis>(`/api/papers/${paperId}/tables-analysis`),
  /** 后台识别还没识别的表格（截图发给视觉模型）；没有要识别的返回 job_id=null；没配置视觉模型时报可读错误。 */
  startTables: (paperId: string, force = false) => apiRequest<{ job_id: string | null }>('POST', `/api/papers/${paperId}/tables-analysis`, { body: { force } }),
  /** 某页任意区域的截图（本地渲染，不调用模型）：内联显示整张图（多个子图合并后的外框）。 */
  regionImage: async (paperId: string, page: number, bbox: [number, number, number, number] | number[], dpi = 130): Promise<Blob> =>
    (await apiFetch(`/api/papers/${paperId}/regions/image`, { query: { page, x0: bbox[0], y0: bbox[1], x1: bbox[2], y1: bbox[3], dpi } })).blob(),
  /** 某页任意区域的矢量图（SVG，本地导出，不调用模型）：线条和文字是矢量，嵌入的位图保持原始分辨率，放大不会糊。导不出来（太复杂等）会抛错。 */
  regionSvg: async (paperId: string, page: number, bbox: [number, number, number, number] | number[]): Promise<Blob> =>
    (await apiFetch(`/api/papers/${paperId}/regions/svg`, { query: { page, x0: bbox[0], y0: bbox[1], x1: bbox[2], y1: bbox[3] } })).blob(),
  /** 这块区域在 PDF 里是矢量还是位图（位图的像素尺寸和等效 dpi）：给界面标注放大会不会糊。 */
  regionKind: (paperId: string, page: number, bbox: [number, number, number, number] | number[]) =>
    apiRequest<RegionKind>('GET', `/api/papers/${paperId}/regions/kind`, { query: { page, x0: bbox[0], y0: bbox[1], x1: bbox[2], y1: bbox[3] } }),
  /** 图的最佳显示：优先用矢量图，导不出来才退回截图（dpi 只对截图有用）。 */
  regionVector: async (paperId: string, page: number, bbox: [number, number, number, number] | number[], dpi = 130): Promise<Blob> => {
    try {
      return await textApi.regionSvg(paperId, page, bbox)
    } catch {
      return textApi.regionImage(paperId, page, bbox, dpi)
    }
  },
  /** 表格区域的截图（本地渲染，不调用模型），用于“对照原图”。 */
  tableImage: async (paperId: string, captionOrd: number, dpi = 150): Promise<Blob> =>
    (await apiFetch(`/api/papers/${paperId}/tables/${captionOrd}/image`, { query: { dpi, rev: TABLE_RENDER_REV } })).blob(),
  /** 表格区域的矢量图（SVG）：放大不糊；导不出来才退回截图。 */
  tableVector: async (paperId: string, captionOrd: number, dpi = 150): Promise<Blob> => {
    try {
      return await (await apiFetch(`/api/papers/${paperId}/tables/${captionOrd}/image`, { query: { vector: true, rev: TABLE_RENDER_REV } })).blob()
    } catch {
      return textApi.tableImage(paperId, captionOrd, dpi)
    }
  },
  /** 公式区域的矢量图（SVG）：放大不糊；导不出来才退回截图。 */
  equationVector: async (paperId: string, equationId: number, dpi = 200): Promise<Blob> => {
    try {
      return await (await apiFetch(`/api/papers/${paperId}/equations/${equationId}/image`, { query: { vector: true } })).blob()
    } catch {
      return textApi.equationImage(paperId, equationId, dpi)
    }
  },
  /** 导入后自动处理的进度（公式、表格、整体分析、语义索引）；只有 overall=done 才能说“已完成”。 */
  processing: (paperId: string) => get<ProcessingSummary>(`/api/papers/${paperId}/processing`),
  /** 启动还没做的自动步骤（幂等）；没配置视觉模型的步骤不会启动，状态里会写明原因。 */
  startProcessing: (paperId: string) =>
    post<{ started: Record<string, string>; processing: ProcessingSummary }>(`/api/papers/${paperId}/processing`),
  figureRegions: (paperId: string) => get<{ items: FigureRegion[] }>(`/api/papers/${paperId}/figures`),
  /** 图/公式区域的截图（本地渲染，不调用模型）。 */
  figureImage: async (paperId: string, figureId: number, dpi = 150): Promise<Blob> =>
    (await apiFetch(`/api/papers/${paperId}/figures/${figureId}/image`, { query: { dpi } })).blob(),
  equationImage: async (paperId: string, equationId: number, dpi = 200): Promise<Blob> =>
    (await apiFetch(`/api/papers/${paperId}/equations/${equationId}/image`, { query: { dpi } })).blob()
}
