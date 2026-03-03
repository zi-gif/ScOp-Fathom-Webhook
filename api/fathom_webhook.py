import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

from google.oauth2 import service_account
from googleapiclient.discovery import build

MAX_TIMESTAMP_AGE_SECONDS = 300
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"

# Quick-start hardcoded fallbacks (replace these if you want true hardcoded config).
HARDCODED_OPENAI_API_KEY = "sk-REPLACE_ME"
HARDCODED_GOOGLE_DRIVE_TARGET_FOLDER_ID = "REPLACE_ME"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OPENAI_CLEANUP_MODEL = "gpt-4o-mini"
AUTOFILL_APP_PROP_DONE = "fathom_autofill_done"


def _extract_signatures(webhook_signature: str) -> list[str]:
    signatures = []
    for value in webhook_signature.split(" "):
        value = value.strip()
        if not value:
            continue
        if "," in value:
            _, sig = value.split(",", 1)
            signatures.append(sig)
        else:
            signatures.append(value)
    return signatures


def _verify_fathom_signature(headers, raw_body: bytes) -> bool:
    webhook_secret = os.environ.get("FATHOM_WEBHOOK_SECRET", "")
    if not webhook_secret.startswith("whsec_"):
        print("Missing or invalid FATHOM_WEBHOOK_SECRET env var.")
        return False

    webhook_id = headers.get("webhook-id")
    webhook_timestamp = headers.get("webhook-timestamp")
    webhook_signature = headers.get("webhook-signature")

    if not webhook_id or not webhook_timestamp or not webhook_signature:
        return False

    try:
        timestamp = int(webhook_timestamp)
    except ValueError:
        return False

    if abs(int(time.time()) - timestamp) > MAX_TIMESTAMP_AGE_SECONDS:
        print("Rejected webhook: timestamp outside allowed window.")
        return False

    secret_base64 = webhook_secret.split("_", 1)[1]
    try:
        secret_bytes = base64.b64decode(secret_base64)
    except Exception:
        print("Invalid webhook secret encoding.")
        return False

    body_str = raw_body.decode("utf-8", errors="replace")
    signed_content = f"{webhook_id}.{webhook_timestamp}.{body_str}"
    expected_sig = base64.b64encode(
        hmac.new(secret_bytes, signed_content.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

    provided_signatures = _extract_signatures(webhook_signature)
    return any(hmac.compare_digest(expected_sig, sig) for sig in provided_signatures)


def _extract_doc_text_from_structural_elements(elements) -> str:
    chunks = []
    for element in elements or []:
        paragraph = element.get("paragraph")
        if paragraph:
            for pe in paragraph.get("elements", []):
                tr = pe.get("textRun")
                if tr and tr.get("content"):
                    chunks.append(tr["content"])

        table = element.get("table")
        if table:
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    chunks.append(
                        _extract_doc_text_from_structural_elements(cell.get("content", []))
                    )

        toc = element.get("tableOfContents")
        if toc:
            chunks.append(_extract_doc_text_from_structural_elements(toc.get("content", [])))
    return "".join(chunks)


def _get_google_clients():
    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not service_account_json:
        raise ValueError("Missing GOOGLE_SERVICE_ACCOUNT_JSON")

    info = json.loads(service_account_json)
    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=[
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents",
        ],
    )
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    docs = build("docs", "v1", credentials=credentials, cache_discovery=False)
    return drive, docs


def _list_candidate_docs(drive_client, folder_id: str):
    query = (
        f"'{folder_id}' in parents and "
        "mimeType = 'application/vnd.google-apps.document' and trashed = false"
    )
    response = (
        drive_client.files()
        .list(
            q=query,
            pageSize=100,
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return response.get("files", [])


def _build_meeting_context(payload: dict) -> dict:
    invitees = payload.get("calendar_invitees", []) or []
    invitee_names = [i.get("name", "").strip() for i in invitees if i.get("name")]
    invitee_domains = [i.get("email_domain", "").strip() for i in invitees if i.get("email_domain")]
    crm_matches = payload.get("crm_matches", {}) or {}
    crm_company_names = [c.get("name", "").strip() for c in crm_matches.get("companies", []) if c.get("name")]

    default_summary = payload.get("default_summary", {}) or {}
    summary_markdown = _cleanup_summary_for_append(
        (default_summary.get("markdown_formatted") or "").strip()
    )
    meeting_title = (payload.get("meeting_title") or payload.get("title") or "Untitled Meeting").strip()

    return {
        "meeting_title": meeting_title,
        "title": (payload.get("title") or "").strip(),
        "share_url": payload.get("share_url"),
        "created_at": payload.get("created_at"),
        "invitee_names": invitee_names,
        "invitee_domains": invitee_domains,
        "crm_company_names": crm_company_names,
        "summary_markdown": summary_markdown,
        "action_items": payload.get("action_items", []) or [],
    }


def _flatten_transcript(payload: dict, max_chars: int = 12000) -> str:
    parts = []
    for item in payload.get("transcript", []) or []:
        speaker = ((item.get("speaker") or {}).get("display_name") or "Unknown").strip()
        text = (item.get("text") or "").strip()
        if text:
            parts.append(f"{speaker}: {text}")
    return "\n".join(parts)[:max_chars]


def _heuristic_match(candidates: list[dict], meeting_context: dict):
    searchable_terms = set()
    searchable_terms.update([x.lower() for x in meeting_context.get("invitee_names", []) if x])
    searchable_terms.update([x.lower() for x in meeting_context.get("invitee_domains", []) if x])
    searchable_terms.update([x.lower() for x in meeting_context.get("crm_company_names", []) if x])

    meeting_title = meeting_context.get("meeting_title", "").lower().strip()
    if meeting_title:
        searchable_terms.add(meeting_title)

    best = None
    best_score = 0
    for candidate in candidates:
        blob = f"{candidate.get('name', '')}\n{candidate.get('text', '')}".lower()
        score = 0
        for term in searchable_terms:
            if term and term in blob:
                score += 1
        if score > best_score:
            best_score = score
            best = candidate
    return best if best_score > 0 else None


def _safe_json_loads(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError("Failed to parse JSON response from model.")


def _call_openai_text(
    system_prompt: str,
    user_text: str,
    model: str,
    temperature: float = 0,
    json_output: bool = False,
):
    api_key = (os.environ.get("OPENAI_API_KEY") or HARDCODED_OPENAI_API_KEY).strip()
    if not api_key or api_key.endswith("REPLACE_ME"):
        raise ValueError("Missing OPENAI_API_KEY")

    request_body = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
    }
    if json_output:
        request_body["response_format"] = {"type": "json_object"}
    body_bytes = json.dumps(request_body).encode("utf-8")
    request = urllib.request.Request(
        OPENAI_CHAT_COMPLETIONS_URL,
        data=body_bytes,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API error {err.code}: {detail}") from err

    parsed = json.loads(raw)
    return (
        parsed.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )


def _light_local_cleanup(summary_text: str) -> str:
    text = summary_text or ""
    # Strip markdown links while keeping anchor text.
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _cleanup_summary_for_append(summary_text: str) -> str:
    raw = (summary_text or "").strip()
    if not raw:
        return "No summary provided by Fathom."

    cleanup_model = os.environ.get("OPENAI_CLEANUP_MODEL", DEFAULT_OPENAI_CLEANUP_MODEL).strip()
    system_prompt = (
        "Rewrite the meeting summary into clean plain text for Google Docs. "
        "Use pleasant, concise formatting with short section labels and bullets where useful. "
        "Remove markdown links and markdown symbols (#, **, []()). "
        "Preserve factual meaning and do not invent information."
    )
    try:
        cleaned = _call_openai_text(system_prompt, raw, model=cleanup_model, temperature=0)
        if cleaned:
            return cleaned
    except Exception as exc:
        print("SUMMARY_CLEANUP_ERROR", str(exc))
    return _light_local_cleanup(raw)


def _parse_labeled_fields(doc_text: str):
    fields = {}
    for line in (doc_text or "").splitlines():
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        label = label.strip()
        if not label:
            continue
        fields[label] = {"value": value.strip(), "line": line}
    return fields


def _is_empty_field_value(value: str) -> bool:
    v = (value or "").strip()
    return v == "" or v == "$"


def _extract_field_updates_from_payload(payload: dict, labeled_fields: dict) -> list[dict]:
    if not labeled_fields:
        return []

    system_prompt = (
        "You update startup notes fields from meeting data.\n"
        "Rules:\n"
        "1) Transcript has higher priority than summary.\n"
        "2) Only include explicit facts; if unsure skip.\n"
        "3) Currency format: $180k. Percent format: 50%.\n"
        "4) Team size should preserve exact text.\n"
        "5) Return strict JSON: {\"updates\":[{\"field\":\"...\",\"mode\":\"fill|append|skip\",\"value\":\"...\"}]}\n"
        "6) fill: only for empty targets. append: only when existing value is present and new value is more accurate."
    )
    user_payload = {
        "fields": {k: v.get("value", "") for k, v in labeled_fields.items()},
        "meeting_title": payload.get("meeting_title") or payload.get("title"),
        "calendar_invitees": payload.get("calendar_invitees", []),
        "crm_matches": payload.get("crm_matches", {}),
        "summary_markdown": ((payload.get("default_summary") or {}).get("markdown_formatted") or "")[:8000],
        "transcript": _flatten_transcript(payload),
    }
    content = _call_openai_text(
        system_prompt,
        json.dumps(user_payload, ensure_ascii=True),
        model=os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL,
        temperature=0,
        json_output=True,
    )
    parsed = _safe_json_loads(content)
    updates = parsed.get("updates", [])
    return updates if isinstance(updates, list) else []


def _apply_field_updates_to_doc(docs_client, doc_id: str, labeled_fields: dict, updates: list[dict]) -> int:
    requests = []
    applied = 0
    for update in updates:
        if not isinstance(update, dict):
            continue
        field = (update.get("field") or "").strip()
        mode = (update.get("mode") or "skip").strip().lower()
        value = (update.get("value") or "").strip()
        if not field or field not in labeled_fields:
            continue

        current = labeled_fields[field]["value"]
        old_line = labeled_fields[field]["line"]

        if mode == "fill":
            if not _is_empty_field_value(current) or not value:
                continue
            new_value = value
        elif mode == "append":
            if _is_empty_field_value(current) or not value:
                continue
            if value.lower() in current.lower():
                continue
            new_value = f"{current} | {value} (auto from fathom)"
        else:
            continue

        new_line = f"{field}: {new_value}"
        if new_line == old_line:
            continue
        requests.append(
            {
                "replaceAllText": {
                    "containsText": {"text": old_line, "matchCase": True},
                    "replaceText": new_line,
                }
            }
        )
        labeled_fields[field]["value"] = new_value
        labeled_fields[field]["line"] = new_line
        applied += 1

    if requests:
        docs_client.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
    return applied


def _is_doc_autofill_done(drive_client, doc_id: str) -> bool:
    meta = (
        drive_client.files()
        .get(fileId=doc_id, fields="id,appProperties", supportsAllDrives=True)
        .execute()
    )
    app_props = meta.get("appProperties", {}) or {}
    return app_props.get(AUTOFILL_APP_PROP_DONE) == "1"


def _mark_doc_autofill_done(drive_client, doc_id: str):
    drive_client.files().update(
        fileId=doc_id,
        supportsAllDrives=True,
        body={
            "appProperties": {
                AUTOFILL_APP_PROP_DONE: "1",
                "fathom_autofill_done_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    ).execute()


def _call_openai_matcher(meeting_context: dict, candidates: list[dict]) -> dict:
    model = os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL

    candidate_payload = []
    for c in candidates:
        candidate_payload.append(
            {
                "doc_id": c["id"],
                "doc_name": c["name"],
                "doc_excerpt": (c.get("text", "") or "")[:3000],
            }
        )

    system_prompt = (
        "You match a meeting to exactly one Google Doc or return null if no match exists. "
        "Prioritize company name and person name overlap. "
        "Return strict JSON with keys: matched_doc_id (string|null), reason (string)."
    )
    user_prompt = json.dumps(
        {"meeting_context": meeting_context, "candidates": candidate_payload},
        ensure_ascii=True,
    )

    content = _call_openai_text(
        system_prompt,
        user_prompt,
        model=model,
        temperature=0,
        json_output=True,
    )
    return _safe_json_loads(content)


def _select_matched_doc(candidates: list[dict], meeting_context: dict):
    if not candidates:
        return None, "No docs found in target folder."

    try:
        model_result = _call_openai_matcher(meeting_context, candidates)
        doc_id = model_result.get("matched_doc_id")
        if doc_id:
            for candidate in candidates:
                if candidate["id"] == doc_id:
                    return candidate, model_result.get("reason", "Matched by LLM")
    except Exception as exc:
        print("LLM_MATCH_ERROR", str(exc))

    heuristic = _heuristic_match(candidates, meeting_context)
    if heuristic:
        return heuristic, "Matched by heuristic fallback"
    return None, "No confident match"


def _already_appended(doc_text: str, meeting_context: dict) -> bool:
    text = (doc_text or "").strip()
    if not text:
        return False

    share_url = (meeting_context.get("share_url") or "").strip()
    if share_url and share_url in text:
        return True

    title = (meeting_context.get("meeting_title") or "").strip()
    created_at = (meeting_context.get("created_at") or "").strip()
    if title and created_at:
        return f"Fathom Call: {title}" in text and f"Recorded At: {created_at}" in text
    return False


def _has_intro_call_section(doc_text: str) -> bool:
    text = doc_text or ""
    return "Intro Call:" in text


def _find_existing_doc_by_share_url(candidates: list[dict], share_url: str):
    marker = (share_url or "").strip()
    if not marker:
        return None
    for candidate in candidates:
        text = candidate.get("text", "")
        if marker and marker in text:
            return candidate
    return None


def _format_date_label(iso_ts: str) -> str:
    raw = (iso_ts or "").strip()
    if not raw:
        return "Unknown Date"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return f"{dt.month}/{dt.day}/{dt.year}"
    except ValueError:
        return raw


def _get_doc_label_value(labeled_fields: dict, key: str) -> str:
    item = labeled_fields.get(key, {})
    return (item.get("value") or "").strip()


def _build_append_section(meeting_context: dict, labeled_fields: dict):
    company_name = (
        _get_doc_label_value(labeled_fields, "Company Name")
        or (meeting_context.get("crm_company_names") or [""])[0]
        or "Unknown Company"
    )
    date_label = _get_doc_label_value(labeled_fields, "Date") or _format_date_label(
        meeting_context.get("created_at")
    )
    share_url = meeting_context.get("share_url") or ""
    summary = (meeting_context.get("summary_markdown") or "No summary provided by Fathom.").strip()
    action_items = meeting_context.get("action_items") or []

    lines = [
        f"Intro Call: {company_name} {date_label}",
        "",
        "Summary",
        summary,
        "",
    ]
    if share_url:
        lines.extend(["Recording", share_url, ""])
    if action_items:
        lines.append("Action Items")
        for item in action_items:
            description = (item.get("description") or "").strip()
            if not description:
                continue
            assignee = ((item.get("assignee") or {}).get("name") or "").strip()
            if assignee:
                lines.append(f"- {description} (Assignee: {assignee})")
            else:
                lines.append(f"- {description}")
        lines.append("")

    text = "\n".join(lines).strip() + "\n"

    bold_targets = [
        f"Intro Call: {company_name} {date_label}",
        "Summary",
        "Recording" if share_url else None,
        "Action Items" if action_items else None,
    ]
    bold_targets = [x for x in bold_targets if x]
    ranges = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        plain_line = line.rstrip("\n")
        if plain_line in bold_targets:
            ranges.append((cursor, cursor + len(plain_line)))
        cursor += len(line)
    return text, ranges


def _append_summary_to_doc(docs_client, doc_id: str, meeting_context: dict, labeled_fields: dict):
    doc = docs_client.documents().get(documentId=doc_id).execute()
    end_index = doc["body"]["content"][-1]["endIndex"] - 1
    section, bold_ranges = _build_append_section(meeting_context, labeled_fields)

    requests = [
        {"insertPageBreak": {"location": {"index": end_index}}},
        {"insertText": {"location": {"index": end_index + 1}, "text": section}},
    ]
    for start_offset, end_offset in bold_ranges:
        requests.append(
            {
                "updateTextStyle": {
                    "range": {
                        "startIndex": end_index + 1 + start_offset,
                        "endIndex": end_index + 1 + end_offset,
                    },
                    "textStyle": {"bold": True},
                    "fields": "bold",
                }
            }
        )

    try:
        docs_client.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests},
        ).execute()
    except Exception as exc:
        print("PAGE_BREAK_INSERT_FAILED", str(exc))
        docs_client.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"insertText": {"location": {"index": end_index}, "text": "\n\n" + section}}]},
        ).execute()


def _process_webhook_payload(payload: dict) -> dict:
    folder_id = (
        os.environ.get("GOOGLE_DRIVE_TARGET_FOLDER_ID")
        or HARDCODED_GOOGLE_DRIVE_TARGET_FOLDER_ID
    ).strip()
    if not folder_id or folder_id == "REPLACE_ME":
        raise ValueError("Missing GOOGLE_DRIVE_TARGET_FOLDER_ID")

    drive_client, docs_client = _get_google_clients()
    meeting_context = _build_meeting_context(payload)

    files = _list_candidate_docs(drive_client, folder_id)
    candidates = []
    for f in files:
        doc = docs_client.documents().get(documentId=f["id"]).execute()
        text = _extract_doc_text_from_structural_elements(doc.get("body", {}).get("content", []))
        candidates.append({"id": f["id"], "name": f.get("name", ""), "text": text})

    existing = _find_existing_doc_by_share_url(candidates, meeting_context.get("share_url"))
    if existing:
        return {
            "ok": True,
            "status": "skipped_duplicate",
            "meeting_title": meeting_context.get("meeting_title"),
            "matched_doc_id": existing["id"],
            "matched_doc_name": existing["name"],
            "reason": "Meeting summary already exists in the target doc (matched by share URL).",
        }

    matched_doc, reason = _select_matched_doc(candidates, meeting_context)
    if not matched_doc:
        return {
            "ok": True,
            "status": "skipped_unmatched",
            "meeting_title": meeting_context.get("meeting_title"),
            "reason": reason,
        }

    autofill_applied = 0
    if not _is_doc_autofill_done(drive_client, matched_doc["id"]):
        try:
            doc_fields = _parse_labeled_fields(matched_doc.get("text", ""))
            autofill_updates = _extract_field_updates_from_payload(payload, doc_fields)
            autofill_applied = _apply_field_updates_to_doc(
                docs_client, matched_doc["id"], doc_fields, autofill_updates
            )
            _mark_doc_autofill_done(drive_client, matched_doc["id"])
        except Exception as exc:
            print("AUTOFILL_ERROR", str(exc))

    latest_doc = docs_client.documents().get(documentId=matched_doc["id"]).execute()
    latest_doc_text = _extract_doc_text_from_structural_elements(
        latest_doc.get("body", {}).get("content", [])
    )
    if _already_appended(latest_doc_text, meeting_context):
        return {
            "ok": True,
            "status": "skipped_duplicate",
            "meeting_title": meeting_context.get("meeting_title"),
            "matched_doc_id": matched_doc["id"],
            "matched_doc_name": matched_doc["name"],
            "reason": "Meeting summary already exists in the target doc.",
        }
    if _has_intro_call_section(latest_doc_text):
        return {
            "ok": True,
            "status": "skipped_duplicate",
            "meeting_title": meeting_context.get("meeting_title"),
            "matched_doc_id": matched_doc["id"],
            "matched_doc_name": matched_doc["name"],
            "reason": "Intro Call section already exists in the target doc.",
        }

    latest_fields = _parse_labeled_fields(latest_doc_text)
    _append_summary_to_doc(docs_client, matched_doc["id"], meeting_context, latest_fields)
    return {
        "ok": True,
        "status": "appended",
        "meeting_title": meeting_context.get("meeting_title"),
        "matched_doc_id": matched_doc["id"],
        "matched_doc_name": matched_doc["name"],
        "reason": reason,
        "autofill_fields_updated": autofill_applied,
    }


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""

        if not _verify_fathom_signature(self.headers, raw_body):
            response = {"ok": False, "error": "Invalid webhook signature"}
            response_bytes = json.dumps(response).encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        try:
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except json.JSONDecodeError:
            body = {"raw": raw_body.decode("utf-8", errors="replace")}

        try:
            result = _process_webhook_payload(body)
            print(
                "FATHOM_WEBHOOK_PROCESSED",
                {
                    "webhook_id": self.headers.get("webhook-id"),
                    "meeting_title": result.get("meeting_title"),
                    "status": result.get("status"),
                    "matched_doc_id": result.get("matched_doc_id"),
                    "reason": result.get("reason"),
                },
            )
            response = result
            status = 200
        except Exception as exc:
            print(
                "FATHOM_WEBHOOK_PROCESSING_ERROR",
                {
                    "webhook_id": self.headers.get("webhook-id"),
                    "meeting_title": body.get("meeting_title"),
                    "error": str(exc),
                },
            )
            response = {"ok": False, "status": "error", "error": str(exc)}
            status = 500

        response_bytes = json.dumps(response).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def do_GET(self):
        response = {"ok": True, "message": "Use POST for webhooks"}
        response_bytes = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)
