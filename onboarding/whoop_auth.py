"""
onboarding/whoop_auth.py
────────────────────────
One-time WHOOP OAuth2 authorization flow.
Run this ONCE per participant to generate and store their tokens.

Usage:
    python onboarding/whoop_auth.py

What it does:
    1. Opens WHOOP's authorization page in the browser
    2. You (or Marcus) log in and approve access
    3. WHOOP redirects to localhost:8080 with an auth code
    4. This script exchanges the code for access + refresh tokens
    5. Tokens are written to .env automatically

WHOOP developer docs: https://developer.whoop.com/docs/developing/authorization
"""

import os
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs
import requests
from dotenv import load_dotenv, set_key

load_dotenv()

CLIENT_ID     = os.getenv("WHOOP_CLIENT_ID")
CLIENT_SECRET = os.getenv("WHOOP_CLIENT_SECRET")
REDIRECT_URI  = os.getenv("WHOOP_REDIRECT_URI", "http://localhost:8080/whoop/callback")
AUTH_URL      = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL     = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES        = "offline read:profile read:recovery read:strain read:sleep read:workout"

ENV_FILE = os.path.join(os.path.dirname(__file__), "../.env")

auth_code_received = threading.Event()
received_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global received_code
        parsed = urlparse(self.path)
        if parsed.path == "/whoop/callback":
            params = parse_qs(parsed.query)
            received_code = params.get("code", [None])[0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                b"<h2>&#x2705; WHOOP connected!</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )
            auth_code_received.set()

    def log_message(self, *args):
        pass  # suppress server logs


def exchange_code_for_tokens(code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  REDIRECT_URI,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def save_tokens(tokens: dict):
    set_key(ENV_FILE, "WHOOP_ACCESS_TOKEN",  tokens["access_token"])
    set_key(ENV_FILE, "WHOOP_REFRESH_TOKEN", tokens["refresh_token"])
    print("✅  Tokens saved to .env")


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌  WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET must be set in .env")
        print("    Register a free app at: https://developer.whoop.com")
        return

    params = urlencode({
        "response_type": "code",
        "client_id":     CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "scope":         SCOPES,
    })
    auth_link = f"{AUTH_URL}?{params}"

    # Start local callback server
    server = HTTPServer(("localhost", 8080), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print("\n🔐  Opening WHOOP authorization in your browser...")
    print(f"    If it doesn't open, visit:\n    {auth_link}\n")
    webbrowser.open(auth_link)

    auth_code_received.wait(timeout=120)
    server.shutdown()

    if not received_code:
        print("❌  No authorization code received. Did you approve access in WHOOP?")
        return

    print("🔄  Exchanging code for tokens...")
    tokens = exchange_code_for_tokens(received_code)
    save_tokens(tokens)
    print("\n✅  WHOOP authorization complete! Marcus is connected.")


if __name__ == "__main__":
    main()
