/** 把一段文字译成中文（流式）：划词翻译组件和“翻译摘要”共用。调用现有 POST /api/ai/translate（SSE），后端按文本哈希缓存，同一段不会重复发送。 */
import { ApiError, api, errorMessage } from './api'

export interface TranslateResult {
  translation: string
  model: string
  cached: boolean
}

export interface TranslateError {
  code: string
  message: string
}

export interface TranslateHandlers {
  onMeta?: (m: { model: string; cached: boolean }) => void
  onDelta: (text: string) => void
  onDone: (r: TranslateResult) => void
  onError: (e: TranslateError) => void
}

/** 没有 API Key / Key 无效时的明确提示（其余错误直接用后端给的可读原因）。 */
export function friendlyTranslateError(e: TranslateError): string {
  if (e.code === 'no_api_key') return '尚未保存 API Key，无法翻译。请先在设置 → AI 模型里填写。'
  if (e.code === 'auth_failed') return 'API Key 无效或没有权限，无法翻译。请到设置 → AI 模型里检查。'
  return e.message || '翻译失败，请重试。'
}

export async function streamTranslate(paperId: string | null, text: string, h: TranslateHandlers, signal: AbortSignal, force = false, contextTitle?: string): Promise<void> {
  let finished = false
  try {
    await api.ai.translate(
      { ...(paperId ? { paper_id: paperId } : { context_title: contextTitle }), text, force },
      (event, data) => {
        const d = (data ?? {}) as Record<string, unknown>
        if (event === 'meta') h.onMeta?.({ model: String(d.model ?? ''), cached: Boolean(d.cached) })
        else if (event === 'delta') h.onDelta(String(d.text ?? ''))
        else if (event === 'done') {
          finished = true
          h.onDone({ translation: String(d.translation ?? ''), model: String(d.model ?? ''), cached: Boolean(d.cached) })
        } else if (event === 'error') {
          finished = true
          h.onError({ code: String(d.code ?? 'internal'), message: String(d.message ?? '翻译失败') })
        }
      },
      signal
    )
    if (!finished && !signal.aborted) h.onError({ code: 'network', message: '连接意外中断，翻译不完整，请重试。' })
  } catch (err) {
    if (signal.aborted) return
    h.onError({ code: err instanceof ApiError ? err.code : 'internal', message: errorMessage(err) })
  }
}
