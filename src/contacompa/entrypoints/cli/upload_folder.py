"""Submit every PDF, JPEG and PNG in a folder to the API and print one line per file.

    uv run python -m contacompa.entrypoints.cli.upload_folder ../Invoices
    uv run python -m contacompa.entrypoints.cli.upload_folder ../Invoices --url http://localhost:8000

The API key comes from API_KEY in the environment or `.env` (the same key the API uses)."""

import argparse
import sys
from pathlib import Path

import httpx

from contacompa.config import get_settings

SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args(argv)

    files = sorted(p for p in args.folder.iterdir() if p.suffix.lower() in SUFFIXES)
    if not files:
        print(f"no PDF/JPEG/PNG files in {args.folder}", file=sys.stderr)
        return 1
    headers = {"X-API-Key": get_settings().api_key.get_secret_value()}
    failures = 0
    with httpx.Client(base_url=args.url, headers=headers, timeout=60) as client:
        for path in files:
            with path.open("rb") as handle:
                response = client.post("/v1/documents", files={"file": (path.name, handle)})
            if response.status_code in (200, 202):
                body = response.json()
                tag = "duplicate" if body["duplicate"] else "queued"
                print(f"{tag:9}  job {body['job_id']}  {path.name}")
            else:
                failures += 1
                print(f"{'error':9}  {response.status_code} {response.text[:120]}  {path.name}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
