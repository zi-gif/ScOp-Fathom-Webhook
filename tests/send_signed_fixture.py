#!/usr/bin/env python3
import argparse
import base64
import hashlib
import hmac
import json
import time
import uuid
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a signed Fathom webhook fixture.")
    parser.add_argument("--url", required=True, help="Webhook URL")
    parser.add_argument("--secret", required=True, help="Fathom webhook secret (whsec_...)")
    parser.add_argument(
        "--fixture",
        default="tests/fixtures/new_meeting_content_ready_alt_x.json",
        help="Path to JSON fixture payload",
    )
    args = parser.parse_args()

    if not args.secret.startswith("whsec_"):
        raise ValueError("Secret must start with whsec_")

    with open(args.fixture, "r", encoding="utf-8") as f:
        body = f.read()
        json.loads(body)

    webhook_id = f"msg_{uuid.uuid4()}"
    webhook_timestamp = str(int(time.time()))

    secret_base64 = args.secret.split("_", 1)[1]
    secret_bytes = base64.b64decode(secret_base64)

    signed_content = f"{webhook_id}.{webhook_timestamp}.{body}"
    signature = base64.b64encode(
        hmac.new(secret_bytes, signed_content.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

    req = urllib.request.Request(
        args.url,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "webhook-id": webhook_id,
            "webhook-timestamp": webhook_timestamp,
            "webhook-signature": f"v1,{signature}",
        },
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        response_body = resp.read().decode("utf-8", errors="replace")
        print(f"status={resp.status}")
        print(response_body)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
