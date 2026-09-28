import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { decisionLabel, isOpen, verdictClass } from "./format"
import { supabase } from "./supabase"

const VIEWS = [
  ["open", "Needs a decision"],
  ["broken", "Confirmed broken"],
  ["clear", "Not broken"],
  ["all", "All"],
]

function matches(view, decision) {
  if (view === "broken") return decision?.verdict === "broken"
  if (view === "clear") return decision?.verdict === "not_broken"
  if (view === "all") return true
  return isOpen(decision)
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
      supabase.from("decisions").select("url, verdict, action"),
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
          .sort((a, b) => a.site.localeCompare(b.site) || a.url.localeCompare(b.url)),
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

  const counts = {
    open: findings.filter((row) => isOpen(row.decision)).length,
    broken: findings.filter((row) => row.decision?.verdict === "broken").length,
    clear: findings.filter((row) => row.decision?.verdict === "not_broken").length,
    all: findings.length,
  }
  const visible = findings.filter((row) => matches(view, row.decision))
  const grouped = new Map()
  for (const row of visible) {
    if (!grouped.has(row.site)) grouped.set(row.site, [])
    grouped.get(row.site).push(row)
  }
  const siteErrors = Array.isArray(scan.errors) ? scan.errors : []

  return (
    <>
      <div className="page-head">
        <h1>Review queue</h1>
        <p className="meta">Scan from {scan.generated_at}</p>
      </div>
      <nav className="views">
        {VIEWS.map(([key, label]) => (
          <Link key={key} className={view === key ? "active" : ""} to={`/?view=${key}`}>
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
          <h2>{site}</h2>
          <ul className="links">
            {links.map((link) => (
              <li key={link.id}>
                <Link to={`/links/${link.id}`}>
                  <span className="url">{link.url}</span>
                  <span className="tags">
                    <span className="tag kind">{link.kind}</span>
                    <span className="tag status">{link.status}</span>
                    <span className={`tag verdict ${verdictClass(link.decision)}`}>
                      {decisionLabel(link.decision)}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  )
}
