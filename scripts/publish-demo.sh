#!/usr/bin/env bash
# Rebuild the static demo from the synthetic household and publish it to the demo site repositories, which
# GitHub Pages serves at demo.aes256afro.com and cybercard.net. Nothing real is involved; each site repo
# holds only the build and its CNAME.
#
#   scripts/publish-demo.sh            from the repository root, on a machine signed in to gh as the owner
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sites=(
  "AES256Afro/financialauthority-demo demo.aes256afro.com"
  "AES256Afro/financialauthority-demo-cybercard cybercard.net"
)

(cd "$root/backend" && uv run --quiet python scripts/capture_demo.py)
(cd "$root/frontend" && npm run build:demo)

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
for entry in "${sites[@]}"; do
  repo="${entry%% *}"
  domain="${entry##* }"
  dir="$work/$(basename "$repo")"
  git clone -q --depth 1 "https://github.com/$repo.git" "$dir"
  (
    cd "$dir"
    git rm -rq --ignore-unmatch assets index.html demo-data.json favicon.svg icon.svg manifest.webmanifest >/dev/null
    cp -r "$root/frontend/dist-demo/assets" .
    cp "$root/frontend/dist-demo/index.html" "$root/frontend/dist-demo/demo-data.json" "$root/frontend/dist-demo/favicon.svg" "$root/frontend/dist-demo/icon.svg" "$root/frontend/dist-demo/manifest.webmanifest" .
    echo "$domain" > CNAME
    touch .nojekyll
    git add -A
    if git diff --cached --quiet; then
      echo "$domain is already current"
    else
      git commit -q -m "Rebuild the demo from FinancialAuthority $(git -C "$root" rev-parse --short HEAD)"
      git push -q origin main
      echo "published: https://$domain (GitHub Pages builds it within a minute)"
    fi
  )
done
