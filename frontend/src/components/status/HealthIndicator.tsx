import type { HealthState } from '../../hooks/useHealth'

const VIEW: Record<HealthState['status'], { label: string; dot: string }> = {
  checking: { label: 'Перевірка з’єднання…', dot: 'bg-slate-400 animate-pulse' },
  online: { label: 'Сервіс працює', dot: 'bg-emerald-500' },
  offline: { label: 'Сервіс недоступний', dot: 'bg-red-500' },
}

export function HealthIndicator({ health }: { health: HealthState }) {
  const view = VIEW[health.status]

  return (
    <div
      role="status"
      className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-sm dark:border-slate-700 dark:bg-slate-900"
    >
      <span aria-hidden="true" className={`size-2.5 rounded-full ${view.dot}`} />
      <span>
        {view.label}
        {health.status === 'online' && (
          <span className="text-slate-500 dark:text-slate-400"> · {health.device}</span>
        )}
      </span>
    </div>
  )
}
