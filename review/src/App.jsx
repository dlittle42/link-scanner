import { useEffect, useRef, useState } from "react"
import { Navigate, Route, Routes, useLocation, useNavigate, useSearchParams } from "react-router-dom"
import { AuthProvider, RequireAuth, useAuth } from "./Auth"
import LinkPage from "./LinkPage"
import Queue from "./Queue"
import { configError, supabase } from "./supabase"

const ERROR_CODES = [
  [
    "404 Not Found",
    "The server answered, and the page is not there. This is the strongest sign the link is broken.",
  ],
  [
    "ConnectError",
    "The checker could not connect to the host. The name may not resolve, the connection was refused, or the request timed out. The link is often broken, though the site can also be down temporarily. Statuses that are not an HTTP code are grouped here.",
  ],
  [
    "403 Forbidden",
    "The server refused the request. This is often a bot-protection challenge, so opening the link in a browser may still work.",
  ],
  [
    "429 Too Many Requests",
    "The server rate-limited the scan. The page is usually fine.",
  ],
]

function CodesModal({ open, onClose }) {
  const dialogRef = useRef(null)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={dialogRef}
      className="modal"
      onClose={onClose}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="modal-body">
        <h2>Error codes</h2>
        <p className="lede">On each site, links are listed in this order, with the ones most likely to be broken first.</p>
        <dl className="codes">
          {ERROR_CODES.map(([term, meaning]) => (
            <div key={term}>
              <dt>{term}</dt>
              <dd>{meaning}</dd>
            </div>
          ))}
        </dl>
       {/* <p className="meta">Any other HTTP status is listed after these. A 5xx means the server failed while answering.</p> */}
        <form method="dialog">
          <button className="secondary" type="submit">
            Close
          </button>
        </form>
      </div>
    </dialog>
  )
}

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
    <div className="login-screen">
      <form className="card" onSubmit={onSubmit}>
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
    </div>
  )
}

function AccountMenu({ auth }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const label = auth.role || "Account"

  useEffect(() => {
    if (!open) return undefined
    function onPointerDown(event) {
      if (!rootRef.current?.contains(event.target)) setOpen(false)
    }
    function onKeyDown(event) {
      if (event.key === "Escape") setOpen(false)
    }
    document.addEventListener("pointerdown", onPointerDown)
    document.addEventListener("keydown", onKeyDown)
    return () => {
      document.removeEventListener("pointerdown", onPointerDown)
      document.removeEventListener("keydown", onKeyDown)
    }
  }, [open])

  return (
    <div className="account" ref={rootRef}>
      <button
        className="role-button"
        type="button"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((value) => !value)}
      >
        {label}
      </button>
      {open ? (
        <div className="account-menu" role="menu">
          <p className="account-email">{auth.email}</p>
          <button
            className="account-logout"
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false)
              auth.signOut()
            }}
          >
            Log out
          </button>
        </div>
      ) : null}
    </div>
  )
}

function Shell() {
  const auth = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [sites, setSites] = useState([])
  const [codesOpen, setCodesOpen] = useState(false)
  const requestedSite = params.get("site") || ""
  const selectedSite = sites.includes(requestedSite) ? requestedSite : ""

  useEffect(() => {
    if (!auth.session || !supabase) {
      setSites([])
      return undefined
    }
    let ignore = false
    Promise.all([
      supabase.from("findings").select("site").order("site").limit(10000),
      supabase.from("scans").select("errors").eq("id", 1).maybeSingle(),
    ]).then(([findingResult, scanResult]) => {
      if (ignore || findingResult.error) return
      const names = new Set()
      for (const row of findingResult.data || []) {
        if (row.site) names.add(row.site)
      }
      const errors = Array.isArray(scanResult.data?.errors) ? scanResult.data.errors : []
      for (const site of errors) {
        if (site?.url) names.add(site.url)
      }
      setSites([...names].sort((a, b) => a.localeCompare(b)))
    })
    return () => {
      ignore = true
    }
  }, [auth.session])

  function onSiteChange(event) {
    const next = new URLSearchParams(params)
    const value = event.target.value
    if (value) next.set("site", value)
    else next.delete("site")
    const query = next.toString()
    navigate(query ? `/?${query}` : "/")
  }

  return (
    <>
      {auth.session ? (
        <header className="top">
          <div className="nav-center">
            {sites.length > 0 ? (
              <label className="site-picker">
                Site
                <select value={selectedSite} onChange={onSiteChange}>
                  <option value="">All sites</option>
                  {sites.map((site) => (
                    <option key={site} value={site}>
                      {site}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>
          <div className="account-tools">
            <button className="help-button" type="button" aria-label="Error codes" onClick={() => setCodesOpen(true)}>
              ?
            </button>
            <AccountMenu auth={auth} />
          </div>
        </header>
      ) : null}
      <CodesModal open={codesOpen} onClose={() => setCodesOpen(false)} />
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
