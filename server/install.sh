#!/bin/bash
# MyBike'nCar – cài lần đầu trên OMV (chạy bằng root qua SSH). Chạy lại nhiều lần cũng không sao.
#   DAS/VM_bikencar/web     mã nguồn (repo GitHub)
#   DAS/VM_bikencar/data    SQLite + ảnh
#   DAS/VM_bikencar/backup  bản sao DB mỗi ngày (30 bản)
set -e
[ "$(id -u)" = 0 ] || { echo "Cần chạy bằng root."; exit 1; }

WEB="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$(dirname "$WEB")"
HTTP_PORT=8096
HTTPS_PORT=8446

echo "==> Thư mục: $BASE"
mkdir -p "$BASE/data/requests" "$BASE/backup"
chmod +x "$WEB/server/"*.sh

echo "==> Cài systemd nhận lệnh 'Cập nhật ngay' từ app"
cat > /etc/systemd/system/vmbikencar-update.service <<EOF
[Unit]
Description=MyBike'nCar: cập nhật từ GitHub
After=docker.service network-online.target

[Service]
Type=oneshot
ExecStart=$WEB/server/update.sh
EOF
cat > /etc/systemd/system/vmbikencar-update.path <<EOF
[Unit]
Description=MyBike'nCar: chờ lệnh cập nhật từ app

[Path]
PathExistsGlob=$BASE/data/requests/update.req
Unit=vmbikencar-update.service

[Install]
WantedBy=multi-user.target
EOF
echo "==> Cài tự cập nhật: kiểm tra GitHub 5 phút một lần"
cat > /etc/systemd/system/vmbikencar-autoupdate.service <<UNIT
[Unit]
Description=MyBike'nCar: tự kiểm tra bản mới trên GitHub
After=docker.service network-online.target

[Service]
Type=oneshot
ExecStart=$WEB/server/update.sh --auto
UNIT
cat > /etc/systemd/system/vmbikencar-autoupdate.timer <<UNIT
[Unit]
Description=MyBike'nCar: tự cập nhật 5 phút một lần

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
Unit=vmbikencar-autoupdate.service

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now vmbikencar-update.path >/dev/null
systemctl enable --now vmbikencar-autoupdate.timer >/dev/null

echo "==> Dựng container vm_bikencar (lần đầu mất khoảng 1-2 phút)"
cd "$WEB"
docker compose up -d --build

for _ in $(seq 1 45); do
  s="$(docker inspect -f '{{.State.Health.Status}}' vm_bikencar 2>/dev/null || true)"
  [ "$s" = healthy ] && break
  sleep 2
done
echo "    Trạng thái: ${s:-?}"

TS_URL=""
if command -v tailscale >/dev/null 2>&1; then
  echo "==> Mở https qua Tailscale (cổng $HTTPS_PORT) để iPhone cài app chạy offline"
  tailscale serve --bg --https=$HTTPS_PORT $HTTP_PORT >/dev/null 2>&1 || echo "    (Không bật được tailscale serve, xem lại Tailscale)"
  DNS="$(tailscale status --json 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
  [ -n "$DNS" ] && TS_URL="https://$DNS:$HTTPS_PORT"
fi

IP="$(hostname -I | awk '{print $1}')"
echo
echo "================ XONG ================"
[ -n "$TS_URL" ] && echo "Mở trên iPhone (Safari, bật Tailscale):  $TS_URL"
echo "Xem nhanh trong nhà (không chạy offline): http://$IP:$HTTP_PORT"
echo "Tự cập nhật:        5 phút một lần (xem: systemctl list-timers 'vmbikencar*')"
echo "Cập nhật bằng SSH:  $WEB/server/update.sh"
echo "Nhật ký cập nhật:   $WEB/update.log"
echo "======================================"
