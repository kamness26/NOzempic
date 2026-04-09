"""
onboarding/whoop_auth.py
────────────────────────
One-time WHOOP OAuth2 authorization flow.
Run this ONCE per participant to generate and store their tokens.

Usage:
    python onboarding/whoop_auth.py

What it does:
    1. Prints the WHOOP authorization URL
    2. Starts a local server to catch the callback automatically (if the
       browser is on this machine), OR lets you paste the redirect URL
       manually (if Nili authorizes on her own device).
    3. Exchanges the auth code for access + refresh tokens
    4. Saves tokens to .env AND updates GitHub Secrets (requires GH_PAT)

WHOOP developer docs: https://developer.whoop.com/docs/developing/authorization
"""

import os
import base64
import json
import subprocess
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


def extract_code_from_url(redirect_url: str) -> str | None:
    """Extract the 'code' query param from a redirect URL."""
    try:
        parsed = urlparse(redirect_url.strip())
        params = parse_qs(parsed.query)
        return params.get("code", [None])[0]
    except Exception:
        return None


def exchange_code_for_tokens(code: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type":   "authorization_code",
            "code":         code,
            "redirect_uri": REDIRECT_URI,
        },
        headers={
            "Content-Type":  "application/x-www-form-urlencoded",
            "Authorization": f"Basic {credentials}",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def save_tokens_to_env(tokens: dict):
    set_key(ENV_FILE, "WHOOP_ACCESS_TOKEN",  tokens["access_token"])
    set_key(ENV_FILE, "WHOOP_REFRESH_TOKEN", tokens["refresh_token"])
    print("✅  Tokens saved to .env")


def save_tokens_to_github(tokens: dict):
    """Push fresh tokens to GitHub Secrets so the next workflow run is ready."""
    gh_pat = os.getenv("GH_PAT")
    if not gh_pat:
        print("⚠️   GH_PAT not set — skipping GitHub Secrets update.")
        print("    Add GH_PAT to your .env or environment to auto-update secrets.")
        return

    env = {**os.environ, "GH_TOKEN": gh_pat}
    for name, value in [("WHOOP_ACCESS_TOKEN",  tokens["access_token"]),
                         ("WHOOP_REFRESH_TOKEN", tokens["refresh_token"])]:
        result = subprocess.run(
            ["gh", "secret", "set", name, "--body", value,
             "--repo", "kamness26/NOzempic"],
            env=env, capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"  🔑  {name} updated in GitHub Secrets")
        else:
            print(f"  ⚠️   Could not update {name}: {result.stderr.strip()}")


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

    # Start local callback server (catches the redirect if browser is on this machine)
    server = HTTPServer(("localhost", 8080), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print("\n🔐  WHOOP Re-Authorization")
    print("=" * 60)
    print("\nStep 1 — Share this link with Nili (or open it yourself):\n")
    print(f"  {auth_link}\n")
    print("Step 2 — Nili clicks the link, logs into WHOOP, and approves access.")
    print("\nStep 3 — One of two things will happen:")
    print("  A) If the browser is on THIS machine: authorization completes automatically.")
    print("  B) If Nili is on a different device: her browser will show a")
    print('     "can\'t connect" page. She should copy the full URL from her')
    print("     address bar and send it to you.\n")
    print("Waiting 120 seconds for automatic callback... (press Enter to skip to manual paste)\n")

    # Wait up to 120s for automatic callback, or let user interrupt early
    import select, sys
    interrupted = False
    try:
        # Use select on stdin so we can wait for either callback or Enter key
        ready, _, _ = select.select([sys.stdin], [], [], 120)
        if ready:
            sys.stdin.readline()  # consume the Enter
            interrupted = True
    except Exception:
        pass  # select not available on Windows

    server.shutdown()

    if received_code and not interrupted:
        print("✅  Authorization code captured automatically.")
        code = received_code
    else:
        print("\nPaste the full redirect URL from Nili's browser address bar")
        print("(it will start with http://localhost:8080/whoop/callback?code=...)\n")
        pasted = input("Redirect URL: ").strip()
        code = extract_code_from_url(pasted)
        if not code:
            print("❌  Could not extract code from that URL. Please try again.")
            return

    print("\n🔄  Exchanging code for tokens...")
    try:
        tokens = exchange_code_for_tokens(code)
    except Exception as e:
        print(f"❌  Token exchange failed: {e}")
        return

    save_tokens_to_env(tokens)
    save_tokens_to_github(tokens)

    print("\n✅  WHOOP re-authorization complete! Nili is connected.")
    print("    Next scheduled run will use the fresh tokens.")


if __name__ == "__main__":
    main()
