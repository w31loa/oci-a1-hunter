#!/bin/sh
# Install the a1-hunter binary from the latest GitHub release.
#   curl -fsSL https://raw.githubusercontent.com/w31loa/oci-a1-hunter/main/install.sh | sh
set -eu

REPO="${A1_HUNTER_REPO:-w31loa/oci-a1-hunter}"
BIN_DIR="${A1_HUNTER_BIN_DIR:-$HOME/.local/bin}"

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=macos ;;
  *) echo "Unsupported OS: $(uname -s). On Windows download the .exe from https://github.com/$REPO/releases" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64 | amd64) arch=x86_64 ;;
  arm64 | aarch64) arch=arm64 ;;
  *) echo "Unsupported CPU: $(uname -m)" >&2; exit 1 ;;
esac

asset="a1-hunter-$os-$arch"
url="https://github.com/$REPO/releases/latest/download/$asset"
sums="https://github.com/$REPO/releases/latest/download/SHA256SUMS.txt"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Downloading $asset ..."
curl -fL --progress-bar "$url" -o "$tmp/$asset"

if curl -fsSL "$sums" -o "$tmp/SHA256SUMS.txt" 2>/dev/null; then
  expected="$(grep " $asset\$" "$tmp/SHA256SUMS.txt" | cut -d' ' -f1)"
  if command -v sha256sum >/dev/null; then actual="$(sha256sum "$tmp/$asset" | cut -d' ' -f1)"
  else actual="$(shasum -a 256 "$tmp/$asset" | cut -d' ' -f1)"; fi
  if [ -n "$expected" ] && [ "$expected" != "$actual" ]; then
    echo "Checksum mismatch, aborting." >&2; exit 1
  fi
fi

mkdir -p "$BIN_DIR"
install -m 755 "$tmp/$asset" "$BIN_DIR/a1-hunter"
echo "Installed to $BIN_DIR/a1-hunter"

case ":$PATH:" in
  *":$BIN_DIR:"*) echo "Run: a1-hunter" ;;
  *) echo "Add $BIN_DIR to PATH, or run: $BIN_DIR/a1-hunter" ;;
esac
