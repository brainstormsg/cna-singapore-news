#!/bin/sh
# Regenerate, encrypt, and publish the CNA archive from a local runtime clone.
# Plain content stays local; GitHub Pages receives only the StatiCrypt gate.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
KEYCHAIN_SERVICE="cna-singapore-news-staticrypt"
cd "$ROOT"

if ! /usr/bin/git pull -q --ff-only origin main; then
  printf '%s git pull failed; retaining local checkout\n' "$(/bin/date '+%F %T')" >&2
  exit 1
fi

STATICRYPT_PASSWORD=$(/usr/bin/security find-generic-password -s "$KEYCHAIN_SERVICE" -w)
export STATICRYPT_PASSWORD
/usr/bin/python3 tools/update_news.py
/usr/bin/python3 tools/protect_site.py
unset STATICRYPT_PASSWORD

/usr/bin/git add -- index.html assets/social-preview.png
if /usr/bin/git diff --cached --quiet; then
  printf '%s publish: no generated changes\n' "$(/bin/date '+%F %T')"
  exit 0
fi

/usr/bin/git commit -m "Daily update $(/bin/date '+%Y-%m-%d %H:%M') SGT"
if /usr/bin/git push origin main; then
  printf '%s publish: pushed\n' "$(/bin/date '+%F %T')"
  exit 0
fi

# Another Mac may have published generated output first. Preserve GitHub's copy;
# the next scheduled run will regenerate from its current source.
/usr/bin/git fetch -q origin
/usr/bin/git reset -q --hard origin/main
printf '%s publish: another Mac pushed first; kept GitHub version\n' "$(/bin/date '+%F %T')" >&2
exit 0
