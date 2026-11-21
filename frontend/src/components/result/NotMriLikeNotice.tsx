import { WarningIcon } from './WarningIcon'

/** Shown above a result that came with the not_mri_like warning, with its message. */
export function NotMriLikeNotice({ message }: { message: string }) {
  return (
    <section
      aria-label="Зображення не схоже на МРТ"
      className="flex gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-100"
    >
      <WarningIcon />
      <p>{message}</p>
    </section>
  )
}
