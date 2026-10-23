#!/usr/bin/env python3
"""Servidor estático do Radar público (porta 8102).

Bind em 0.0.0.0 porque o cloudflared roda em outra máquina e chega por LAN
(mesmo arranjo do portfolio-site). Serve só o que está na allowlist:
  /                -> site/index.html
  /assets/<...>    -> arquivos em site/assets
  /data/<x>.json   -> agregados gerados por gerar.py
Qualquer outra coisa (diretórios, dotfiles, .py) é 404.

Uso: python3 serve.py [porta]
"""
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")

CSP = (
    "default-src 'self'; "
    "script-src 'self' 'sha256-{inline}' https://static.cloudflareinsights.com; "
    "style-src 'self' 'unsafe-inline'; "  # o Leaflet posiciona camadas com style=
    "img-src 'self' data: https://server.arcgisonline.com; "
    "font-src 'self'; connect-src 'self' https://api.github.com https://cloudflareinsights.com; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


def hash_inline():
    """sha256 do <script> inline do index.html (tema/idioma antes do primeiro paint)."""
    import base64
    import hashlib
    import re
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    m = re.search(r"<script>(.*?)</script>", html, re.S)
    return base64.b64encode(hashlib.sha256(m.group(1).encode()).digest()).decode() if m else ""


def is_public(path):
    parts = [p for p in path.split("/") if p]
    if not parts or any(p.startswith(".") or p == ".." for p in parts):
        return False
    if parts in (["index.html"], ["robots.txt"], ["sitemap.xml"]):
        return True
    if parts[0] == "assets":
        return len(parts) > 1 and os.path.isfile(os.path.join(ROOT, *parts))
    if parts[0] == "data":
        return len(parts) == 2 and parts[1].endswith(".json")
    return False


class Handler(SimpleHTTPRequestHandler):
    server_version = "radar/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".json": "application/json", ".woff2": "font/woff2"}

    def do_POST(self):
        self.send_error(405)

    do_PUT = do_DELETE = do_PATCH = do_POST

    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean in ("/", "/index.html"):
            clean = "/index.html"
        if not is_public(clean):
            return os.path.join(ROOT, ".blocked")  # nunca existe -> 404
        return super().translate_path(clean)

    def list_directory(self, path):
        self.send_error(404)
        return None

    def end_headers(self):
        p = self.path.split("?", 1)[0]
        if p.startswith("/assets/"):
            self.send_header("Cache-Control", "public, max-age=86400")
        elif p == "/data/meta.json":
            # o meta diz qual geração está no ar; os outros JSON são pedidos com ?g=<geração>
            self.send_header("Cache-Control", "public, max-age=60")
        elif p.startswith("/data/"):
            self.send_header("Cache-Control", "public, max-age=900")
        else:
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.send_header("Content-Security-Policy", CSP.format(inline=self.server.inline_hash))
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8102
    os.chdir(ROOT)
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.inline_hash = hash_inline()
    sys.stderr.write("radar em 0.0.0.0:%d (%s)\n" % (port, ROOT))
    srv.serve_forever()


if __name__ == "__main__":
    main()
