#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
git -C "$REPO_ROOT" pull --ff-only
exec "$REPO_ROOT/install.sh"
