export interface IconProps {
  name: string;
  size?: number;
  color?: string;
  className?: string;
}

// Simple SVG icon component that can be extended with more icons
export const Icon = (props: IconProps) => {
  const getIcon = () => {
    switch (props.name) {
      case 'search':
        return (
          <svg width={props.size || 20} height={props.size || 20} viewBox="0 0 24 24" fill="none">
            <path d="M21 21L15 15M17 10C17 13.866 13.866 17 10 17C6.13401 17 3 13.866 3 10C3 6.13401 6.13401 3 10 3C13.866 3 17 6.13401 17 10Z" 
                  stroke={props.color || "currentColor"} stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        );
      case 'chat':
        return (
          <svg width={props.size || 20} height={props.size || 20} viewBox="0 0 24 24" fill="none">
            <path d="M21 15C21 15.5304 20.7893 16.0391 20.4142 16.4142C20.0391 16.7893 19.5304 17 19 17H7L3 21V5C3 4.46957 3.21071 3.96086 3.58579 3.58579C3.96086 3.21071 4.46957 3 5 3H19C19.5304 3 20.0391 3.21071 20.4142 3.58579C20.7893 3.96086 21 4.46957 21 5V15Z" 
                  stroke={props.color || "currentColor"} stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        );
      case 'brain':
        return (
          <svg width={props.size || 20} height={props.size || 20} viewBox="0 0 24 24" fill="none">
            <path d="M12 5L16 8M12 5L8 8M12 5C8.5 5 5 7.7 5 11.5C5 13.6 5.95 15.4 7.45 16.4M12 5C15.5 5 19 7.7 19 11.5C19 13.6 18.05 15.4 16.55 16.4M12 11.5L16 14M12 11.5L8 14M12 19H13C15.7614 19 18 16.7614 18 14" 
                  stroke={props.color || "currentColor"} stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        );
      default:
        return <span>?</span>;
    }
  };
  
  return (
    <span class={`icon ${props.className || ''}`}>
      {getIcon()}
    </span>
  );
};