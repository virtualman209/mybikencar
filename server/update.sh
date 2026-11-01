#!/bin/bash
# MyBike'nCar – cập nhật bản mới từ GitHub rồi dựng lại container.
#   ./server/update.sh           cập nhật nếu GitHub có bản mới
#   ./server/update.sh --force   dựng lại kể cả khi không có gì mới
#   ./server/update.sh --auto    dùng cho hẹn giờ 5 phút: không có gì mới thì im lặng thoát
# Bản mới không chạy được thì tự quay về bản cũ (ghi mã lỗi vào .bad_commit).
# Nút "Cập nhật ngay" trong app tạo data/requests/update.req -> systemd (vmbikencar-update.path) gọi script này.

# Chạy từ bản sao trong /tmp để git không ghi đè chính script đang chạy.
if [ -z "${BIKENCAR_UPDATE_COPY:-}" ]; then
  WEB="$(cd "$(dirname "$0")/.." && pwd)"
  TMP="$(mktemp /tmp/vmbikencar-update.XXXXXX)"
  cp "$0" "$TMP"
  BIKENCAR_UPDATE_COPY=1 exec bash "$TMP" "$WEB" "$@"
fi
rm -f "$0"   # bản sao tạm: bash đã mở tệp nên xoá được ngay

WEB="$1"; shift
FORCE=0; AUTO=0
for a in "$@"; do
  [ "$a" = "--force" ] && FORCE=1
  [ "$a" = "--auto" ] && AUTO=1
done
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
if [ "$AUTO" = 1 ]; then
  flock -n 9 || exit 0                     # đang có lần cập nhật khác: bỏ qua lượt này
else
  flock -w 900 9 || { echo "Đang có một lần cập nhật khác chạy."; exit 0; }
fi
exec >>"$LOG" 2>&1
[ "$AUTO" = 1 ] || rm -f "$REQ/update.req"
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

if [ "$AUTO" = 1 ]; then
  # Hẹn giờ 5 phút: chỉ ghi nhật ký khi thật sự có bản mới.
  git fetch --quiet origin main 2>/dev/null || exit 0
  OLD="$(git rev-parse HEAD)"; NEW="$(git rev-parse origin/main)"
  [ "$OLD" = "$NEW" ] && exit 0
  [ -f .bad_commit ] && [ "$(cat .bad_commit)" = "$NEW" ] && exit 0
  echo "[$(date '+%F %T')] Tự cập nhật: có bản mới ${NEW:0:7}"
else
  status running "Đang tải bản mới từ GitHub…"
  if ! git fetch --quiet origin main; then
    status failed "Không kết nối được GitHub"; exit 1
  fi
  OLD="$(git rev-parse HEAD)"
  NEW="$(git rev-parse origin/main)"
fi

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
