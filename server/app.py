"""MyBike'nCar – máy chủ trên OMV.

Lưu vị trí đỗ xe, lịch bảo dưỡng và ảnh vào DAS/VM_bikencar/data (SQLite + thư mục ảnh).
App trên iPhone giữ một bản sao để chạy offline và đồng bộ lên đây khi có mạng.
"""
import base64
import ipaddress
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

import feemail
import ticklink

KINDS = {"service": "Phí dịch vụ Seasons Avenue", "sunponte": "Phí dịch vụ Sun Ponte"}

DATA = Path(os.environ.get("DATA_DIR", "/app/data"))
BACKUP = Path(os.environ.get("BACKUP_DIR", "/backup"))
STATIC = Path(os.environ.get("STATIC_DIR", str(Path(__file__).parent / "static")))
PHOTOS = DATA / "photos"
REQ = DATA / "requests"
DB_PATH = DATA / "mybikencar.db"
REPO_RAW = os.environ.get("REPO_RAW", "https://raw.githubusercontent.com/virtualman209/mybikencar/main/index.html")
KEEP_BACKUPS = 30
MAX_PHOTO = 25 * 1024 * 1024
VN = timezone(timedelta(hours=7))
STORES = ("parkings", "maint", "fees", "prefs")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STATIC_FILES = {"index.html", "sw.js", "manifest.webmanifest", "icon-180.png", "icon-192.png", "icon-512.png"}

for d in (DATA, PHOTOS, REQ):
    d.mkdir(parents=True, exist_ok=True)


def app_version() -> str:
    try:
        m = re.search(r"APP_VERSION\s*=\s*'([^']+)'", (STATIC / "index.html").read_text("utf-8"))
        return m.group(1) if m else "?"
    except OSError:
        return "?"


VERSION = app_version()

# ---------------------------------------------------------------- database
_lock = threading.RLock()
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row
_db.execute("PRAGMA journal_mode=WAL")
_db.execute("PRAGMA synchronous=NORMAL")
_db.executescript(
    """
    CREATE TABLE IF NOT EXISTS records (
        store TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, updated REAL NOT NULL,
        PRIMARY KEY (store, id));
    CREATE TABLE IF NOT EXISTS photos (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, ref TEXT, ts INTEGER NOT NULL, updated REAL NOT NULL);
    """
)
_db.commit()


def q(sql, args=()):
    with _lock:
        cur = _db.execute(sql, args)
        rows = cur.fetchall()
        _db.commit()
        return rows


def check_id(i: str) -> str:
    if not ID_RE.match(i or ""):
        raise HTTPException(400, "id không hợp lệ")
    return i


def photo_path(pid: str, variant: str) -> Path:
    return PHOTOS / f"{check_id(pid)}_{'thumb' if variant == 'thumb' else 'full'}.jpg"


def photo_meta(r) -> dict:
    return {"id": r["id"], "kind": r["kind"], "ref": r["ref"], "ts": r["ts"]}


def save_record(store: str, obj: dict):
    q("INSERT INTO records(store,id,data,updated) VALUES(?,?,?,?) "
      "ON CONFLICT(store,id) DO UPDATE SET data=excluded.data, updated=excluded.updated",
      (store, obj["id"], json.dumps(obj, ensure_ascii=False), time.time()))


def save_photo_meta(meta: dict):
    q("INSERT INTO photos(id,kind,ref,ts,updated) VALUES(?,?,?,?,?) "
      "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, ref=excluded.ref, ts=excluded.ts, updated=excluded.updated",
      (meta["id"], meta.get("kind") or "parking", meta.get("ref"), int(meta.get("ts") or time.time() * 1000), time.time()))


def write_file(path: Path, data: bytes):
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def remove_photo(pid: str):
    with _lock:
        q("DELETE FROM photos WHERE id=?", (pid,))
        # gỡ id ảnh khỏi các bản ghi đang trỏ tới nó
        for r in q("SELECT store,id,data FROM records WHERE data LIKE ?", (f"%{pid}%",)):
            obj = json.loads(r["data"])
            if pid in (obj.get("photoIds") or []):
                obj["photoIds"] = [x for x in obj["photoIds"] if x != pid]
                save_record(r["store"], obj)
    for v in ("full", "thumb"):
        try:
            photo_path(pid, v).unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- app
app = FastAPI(title="MyBike'nCar", docs_url=None, redoc_url=None, openapi_url=None)


def _trusted_origin(origin: str, request: Request) -> bool:
    host = urlparse(origin).hostname or ""
    if host in {"localhost", "127.0.0.1"} or host.endswith(".ts.net"):
        return True
    for h in (request.headers.get("host"), request.headers.get("x-forwarded-host")):
        if h and h.split(":")[0] == host:
            return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


@app.middleware("http")
async def guard(request: Request, call_next):
    # Chặn trang web lạ gửi lệnh ghi/xoá tới máy chủ trong nhà.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and not _trusted_origin(origin, request):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
    return await call_next(request)


@app.get("/api/info")
def info():
    return {"app": "mybikencar", "version": VERSION, "update": update_status()}


@app.get("/api/state")
def state():
    out = {s: [] for s in STORES}
    for r in q("SELECT store,data FROM records ORDER BY updated"):
        if r["store"] in out:
            out[r["store"]].append(json.loads(r["data"]))
    out["photos"] = [photo_meta(r) for r in q("SELECT * FROM photos ORDER BY ts")]
    return out


@app.put("/api/{store}/{rid}")
async def put_record(store: str, rid: str, request: Request):
    if store not in STORES:
        raise HTTPException(404)
    check_id(rid)
    obj = await request.json()
    if not isinstance(obj, dict) or obj.get("id") != rid:
        raise HTTPException(400, "dữ liệu không hợp lệ")
    old = None
    if store == "fees":
        r = q("SELECT data FROM records WHERE store='fees' AND id=?", (rid,))
        old = json.loads(r[0]["data"]) if r else None
    save_record(store, obj)
    if store == "fees":
        ticklink.on_fee_saved(old, obj)
    return {"ok": True}


@app.delete("/api/{store}/{rid}")
def delete_record(store: str, rid: str):
    if store == "photos":
        remove_photo(check_id(rid))
        return {"ok": True}
    if store not in STORES:
        raise HTTPException(404)
    q("DELETE FROM records WHERE store=? AND id=?", (store, check_id(rid)))
    return {"ok": True}


@app.post("/api/photos")
async def upload_photo(meta: str = Form(...), full: UploadFile | None = File(None), thumb: UploadFile | None = File(None)):
    m = json.loads(meta)
    pid = check_id(m.get("id", ""))
    if m.get("kind") not in ("parking", "receipt"):
        raise HTTPException(400, "loại ảnh không hợp lệ")
    for f, v in ((full, "full"), (thumb, "thumb")):
        if f is not None:
            data = await f.read()
            if not data or len(data) > MAX_PHOTO:
                raise HTTPException(400, "ảnh rỗng hoặc quá lớn")
            write_file(photo_path(pid, v), data)
    if not photo_path(pid, "full").exists():
        raise HTTPException(409, "thiếu tệp ảnh")
    if not photo_path(pid, "thumb").exists():
        write_file(photo_path(pid, "thumb"), photo_path(pid, "full").read_bytes())
    save_photo_meta(m)
    return {"ok": True}


@app.get("/api/photos/{pid}/{variant}")
def get_photo(pid: str, variant: str):
    p = photo_path(pid, variant)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ---------------------------------------------------------------- backup / restore
@app.get("/api/backup")
def export_backup():
    st = state()

    def gen():
        head = {"app": "mybikencar", "v": 1, "at": int(time.time() * 1000), **st}
        yield json.dumps(head, ensure_ascii=False)[:-1] + ',"blobs":['
        first = True
        for p in st["photos"]:
            try:
                full = photo_path(p["id"], "full").read_bytes()
                thumb = photo_path(p["id"], "thumb").read_bytes()
            except OSError:
                continue
            item = {"id": p["id"], "full": base64.b64encode(full).decode(), "thumb": base64.b64encode(thumb).decode()}
            yield ("" if first else ",") + json.dumps(item)
            first = False
        yield "]}"

    name = f"MyBikenCar-saoluu-{datetime.now(VN):%Y-%m-%d}.json"
    return StreamingResponse(gen(), media_type="application/json",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/import")
async def import_backup(request: Request):
    if "application/json" not in (request.headers.get("content-type") or ""):
        raise HTTPException(415)
    d = json.loads(await request.body())
    if d.get("app") != "mybikencar":
        raise HTTPException(400, "Tệp không phải bản sao lưu MyBike'nCar")
    n = 0
    for b in d.get("blobs") or []:
        pid = check_id(b["id"])
        write_file(photo_path(pid, "full"), base64.b64decode(b["full"]))
        write_file(photo_path(pid, "thumb"), base64.b64decode(b.get("thumb") or b["full"]))
    for p in d.get("photos") or []:
        if photo_path(check_id(p["id"]), "full").exists():
            save_photo_meta(p)
            n += 1
    counts = {}
    for s in STORES:
        for o in d.get(s) or []:
            check_id(o.get("id", ""))
            save_record(s, o)
        counts[s] = len(d.get(s) or [])
    return {"ok": True, "photos": n, **counts}


def daily_backup_loop():
    """Mỗi ngày sau 03:00 chép DB sang /backup, giữ 30 bản (giống các web VM khác)."""
    while True:
        try:
            now = datetime.now(VN)
            target = BACKUP / f"mybikencar-{now:%Y%m%d}.db"
            if now.hour >= 3 and BACKUP.is_dir() and not target.exists():
                dst = sqlite3.connect(target)
                with _lock:
                    _db.backup(dst)
                dst.close()
                olds = sorted(BACKUP.glob("mybikencar-*.db"))
                for f in olds[:-KEEP_BACKUPS]:
                    f.unlink()
        except Exception as e:  # noqa: BLE001
            print("backup error:", e)
        time.sleep(900)


threading.Thread(target=daily_backup_loop, daemon=True).start()


# ---------------------------------------------------------------- update
_latest = {"at": 0.0, "v": None}


def latest_version():
    if time.time() - _latest["at"] > 300:
        _latest["at"] = time.time()
        try:
            req = urllib.request.Request(REPO_RAW, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=6) as r:
                m = re.search(r"APP_VERSION\s*=\s*'([^']+)'", r.read().decode("utf-8", "ignore"))
                _latest["v"] = m.group(1) if m else None
        except Exception:  # noqa: BLE001
            _latest["v"] = None
    return _latest["v"]


def update_status():
    try:
        st = json.loads((REQ / "update.status").read_text("utf-8"))
    except (OSError, ValueError):
        st = None
    return {"requested": (REQ / "update.req").exists(), "last": st}


@app.get("/api/update")
def get_update():
    return {"current": VERSION, "latest": latest_version(), **update_status()}


@app.post("/api/update")
def request_update():
    # update.sh trên máy OMV (chạy ngoài container, qua systemd) nhận tệp này rồi kéo bản mới từ GitHub.
    write_file(REQ / "update.req", datetime.now(VN).isoformat().encode())
    return {"ok": True}



# ---------------------------------------------------------------- fees: Gmail + VietQR + push
MAIL_CFG = DATA / "mail.json"
PUSH_CFG = DATA / "push.json"
VAPID_KEY = DATA / "vapid_private.pem"
FEE_PDF = DATA / "feepdf"   # v1.8.0: PDF báo phí (Seasons Avenue, Sun Ponte) để xem lại
_mail_lock = threading.Lock()
_mail_wake = threading.Event()


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def mail_mode(cfg: dict) -> str:
    if cfg.get("mode") == "script" and cfg.get("scriptUrl") and cfg.get("scriptKey"):
        return "script"
    if cfg.get("user") and cfg.get("password"):
        return "imap"
    return ""


def mail_public(cfg: dict) -> dict:
    mode = mail_mode(cfg)
    return {k: cfg.get(k) for k in ("user", "keyword", "lastCheck", "lastError", "lastFound")} | {
        "configured": bool(mode), "mode": mode, "scriptOld": mode == "script" and bool(cfg.get("scriptOld")), "user": cfg.get("user") if mode == "imap" else (cfg.get("scriptAccount") or "Google Apps Script")}


def mail_error(e: Exception) -> str:
    msg = str(e)
    if any(x in msg for x in ("AUTHENTICATIONFAILED", "Invalid credentials", "Application-specific", "LOGIN failed")):
        return "Gmail từ chối: sai email hoặc mật khẩu ứng dụng"
    if isinstance(e, OSError):
        return "Máy chủ OMV chưa kết nối được tới Gmail (mạng / DNS)"
    return msg[:200]


def check_mail_now() -> dict:
    """Đọc Gmail, thêm phí mới vào DB, gửi thông báo. Trả về trạng thái."""
    with _mail_lock:
        cfg = read_json(MAIL_CFG, {})
        mode = mail_mode(cfg)
        if not mode:
            return mail_public(cfg)
        seen = set(cfg.get("seen") or [])
        try:
            if mode == "script":
                since = int(cfg.get("scriptSince") or 0)
                new, ver = feemail.fetch_fees_script(cfg["scriptUrl"], cfg["scriptKey"], seen, since - 7 * 86400 if since else 0)
                cfg["scriptOld"] = ver < feemail.SCRIPT_V
                if not cfg["scriptOld"]:
                    cfg["scriptSince"] = int(time.time())
            else:
                new = feemail.fetch_fees(cfg["user"], cfg["password"], cfg.get("keyword", "seasons"), seen)
            for fee, pdf in new:
                exists = q("SELECT 1 FROM records WHERE store='fees' AND id=?", (fee["id"],))
                if not exists:
                    if pdf:
                        FEE_PDF.mkdir(parents=True, exist_ok=True)
                        write_file(FEE_PDF / f"{fee['id']}.pdf", pdf)
                    save_record("fees", fee)
                    notify_fee(fee)
                seen.add(fee["id"])
            mark_replaced()
            cfg.update(lastCheck=int(time.time() * 1000), lastError=None, lastFound=len(new), seen=sorted(seen)[-500:])
        except Exception as e:  # noqa: BLE001
            msg = mail_error(e)
            cfg.update(lastCheck=int(time.time() * 1000), lastError=msg[:200])
        write_json(MAIL_CFG, cfg)
        return mail_public(cfg)


def all_fees() -> list:
    return [json.loads(r["data"]) for r in q("SELECT data FROM records WHERE store='fees'")]


def mark_replaced():
    """Cùng nơi + căn hộ + tháng có thông báo lần mới hơn -> khoản cũ chưa trả ghi replacedBy (không cần trả nữa)."""
    groups = {}
    for f in all_fees():
        if f.get("kind") in KINDS and f.get("month"):
            groups.setdefault((f["kind"], f.get("apartment") or "", f["month"]), []).append(f)
    for fs in groups.values():
        fs.sort(key=lambda f: (f.get("round") or 1, f.get("received") or 0))
        last = fs[-1]
        for f in fs:
            want = None if f is last or f.get("paidAt") else last["id"]
            if f.get("replacedBy") != want:
                f["replacedBy"] = want
                save_record("fees", f)


def mail_loop():
    while True:
        try:
            check_mail_now()
        except Exception as e:  # noqa: BLE001
            print("mail error:", e)
        _mail_wake.wait(600)  # 10 phút một lần, hoặc ngay khi được gọi
        _mail_wake.clear()


threading.Thread(target=mail_loop, daemon=True).start()
ticklink.init(DATA)


# ---------------------------------------------------------------- VM-TickTick: trả phí xong -> tick việc
@app.get("/api/ticktick")
def ticktick_status(fee: str = ""):
    return ticklink.status(check_id(fee) if fee else None)


@app.post("/api/ticktick")
async def ticktick_keys(request: Request):
    body = await request.json()
    ticklink.set_keys(body if isinstance(body, dict) else {})
    return ticklink.status()


@app.get("/api/mail")
def get_mail():
    return mail_public(read_json(MAIL_CFG, {}))


@app.post("/api/mail")
async def set_mail(request: Request):
    if "application/json" not in (request.headers.get("content-type") or ""):
        raise HTTPException(415)
    body = await request.json()
    cfg = read_json(MAIL_CFG, {})
    if body.get("disconnect"):
        cfg.pop("password", None)
        cfg.update(user="", mode="", scriptUrl="")
        write_json(MAIL_CFG, cfg)
        return mail_public(cfg)
    if body.get("mode") == "script":
        url = (body.get("url") or "").strip()
        if not re.match(r"^https://script\.google\.com/(a/[^/]+/)?macros/s/[A-Za-z0-9_-]+/exec$", url):
            raise HTTPException(400, "Đường link phải có dạng https://script.google.com/macros/s/…/exec")
        key = cfg.get("scriptKey") or script_key(cfg)
        try:
            info = feemail.script_ping(url, key)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, feemail.script_error(e))
        cfg.update(mode="script", scriptUrl=url, scriptKey=key, scriptAccount=info.get("account") or "", lastError=None)
        write_json(MAIL_CFG, cfg)
        return check_mail_now()
    user = (body.get("user") or "").strip()
    password = re.sub(r"\s", "", body.get("password") or "") or cfg.get("password", "")
    if not user or not password:
        raise HTTPException(400, "Nhập email và mật khẩu ứng dụng")
    try:
        feemail.test_login(user, password)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, mail_error(e))
    cfg.update(user=user, password=password, keyword=(body.get("keyword") or cfg.get("keyword") or "seasons").strip(), lastError=None)
    write_json(MAIL_CFG, cfg)
    return check_mail_now()


def script_key(cfg: dict) -> str:
    import secrets
    cfg["scriptKey"] = secrets.token_urlsafe(24)
    write_json(MAIL_CFG, cfg)
    return cfg["scriptKey"]


@app.get("/api/mail/script")
def get_script():
    cfg = read_json(MAIL_CFG, {})
    key = cfg.get("scriptKey") or script_key(cfg)
    return {"code": feemail.SCRIPT_CODE.replace("__KEY__", key), "manifest": feemail.SCRIPT_MANIFEST}


@app.get("/gmail")
def gmail_page():
    return FileResponse(Path(__file__).parent / "gmail.html", headers={"Cache-Control": "no-cache"})


@app.post("/api/mail/check")
def mail_check():
    return check_mail_now()


@app.get("/api/fees/{fid}/qr.png")
def fee_qr(fid: str, tn: str = ""):
    rows = q("SELECT data FROM records WHERE store='fees' AND id=?", (check_id(fid),))
    if not rows:
        raise HTTPException(404)
    fee = json.loads(rows[0]["data"])
    if not fee.get("bin") and not fee.get("qrPayload"):
        raise HTTPException(400, "Không rõ ngân hàng nhận")
    import io
    import qrcode
    import qrcode.image.pure
    # Sun Ponte gửi sẵn mã QR trong PDF: dùng nguyên chuỗi đó (không đổi nội dung chuyển khoản).
    payload = fee.get("qrPayload") or feemail.vietqr_payload(fee["bin"], fee["accountNo"], fee.get("amount") or 0, tn)
    img = qrcode.make(payload,
                      image_factory=qrcode.image.pure.PyPNGImage, box_size=10, border=3)
    buf = io.BytesIO()
    img.save(buf)
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


def parking_note(f: dict, p: dict) -> str:
    """Nội dung chuyển khoản phí gửi xe (giống transferNote trong app iPhone)."""
    plate = re.sub(r"[^A-Za-z0-9]+", "", feemail.no_accent((p.get("plateBike") if f.get("vehicle") == "bike" else p.get("plateCar")) or "")).upper()
    t = f.get("descTemplate") or "[số căn hộ] đóng phí gửi xe tháng [...] cho xe [biển số]"
    t = re.sub(r"\[\s*số căn hộ\s*\]", (p.get("apartment") or "").strip() or "so can ho", t, flags=re.I)
    t = re.sub(r"\[\s*(?:…|\.\.\.|tháng)\s*\]", f.get("month") or "", t, flags=re.I)
    t = re.sub(r"\[\s*biển số\s*\]", plate or "bien so", t, flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]+", " ", feemail.no_accent(t))).strip()


@app.get("/api/fees")
def list_fees():
    """Cho VM Dashboard (mục Thanh toán phí): mọi khoản phí + trạng thái đọc Gmail."""
    fees = sorted(all_fees(), key=lambda f: -(f.get("received") or 0))
    prof = {}
    for r in q("SELECT data FROM records WHERE store='prefs' AND id='profile'"):
        prof = json.loads(r["data"])
    for f in fees:
        f["title"] = fee_title(f)
        if f.get("kind") in (None, "", "parking") and not f.get("note"):
            f["note"] = parking_note(f, prof)
        f["hasPdf"] = (FEE_PDF / f"{f['id']}.pdf").exists()
    return {"fees": fees, "mail": mail_public(read_json(MAIL_CFG, {}))}


@app.post("/api/fees/{fid}/paid")
async def fee_paid(fid: str, request: Request):
    """Đánh dấu đã trả / bỏ đánh dấu (VM Dashboard gọi). {"on": true|false}"""
    rows = q("SELECT data FROM records WHERE store='fees' AND id=?", (check_id(fid),))
    if not rows:
        raise HTTPException(404, "Không tìm thấy khoản phí")
    try:
        body = await request.json()
    except ValueError:
        body = {}
    old = json.loads(rows[0]["data"])
    fee = dict(old, paidAt=(int(time.time() * 1000) if body.get("on", True) else None))
    if fee["paidAt"] and old.get("paidAt"):
        fee["paidAt"] = old["paidAt"]
    save_record("fees", fee)
    ticklink.on_fee_saved(old, fee)
    mark_replaced()
    return {"ok": True, "fee": fee}


@app.get("/api/fees/{fid}/pdf")
def fee_pdf(fid: str):
    p = FEE_PDF / f"{check_id(fid)}.pdf"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf", headers={"Cache-Control": "private, max-age=86400"})


# ---- web push (thông báo trên iPhone khi có phí mới)
def vapid():
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid
    if not VAPID_KEY.exists():
        v = Vapid()
        v.generate_keys()
        v.save_key(str(VAPID_KEY))
        os.chmod(VAPID_KEY, 0o600)
    v = Vapid.from_file(str(VAPID_KEY))
    pub = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(pub).decode().rstrip("=")


def send_push(payload: dict) -> int:
    from pywebpush import WebPushException, webpush
    subs = read_json(PUSH_CFG, [])
    keep, sent = [], 0
    for sub in subs:
        try:
            webpush(subscription_info=sub, data=json.dumps(payload, ensure_ascii=False), vapid_private_key=str(VAPID_KEY),
                    vapid_claims={"sub": "https://virtualman209.github.io"}, ttl=86400, timeout=15)
            sent += 1
            keep.append(sub)
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code not in (404, 410):  # 404/410 = iPhone đã huỷ đăng ký
                keep.append(sub)
            print("push error:", code, e)
        except Exception as e:  # noqa: BLE001
            keep.append(sub)
            print("push error:", e)
    if len(keep) != len(subs):
        write_json(PUSH_CFG, keep)
    return sent


def fee_title(fee: dict) -> str:
    if fee.get("kind") in KINDS:
        return KINDS[fee["kind"]]
    return "Phí gửi " + ("ô tô" if fee.get("vehicle") == "car" else "xe máy")


def notify_fee(fee: dict):
    due = fee.get("due") or ""
    due_txt = f" · hạn {due[8:10]}/{due[5:7]}" if len(due) == 10 else ""
    try:
        send_push({"title": fee_title(fee) + (f" tháng {fee['month']}" if fee.get("month") else ""),
                   "body": f"{fee['amount']:,} đ".replace(",", ".") + due_txt + " · Bấm để thanh toán",
                   "url": f"./?fee={fee['id']}", "tag": fee["id"]})
    except Exception as e:  # noqa: BLE001
        print("notify error:", e)


@app.get("/api/push/key")
def push_key():
    return {"key": vapid(), "count": len(read_json(PUSH_CFG, []))}


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request):
    sub = await request.json()
    if not isinstance(sub, dict) or not str(sub.get("endpoint", "")).startswith("https://"):
        raise HTTPException(400)
    vapid()
    subs = [s for s in read_json(PUSH_CFG, []) if s.get("endpoint") != sub["endpoint"]]
    subs.append({"endpoint": sub["endpoint"], "keys": sub.get("keys", {})})
    write_json(PUSH_CFG, subs)
    return {"ok": True, "count": len(subs)}


@app.post("/api/push/test")
def push_test():
    return {"sent": send_push({"title": "MyBike'nCar", "body": "Thông báo đã hoạt động ✓", "url": "./"})}


# ---------------------------------------------------------------- static app
@app.get("/")
def root():
    return static_file("index.html")


@app.get("/{name}")
def static_file(name: str):
    if name not in STATIC_FILES:
        raise HTTPException(404)
    headers = {"Cache-Control": "no-cache"} if name in ("index.html", "sw.js") else {"Cache-Control": "public, max-age=86400"}
    media = "application/manifest+json" if name.endswith(".webmanifest") else None
    return FileResponse(STATIC / name, headers=headers, media_type=media)


@app.head("/api/info")
def head_info():
    return Response(status_code=200)
