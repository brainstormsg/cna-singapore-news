#!/usr/bin/env python3
"""Encrypt the generated CNA page with StatiCrypt for GitHub Pages deployment."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "index.unlocked.html"
OUTPUT = ROOT / "index.html"
SITE_URL = "https://brainstormsg.github.io/cna-singapore-news/"
# This salt is public and non-secret. It is fixed so scheduled encryption is reproducible.
STATICRYPT_SALT = "3f7711fdc6b2848a9ee5058eb59a0adc"
SOCIAL_HEAD = f'''<meta name="robots" content="noindex, nofollow, noarchive">
<meta name="description" content="Password-protected audio news playlist.">
<link rel="canonical" href="{SITE_URL}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="CNA Singapore &amp; World News">
<meta property="og:title" content="CNA Singapore &amp; World News">
<meta property="og:description" content="Password-protected audio news playlist.">
<meta property="og:url" content="{SITE_URL}">
<meta property="og:image" content="{SITE_URL}assets/social-preview.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="CNA Singapore and World News audio playlist">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="CNA Singapore &amp; World News">
<meta name="twitter:description" content="Password-protected audio news playlist.">
<meta name="twitter:image" content="{SITE_URL}assets/social-preview.png">
<meta name="twitter:image:alt" content="CNA Singapore and World News audio playlist">'''


def main() -> int:
    if not SOURCE.exists():
        print(f"Missing generated source: {SOURCE}", file=sys.stderr)
        return 1
    if not os.environ.get("STATICRYPT_PASSWORD"):
        print("STATICRYPT_PASSWORD is not set", file=sys.stderr)
        return 1
    npx = shutil.which("npx")
    if not npx:
        print("npx is required to run StatiCrypt", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="cna-staticrypt-") as temp_dir:
        encrypted_source = Path(temp_dir) / SOURCE.name
        shutil.copyfile(SOURCE, encrypted_source)
        command = [
            npx, "--yes", "staticrypt@3.5.4", str(encrypted_source), "--directory", temp_dir,
            "--config", "false", "--salt", STATICRYPT_SALT,
            "--template-title", "CNA Singapore & World News", "--template-button", "Unlock",
            "--template-color-primary", "#e51b23", "--template-color-secondary", "#f6f6f6",
            "--template-instructions", "Enter the access password to open the audio playlist.",
            "--template-error", "Incorrect password.", "--remember", "false", "--short",
        ]
        result = subprocess.run(command, env=os.environ.copy(), capture_output=True, text=True)
        if result.returncode:
            print(result.stderr.strip() or "StatiCrypt failed", file=sys.stderr)
            return result.returncode
        encrypted = encrypted_source.read_text(encoding="utf-8")

    if "</head>" not in encrypted or "staticryptEncryptedMsgUniqueVariableName" not in encrypted:
        print("StatiCrypt did not create a valid encrypted page", file=sys.stderr)
        return 1
    OUTPUT.write_text(encrypted.replace("</head>", f"{SOCIAL_HEAD}\n</head>", 1), encoding="utf-8")
    print("Encrypted index.html with StatiCrypt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
