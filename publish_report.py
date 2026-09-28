#!/usr/bin/env python3
"""Replace the hosted scan in Supabase. Decisions keyed by URL are left alone."""

from __future__ import annotations

import json
import os
import sys

from supabase import create_client

import checker

INSERT_BATCH = 200


def rows_from_report(data: dict) -> tuple[str, list[dict], list[dict]]:
    generated_at = data.get("generatedAt")
    sites = data.get("sites")
    if not isinstance(generated_at, str) or not isinstance(sites, list):
        raise ValueError("report.json needs generatedAt and sites")

    findings = []
    errors = []
    for site in sites:
        if not isinstance(site, dict):
            continue
        site_url = site.get("url") or ""
        if site.get("error"):
            errors.append({"url": site_url, "error": site["error"]})
            continue
        for link in site.get("links") or []:
            if not isinstance(link, dict) or not isinstance(link.get("url"), str):
                continue
            sources = link.get("sources") or []
            if not isinstance(sources, list):
                sources = []
            findings.append(
                {
                    "site": site_url,
                    "url": link["url"],
                    "kind": link.get("kind") or "broken",
                    "status": link.get("status") or "",
                    "sources": [source for source in sources if isinstance(source, str)],
                }
            )
    return generated_at, findings, errors


def publish(report_path: str, supabase_url: str, service_role_key: str) -> None:
    with open(report_path, encoding="utf-8") as handle:
        generated_at, findings, errors = rows_from_report(json.load(handle))

    client = create_client(supabase_url, service_role_key)
    client.table("findings").delete().gte("id", 0).execute()
    for start in range(0, len(findings), INSERT_BATCH):
        client.table("findings").insert(findings[start : start + INSERT_BATCH]).execute()
    client.table("scans").upsert(
        {"id": 1, "generated_at": generated_at, "errors": errors}
    ).execute()
    print(
        f"Published {len(findings)} links from {generated_at}",
        file=sys.stderr,
    )


def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not service_role_key:
        print("Supabase secrets are not set; skipping publish.", file=sys.stderr)
        return
    report_path = os.environ.get("REPORT_JSON", checker.REPORT_JSON_PATH)
    if not os.path.exists(report_path):
        print(f"No {report_path} to publish.", file=sys.stderr)
        return
    publish(report_path, supabase_url, service_role_key)


if __name__ == "__main__":
    main()
