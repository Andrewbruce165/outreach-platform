#!/usr/bin/env bash
# Manual frontend deploy for aimly.agsventurelab.com (mirrors /root/apps/vitrina/deploy.sh).
# Builds the TanStack Start SPA in a Docker bun stage (host has no bun) and rsyncs the
# static client bundle into the nginx webroot. No always-on container — nginx serves the
# static files directly; /api/ is reverse-proxied to the backend on 127.0.0.1:8005.
set -euo pipefail

cd "$(dirname "$0")/frontend"

echo "==> Build SPA (docker bun)"
docker run --rm -v "$PWD":/app -w /app oven/bun:1 \
  sh -c "bun install --frozen-lockfile && bun run build"

echo "==> Publish dist/client -> /var/www/aimly"
mkdir -p /var/www/aimly
# SPA build emits dist/client/{_shell.html,assets/}; dist/server is the inert SSR bundle (not served).
rsync -a --delete dist/client/ /var/www/aimly/

echo "==> Done. Shell: /var/www/aimly/_shell.html"
