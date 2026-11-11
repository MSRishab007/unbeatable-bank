#!/usr/bin/env bash
# Installs Chromium for the browser tests in CI.
#
# `--with-deps` also apt-installs system libraries and fonts, which needs the Ubuntu
# package mirrors. When those are unreachable from the runner (it happens: DNS failures
# for archive.ubuntu.com), retry once, then install Chromium alone: GitHub's runner
# image already has the libraries Chromium needs; only some extra fonts would be missing,
# which the tests don't depend on.
set -uo pipefail

for attempt in 1 2; do
  if playwright install --with-deps chromium; then
    exit 0
  fi
  echo "::warning::browser system packages failed to install (attempt $attempt); Ubuntu mirrors unreachable?"
  sleep 20
done

echo "::warning::installing Chromium without system packages; the runner image already has its libraries"
playwright install chromium
