from html import escape
from pathlib import Path
from http.server import BaseHTTPRequestHandler


def _load_readme() -> str:
    readme_path = Path(__file__).resolve().parent.parent / "README.md"
    if not readme_path.exists():
        return "README.md not found."
    return readme_path.read_text(encoding="utf-8")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        readme_text = _load_readme()
        html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Fathom Webhook README</title>
    <style>
      body {{
        margin: 0;
        padding: 24px;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Courier New", monospace;
        background: #f7f7f8;
        color: #111827;
      }}
      .container {{
        max-width: 980px;
        margin: 0 auto;
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
        padding: 20px;
      }}
      pre {{
        margin: 0;
        white-space: pre-wrap;
        word-break: break-word;
        line-height: 1.5;
      }}
      .muted {{
        color: #6b7280;
      }}
    </style>
  </head>
  <body>
    <div class="container">
      <h1>Fathom Webhook Project</h1>
      <p class="muted">Root URL displays README.md</p>
      <pre>{escape(readme_text)}</pre>
    </div>
  </body>
</html>"""
        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
