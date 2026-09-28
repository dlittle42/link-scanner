import { createClient } from "@supabase/supabase-js"

const url = import.meta.env.VITE_SUPABASE_URL
const key = import.meta.env.VITE_SUPABASE_ANON_KEY

export const configError =
  url && key ? null : "Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY."

export const supabase = configError ? null : createClient(url, key)
