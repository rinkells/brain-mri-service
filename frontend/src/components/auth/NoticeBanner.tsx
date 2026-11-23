import { useAuth } from '../../hooks/useAuth'
import { VIEW_HASH } from '../../hooks/useView'

const TONE_CLASS = {
  alert:
    'border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-700/70 dark:bg-amber-950/40 dark:text-amber-100',
  info: 'border-sky-300 bg-sky-50 text-sky-950 dark:border-sky-800/70 dark:bg-sky-950/40 dark:text-sky-100',
} as const

/** A message about the account above the page, e.g. that the session ended. */
export function NoticeBanner() {
  const { notice, dismissNotice } = useAuth()
  if (!notice) {
    return null
  }

  return (
    <div
      role={notice.tone === 'alert' ? 'alert' : 'status'}
      aria-label="Повідомлення"
      className={`mb-6 flex flex-wrap items-start justify-between gap-3 rounded-xl border p-4 ${TONE_CLASS[notice.tone]}`}
    >
      <div>
        <p className="font-semibold">{notice.title}</p>
        <p className="mt-1 text-sm">
          {notice.description}
          {notice.offerSignIn && (
            <>
              {' '}
              <a href={VIEW_HASH.login} className="font-medium underline underline-offset-2">
                Увійти
              </a>
            </>
          )}
        </p>
      </div>
      <button
        type="button"
        onClick={dismissNotice}
        className="rounded-lg px-2 py-1 text-sm hover:bg-black/5 focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:outline-none dark:hover:bg-white/10"
      >
        Закрити
      </button>
    </div>
  )
}
