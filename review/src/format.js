export function nowLabel() {
  const date = new Date()
  const pad = (value) => String(value).padStart(2, "0")
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`
}

export function decisionLabel(decision) {
  if (!decision?.verdict) return "Needs confirmation"
  if (decision.verdict === "not_broken") return "Not broken"
  if (decision.action === "replace") return "Replacement suggested"
  if (decision.action === "delete") return "Deletion requested"
  return "Confirmed broken"
}

export function kindLabel(kind) {
  if (kind === "unchecked") return "Review: Inaccessible"
  return "Review: Error"
}

export function verdictClass(decision) {
  if (!decision?.verdict) return "open"
  return decision.verdict
}

export function isOpen(decision) {
  if (!decision?.verdict) return true
  return decision.verdict === "broken" && !decision.action
}

const HTTP_STATUS = /\bHTTP\s+(\d+)\b/

// Most likely broken first: 404, then a failed connection, then 403, then 429.
export function errorRank(status) {
  const match = String(status || "").match(HTTP_STATUS)
  if (!match) return 1
  const code = Number(match[1])
  if (code === 404) return 0
  if (code === 403) return 2
  if (code === 429) return 3
  return 4
}

export function isHttpUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === "http:" || url.protocol === "https:"
  } catch {
    return false
  }
}
