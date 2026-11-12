import { VIEW_HASH, type View } from '../../hooks/useView'

const TABS: { view: View; label: string }[] = [
  { view: 'analysis', label: 'Аналіз' },
  { view: 'history', label: 'Історія' },
]

export function ViewTabs({ current }: { current: View }) {
  return (
    <nav aria-label="Розділи">
      <ul className="flex gap-1 rounded-full bg-slate-100 p-1 dark:bg-slate-800">
        {TABS.map(({ view, label }) => {
          const active = view === current
          return (
            <li key={view}>
              <a
                href={VIEW_HASH[view]}
                aria-current={active ? 'page' : undefined}
                className={[
                  'block rounded-full px-4 py-1.5 text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:outline-none',
                  active
                    ? 'bg-white text-slate-900 shadow-sm dark:bg-slate-950 dark:text-slate-100'
                    : 'text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white',
                ].join(' ')}
              >
                {label}
              </a>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
