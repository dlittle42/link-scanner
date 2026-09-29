import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { decisionLabel, errorRank, isResolved, kindLabel, verdictClass } from "./format"
import { supabase } from "./supabase"

const VIEWS = [
  ["open", "Needs Confirmation", "status-open"],
  ["broken", "Confirmed error", "status-error"],
  ["clear", "No error", "status-clear"],
  ["resolved", "Resolved", "status-resolved"],
  ["all", "All", ""],
]

const GROUPS = [
  ["broken", kindLabel("broken")],
  ["unchecked", kindLabel("unchecked")],
]

function matches(view, decision) {
  if (view === "broken") return decision?.verdict === "broken" && !isResolved(decision)
  if (view === "clear") return decision?.verdict === "not_broken"
  if (view === "resolved") return isResolved(decision)
  if (view === "all") return true
  return !decision?.verdict
}

function kindRank(kind) {
  return kind === "unchecked" ? 1 : 0
}

export default function Queue() {
  const [params] = useSearchParams()
  const requested = params.get("view") || "open"
  const view = VIEWS.some(([key]) => key === requested) ? requested : "open"
  const [scan, setScan] = useState(null)
  const [findings, setFindings] = useState(null)
  const [error, setError] = useState("")

  useEffect(() => {
    let ignore = false
    Promise.all([
      supabase.from("scans").select("generated_at, errors").eq("id", 1).maybeSingle(),
      supabase.from("findings").select("id, site, url, kind, status").order("site").order("url"),
      supabase.from("decisions").select("url, verdict, action, resolved_at"),
    ]).then(([scanResult, findingResult, decisionResult]) => {
      if (ignore) return
      const message = scanResult.error || findingResult.error || decisionResult.error
      if (message) {
        setError(message.message)
        return
      }
      const byUrl = new Map((decisionResult.data || []).map((row) => [row.url, row]))
      setScan(scanResult.data)
      setFindings(
        (findingResult.data || [])
          .map((row) => ({
            ...row,
            decision: byUrl.get(row.url) || null,
          }))
          .sort(
            (a, b) =>
              a.site.localeCompare(b.site) ||
              kindRank(a.kind) - kindRank(b.kind) ||
              errorRank(a.status) - errorRank(b.status) ||
              a.url.localeCompare(b.url),
          ),
      )
    })
    return () => {
      ignore = true
    }
  }, [])

  if (error) return <p className="flash warn">{error}</p>
  if (!findings) return <p className="meta">Loading the queue…</p>
  if (!scan) {
    return (
      <section className="card">
        <h1>No scan yet</h1>
        <p>The Monday GitHub Action publishes the first queue after it runs.</p>
      </section>
    )
  }

  const requestedSite = params.get("site") || ""
  const scoped = requestedSite ? findings.filter((row) => row.site === requestedSite) : findings
  const counts = Object.fromEntries(
    VIEWS.map(([key]) => [key, scoped.filter((row) => matches(key, row.decision)).length]),
  )
  const visible = scoped.filter((row) => matches(view, row.decision))
  const grouped = new Map()
  for (const row of visible) {
    if (!grouped.has(row.site)) grouped.set(row.site, [])
    grouped.get(row.site).push(row)
  }
  const siteErrors = (Array.isArray(scan.errors) ? scan.errors : []).filter(
    (site) => !requestedSite || site.url === requestedSite,
  )

  function viewPath(key) {
    const next = new URLSearchParams({ view: key })
    if (requestedSite) next.set("site", requestedSite)
    return `/?${next.toString()}`
  }

  function reviewPath(link) {
    const next = new URLSearchParams(params)
    next.set("site", link.site)
    return `/links/${link.id}?${next.toString()}`
  }

  return (
    <>
      <div className="page-head">
        <h1>Review queue</h1>
        <p className="meta">Scan from {scan.generated_at}</p>
      </div>
      <nav className="views">
        {VIEWS.map(([key, label, tone]) => (
          <Link key={key} className={[tone, view === key ? "active" : ""].filter(Boolean).join(" ")} to={viewPath(key)}>
            {label} <span>{counts[key]}</span>
          </Link>
        ))}
      </nav>
      {siteErrors.map((site) => (
        <p className="flash warn" key={site.url}>
          <strong>{site.url}</strong> could not be crawled: {site.error}
        </p>
      ))}
      {grouped.size === 0 ? <p className="empty">Nothing in this view.</p> : null}
      {[...grouped.entries()].map(([site, links]) => (
        <section className="site" key={site}>
          <header>
            <h2>{site}</h2>
          </header>
          {GROUPS.map(([kind, label]) => {
            const group = links.filter((link) => (kind === "unchecked" ? link.kind === "unchecked" : link.kind !== "unchecked"))
            if (group.length === 0) return null
            return (
              <div className="group" key={kind}>
                <h3>
                  {label} <span>{group.length}</span>
                </h3>
                <ul className="links">
                  {group.map((link) => (
                    <li key={link.id}>
                      <a className="url" href={link.url} target="_blank" rel="noopener noreferrer">
                        {link.url}
                      </a>
                      <Link className="link-tags" to={reviewPath(link)}>
                        <span className="tags">
                          <span className="tag status">{link.status}</span>
                          <span className={`tag verdict ${verdictClass(link.decision)}`}>
                            {decisionLabel(link.decision)}
                          </span>
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )
          })}
        </section>
      ))}
    </>
  )
}
