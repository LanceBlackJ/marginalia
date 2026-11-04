import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Spinner } from './Feedback'
import { Tooltip } from './Tooltip'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
  icon?: ReactNode
  loading?: boolean
  /** 危险按钮实心样式（用于确认删除） */
  solid?: boolean
  error?: boolean
  /** 禁用时的原因，以 tooltip 呈现 */
  disabledReason?: string
}

export function Button({
  variant = 'secondary',
  size = 'md',
  icon,
  loading,
  solid,
  error,
  disabledReason,
  className,
  children,
  disabled,
  type = 'button',
  ...rest
}: ButtonProps) {
  const cls = ['pd-btn', variant !== 'secondary' && `pd-btn--${variant}`, size === 'sm' && 'pd-btn--sm', solid && 'pd-btn--solid', className]
    .filter(Boolean)
    .join(' ')
  const isDisabled = disabled || loading
  const btn = (
    <button
      {...rest}
      type={type}
      className={cls}
      disabled={isDisabled}
      data-loading={loading ? 'true' : undefined}
      data-error={error ? 'true' : undefined}
      aria-busy={loading || undefined}
    >
      {loading ? <Spinner size={14} /> : icon}
      {children}
    </button>
  )
  return disabled && disabledReason ? <Tooltip content={disabledReason}>{btn}</Tooltip> : btn
}

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  icon: ReactNode
  /** 无障碍名称，同时作为悬停提示 */
  label: string
  size?: 'sm' | 'md'
  selected?: boolean
  error?: boolean
  disabledReason?: string
  tooltipSide?: 'top' | 'bottom'
}

export function IconButton({
  icon,
  label,
  size = 'md',
  selected,
  error,
  disabledReason,
  tooltipSide,
  className,
  disabled,
  type = 'button',
  ...rest
}: IconButtonProps) {
  const cls = ['pd-iconbtn', size === 'sm' && 'pd-iconbtn--sm', className].filter(Boolean).join(' ')
  const tip = disabled && disabledReason ? `${label}：${disabledReason}` : label
  return (
    <Tooltip content={tip} side={tooltipSide}>
      <button
        {...rest}
        type={type}
        className={cls}
        disabled={disabled}
        aria-label={label}
        aria-pressed={selected === undefined ? undefined : selected}
        data-error={error ? 'true' : undefined}
      >
        {icon}
      </button>
    </Tooltip>
  )
}
