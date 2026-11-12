import type { Theme } from '../../hooks/useTheme'

interface ThemeToggleProps {
  theme: Theme
  onToggle: () => void
}

export function ThemeToggle({ theme, onToggle }: ThemeToggleProps) {
  const label = theme === 'dark' ? 'Увімкнути світлу тему' : 'Увімкнути темну тему'

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={label}
      title={label}
      className="rounded-full border border-slate-200 bg-white p-2 text-slate-700 hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
    >
      {theme === 'dark' ? (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="size-5" fill="currentColor">
          <path d="M12 4a1 1 0 0 1-1-1V2a1 1 0 1 1 2 0v1a1 1 0 0 1-1 1zm0 16a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0v-1a1 1 0 0 1 1-1zm8-8a1 1 0 0 1 1-1h1a1 1 0 1 1 0 2h-1a1 1 0 0 1-1-1zM4 12a1 1 0 0 1-1 1H2a1 1 0 1 1 0-2h1a1 1 0 0 1 1 1zm13.66-5.66a1 1 0 0 1 0-1.41l.71-.71a1 1 0 1 1 1.41 1.41l-.71.71a1 1 0 0 1-1.41 0zM6.34 17.66a1 1 0 0 1 0 1.41l-.71.71a1 1 0 0 1-1.41-1.41l.71-.71a1 1 0 0 1 1.41 0zm11.32 0a1 1 0 0 1 1.41 0l.71.71a1 1 0 0 1-1.41 1.41l-.71-.71a1 1 0 0 1 0-1.41zM6.34 6.34a1 1 0 0 1-1.41 0l-.71-.71a1 1 0 0 1 1.41-1.41l.71.71a1 1 0 0 1 0 1.41zM12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10z" />
        </svg>
      ) : (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="size-5" fill="currentColor">
          <path d="M21 14.5A9 9 0 0 1 9.5 3a.75.75 0 0 0-.98-.9A10.5 10.5 0 1 0 21.9 15.48a.75.75 0 0 0-.9-.98z" />
        </svg>
      )}
    </button>
  )
}
