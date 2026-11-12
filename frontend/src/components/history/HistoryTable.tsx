import { useState } from 'react'

import type { PredictionRecord } from '../../api/types'
import { CLASS_LABELS } from '../../lib/classes'
import { formatDateTime, formatPercent } from '../../lib/format'

interface HistoryTableProps {
  items: PredictionRecord[]
  loading: boolean
  emptyText: string
  onDelete: (id: string) => Promise<void>
}

const cell = 'px-3 py-2.5 text-left align-middle'
const smallButton =
  'rounded-md px-2 py-1 text-sm font-medium focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:outline-none disabled:opacity-50'

export function HistoryTable({ items, loading, emptyText, onDelete }: HistoryTableProps) {
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  async function confirmDelete(id: string) {
    setDeletingId(id)
    try {
      await onDelete(id)
    } finally {
      setDeletingId(null)
      setConfirmingId(null)
    }
  }

  return (
    <div className="overflow-x-auto">
      <table
        aria-busy={loading}
        className={`w-full min-w-[40rem] text-sm transition-opacity ${loading ? 'opacity-60' : ''}`}
      >
        <thead className="border-b border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300">
          <tr>
            <th scope="col" className={cell}>
              Час
            </th>
            <th scope="col" className={cell}>
              Клас
            </th>
            <th scope="col" className={cell}>
              Впевненість
            </th>
            <th scope="col" className={cell}>
              Розмір
            </th>
            <th scope="col" className={cell}>
              Попередження
            </th>
            <th scope="col" className={`${cell} text-right`}>
              <span className="sr-only">Дії</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {items.length === 0 ? (
            <tr>
              <td colSpan={6} className="px-3 py-8 text-center text-slate-500 dark:text-slate-400">
                {loading ? 'Завантаження…' : emptyText}
              </td>
            </tr>
          ) : (
            items.map((item) => {
              const time = formatDateTime(item.created_at)
              const confidence = item.probabilities[item.predicted_class] ?? 0
              const busy = deletingId === item.id
              return (
                <tr key={item.id}>
                  <td className={`${cell} tabular-nums`}>
                    <time dateTime={item.created_at}>{time}</time>
                  </td>
                  <td className={`${cell} font-medium`}>{CLASS_LABELS[item.predicted_class]}</td>
                  <td className={`${cell} tabular-nums`}>{formatPercent(confidence)}</td>
                  <td className={`${cell} tabular-nums`}>
                    {item.image_size.width}×{item.image_size.height}
                  </td>
                  <td className={cell}>
                    {item.warnings.length > 0 ? (
                      <span
                        title={item.warnings.map((warning) => warning.message).join('\n')}
                        className="inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900 dark:bg-amber-900/50 dark:text-amber-100"
                      >
                        Так
                      </span>
                    ) : (
                      <span className="text-slate-500 dark:text-slate-400">Ні</span>
                    )}
                  </td>
                  <td className={`${cell} text-right whitespace-nowrap`}>
                    {confirmingId === item.id ? (
                      <span className="inline-flex items-center gap-1">
                        <span className="mr-1 text-slate-600 dark:text-slate-300">Видалити?</span>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void confirmDelete(item.id)}
                          className={`${smallButton} bg-red-600 text-white hover:bg-red-700`}
                        >
                          Так
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => setConfirmingId(null)}
                          className={`${smallButton} text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800`}
                        >
                          Ні
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        aria-label={`Видалити запис від ${time}`}
                        onClick={() => setConfirmingId(item.id)}
                        className={`${smallButton} text-red-700 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40`}
                      >
                        Видалити
                      </button>
                    )}
                  </td>
                </tr>
              )
            })
          )}
        </tbody>
      </table>
    </div>
  )
}
