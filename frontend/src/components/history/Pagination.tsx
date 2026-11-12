interface PaginationProps {
  total: number
  offset: number
  limit: number
  disabled: boolean
  onChange: (offset: number) => void
}

const button =
  'rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:hover:bg-slate-800'

export function Pagination({ total, offset, limit, disabled, onChange }: PaginationProps) {
  if (total === 0) {
    return null
  }
  const from = offset + 1
  const to = Math.min(offset + limit, total)

  return (
    <nav
      aria-label="Сторінки історії"
      className="flex flex-wrap items-center justify-between gap-3"
    >
      <p className="text-sm text-slate-600 tabular-nums dark:text-slate-300">
        Показано {from}–{to} з {total}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          className={button}
          disabled={disabled || offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          Попередня
        </button>
        <button
          type="button"
          className={button}
          disabled={disabled || to >= total}
          onClick={() => onChange(offset + limit)}
        >
          Наступна
        </button>
      </div>
    </nav>
  )
}
