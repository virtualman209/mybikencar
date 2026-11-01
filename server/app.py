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
STORES = ("parkings", "maint")
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
    save_record(store, obj)
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
