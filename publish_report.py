#!/usr/bin/env python3
"""Replace the hosted scan in Supabase.

Decisions keyed by URL stay in place. A review is removed when this scan's
error code for that URL differs from the status that was confirmed.
"""

from __future__ import annotations

import json
import os
import re
import sys

from supabase import create_client

import checker

INSERT_BATCH = 200
DECISION_PAGE = 1000
HTTP_STATUS = re.compile(r"\bHTTP\s+(\d+)\b")


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


def error_code(status: str) -> str:
    """HTTP status number when present, otherwise the full status text."""
    match = HTTP_STATUS.search(status or "")
    if match:
        return match.group(1)
    return status or ""


def same_error_code(left: str, right: str) -> bool:
    return error_code(left) == error_code(right)


def statuses_by_url(findings: list[dict]) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}
    for finding in findings:
        grouped.setdefault(finding["url"], set()).add(finding.get("status") or "")
    return grouped


def review_changes(
    findings: list[dict], reviewed: dict[str, str | None]
) -> tuple[list[tuple[str, str]], list[str]]:
    """Baselines to store, and URLs whose review should be cleared.

    A missing reviewed status is filled from this scan when every finding for
    that URL shares one error code. A stored code that differs is cleared.
    """
    baselines: list[tuple[str, str]] = []
    clear: list[str] = []
    for url, statuses in statuses_by_url(findings).items():
        if url not in reviewed:
            continue
        stored = reviewed[url]
        if not stored:
            codes = {error_code(status) for status in statuses}
            if len(codes) == 1:
                baselines.append((url, next(iter(statuses))))
            continue
        if any(not same_error_code(stored, status) for status in statuses):
            clear.append(url)
    return baselines, clear


def fetch_reviewed_statuses(client) -> dict[str, str | None]:
    reviewed: dict[str, str | None] = {}
    start = 0
    while True:
        response = (
            client.table("decisions")
            .select("url, reviewed_status")
            .range(start, start + DECISION_PAGE - 1)
            .execute()
        )
        rows = response.data or []
        for row in rows:
            url = row.get("url")
            if isinstance(url, str):
                reviewed[url] = row.get("reviewed_status") or None
        if len(rows) < DECISION_PAGE:
            break
        start += DECISION_PAGE
    return reviewed


def apply_review_changes(client, findings: list[dict]) -> int:
    baselines, clear = review_changes(findings, fetch_reviewed_statuses(client))
    for url, status in baselines:
        client.table("decisions").update({"reviewed_status": status}).eq("url", url).execute()
    for start in range(0, len(clear), INSERT_BATCH):
        batch = clear[start : start + INSERT_BATCH]
        client.table("resolutions").delete().in_("url", batch).execute()
        client.table("decisions").delete().in_("url", batch).execute()
    return len(clear)


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
    cleared = apply_review_changes(client, findings)
    print(
        f"Published {len(findings)} links from {generated_at}",
        file=sys.stderr,
    )
    if cleared:
        print(
            f"Cleared {cleared} reviews whose error code changed",
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
