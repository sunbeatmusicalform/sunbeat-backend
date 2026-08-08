"""Generate a read-only subject-access report for an isolated QA workspace."""
from __future__ import annotations

import argparse
import json

import httpx

from app.core.database import build_supabase_client
from app.services.lgpd_subject_access import build_qa_subject_access_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="QA workspace slug (qa-* or sunbeat-qa-*)")
    parser.add_argument("--email", required=True, help="Exact QA workspace owner email")
    parser.add_argument("--request-id", required=True, help="External request/audit identifier")
    parser.add_argument("--summary-only", action="store_true", help="Print counts and safety evidence, not row data")
    args = parser.parse_args()

    # The hosted Data API can terminate reused HTTP/2 streams during a long
    # inventory. Keep this operational utility isolated from the app client and
    # use a short-lived HTTP/1.1 connection pool for deterministic read-only work.
    with httpx.Client(http2=False, timeout=120) as http_client:
        database = build_supabase_client(httpx_client=http_client)
        report = build_qa_subject_access_report(
            database,
            workspace_slug=args.workspace,
            email=args.email,
            request_id=args.request_id,
        )
    if args.summary_only:
        report = {
            key: report[key]
            for key in ("request_id", "action", "mode", "generated_at", "workspace_slug", "record_counts", "safety")
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
