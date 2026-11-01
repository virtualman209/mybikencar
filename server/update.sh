#!/bin/bash
# MyBike'nCar – cập nhật bản mới từ GitHub rồi dựng lại container.
#   ./server/update.sh           cập nhật nếu GitHub có bản mới
#   ./server/update.sh --force   dựng lại kể cả khi không có gì mới
# Bản mới không chạy được thì tự quay về bản cũ (ghi mã lỗi vào .bad_commit).
# Nút "Cập nhật ngay" trong app tạo data/requests/update.req -> systemd (vmbikencar-update.path) gọi script này.

# Chạy từ bản sao trong /tmp để git không ghi đè chính script đang chạy.
if [ -z "${BIKENCAR_UPDATE_COPY:-}" ]; then
  WEB="$(cd "$(dirname "$0")/.." && pwd)"
  cp "$0" /tmp/vmbikencar-update.sh
  BIKENCAR_UPDATE_COPY=1 exec bash /tmp/vmbikencar-update.sh "$WEB" "$@"
fi

WEB="$1"; shift
FORCE=0
for a in "$@"; do [ "$a" = "--force" ] && FORCE=1; done
BASE="$(dirname "$WEB")"
REQ="$BASE/data/requests"
LOG="$WEB/update.log"
CONTAINER=vm_bikencar
mkdir -p "$REQ"

ver() { grep -oP "APP_VERSION = '\K[^']+" "$WEB/index.html" 2>/dev/null || echo "?"; }
status() {  # state, message
  local msg="${2//\"/\'}"
  printf '{"state":"%s","msg":"%s","version":"%s","at":"%s"}\n' "$1" "$msg" "$(ver)" "$(date -Iseconds)" > "$REQ/update.status.tmp"
  mv -f "$REQ/update.status.tmp" "$REQ/update.status"
  echo "[$(date '+%F %T')] $1: $2"
}

exec 9>/tmp/vmbikencar-update.lock
flock -n 9 || { echo "Đang có một lần cập nhật khác chạy."; exit 0; }
exec >>"$LOG" 2>&1
rm -f "$REQ/update.req"
cd "$WEB" || exit 1

healthy() {
  for _ in $(seq 1 45); do
    s="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER" 2>/dev/null)"
    [ "$s" = "healthy" ] && return 0
    sleep 2
  done
  return 1
}
rebuild() { docker compose up -d --build --remove-orphans; }

status running "Đang tải bản mới từ GitHub…"
if ! git fetch --quiet origin main; then
  status failed "Không kết nối được GitHub"; exit 1
fi
OLD="$(git rev-parse HEAD)"
NEW="$(git rev-parse origin/main)"

if [ "$OLD" = "$NEW" ] && [ "$FORCE" = 0 ]; then
  status uptodate "Đang ở bản mới nhất $(ver)"; exit 0
fi
if [ "$FORCE" = 0 ] && [ -f .bad_commit ] && [ "$(cat .bad_commit)" = "$NEW" ]; then
  status failed "Bản mới trên GitHub đã lỗi lần trước, bỏ qua (dùng --force để thử lại)"; exit 1
fi

git reset --hard --quiet "$NEW"
status running "Đang dựng lại app $(ver)…"
if rebuild && healthy; then
  rm -f .bad_commit
  status done "Đã cập nhật lên $(ver) (${NEW:0:7})"
  docker image prune -f >/dev/null 2>&1
  exit 0
fi

echo "$NEW" > .bad_commit
git reset --hard --quiet "$OLD"
rebuild && healthy
status failed "Bản mới bị lỗi nên đã quay về $(ver). Xem $LOG"
exit 1
