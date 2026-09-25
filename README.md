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

The workflow file does not contain these values. [`.github/workflows/link-check.yml`](.github/workflows/link-check.yml) runs:

- on schedule, Mondays at 07:00 UTC (`0 7 * * 1`)
- when you click **Run workflow** (**Actions → Broken link report → Run workflow**)

Each run uploads `report.html` as the `link-report` artifact, including when the check fails. Open the run and download it from the Artifacts section.

A public repository does not spend GitHub Actions minutes on this job. A private repository does. The runner needs network access to your sites and to the SMTP server.

## Report

The email and `report.html` group results by site. Each broken link shows the URL, the HTTP status or error, and the pages where it was found (up to 10 source pages). Unchecked links (HTTP 429 and bot-protection challenges) are listed separately.
