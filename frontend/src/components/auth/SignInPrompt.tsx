import { VIEW_HASH } from '../../hooks/useView'

/** In place of the history for a visitor who is not signed in. */
export function SignInPrompt() {
  return (
    <section
      aria-labelledby="sign-in-prompt-heading"
      className="mx-auto max-w-xl space-y-4 rounded-2xl border border-slate-200 bg-white p-6 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900"
    >
      <h2 id="sign-in-prompt-heading" className="text-lg font-semibold">
        Історія доступна після входу
      </h2>
      <p className="text-sm text-slate-600 dark:text-slate-300">
        Аналізувати знімки можна й без облікового запису, але тоді результат не зберігається.
        Увійдіть або зареєструйтеся, щоб зберігати результати й бачити історію та статистику своїх
        прогнозів. Інші користувачі їх не бачать.
      </p>
      <div className="flex flex-wrap justify-center gap-3">
        <a
          href={VIEW_HASH.login}
          className="rounded-lg bg-sky-600 px-5 py-2.5 font-medium text-white shadow-sm hover:bg-sky-700 focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:outline-none dark:focus-visible:ring-offset-slate-900"
        >
          Увійти
        </a>
        <a
          href={VIEW_HASH.register}
          className="rounded-lg border border-slate-300 px-5 py-2.5 font-medium hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:outline-none dark:border-slate-700 dark:hover:bg-slate-800"
        >
          Зареєструватися
        </a>
      </div>
    </section>
  )
}
