import { useEffect, useState } from 'react';
export function useTheme() {
  const [dark, setDark] = useState(() => {
    try { const saved = localStorage.getItem('t2g-theme'); if (saved) return saved === 'dark'; } catch { /* Use system theme. */ }
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  });
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    try { localStorage.setItem('t2g-theme', dark ? 'dark' : 'light'); } catch { /* Theme still works for this session. */ }
  }, [dark]);
  return { dark, toggle: () => setDark(value => !value) };
}
