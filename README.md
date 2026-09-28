# Broken link monitor

Crawls one or more websites, checks outbound links, and emails an HTML report. A GitHub Actions workflow runs the check every Monday at 07:00 UTC and can also be started by hand.

The crawler stays on each site's host. It records links that point at a different host, then requests each of those URLs once. A link is broken when the final response is HTTP 4xx or 5xx, or when the host cannot be reached (timeout, DNS failure, connection error). HTTP 429 is listed as unchecked so a rate limit is not treated as a dead link. A 4xx or 5xx page that is a bot-protection challenge (Cloudflare, DataDome, or an Akamai "Access Denied" interstitial) is also unchecked: a browser can pass the challenge, and this client cannot. `mailto:`, `tel:`, `javascript:`, and fragment-only links are skipped.

Broken links in the report do not fail the job. The job fails when a start URL cannot be fetched, or when email cannot be sent.

## Configure the sites

Edit [`config.json`](config.json). Each site has its own page cap. `concurrency` is the number of requests in flight, and `timeoutSeconds` is the per-request timeout.

Set `javascript` to `true` for a site that only adds its links after scripts run, such as a client-rendered React app or a Next.js page whose links are not in the first HTML response. Those pages are opened in headless Chromium. The crawler waits for late anchors (a splash screen or a carousel) and also reads `sitemap.xml`, so pages that are not linked with an `<a>` tag are still opened. Outbound URLs are still checked with a plain HTTP request.

```json
{
  "sites": [
    { "url": "https://example.com", "maxPages": 50 },
    { "url": "https://docs.example.com", "maxPages": 100 },
    { "url": "https://app.example.com", "maxPages": 60, "javascript": true }
  ],
  "concurrency": 8,
  "timeoutSeconds": 15
}
```

The file in this repo starts with `https://example.com`. Replace that with the sites you want checked, then commit the change. The file only holds public URLs.

## Run it locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python checker.py --config config.json --dry-run
```

`playwright install chromium` is only required when a site sets `javascript` to `true`.

`--dry-run` prints the report, writes `report.html`, and does not send email.

To send email from your machine, export the same variables the GitHub Actions job uses:

```bash
export SMTP_HOST=smtp.example.com
export SMTP_PORT=587
export SMTP_USER=you@example.com
export SMTP_PASSWORD=your-smtp-password
export EMAIL_FROM=you@example.com
export EMAIL_TO=you@example.com,team@example.com
python checker.py --config config.json
```

Port 465 uses implicit TLS. Any other port, including 587, uses STARTTLS. `EMAIL_TO` accepts a comma-separated list. Keep the password in the environment or in a gitignored `.env` file you load yourself. Do not put it in `config.json`.

## Schedule it on GitHub Actions

Push this repository to GitHub, then add these repository secrets. In the repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Example |
| --- | --- |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `you@gmail.com` |
| `SMTP_PASSWORD` | app password or SMTP password |
| `EMAIL_FROM` | `you@gmail.com` |
| `EMAIL_TO` | `you@gmail.com,team@example.com` |
| `SUPABASE_URL` | `https://your-project.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | service role key, not the anon key |

The workflow file does not contain these values. [`.github/workflows/link-check.yml`](.github/workflows/link-check.yml) runs:

- on schedule, Mondays at 07:00 UTC (`0 7 * * 1`)
- when you click **Run workflow** (**Actions → Broken link report → Run workflow**)

Each run uploads `report.html` as the `link-report` artifact, including when the check fails. Open the run and download it from the Artifacts section. When the Supabase secrets are set, the same run publishes `report.json` for the hosted review app. Decisions already stored for a URL are left in place.

A public repository does not spend GitHub Actions minutes on this job. A private repository does. The runner needs network access to your sites and to the SMTP server.

## Hosted review

The team reviews flagged links in the React app under [`review/`](review/). The weekly job fills Supabase. People open the hosted site, sign in, and do not run the checker themselves.

1. Create a Supabase project and run [`supabase/schema.sql`](supabase/schema.sql) in the SQL editor.
2. In Authentication, invite each person by email. New accounts start as `member`. Promote an admin:

```sql
update public.profiles
set role = 'admin'
where id = (select id from auth.users where email = 'you@example.com');
```

3. Add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` to the GitHub Actions secrets. The service role key writes scans. It must not be used in the website.
4. Deploy `review/` as a static site. Set `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` from [`review/.env.example`](review/.env.example).

To run the review app locally:

```bash
cd review
cp .env.example .env.local
npm install
npm run dev
```

An admin confirms whether a link is really broken. After that, anyone signed in can suggest a replacement URL or request that the link be deleted.

## Review links locally

The checker also writes `report.json`. A small web app reads that file so people can decide what to do with each flagged URL.

An admin confirms whether the link is really broken. After that, anyone on the team can suggest a replacement URL or request that the link be deleted. Those decisions are stored in `decisions.db` and stay attached to the URL when the next scan arrives.

```bash
cp users.example.json users.json
python web.py
```

Open http://127.0.0.1:5000. The example file signs in `admin` / `admin-pass` and `team` / `team-pass`. Change both passwords before anyone else can reach the app:

```bash
python web.py hash-password
```

Paste each printed hash into `users.json`. Also replace the `secret` string. `users.json` is gitignored.

## Report

The email and `report.html` group results by site. Each review error shows the URL, the HTTP status or error, and the pages where it was found (up to 10 source pages). Inaccessible links are not listed. The report links to the review app with the count for that site.
