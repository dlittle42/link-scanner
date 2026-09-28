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

export function verdictClass(decision) {
  if (!decision?.verdict) return "open"
  return decision.verdict
}

export function isOpen(decision) {
  if (!decision?.verdict) return true
  return decision.verdict === "broken" && !decision.action
}

export function isHttpUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === "http:" || url.protocol === "https:"
  } catch {
    return false
  }
}
