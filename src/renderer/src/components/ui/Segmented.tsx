import type { ReactNode } from 'react'
import { Tooltip } from './Tooltip'

interface Option<T extends string> {
  value: T
  label: ReactNode
  disabled?: boolean
  disabledReason?: string
}

/** variant="pill"：带细边框的分段控件；variant="text"：纯文字，选中项加粗并带黑色下划线。 */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  variant = 'pill',
  'aria-label': ariaLabel
}: {
  value: T
  options: Array<Option<T>>
  onChange: (v: T) => void
  variant?: 'pill' | 'text'
  'aria-label'?: string
}) {
  return (
    <div className={`pd-segmented${variant === 'text' ? ' pd-segmented--text' : ''}`} role="group" aria-label={ariaLabel}>
      {options.map((o) => {
        const btn = (
          <button key={o.value} type="button" aria-pressed={value === o.value} disabled={o.disabled} onClick={() => onChange(o.value)}>
            {o.label}
          </button>
        )
        return o.disabled && o.disabledReason ? (
          <Tooltip key={o.value} content={o.disabledReason}>
            {btn}
          </Tooltip>
        ) : (
          btn
        )
      })}
    </div>
  )
}
