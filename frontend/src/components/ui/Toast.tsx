import { createSignal, Show, onCleanup } from 'solid-js';

export interface ToastProps {
  message: string;
  type?: 'success' | 'error' | 'warning' | 'info';
  duration?: number;
  onClose?: () => void;
}

export const Toast = (props: ToastProps) => {
  const [isVisible, setIsVisible] = createSignal(true);
  
  // Auto-hide after duration
  if (props.duration) {
    const timer = setTimeout(() => {
      setIsVisible(false);
      if (props.onClose) props.onClose();
    }, props.duration);
    
    onCleanup(() => clearTimeout(timer));
  }
  
  return (
    <Show when={isVisible()}>
      <div class={`toast toast-${props.type || 'info'}`}>
        {props.message}
      </div>
    </Show>
  );
};