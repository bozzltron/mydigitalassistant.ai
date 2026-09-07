import { createSignal, createEffect } from 'solid-js';

export const ThemeToggle = () => {
  const [isDark, setIsDark] = createSignal(false);
  
  // Initialize theme based on system preference
  createEffect(() => {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    setIsDark(prefersDark);
    
    if (isDark()) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  });
  
  const toggleTheme = () => {
    const newTheme = !isDark();
    setIsDark(newTheme);
    
    if (newTheme) {
      document.documentElement.classList.add('dark');
      localStorage.setItem('theme', 'dark');
    } else {
      document.documentElement.classList.remove('dark');
      localStorage.setItem('theme', 'light');
    }
  };
  
  return (
    <button
      class="theme-toggle"
      onClick={toggleTheme}
      aria-label={isDark() ? 'Switch to light mode' : 'Switch to dark mode'}
    >
      {isDark() ? '☀️' : '🌙'}
    </button>
  );
};