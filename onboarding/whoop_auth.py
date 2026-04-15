"""
onboarding/whoop_auth.py
────────────────────────
One-time WHOOP OAuth2 authorization flow.
Run this ONCE to generate and store fresh tokens.

Usage:
    python3 onboarding/whoop_auth.py

What it does:
    1. Prints the WHOOP authorization URL — open it and log in as Nili
    2. After approving, your browser redirects to a GitHub Pages URL
    3. Copy that full URL from the address bar and paste it here
    4. Script exchanges the code for tokens and saves them to GitHub Secrets

WHOOP developer docs: https://developer.whoop.com/docs/developing/authorization
"""

import os
import subprocess
from urllib.parse import urlencode, urlparse, parse_qs
import requests
from dotenv import load_dotenv, set_key

load_dotenv()

CLIENT_ID     = os.getenv("WHOOP_CLIENT_ID")
CLIENT_SECRET = os.getenv("WHOOP_CLIENT_SECRET")
REDIRECT_URI  = "https://kamness26.github.io/NOzempic/onboarding/whoop_callback.html"
AUTH_URL      = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL     = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES        = "offline read:profile read:recovery read:cycles read:sleep read:workout"
STATE         = "nozempic_reauth_2026"

ENV_FILE = os.path.join(os.path.dirname(__file__), "../.env")


def extract_code_from_url(redirect_url: str) -> str | None:
    """Extract the 'code' query param from a redirect URL."""
    try:
        parsed = urlparse(redirect_url.strip())
        params = parse_qs(parsed.query)
        return params.get("code", [None])[0]
    except Exception:
        return None


def exchange_code_for_tokens(code: str) -> dict:
    """Exchange authorization code for access + refresh tokens.

    WHOOP app uses client_secret_post — credentials go in the POST body,
    not as a Basic Auth header.
    """
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  REDIRECT_URI,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
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
    """Push fresh tokens to GitHub Secrets using gh CLI (already authenticated)."""
    for name, value in [("WHOOP_ACCESS_TOKEN",  tokens["access_token"]),
                         ("WHOOP_REFRESH_TOKEN", tokens["refresh_token"])]:
        result = subprocess.run(
            ["gh", "secret", "set", name, "--body", value,
             "--repo", "kamness26/NOzempic"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"  🔑  {name} updated in GitHub Secrets")
        else:
            print(f"  ⚠️   Could not update {name}: {result.stderr.strip()}")


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌  WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET must be set in .env")
        return

    params = urlencode({
        "response_type": "code",
        "client_id":     CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "scope":         SCOPES,
        "state":         STATE,
    })
    auth_link = f"{AUTH_URL}?{params}"

    print("\n🔐  WHOOP Re-Authorization")
    print("=" * 60)
    print("\nStep 1 — Open this link in your browser:\n")
    print(f"  {auth_link}\n")
    print("Step 2 — Log in as Nili (payamnili@yahoo.com) and approve access.")
    print("\nStep 3 — After approving, your browser will redirect to a GitHub")
    print("         Pages URL. Copy the FULL URL from the address bar and")
    print("         paste it below.\n")

    pasted = input("Paste the full redirect URL here: ").strip()
    code = extract_code_from_url(pasted)
    if not code:
        print("❌  Could not extract code from that URL. Make sure you copied the full URL.")
        return

    print("\n🔄  Exchanging code for tokens...")
    try:
        tokens = exchange_code_for_tokens(code)
    except Exception as e:
        print(f"❌  Token exchange failed: {e}")
        return

    save_tokens_to_env(tokens)
    save_tokens_to_github(tokens)

    print("\n✅  WHOOP re-authorization complete!")
    print("    Run the workflow now to confirm Nili's data loads correctly.")


if __name__ == "__main__":
    main()
