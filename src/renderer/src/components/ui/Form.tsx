import { useId, type CSSProperties, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from 'react'
import { X } from 'lucide-react'
import { IconButton } from './Button'

interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'onChange' | 'value'> {
  value: string
  onChange: (value: string) => void
  icon?: ReactNode
  clearable?: boolean
  invalid?: boolean
  onEnter?: () => void
}

export function Input({ value, onChange, icon, clearable, invalid, onEnter, onKeyDown, className, ...rest }: InputProps) {
  const showClear = clearable && value.length > 0 && !rest.disabled
  return (
    <div className="pd-input-wrap">
      {icon && <span className="pd-input-icon">{icon}</span>}
      <input
        {...rest}
        className={['pd-input', icon && 'pd-input--with-icon', clearable && 'pd-input--clearable', className].filter(Boolean).join(' ')}
        value={value}
        aria-invalid={invalid || undefined}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          onKeyDown?.(e)
          if (e.key === 'Enter' && !e.nativeEvent.isComposing) onEnter?.()
        }}
      />
      {showClear && (
        <span className="pd-input-clear">
          <IconButton icon={<X size={14} />} label="清除" size="sm" onClick={() => onChange('')} />
        </span>
      )}
    </div>
  )
}

interface TextAreaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'onChange' | 'value'> {
  value: string
  onChange: (value: string) => void
  invalid?: boolean
}

export function TextArea({ value, onChange, invalid, className, ...rest }: TextAreaProps) {
  return (
    <textarea
      {...rest}
      className={['pd-textarea', className].filter(Boolean).join(' ')}
      value={value}
      aria-invalid={invalid || undefined}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

interface SelectProps {
  id?: string
  value: string
  onChange: (value: string) => void
  options: Array<{ value: string; label: string; disabled?: boolean }>
  size?: 'sm' | 'md'
  disabled?: boolean
  'aria-label'?: string
  style?: CSSProperties
}

export function Select({ id, value, onChange, options, size = 'md', disabled, style, ...rest }: SelectProps) {
  return (
    <select
      id={id}
      className={`pd-select${size === 'sm' ? ' pd-select--sm' : ''}`}
      value={value}
      disabled={disabled}
      aria-label={rest['aria-label']}
      style={style}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value} disabled={o.disabled}>
          {o.label}
        </option>
      ))}
    </select>
  )
}

export function Switch({
  checked,
  onChange,
  label,
  disabled
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: ReactNode
  disabled?: boolean
}) {
  return (
    <label className="pd-switch">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="pd-switch-track" />
      <span>{label}</span>
    </label>
  )
}

export function Field({
  label,
  hint,
  error,
  children
}: {
  label: ReactNode
  hint?: ReactNode
  error?: ReactNode
  children: (id: string) => ReactNode
}) {
  const id = useId()
  return (
    <div className="pd-field">
      <label className="pd-field-label" htmlFor={id}>
        {label}
      </label>
      {children(id)}
      {error ? <div className="pd-field-error">{error}</div> : hint ? <div className="pd-field-hint">{hint}</div> : null}
    </div>
  )
}
