#!/usr/bin/env bash
# Start a Cloudflare quick tunnel to the Blindsight server and launch Expo with the
# server address pre-filled. Falls back to the LAN IP if the tunnel fails.
#   scripts/tunnel.sh            # tunnel + expo (scan the QR with Expo Go)
#   scripts/tunnel.sh --dry-run  # print address + QR only, do not start expo
# Env: BLINDSIGHT_PORT (8000), BLINDSIGHT_TUNNEL_TIMEOUT (30 s), BLINDSIGHT_EXPO_ARGS, BLINDSIGHT_NO_EXPO
set -u
PORT="${BLINDSIGHT_PORT:-8000}"
TIMEOUT="${BLINDSIGHT_TUNNEL_TIMEOUT:-30}"
DRY=0; [[ "${1:-}" == "--dry-run" ]] && DRY=1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$(mktemp -t blindsight-tunnel)"
CF_PID=""
cleanup() { [[ -n "$CF_PID" ]] && kill "$CF_PID" 2>/dev/null; rm -f "$LOG"; }
trap cleanup EXIT INT TERM

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared missing; installing with brew..." >&2
  brew install cloudflared >&2 || echo "brew install failed; will use LAN fallback" >&2
fi

URL=""
if command -v cloudflared >/dev/null 2>&1; then
  cloudflared tunnel --url "http://localhost:${PORT}" >"$LOG" 2>&1 &
  CF_PID=$!
  for ((i = 0; i < TIMEOUT * 5; i++)); do
    HOST="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 | sed 's#https://##')"
    [[ -n "$HOST" ]] && { URL="wss://${HOST}/ws/phone"; break; }
    kill -0 "$CF_PID" 2>/dev/null || break
    sleep 0.2
  done
fi

if [[ -z "$URL" ]]; then
  IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
  URL="ws://${IP:-127.0.0.1}:${PORT}/ws/phone"
  echo "Tunnel unavailable: using LAN fallback ${URL} (iOS may refuse ws://; try ngrok or Tailscale with wss://)" >&2
fi

echo "EXPO_PUBLIC_SERVER_URL=${URL}"
if command -v uv >/dev/null 2>&1; then
  (cd "$ROOT" && uv run python3 -c "import qrcode,sys; q=qrcode.QRCode(border=1); q.add_data(sys.argv[1]); q.print_ascii(invert=True)" "$URL") 2>/dev/null \
    || echo "(QR unavailable; type the address into the app)" >&2
fi

if [[ $DRY -eq 1 || -n "${BLINDSIGHT_NO_EXPO:-}" ]]; then exit 0; fi
cd "$ROOT/app" && EXPO_PUBLIC_SERVER_URL="$URL" npx expo start ${BLINDSIGHT_EXPO_ARGS:-}
