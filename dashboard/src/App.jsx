import { useEffect, useState } from 'react'
import Login from './Login.jsx'
import Dashboard from './Dashboard.jsx'
import { api } from './api.js'

const STORAGE_KEY = 'invigilai.auth'
// used when the server runs with DEV_NO_AUTH=true: no token needed, the server treats us as admin
const DEV_AUTH = { token: null, username: 'dev-no-auth', role: 'admin' }

function loadAuth() {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY))
  } catch {
    return null
  }
}

export default function App() {
  // { token, username, role }; kept for this browser tab only
  const [auth, setAuth] = useState(loadAuth)
  // null until the server answers; then whether login is switched off (DEV_NO_AUTH)
  const [devNoAuth, setDevNoAuth] = useState(null)

  useEffect(() => {
    api('/auth/mode')
      .then((m) => setDevNoAuth(Boolean(m.dev_no_auth)))
      .catch(() => setDevNoAuth(false))
  }, [])

  function login(next) {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    setAuth(next)
  }

  function logout() {
    sessionStorage.removeItem(STORAGE_KEY)
    setAuth(null)
  }

  if (devNoAuth) return <Dashboard auth={DEV_AUTH} onLogout={logout} />
  if (!auth && devNoAuth === null) return null // asking the server whether login is needed
  if (!auth) return <Login onLogin={login} />
  return <Dashboard auth={auth} onLogout={logout} />
}
