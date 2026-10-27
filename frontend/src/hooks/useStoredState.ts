import { useEffect, useState } from 'react';

export function useStoredState<T>(key: string, fallback: T, validate: (value: unknown) => value is T) {
  const [warning, setWarning] = useState('');
  const [value, update] = useState<T>(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(key) ?? 'null');
      return validate(saved) ? saved : fallback;
    } catch { return fallback; }
  });
  useEffect(() => {
    let active = true;
    let message = '';
    try { localStorage.setItem(key, JSON.stringify(value)); }
    catch { message = 'Browser storage is unavailable or full. Changes will only last for this session.'; }
    // Report storage results after the write, outside the synchronous effect.
    queueMicrotask(() => { if (active) setWarning(message); });
    return () => { active = false; };
  }, [key, value]);
  return { value, update, warning };
}
