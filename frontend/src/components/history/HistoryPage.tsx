import { useId, useState } from 'react'

import { deletePrediction } from '../../api/client'
import type { ClassName } from '../../api/types'
import { useHistoryPage, useStats } from '../../hooks/useHistoryData'
import { CLASS_LABELS, CLASS_ORDER } from '../../lib/classes'
import { ErrorAlert } from '../status/ErrorAlert'
import { HistoryTable } from './HistoryTable'
import { Pagination } from './Pagination'
import { StatsPanel } from './StatsPanel'

export const HISTORY_PAGE_SIZE = 10

/** Saved predictions (GET /predictions) with paging, a class filter and deletion. */
export function HistoryPage() {
  const filterId = useId()
  const [offset, setOffset] = useState(0)
  const [classFilter, setClassFilter] = useState<ClassName | ''>('')
  const [reloadToken, setReloadToken] = useState(0)
  const [deleteError, setDeleteError] = useState<unknown>(null)

  const history = useHistoryPage(
    { limit: HISTORY_PAGE_SIZE, offset, predictedClass: classFilter || null },
    reloadToken,
  )
  const stats = useStats(reloadToken)
  const page = history.data

  function changeFilter(value: ClassName | '') {
    setClassFilter(value)
    setOffset(0)
    setDeleteError(null)
  }

  async function handleDelete(id: string) {
    setDeleteError(null)
    try {
      await deletePrediction(id)
    } catch (error) {
      // A 404 means it is already gone; reloading below shows the current state anyway.
      setDeleteError(error)
    }
    // Step back when the last record of a later page was removed.
    if (page && page.items.length === 1 && offset > 0) {
      setOffset(Math.max(0, offset - HISTORY_PAGE_SIZE))
    }
    setReloadToken((token) => token + 1)
  }

  const emptyText = classFilter
    ? `Немає прогнозів класу «${CLASS_LABELS[classFilter]}».`
    : 'Історія порожня. Проаналізуйте знімок на вкладці «Аналіз».'

  return (
    <div className="space-y-6">
      <StatsPanel stats={stats.data} error={stats.error} />

      <section
        aria-labelledby="history-heading"
        className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900"
      >
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 id="history-heading" className="font-medium">
            Історія прогнозів
          </h2>
          <div className="flex items-center gap-2 text-sm">
            <label htmlFor={filterId}>Клас</label>
            <select
              id={filterId}
              value={classFilter}
              onChange={(event) => changeFilter(event.target.value as ClassName | '')}
              className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 dark:border-slate-700 dark:bg-slate-900"
            >
              <option value="">Усі класи</option>
              {CLASS_ORDER.map((name) => (
                <option key={name} value={name}>
                  {CLASS_LABELS[name]}
                </option>
              ))}
            </select>
          </div>
        </div>

        {deleteError != null && <ErrorAlert error={deleteError} />}

        {history.error != null ? (
          <ErrorAlert error={history.error} />
        ) : (
          <HistoryTable
            items={page?.items ?? []}
            loading={history.loading}
            emptyText={emptyText}
            onDelete={handleDelete}
          />
        )}

        {page && history.error == null && (
          <Pagination
            total={page.total}
            offset={offset}
            limit={HISTORY_PAGE_SIZE}
            disabled={history.loading}
            onChange={setOffset}
          />
        )}
      </section>
    </div>
  )
}
