#!/usr/bin/env python3
"""
One-time script to get WHOOP OAuth tokens.
Run locally: python whoop_auth.py

Opens browser for authorization, catches the callback,
exchanges code for access + refresh tokens.
Save the tokens to Railway env vars.
"""

import os
import json
import http.server
import urllib.parse
import webbrowser
import threading

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("WHOOP_CLIENT_ID")
CLIENT_SECRET = os.getenv("WHOOP_CLIENT_SECRET")
REDIRECT_URI = "https://localhost:3000/callback"
AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES = "offline read:recovery read:sleep read:workout read:cycles read:body_measurement read:profile"

# We'll use http://localhost:3000 for the actual server
# but WHOOP redirect is set to https://localhost:3000/callback
# The browser will show a cert error — just copy the code from URL bar

def get_auth_url():
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": "geekbot_auth_2026",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(code: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    resp = requests.post(TOKEN_URL, data=data)
    resp.raise_for_status()
    return resp.json()


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Set WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET in .env")
        return

    auth_url = get_auth_url()
    print(f"\nOpen this URL in your browser:\n\n{auth_url}\n")
    print("After authorizing, the browser will redirect to https://localhost:3000/callback")
    print("You'll get an SSL error. That's fine.")
    print("Copy the 'code' parameter from the URL bar.\n")

    webbrowser.open(auth_url)

    code = input("Paste the authorization code here: ").strip()

    if not code:
        print("No code provided.")
        return

    print("\nExchanging code for tokens...")
    tokens = exchange_code(code)

    print("\n=== TOKENS ===")
    print(f"Access Token:  {tokens.get('access_token', 'N/A')[:50]}...")
    print(f"Refresh Token: {tokens.get('refresh_token', 'N/A')[:50]}...")
    print(f"Expires In:    {tokens.get('expires_in', 'N/A')} seconds")

    # Save to file for reference
    with open("whoop_tokens.json", "w") as f:
        json.dump(tokens, f, indent=2)
    print("\nSaved to whoop_tokens.json")

    print("\n=== ADD TO RAILWAY ===")
    print(f"WHOOP_ACCESS_TOKEN={tokens.get('access_token', '')}")
    print(f"WHOOP_REFRESH_TOKEN={tokens.get('refresh_token', '')}")


if __name__ == "__main__":
    main()
