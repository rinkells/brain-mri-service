import type { StatsResponse } from '../../api/types'
import { CLASS_LABELS, CLASS_ORDER } from '../../lib/classes'
import { formatMilliseconds, formatPercent } from '../../lib/format'

interface StatsPanelProps {
  stats?: StatsResponse
  error?: unknown
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-slate-100 p-4 dark:bg-slate-800">
      <dt className="text-sm text-slate-600 dark:text-slate-300">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold tabular-nums">{value}</dd>
    </div>
  )
}

/** Summary from GET /stats: totals, share with warnings, latency and classes. */
export function StatsPanel({ stats, error }: StatsPanelProps) {
  return (
    <section
      aria-labelledby="stats-heading"
      className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900"
    >
      <h2 id="stats-heading" className="font-medium">
        Статистика
      </h2>

      {!stats ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">
          {error ? 'Статистика недоступна.' : 'Завантаження статистики…'}
        </p>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <dl className="grid gap-3 self-start sm:grid-cols-3">
            <StatCard label="Усього прогнозів" value={String(stats.total)} />
            <StatCard
              label="З попередженнями"
              value={
                stats.with_warnings_share == null ? '—' : formatPercent(stats.with_warnings_share)
              }
            />
            <StatCard
              label="Середній час обробки"
              value={
                stats.average_latency_ms == null
                  ? '—'
                  : formatMilliseconds(stats.average_latency_ms)
              }
            />
          </dl>

          <div>
            <h3 className="mb-2 text-sm text-slate-600 dark:text-slate-300">Розподіл за класами</h3>
            <ul className="space-y-2" aria-label="Розподіл за класами">
              {CLASS_ORDER.map((name) => {
                const count = stats.by_class[name] ?? 0
                const share = stats.total ? count / stats.total : 0
                return (
                  <li key={name} className="text-sm">
                    <div className="flex justify-between">
                      <span>{CLASS_LABELS[name]}</span>
                      <span className="tabular-nums text-slate-600 dark:text-slate-300">
                        {count}
                      </span>
                    </div>
                    <div
                      aria-hidden="true"
                      className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800"
                    >
                      <div
                        className="h-full rounded-full bg-sky-600 dark:bg-sky-500"
                        style={{ width: `${share * 100}%` }}
                      />
                    </div>
                  </li>
                )
              })}
            </ul>
          </div>
        </div>
      )}
    </section>
  )
}
