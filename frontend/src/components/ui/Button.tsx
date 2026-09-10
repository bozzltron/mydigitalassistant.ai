import { createMemo } from 'solid-js'

export interface ButtonProps {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  size?: 'small' | 'medium' | 'large'
  disabled?: boolean
  loading?: boolean
  onClick?: () => void
  children: string | JSX.Element
}

export const Button = (props: ButtonProps) => {
  const handleClick = () => {
    if (!props.disabled && !props.loading && props.onClick) {
      props.onClick()
    }
  }

  const className = createMemo(() => `button button-${props.variant || 'primary'} ${
    props.size || 'medium'
  } ${props.disabled ? 'disabled' : ''} ${props.loading ? 'loading' : ''}`)

  return (
    <button
      class={className()}
      onClick={handleClick}
      disabled={props.disabled || props.loading}
    >
      {props.loading ? 'Loading...' : props.children}
    </button>
  )
}