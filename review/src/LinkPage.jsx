import { useEffect, useState } from "react"
import { Link, useLocation, useParams } from "react-router-dom"
import { useAuth } from "./Auth"
import { decisionTab, isHttpUrl, kindLabel, nowLabel } from "./format"
import { supabase } from "./supabase"

function sourceUrl(source) {
  const match = String(source).match(/^(https?:\/\/\S+?)(?: \(.*\))?$/)
  return match ? match[1] : null
}

function OpenOut({ href }) {
  return (
    <a className="open-out" href={href} target="_blank" rel="noopener noreferrer" aria-label="Open in a new window">
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <path fill="currentColor" d="M6.5 2H3a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V9.5H12.5V13h-9V3.5H6.5V2z" />
        <path fill="currentColor" d="M9 2h5v5h-1.5V4.56L8.03 9.03 6.97 7.97l4.47-4.47H9V2z" />
      </svg>
    </a>
  )
}

export default function LinkPage() {
  const { id } = useParams()
  const location = useLocation()
  const auth = useAuth()
  const [link, setLink] = useState(null)
  const [missing, setMissing] = useState(false)
  const [error, setError] = useState("")
  const [message, setMessage] = useState("")
  const [action, setAction] = useState("replace")
  const [alternative, setAlternative] = useState("")
  const [note, setNote] = useState("")
  const [saving, setSaving] = useState(false)
  const [selection, setSelection] = useState("open")

  async function load() {
    const findingResult = await supabase
      .from("findings")
      .select("id, site, url, kind, status, sources")
      .eq("id", id)
      .maybeSingle()
    if (findingResult.error) {
      setError(findingResult.error.message)
      return
    }
    if (!findingResult.data) {
      setMissing(true)
      return
    }
    const [decisionResult, resolutionResult] = await Promise.all([
      supabase.from("decisions").select("*").eq("url", findingResult.data.url).maybeSingle(),
      supabase
        .from("resolutions")
        .select("resolved_by, resolved_at")
        .eq("site", findingResult.data.site)
        .eq("url", findingResult.data.url)
        .maybeSingle(),
    ])
    if (decisionResult.error || resolutionResult.error) {
      setError((decisionResult.error || resolutionResult.error).message)
      return
    }
    const decision = decisionResult.data
      ? {
          ...decisionResult.data,
          resolved_at: resolutionResult.data?.resolved_at || null,
          resolved_by: resolutionResult.data?.resolved_by || null,
        }
      : null
    setLink({ ...findingResult.data, decision })
    setSelection(decisionTab(decision))
    setAction(decision?.action === "delete" ? "delete" : "replace")
    setAlternative(decision?.alternative_url || "")
    setNote(decision?.action_note || "")
  }

  useEffect(() => {
    load()
  }, [id])

  async function saveVerdict(verdict) {
    setSaving(true)
    setError("")
    setMessage("")
    const row = {
      url: link.url,
      verdict,
      verdict_by: auth.email,
      verdict_at: nowLabel(),
      reviewed_status: link.status,
    }
    const { error: saveError } = await supabase.from("decisions").upsert(row, { onConflict: "url" })
    setSaving(false)
    if (saveError) {
      setError(saveError.message)
      return false
    }
    setMessage(verdict === "broken" ? "Marked as Confirmed error." : "Marked as No error.")
    await load()
    return true
  }

  async function markResolved(resolved) {
    setSaving(true)
    setError("")
    setMessage("")
    const query = resolved
      ? supabase.from("resolutions").upsert(
          {
            site: link.site,
            url: link.url,
            resolved_at: nowLabel(),
            resolved_by: auth.email,
          },
          { onConflict: "site,url" },
        )
      : supabase.from("resolutions").delete().eq("site", link.site).eq("url", link.url)
    const { error: saveError } = await query
    setSaving(false)
    if (saveError) {
      setError(saveError.message)
      return false
    }
    setMessage(resolved ? "Marked as Resolved." : "Marked as Confirmed error.")
    await load()
    return true
  }

  async function choose(next) {
    if (next === "open") return
    if (next === "resolved") {
      if (link?.decision?.resolved_at) return
      if (link?.decision?.verdict !== "broken") {
        if (auth.role !== "admin") {
          setError("An admin has to confirm the error before it can be resolved.")
          return
        }
        const saved = await saveVerdict("broken")
        if (!saved) return
      }
      await markResolved(true)
      return
    }
    if (auth.role !== "admin") return
    if (next === "clear") {
      await saveVerdict("not_broken")
      return
    }
    if (link?.decision?.resolved_at) {
      await markResolved(false)
      return
    }
    if (link?.decision?.verdict !== "broken") await saveVerdict("broken")
  }

  async function saveAction(event) {
    event.preventDefault()
    setError("")
    setMessage("")
    if (action === "replace" && !isHttpUrl(alternative.trim())) {
      setError("Enter a full http or https URL for the replacement.")
      return
    }
    setSaving(true)
    const { error: saveError } = await supabase
      .from("decisions")
      .update({
        action,
        alternative_url: action === "replace" ? alternative.trim() : null,
        action_note: note.trim() || null,
        action_by: auth.email,
        action_at: nowLabel(),
      })
      .eq("url", link.url)
    setSaving(false)
    if (saveError) {
      setError(saveError.message)
      return
    }
    setMessage(action === "replace" ? "Saved the replacement link." : "Requested that the link be deleted.")
    await load()
  }

  if (error && !link) return <p className="flash warn">{error}</p>
  if (missing) return <p className="flash warn">That link is not in the latest scan.</p>
  if (!link) return <p className="meta">Loading…</p>

  const decision = link.decision
  const sources = Array.isArray(link.sources) ? link.sources : []

  return (
    <>
      <p className="back">
        <Link to={`/${location.search}`}>Back to the queue</Link>
      </p>
      {message ? <p className="flash">{message}</p> : null}
      {error ? <p className="flash warn">{error}</p> : null}
      <article className="card">
        <p className="meta">{link.site}</p>
        <h1>
          <a href={link.url} target="_blank" rel="noopener noreferrer">{link.url}</a>
        </h1>
        <p className="tags">
          <span className="tag kind">{kindLabel(link.kind)}</span>
          <span className="tag status">{link.status}</span>
        </p>
        {sources.length > 0 ? (
          <>
            <h2>Found on</h2>
            <ul className="sources">
              {sources.map((source) => {
                const href = sourceUrl(source)
                return (
                  <li key={source}>
                    <span>{source}</span>
                    {href ? <OpenOut href={href} /> : null}
                  </li>
                )
              })}
            </ul>
          </>
        ) : null}
      </article>

      <section className="card">
        <h2>Status</h2>
        <div className="views">
          <button type="button" className={selection === "open" ? "status-open active" : "status-open"} disabled>
            Needs Confirmation
          </button>
          <button
            type="button"
            className={selection === "broken" ? "status-error active" : "status-error"}
            disabled={saving || auth.role !== "admin"}
            onClick={() => choose("broken")}
          >
            Confirmed error
          </button>
          <button
            type="button"
            className={selection === "clear" ? "status-clear active" : "status-clear"}
            disabled={saving || auth.role !== "admin"}
            onClick={() => choose("clear")}
          >
            No error
          </button>
          {decision?.verdict === "broken" ? (
            <button
              type="button"
              className={selection === "resolved" ? "status-resolved active" : "status-resolved"}
              disabled={saving}
              onClick={() => choose("resolved")}
            >
              Resolved
            </button>
          ) : null}
        </div>
        {auth.profileReady && auth.session && !auth.role ? (
          <p className="flash warn">This account has no profile role yet. In Supabase, set profiles.role to admin or member.</p>
        ) : null}
        {decision?.verdict ? (
          <p className="meta">
            Last confirmation{decision.verdict_by ? ` by ${decision.verdict_by}` : ""}
            {decision.verdict_at ? ` on ${decision.verdict_at}` : ""}.
          </p>
        ) : (
          <p className="lede">An admin confirms whether this is an error. Mark it resolved only after the error has been taken care of.</p>
        )}
      </section>

      {decision?.verdict === "broken" && !decision.resolved_at ? (
        <section className="card">
          <h2>What should happen to it?</h2>
          {decision.action === "replace" ? (
            <p>
              Replacement suggested{decision.action_by ? ` by ${decision.action_by}` : ""}
              {decision.action_at ? ` on ${decision.action_at}` : ""}:
            </p>
          ) : null}
          {decision.action === "replace" && decision.alternative_url ? (
            <p>
              <a href={decision.alternative_url} target="_blank" rel="noopener noreferrer">{decision.alternative_url}</a>
            </p>
          ) : null}
          {decision.action === "delete" ? (
            <p>
              Deletion requested{decision.action_by ? ` by ${decision.action_by}` : ""}
              {decision.action_at ? ` on ${decision.action_at}` : ""}.
            </p>
          ) : null}
          {!decision.action ? <p className="lede">Suggest a page that should replace this link, or ask for the link to be removed.</p> : null}
          {decision.action_note ? <p className="note">{decision.action_note}</p> : null}
          <form className="stack" onSubmit={saveAction}>
            <label className="choice">
              <input type="radio" name="action" value="replace" checked={action === "replace"} onChange={() => setAction("replace")} />
              Suggest a replacement
            </label>
            <label>
              Replacement URL
              <input value={alternative} placeholder="https://" onChange={(event) => setAlternative(event.target.value)} />
            </label>
            <label className="choice">
              <input type="radio" name="action" value="delete" checked={action === "delete"} onChange={() => setAction("delete")} />
              Request that the link be deleted
            </label>
            <label>
              Note
              <textarea rows={3} value={note} onChange={(event) => setNote(event.target.value)} />
            </label>
            <button type="submit" disabled={saving}>
              {decision.action ? "Update action" : "Save action"}
            </button>
          </form>
          <button className="mark-resolved" type="button" disabled={saving} onClick={() => choose("resolved")}>
            Mark resolved
          </button>
        </section>
      ) : null}
      {decision?.resolved_at ? (
        <section className="card">
          <h2>Resolved</h2>
          <p>
            Marked resolved{decision.resolved_by ? ` by ${decision.resolved_by}` : ""}
            {decision.resolved_at ? ` on ${decision.resolved_at}` : ""}.
          </p>
          {decision.action === "replace" && decision.alternative_url ? (
            <p>
              Replacement: <a href={decision.alternative_url} target="_blank" rel="noopener noreferrer">{decision.alternative_url}</a>
            </p>
          ) : null}
          {decision.action === "delete" ? <p>Deletion was requested.</p> : null}
          {decision.action_note ? <p className="note">{decision.action_note}</p> : null}
        </section>
      ) : null}
    </>
  )
}
