import { useEffect, useState } from "react"
import { Link, useLocation, useParams } from "react-router-dom"
import { useAuth } from "./Auth"
import { decisionLabel, isHttpUrl, kindLabel, nowLabel, verdictClass } from "./format"
import { supabase } from "./supabase"

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
    const decisionResult = await supabase
      .from("decisions")
      .select("*")
      .eq("url", findingResult.data.url)
      .maybeSingle()
    if (decisionResult.error) {
      setError(decisionResult.error.message)
      return
    }
    const decision = decisionResult.data
    setLink({ ...findingResult.data, decision })
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
    }
    const { error: saveError } = await supabase.from("decisions").upsert(row, { onConflict: "url" })
    setSaving(false)
    if (saveError) {
      setError(saveError.message)
      return
    }
    setMessage(verdict === "broken" ? "Confirmed broken. Choose a replacement or ask for the link to be deleted." : "Marked as not broken.")
    await load()
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
          <a href={link.url}>{link.url}</a>
        </h1>
        <p className="tags">
          <span className="tag kind">{kindLabel(link.kind)}</span>
          <span className="tag status">{link.status}</span>
          <span className={`tag verdict ${verdictClass(decision)}`}>{decisionLabel(decision)}</span>
        </p>
        {sources.length > 0 ? (
          <>
            <h2>Found on</h2>
            <ul className="sources">
              {sources.map((source) => (
                <li key={source}>{source}</li>
              ))}
            </ul>
          </>
        ) : null}
      </article>

      <section className="card">
        <h2>Is this link broken?</h2>
        {auth.role === "admin" ? (
          <>
            <p className="lede">Confirm what a person should treat as true. Bot walls and rate limits often look broken to the scanner and are not.</p>
            <div className="row-form">
              <button type="button" disabled={saving} onClick={() => saveVerdict("broken")}>
                Confirm broken
              </button>
              <button type="button" className="secondary" disabled={saving} onClick={() => saveVerdict("not_broken")}>
                Not broken
              </button>
            </div>
          </>
        ) : null}
        {auth.role !== "admin" && decision?.verdict === "broken" ? (
          <p>
            Confirmed broken{decision.verdict_by ? ` by ${decision.verdict_by}` : ""}
            {decision.verdict_at ? ` on ${decision.verdict_at}` : ""}.
          </p>
        ) : null}
        {auth.role !== "admin" && decision?.verdict === "not_broken" ? (
          <p>
            An admin marked this link as not broken
            {decision.verdict_by ? ` (${decision.verdict_by}${decision.verdict_at ? `, ${decision.verdict_at}` : ""})` : ""}.
          </p>
        ) : null}
        {auth.role !== "admin" && !decision?.verdict ? <p>An admin still needs to confirm whether this link is broken.</p> : null}
        {auth.profileReady && auth.session && !auth.role ? (
          <p className="flash warn">This account has no profile role yet. In Supabase, set profiles.role to admin or member.</p>
        ) : null}
        {auth.role === "admin" && decision?.verdict ? (
          <p className="meta">
            Last confirmation{decision.verdict_by ? ` by ${decision.verdict_by}` : ""}
            {decision.verdict_at ? ` on ${decision.verdict_at}` : ""}.
          </p>
        ) : null}
      </section>

      {decision?.verdict === "broken" ? (
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
              <a href={decision.alternative_url}>{decision.alternative_url}</a>
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
        </section>
      ) : null}
    </>
  )
}
