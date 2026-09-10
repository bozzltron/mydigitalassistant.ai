import { createSignal, Show, onCleanup, createEffect } from 'solid-js'

export interface ToastProps {
  message: string
  type?: 'success' | 'error' | 'warning' | 'info'
  duration?: number
  onClose?: () => void
}

export const Toast = (props: ToastProps) => {
  const [isVisible, setIsVisible] = createSignal(true)

  // Auto-hide after duration
  createEffect(() => {
    const { duration, onClose } = props
    if (duration) {
      const timer = setTimeout(() => {
        setIsVisible(false)
        onClose?.()
      }, duration)

      onCleanup(() => clearTimeout(timer))
    }
  })

  return (
    <Show when={isVisible()}>
      <div class={`toast toast-${props.type || 'info'}`}>
        {props.message}
      </div>
    </Show>
  )
}