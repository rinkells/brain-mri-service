import type { User } from '../../api/types'
import { useAuth } from '../../hooks/useAuth'
import { VIEW_HASH } from '../../hooks/useView'
import type { CredentialsMode } from '../../lib/credentials'
import { AuthForm } from './AuthForm'

interface AuthPageProps {
  mode: CredentialsMode
  /** Where to go once signed in: the page the visitor came from. */
  returnHash: string
}

export function AuthPage({ mode, returnHash }: AuthPageProps) {
  const { auth, signedIn } = useAuth()

  function handleSuccess(user: User) {
    signedIn(user)
    window.location.hash = returnHash
  }

  if (auth.status === 'loading') {
    return <p className="text-center text-slate-500 dark:text-slate-400">Перевіряємо вхід…</p>
  }
  if (auth.status === 'signed-in') {
    return (
      <p className="text-center text-slate-600 dark:text-slate-300">
        Ви вже увійшли як {auth.user.username}.{' '}
        <a href={VIEW_HASH.history} className="font-medium text-sky-700 dark:text-sky-400">
          Перейти до історії
        </a>
      </p>
    )
  }
  // Keyed by mode: switching between the two pages starts with an empty form.
  return <AuthForm key={mode} mode={mode} onSuccess={handleSuccess} />
}
