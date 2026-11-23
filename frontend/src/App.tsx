import { useState } from 'react'

import { AnalysisPage } from './components/analysis/AnalysisPage'
import { AccountArea } from './components/auth/AccountArea'
import { AuthPage } from './components/auth/AuthPage'
import { AuthProvider } from './components/auth/AuthProvider'
import { NoticeBanner } from './components/auth/NoticeBanner'
import { SignInPrompt } from './components/auth/SignInPrompt'
import { HistoryPage } from './components/history/HistoryPage'
import { ViewTabs } from './components/layout/ViewTabs'
import { HealthIndicator } from './components/status/HealthIndicator'
import { ThemeToggle } from './components/theme/ThemeToggle'
import { useAuth, type AuthState } from './hooks/useAuth'
import { useHealth } from './hooks/useHealth'
import { useTheme } from './hooks/useTheme'
import { VIEW_HASH, isContentView, useView, type ContentView } from './hooks/useView'

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  )
}

function Shell() {
  const health = useHealth()
  const { theme, toggleTheme } = useTheme()
  const view = useView()
  const { auth, resetCount } = useAuth()
  // The page to return to after signing in: the last one that was not a sign-in page.
  const [lastContentView, setLastContentView] = useState<ContentView>('analysis')
  if (isContentView(view) && view !== lastContentView) {
    setLastContentView(view)
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <header className="border-b border-slate-200 bg-white/80 dark:border-slate-800 dark:bg-slate-900/80">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-4">
          <div>
            <h1 className="text-xl font-semibold">Класифікація МРТ мозку</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Гліома, менінгіома, пухлина гіпофіза або відсутність пухлини
            </p>
          </div>
          <ViewTabs current={view} />
          <div className="flex flex-wrap items-center gap-3">
            <HealthIndicator health={health} />
            <ThemeToggle theme={theme} onToggle={toggleTheme} />
            <AccountArea />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-6">
        <NoticeBanner />
        {/* Kept mounted while hidden, so the chosen scan and result survive a visit to
            History. Signing out or deleting the account starts it afresh (resetCount). */}
        <div hidden={view !== 'analysis'}>
          <AnalysisPage key={resetCount} />
        </div>
        {/* Mounted on each visit, so the history is always fresh and is gone from memory
            as soon as the user leaves it or signs out. */}
        {view === 'history' && <HistoryView status={auth.status} />}
        {(view === 'login' || view === 'register') && (
          <AuthPage mode={view} returnHash={VIEW_HASH[lastContentView]} />
        )}
      </main>

      <footer className="mx-auto max-w-6xl px-4 pb-8 text-xs text-slate-500 dark:text-slate-400">
        Навчальний проєкт, а не медичний виріб. Результати не можна використовувати для діагностики
        чи лікування. В історії зберігаються лише результати прогнозів, а не самі знімки.
      </footer>
    </div>
  )
}

function HistoryView({ status }: { status: AuthState['status'] }) {
  if (status === 'loading') {
    return <p className="text-center text-slate-500 dark:text-slate-400">Перевіряємо вхід…</p>
  }
  if (status === 'anonymous') {
    return <SignInPrompt />
  }
  // Signed in, or not known yet: the session cookie may well be valid, so the history is
  // asked for; a 401 makes the visitor a guest (AuthProvider).
  return <HistoryPage />
}
