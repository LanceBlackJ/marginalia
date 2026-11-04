import { AlertCircle, AlertTriangle, CheckCircle2, Info, X } from 'lucide-react'
import type { ReactElement } from 'react'
import { create } from 'zustand'
import { Button, IconButton } from './Button'

type Tone = 'success' | 'error' | 'warning' | 'info'

/** 提示里可附带一个操作按钮（点击后自动关闭这条提示）。 */
export interface ToastAction {
  label: string
  onClick: () => void
}

interface ToastItem {
  id: number
  tone: Tone
  title: string
  description?: string
  action?: ToastAction
}

interface ToastState {
  items: ToastItem[]
  push: (tone: Tone, title: string, description?: string, duration?: number, action?: ToastAction) => void
  dismiss: (id: number) => void
}

let seq = 0

export const useToastStore = create<ToastState>((set, get) => ({
  items: [],
  push: (tone, title, description, duration, action) => {
    const id = ++seq
    set((s) => ({ items: [...s.items.slice(-3), { id, tone, title, description, action }] }))
    const ms = duration ?? (action ? 12000 : tone === 'error' ? 8000 : 4500)
    if (ms > 0) window.setTimeout(() => get().dismiss(id), ms)
  },
  dismiss: (id) => set((s) => ({ items: s.items.filter((t) => t.id !== id) }))
}))

export const toast = {
  success: (title: string, description?: string, action?: ToastAction) =>
    useToastStore.getState().push('success', title, description, undefined, action),
  error: (title: string, description?: string, action?: ToastAction) => useToastStore.getState().push('error', title, description, undefined, action),
  warning: (title: string, description?: string, action?: ToastAction) =>
    useToastStore.getState().push('warning', title, description, undefined, action),
  info: (title: string, description?: string, action?: ToastAction) => useToastStore.getState().push('info', title, description, undefined, action)
}

const ICONS: Record<Tone, ReactElement> = {
  success: <CheckCircle2 size={16} />,
  error: <AlertCircle size={16} />,
  warning: <AlertTriangle size={16} />,
  info: <Info size={16} />
}

export function ToastHost() {
  const items = useToastStore((s) => s.items)
  const dismiss = useToastStore((s) => s.dismiss)
  if (items.length === 0) return null
  return (
    <div className="pd-toasts" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className="pd-toast" data-tone={t.tone} role={t.tone === 'error' ? 'alert' : 'status'}>
          <span style={{ paddingTop: 2 }}>{ICONS[t.tone]}</span>
          <div className="pd-toast-body">
            <div className="pd-toast-title">{t.title}</div>
            {t.description && <div className="pd-toast-desc">{t.description}</div>}
            {t.action && (
              <div style={{ marginTop: 8 }}>
                <Button
                  size="sm"
                  onClick={() => {
                    dismiss(t.id)
                    t.action?.onClick()
                  }}
                >
                  {t.action.label}
                </Button>
              </div>
            )}
          </div>
          <IconButton icon={<X size={14} />} label="关闭" size="sm" onClick={() => dismiss(t.id)} />
        </div>
      ))}
    </div>
  )
}
