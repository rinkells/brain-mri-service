import { useCallback, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

// The same key is read by the inline script in index.html to avoid a flash on load.
const STORAGE_KEY = 'theme'

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'light' || saved === 'dark') {
      return saved
    }
  } catch {
    // Storage can be blocked; fall back to the system setting.
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme)

  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('dark', theme === 'dark')
    root.style.colorScheme = theme
  }, [theme])

  const toggleTheme = useCallback(() => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // Not saved; the choice still applies until the page is reloaded.
    }
  }, [theme])

  return { theme, toggleTheme }
}
