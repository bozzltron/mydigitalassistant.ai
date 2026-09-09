import { createSignal, Show } from 'solid-js';

export interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: JSX.Element | JSX.Element[];
  size?: 'small' | 'medium' | 'large';
}

export const Modal = (props: ModalProps) => {
  const [isAnimating, setIsAnimating] = createSignal(false);
  
  const handleClose = () => {
    setIsAnimating(true);
    setTimeout(() => {
      props.onClose();
      setIsAnimating(false);
    }, 150);
  };
  
  return (
    <Show when={props.isOpen}>
      <div class="modal-overlay">
        <div 
          class={`modal ${props.size || 'medium'} ${isAnimating() ? 'closing' : ''}`}
          onClick={(e) => e.stopPropagation()}
        >
          <div class="modal-header">
            {props.title && <h3>{props.title}</h3>}
            <button 
              class="modal-close"
              onClick={handleClose}
              aria-label="Close modal"
            >
              ✕
            </button>
          </div>
          <div class="modal-content">
            {props.children}
          </div>
        </div>
      </div>
    </Show>
  );
};