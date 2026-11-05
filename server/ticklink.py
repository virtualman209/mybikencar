"""Nối MyBike'nCar với VM-TickTick (cổng 8099): bấm "Đã chuyển khoản xong" một khoản phí gửi xe
-> máy chủ tự đánh dấu xong việc tương ứng trong TickTick (vd "đóng vé xe ô tô tháng").

Cách tìm việc: việc CHƯA xong có tên chứa từ khóa (không phân biệt dấu / hoa thường; ô tô: "vé xe ô tô",
xe máy: "vé xe máy"), ngày của việc không sau tháng của khoản phí (việc lặp lại hằng tháng thì tick đúng
lần của tháng đó, không tick lấn sang tháng sau). Nhiều việc khớp thì lấy việc có ngày sớm nhất.
VM-TickTick tắt / mất mạng thì giữ lại, thử lại 5 phút / lần (tối đa 2 ngày).
Trạng thái: DATA/ticktick.json {keys, pending {fee: {...}}, done {fee: {...}}}.
"""
import json
import os
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

URL = os.environ.get("VMTT_URL", "http://host.docker.internal:8099").rstrip("/")
DEFAULT_KEYS = {"car": "vé xe ô tô", "bike": "vé xe máy"}
RETRY_S = 300
MAX_TRIES = 576  # 2 ngày
VN = timezone(timedelta(hours=7))

_lock = threading.RLock()
_wake = threading.Event()
_file: Path | None = None


def init(data_dir: Path):
    global _file
    _file = Path(data_dir) / "ticktick.json"
    threading.Thread(target=_loop, daemon=True).start()


def _read() -> dict:
    try:
        d = json.loads(_file.read_text("utf-8"))
    except (OSError, ValueError, AttributeError):
        d = {}
    d.setdefault("pending", {})
    d.setdefault("done", {})
    d.setdefault("keys", {})
    return d


def _write(d: dict):
    tmp = _file.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, _file)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower().replace("đ", "d"))
    return " ".join("".join(c for c in s if unicodedata.category(c) != "Mn").split())


def keys() -> dict:
    return DEFAULT_KEYS | {k: v for k, v in _read()["keys"].items() if v}


def _req(method: str, path: str, body=None):
    """Gọi VM-TickTick. Trả JSON; lỗi mạng -> OSError. (Test thay hàm này.)"""
    data = json.dumps(body or {}).encode() if method == "POST" else None
    r = urllib.request.Request(URL + path, data=data, method=method,
                               headers={"Content-Type": "application/json", "X-VM-Client": "MyBike'nCar"})
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}") | {"_status": e.code}
        except ValueError:
            return {"error": f"HTTP {e.code}", "_status": e.code}


def fee_month(fee: dict) -> str:
    """'YYYY-MM' của khoản phí: tháng ghi trong email, không có thì theo hạn trả, rồi theo lúc trả."""
    m = (fee.get("month") or "").strip()
    if "/" in m:
        mm, yy = m.split("/", 1)
        if mm.isdigit() and yy.isdigit():
            return f"{yy}-{int(mm):02d}"
    if (fee.get("due") or "")[:7].count("-") == 1:
        return fee["due"][:7]
    ts = fee.get("paidAt") or time.time() * 1000
    return datetime.fromtimestamp(ts / 1000, VN).strftime("%Y-%m")


def on_fee_saved(old: dict | None, new: dict):
    """Gọi sau khi lưu một khoản phí. Vừa được đánh dấu đã trả -> xếp hàng tick TickTick."""
    fid = new.get("id")
    paid, was = new.get("paidAt"), (old or {}).get("paidAt")
    with _lock:
        d = _read()
        if paid and not was and fid not in d["done"]:
            d["pending"][fid] = {"vehicle": new.get("vehicle") or "car", "month": fee_month(new),
                                 "paidAt": paid, "tries": 0, "error": "", "at": int(time.time())}
            _write(d)
            _wake.set()
        elif not paid and fid in d["pending"]:   # bỏ đánh dấu trước khi kịp tick
            d["pending"].pop(fid)
            _write(d)


def find_task(vehicle: str, month: str):
    key = fold(keys().get(vehicle) or DEFAULT_KEYS.get(vehicle, ""))
    r = _req("GET", "/api/v1/tasks?view=all")
    if "tasks" not in r:
        raise OSError(r.get("error") or "VM-TickTick không trả danh sách việc")
    hits = [t for t in r["tasks"] if key and key in fold(t.get("title"))
            and not ((t.get("date") or "")[:7] > month)]
    hits.sort(key=lambda t: t.get("date") or "9999")
    return hits[0] if hits else None


def _handle(p: dict) -> dict | None:
    """Tick một khoản. Trả kết quả cuối cùng, hoặc None = lỗi tạm, thử lại sau (ghi p['error'])."""
    try:
        t = find_task(p["vehicle"], p["month"])
        if not t:
            return {"ok": False, "msg": f"Không thấy việc nào có chữ “{keys().get(p['vehicle'])}” "
                    f"cần làm cho tháng {p['month'][5:]}/{p['month'][:4]}", "at": int(time.time())}
        r = _req("POST", f"/api/v1/tasks/{urllib.parse.quote(t['id'])}/complete")
        if r.get("error"):
            if r.get("_status", 500) >= 500:
                raise OSError(r["error"])
            return {"ok": False, "msg": str(r["error"])[:200], "at": int(time.time())}
        return {"ok": True, "task": t["id"], "title": (t.get("title") or "").strip(),
                "date": t.get("date", ""), "at": int(time.time())}
    except (OSError, ValueError) as e:
        p["error"] = "Chưa kết nối được VM-TickTick" + (f" ({str(e)[:120]})" if str(e) else "")
        return None


def process():
    # Gọi mạng ngoài khóa để không làm chậm việc lưu phí.
    for fid, p in list(_read()["pending"].items()):
        res = _handle(p)
        with _lock:
            d = _read()
            if fid not in d["pending"]:      # vừa bỏ đánh dấu đã trả
                continue
            if res is None:
                q = d["pending"][fid]
                q["tries"] = q.get("tries", 0) + 1
                q["error"] = p["error"]
                if q["tries"] < MAX_TRIES:
                    _write(d)
                    continue
                res = {"ok": False, "msg": q["error"], "at": int(time.time())}
            d["pending"].pop(fid)
            d["done"][fid] = res
            if len(d["done"]) > 200:
                d["done"] = dict(sorted(d["done"].items(), key=lambda kv: kv[1].get("at", 0))[-200:])
            _write(d)


def status(fid: str | None = None) -> dict:
    d = _read()
    if fid:
        if fid in d["pending"]:
            return {"state": "pending", "error": d["pending"][fid].get("error", "")}
        if fid in d["done"]:
            x = d["done"][fid]
            return {"state": "done" if x.get("ok") else "failed"} | x
        return {"state": "none"}
    return {"url": URL, "keys": keys(), "pending": len(d["pending"]),
            "recent": sorted(d["done"].values(), key=lambda x: -x.get("at", 0))[:5]}


def set_keys(new: dict):
    with _lock:
        d = _read()
        d["keys"] = {k: (str(new.get(k) or "").strip()[:80]) for k in DEFAULT_KEYS}
        _write(d)


def _loop():
    while True:
        try:
            if _read()["pending"]:
                process()
        except Exception as e:  # noqa: BLE001
            print("ticktick link error:", e)
        _wake.wait(RETRY_S)
        _wake.clear()
