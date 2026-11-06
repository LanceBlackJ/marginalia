/** 公式与图表理解 API 客户端（F5）。 */
import { apiFetch, apiPostSSE, apiRequest } from './api'
import type {
  EquationItem,
  EquationLatex,
  ExplainEquationRequest,
  ExplainFigureRequest,
  FigureAnalysis,
  FigureAnswer,
  FigureGroup,
  FigureKind,
  LocateResult,
  VisionStatus
} from './types.vision'

const get = <T>(path: string, signal?: AbortSignal): Promise<T> => apiRequest<T>('GET', path, { signal })
const post = <T>(path: string, body?: unknown): Promise<T> => apiRequest<T>('POST', path, { body: body ?? {} })

export const visionApi = {
  equations: (paperId: string) => get<{ items: EquationItem[] }>(`/api/papers/${paperId}/equations`),
  latex: (paperId: string, equationId: number) => get<EquationLatex>(`/api/papers/${paperId}/equations/${equationId}/latex`),
  transcribe: (paperId: string, equationId: number, force = false) =>
    post<EquationLatex>(`/api/papers/${paperId}/equations/${equationId}/latex`, { force }),
  locate: (paperId: string, body: { page?: number; text?: string; bbox?: number[] }) =>
    post<LocateResult>(`/api/papers/${paperId}/equations/locate`, body),
  explainEquation: (body: ExplainEquationRequest, onEvent: (event: string, data: unknown) => void, signal?: AbortSignal) =>
    apiPostSSE('/api/ai/explain-equation', body, onEvent, signal),
  /** 图/表的 AI 解析：流式产出一张对话卡片（放进右侧面板），事件和公式解释一样 */
  explainFigure: (body: ExplainFigureRequest, onEvent: (event: string, data: unknown) => void, signal?: AbortSignal) =>
    apiPostSSE('/api/ai/explain-figure', body, onEvent, signal),

  figures: (paperId: string) => get<{ items: FigureGroup[] }>(`/api/papers/${paperId}/vision/figures`),
  status: (paperId: string) => get<VisionStatus>(`/api/papers/${paperId}/vision/status`),
  analysis: (paperId: string, figureId: number) => get<FigureAnalysis>(`/api/papers/${paperId}/figures/${figureId}/analysis`),
  analyze: (paperId: string, figureId: number, opts: { force?: boolean; kind?: FigureKind | 'auto' } = {}) =>
    post<{ job_id: string | null; status: string }>(`/api/papers/${paperId}/figures/${figureId}/analyze`, opts),
  removeAnalysis: (paperId: string, figureId: number) => apiRequest<unknown>('DELETE', `/api/papers/${paperId}/figures/${figureId}/analysis`),
  askFigure: (paperId: string, figureId: number, question: string) =>
    post<FigureAnswer>(`/api/papers/${paperId}/figures/${figureId}/ask`, { question }),

  /** 公式区域截图（PNG Blob）。 */
  equationImage: async (paperId: string, equationId: number, dpi = 200): Promise<Blob> => {
    const res = await apiFetch(`/api/papers/${paperId}/equations/${equationId}/image`, { query: { dpi } })
    return res.blob()
  },
  /** 合并后的整张图区域截图（需要带令牌，所以取成 Blob 再交给 <img>）。 */
  regionImage: async (paperId: string, page: number, bbox: number[], dpi = 110): Promise<Blob> => {
    const res = await apiFetch(`/api/papers/${paperId}/regions/image`, {
      query: { page, x0: bbox[0], y0: bbox[1], x1: bbox[2], y1: bbox[3], dpi }
    })
    return res.blob()
  }
}
