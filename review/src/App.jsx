import { useState } from "react"
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom"
import { AuthProvider, RequireAuth, useAuth } from "./Auth"
import LinkPage from "./LinkPage"
import Queue from "./Queue"
import { configError, supabase } from "./supabase"

function Login() {
  const auth = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")

  if (auth.session) return <Navigate to={location.state?.from || "/"} replace />

  async function onSubmit(event) {
    event.preventDefault()
    setError("")
    const { error: signInError } = await supabase.auth.signInWithPassword({
      email: username.trim(),
      password,
    })
    if (signInError) {
      setError("That email or password is not recognized.")
      return
    }
    navigate(location.state?.from || "/", { replace: true })
  }

  return (
    <form className="card narrow" onSubmit={onSubmit}>
      <h1>Log in</h1>
      <p className="lede">Admins confirm whether a link is broken. Anyone on the team can then suggest a replacement or ask for it to be deleted.</p>
      {error ? <p className="flash warn">{error}</p> : null}
      <label>
        Email
        <input type="email" autoComplete="username" required value={username} onChange={(event) => setUsername(event.target.value)} />
      </label>
      <label>
        Password
        <input type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
      </label>
      <button type="submit">Log in</button>
    </form>
  )
}

function Shell() {
  const auth = useAuth()
  return (
    <>
      <header className="top">
        <Link className="brand" to="/">
          Link review
        </Link>
        {auth.session ? (
          <div className="who">
            <span>{auth.email}</span>
            {auth.role ? <span className="role">{auth.role}</span> : null}
            <button className="text" type="button" onClick={() => auth.signOut()}>
              Log out
            </button>
          </div>
        ) : null}
      </header>
      <main>
        {configError ? <p className="flash warn">{configError}</p> : null}
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<RequireAuth><Queue /></RequireAuth>} />
          <Route path="/links/:id" element={<RequireAuth><LinkPage /></RequireAuth>} />
        </Routes>
      </main>
    </>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  )
}
