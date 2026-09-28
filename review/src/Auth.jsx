import { createContext, useContext, useEffect, useState } from "react"
import { Navigate, useLocation } from "react-router-dom"
import { configError, supabase } from "./supabase"

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null)
  const [profile, setProfile] = useState(null)
  const [profileReady, setProfileReady] = useState(false)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    if (!supabase) {
      setReady(true)
      return undefined
    }
    let ignore = false
    supabase.auth.getSession().then(({ data }) => {
      if (ignore) return
      setSession(data.session)
      setReady(true)
    })
    const { data } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next)
    })
    return () => {
      ignore = true
      data.subscription.unsubscribe()
    }
  }, [])

  useEffect(() => {
    if (!supabase || !session) {
      setProfile(null)
      setProfileReady(false)
      return undefined
    }
    let ignore = false
    setProfileReady(false)
    supabase
      .from("profiles")
      .select("role")
      .eq("id", session.user.id)
      .maybeSingle()
      .then(({ data }) => {
        if (ignore) return
        setProfile(data)
        setProfileReady(true)
      })
    return () => {
      ignore = true
    }
  }, [session])

  const value = {
    session,
    profile,
    ready,
    email: session?.user?.email || "",
    role: profile?.role || "",
    profileReady,
    signOut: () => supabase.auth.signOut(),
  }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  return useContext(AuthContext)
}

export function RequireAuth({ children }) {
  const auth = useAuth()
  const location = useLocation()
  if (configError) return <p className="flash warn">{configError}</p>
  if (!auth.ready) return <p className="meta">Loading…</p>
  if (!auth.session) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return children
}
