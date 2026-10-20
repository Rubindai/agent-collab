#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/install-linux-isolation-deps.sh --check|--install

  --check    Check the Linux commands required by isolated peer workspaces.
  --install  Install missing commands with apt-get (Debian/Ubuntu) or dnf (Fedora).

This installs OS packages only. Agent Collab plugins are installed through their marketplaces.
USAGE
}

if [[ $# -ne 1 ]]; then
  usage >&2
  exit 2
fi

case "$1" in
  --check|--install) mode=$1 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if [[ $(uname -s) != Linux ]]; then
  echo "Linux isolation dependencies can only be checked or installed on Linux." >&2
  exit 2
fi

check_commands() {
  local missing=0 tool path
  for tool in bwrap fuse-overlayfs; do
    if path=$(command -v "$tool"); then
      printf '%s: %s\n' "$tool" "$path"
    else
      printf '%s: missing\n' "$tool" >&2
      missing=1
    fi
  done
  if path=$(command -v fusermount3) || path=$(command -v fusermount); then
    printf 'FUSE unmount tool: %s\n' "$path"
  else
    echo "fusermount3 or fusermount: missing" >&2
    missing=1
  fi
  return "$missing"
}

if [[ $mode == --check ]]; then
  check_commands
  exit $?
fi

if check_commands; then
  echo "Linux isolation commands are already available."
  exit 0
fi

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot identify this Linux distribution. Install bubblewrap, fuse-overlayfs, and fuse3 with its package manager." >&2
  exit 2
fi

# os-release is supplied by the local operating system, not by the plugin.
# shellcheck source=/etc/os-release
. /etc/os-release

case "${ID:-}" in
  ubuntu|debian) manager=apt-get ;;
  fedora) manager=dnf ;;
  *)
    case " ${ID_LIKE:-} " in
      *" debian "*) manager=apt-get ;;
      *" fedora "*) manager=dnf ;;
      *)
        echo "Automatic installation supports Debian/Ubuntu and Fedora. Install bubblewrap, fuse-overlayfs, and fuse3 with your distribution's package manager." >&2
        exit 2
        ;;
    esac
    ;;
esac

if ! command -v "$manager" >/dev/null 2>&1; then
  echo "$manager is unavailable; install bubblewrap, fuse-overlayfs, and fuse3 with your distribution's package manager." >&2
  exit 2
fi

elevate=()
if (( EUID != 0 )); then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Run this script as root or install sudo to install system packages." >&2
    exit 2
  fi
  elevate=(sudo)
fi

case "$manager" in
  apt-get)
    "${elevate[@]}" apt-get update
    "${elevate[@]}" apt-get install --no-install-recommends -y bubblewrap fuse-overlayfs fuse3
    ;;
  dnf)
    "${elevate[@]}" dnf install -y bubblewrap fuse-overlayfs fuse3
    ;;
esac

check_commands
echo "Run 'python tools/agent-collab/scripts/host.py doctor' to verify FUSE and user namespaces."
