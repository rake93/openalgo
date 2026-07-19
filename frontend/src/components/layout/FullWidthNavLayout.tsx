import { Navigate, Outlet } from 'react-router-dom'
import { SocketProvider } from '@/components/socket/SocketProvider'
import { useAuthStore } from '@/stores/authStore'
import { Navbar } from './Navbar'

/**
 * Full-width layout WITH the OpenAlgo navbar. For immersive full-bleed apps
 * (the /charts workspace + editor) that should still live inside the OpenAlgo
 * body — so navigating back is one click — unlike the bare FullWidthLayout.
 * The Outlet child fills the remaining height via flex-1.
 */
export function FullWidthNavLayout() {
  const { isAuthenticated, user } = useAuthStore()

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  if (!user?.broker) {
    return <Navigate to="/broker" replace />
  }

  return (
    <SocketProvider>
      <div className="flex h-screen flex-col overflow-hidden bg-background">
        <Navbar />
        <Outlet />
      </div>
    </SocketProvider>
  )
}
