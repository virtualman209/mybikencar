"""Đọc email báo phí gửi xe (Gmail, IMAP) và tạo mã VietQR để chuyển khoản."""
import email
import hashlib
import html
import imaplib
import re
import unicodedata
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

# Mã ngân hàng (BIN NAPAS, mã ngắn VietQR) để tạo mã QR chuyển khoản.
BANKS = {
    "vietinbank": ("970415", "ICB"), "vietcombank": ("970436", "VCB"), "bidv": ("970418", "BIDV"),
    "agribank": ("970405", "VBA"), "techcombank": ("970407", "TCB"), "mbbank": ("970422", "MB"),
    "mb": ("970422", "MB"), "acb": ("970416", "ACB"), "vpbank": ("970432", "VPB"), "tpbank": ("970423", "TPB"),
    "sacombank": ("970403", "STB"), "hdbank": ("970437", "HDB"), "vib": ("970441", "VIB"), "shb": ("970443", "SHB"),
    "ocb": ("970448", "OCB"), "msb": ("970426", "MSB"), "seabank": ("970440", "SEAB"), "eximbank": ("970431", "EIB"),
}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------- parsing
def html_to_text(h: str) -> str:
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?i)<(br|/p|/div|/li|li|/tr|/h\d)[^>]*>", "\n", h)
    return html.unescape(re.sub(r"<[^>]+>", " ", h))


def body_text(msg) -> str:
    plain, rich = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        (plain if ctype == "text/plain" else rich).append(text)
    if rich:
        return "\n".join(html_to_text(t) for t in rich)
    return "\n".join(plain)


def header(msg, name) -> str:
    try:
        return str(make_header(decode_header(msg.get(name, ""))))
    except Exception:  # noqa: BLE001
        return msg.get(name, "")


def parse_fee(text: str):
    """Lấy thông tin phí từ nội dung email. Trả về None nếu không phải email báo phí."""
    flat = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()
    amt = re.search(r"(?:Số tiền|Amount)[^:\d]{0,30}:\s*([\d][\d.,\s]*\d)\s*(?:VNĐ|VND|đ|đồng|dong)", flat, re.I)
    acc = re.search(r"(?:Số tài khoản|Account No)[^:\d]{0,30}:\s*(\d[\d .-]{4,}\d)", flat, re.I)
    if not amt or not acc:
        return None
    fee = {
        "amount": int(re.sub(r"\D", "", amt.group(1))),
        "accountNo": re.sub(r"\D", "", acc.group(1)),
        "vehicle": "car", "month": "", "due": "", "accountName": "", "bank": "", "bin": "", "bankCode": "", "descTemplate": "",
    }
    v = re.search(r"phí\s+(?:gửi|đỗ|giữ)\s+xe\s+(ô\s*tô|oto|máy|điện|đạp)", flat, re.I)
    if v and not re.match(r"ô|o", v.group(1), re.I):
        fee["vehicle"] = "bike"
    m = re.search(r"tháng\s+(\d{1,2})\s*/\s*(\d{4})", flat, re.I)
    if m:
        fee["month"] = f"{int(m.group(1)):02d}/{m.group(2)}"
    d = re.search(r"(?:trước|hạn)\s+(?:ngày\s+)?(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})", flat, re.I)
    if d:
        fee["due"] = f"{d.group(3)}-{int(d.group(2)):02d}-{int(d.group(1)):02d}"
    n = re.search(r"Tên tài khoản[^:]{0,30}:\s*(.+?)\s*(?:Số tài khoản|Account No)", flat, re.I)
    if n:
        fee["accountName"] = n.group(1).strip(" •-–")
    b = re.search(r"tại\s*/?\s*(?:at\s+)?([A-Za-zÀ-ỹ]+(?:\s?bank)?)", flat[acc.end():acc.end() + 60], re.I)
    if b:
        fee["bank"] = b.group(1).strip()
        key = re.sub(r"\s", "", fee["bank"].lower())
        if key in BANKS:
            fee["bin"], fee["bankCode"] = BANKS[key]
    t = re.search(r"Nội dung chuyển khoản[^:]{0,40}:\s*(.+?)\s*/\s*\[", flat, re.I) or \
        re.search(r"Nội dung chuyển khoản[^:]{0,40}:\s*(.+?)\s*(?:Số tiền|Amount)", flat, re.I)
    if t:
        fee["descTemplate"] = t.group(1).strip(" •")
    return fee


# ---------------------------------------------------------------- IMAP
def _all_mail_folder(M):
    typ, boxes = M.list()
    for raw in boxes or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        m = re.match(r'\((?P<flags>[^)]*)\) "[^"]*" (?P<name>.+)$', line)
        if m and "\\All" in m.group("flags"):
            return m.group("name")
    return "INBOX"


SENDERS = ("sun ponte", "sungroup")   # v1.8.0: ngoài từ khóa (mặc định "seasons") còn tìm email phí Sun Ponte


def pdf_parts(msg):
    """[(tên file, dữ liệu)] các file PDF đính kèm (≤ 5 MB)."""
    out = []
    for part in msg.walk():
        name = part.get_filename() or ""
        try:
            name = str(make_header(decode_header(name)))
        except Exception:  # noqa: BLE001
            pass
        if part.get_content_type() == "application/pdf" or name.lower().endswith(".pdf"):
            data = part.get_payload(decode=True) or b""
            if data[:5] == b"%PDF-" and len(data) <= 5 * 1024 * 1024:
                out.append((name or "phi.pdf", data))
    return out


def fees_from_message(mid: str, subject: str, sender: str, received: int, text: str, pdfs, seen: set):
    """Một email -> danh sách (phí, dữ liệu PDF hoặc None). Email báo phí gửi xe: đọc nội dung;
    email có PDF (phí dịch vụ Seasons Avenue, Sun Ponte): đọc từng PDF. Đã đọc (kể cả không phải phí) thì thêm vào `seen`."""
    import feepdf
    out = []
    fid = "fee" + hashlib.sha1(mid.encode()).hexdigest()[:16]
    if fid not in seen:
        fee = parse_fee(text)
        if fee:
            fee.update(kind="parking")
            out.append((fee | {"id": fid}, None))
        elif not pdfs:
            seen.add(fid)
    for name, data in pdfs or []:
        pid = "fee" + hashlib.sha1(f"{mid}|{name}".encode()).hexdigest()[:16]
        if pid in seen:
            continue
        fee = feepdf.parse_pdf(data, subject, text)
        if fee:
            out.append((fee | {"id": pid, "pdf": name}, data))
        else:
            seen.add(pid)
    if pdfs and not any(f.get("kind") == "parking" for f, _ in out):
        seen.add(fid)
    for fee, _ in out:
        fee.update(subject=subject, sender=sender, received=received, paidAt=None)
    return out


def fetch_fees(user: str, password: str, keyword: str, seen: set, days: int = 45):
    """Đăng nhập Gmail (chỉ đọc), tìm email báo phí, trả về [(phí, PDF|None)] mới."""
    found = []
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
    try:
        M.login(user, password)
        M.select(_all_mail_folder(M), readonly=True)
        since = date.today() - timedelta(days=days)
        crit = ["SINCE", f"{since.day:02d}-{MONTHS[since.month - 1]}-{since.year}"]
        names = [k for k in ((keyword or "seasons"),) + SENDERS if k]
        crit += ["OR"] * (len(names) - 1)
        for k in names:
            crit += ["FROM", f'"{k}"']
        typ, data = M.search(None, *crit)
        for num in (data[0].split() if data and data[0] else [])[-40:]:
            typ, d = M.fetch(num, "(BODY.PEEK[])")
            raw = next((x[1] for x in d if isinstance(x, tuple)), None)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            mid = (msg.get("Message-ID") or "").strip() or hashlib.sha1(raw[:4000]).hexdigest()
            try:
                received = int(parsedate_to_datetime(msg.get("Date")).timestamp() * 1000)
            except Exception:  # noqa: BLE001
                received = 0
            found += fees_from_message(mid, header(msg, "Subject"), header(msg, "From"), received, body_text(msg), pdf_parts(msg), seen)
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass
    return found


def test_login(user: str, password: str):
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=20)
    try:
        M.login(user, password)
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- VietQR
def _tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def _crc16(data: str) -> str:
    crc = 0xFFFF
    for ch in data.encode("utf-8"):
        crc ^= ch << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def no_accent(s: str) -> str:
    s = unicodedata.normalize("NFD", s).replace("đ", "d").replace("Đ", "D")
    return re.sub(r"[^\x20-\x7e]", "", "".join(c for c in s if unicodedata.category(c) != "Mn"))


def vietqr_payload(bin_: str, account: str, amount: int, note: str) -> str:
    """Chuỗi mã VietQR (chuẩn EMVCo/NAPAS) chuyển khoản tới tài khoản, kèm số tiền và nội dung."""
    acct = _tlv("00", "A000000727") + _tlv("01", _tlv("00", bin_) + _tlv("01", account)) + _tlv("02", "QRIBFTTA")
    s = _tlv("00", "01") + _tlv("01", "12") + _tlv("38", acct) + _tlv("53", "704")
    if amount:
        s += _tlv("54", str(int(amount)))
    s += _tlv("58", "VN")
    note = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]+", " ", no_accent(note))).strip()[:90]  # chỉ chữ, số, dấu cách
    if note:
        s += _tlv("62", _tlv("08", note))
    s += "6304"
    return s + _crc16(s)


# ---------------------------------------------------------------- Google Apps Script (không cần mật khẩu)
# Đoạn mã chạy trong tài khoản Google của chủ nhà, quyền CHỈ ĐỌC Gmail. Máy chủ OMV gọi link /exec kèm khóa bí mật.
SCRIPT_MANIFEST = """{
  "timeZone": "Asia/Ho_Chi_Minh",
  "runtimeVersion": "V8",
  "exceptionLogging": "STACKDRIVER",
  "dependencies": {
    "enabledAdvancedServices": [{ "userSymbol": "Gmail", "serviceId": "gmail", "version": "v1" }]
  },
  "oauthScopes": [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.email"
  ],
  "webapp": { "executeAs": "USER_DEPLOYING", "access": "ANYONE_ANONYMOUS" }
}
"""

SCRIPT_CODE = r"""// MyBike'nCar – tìm email báo phí (gửi xe, phí dịch vụ Seasons Avenue, Sun Ponte), CHỈ ĐỌC Gmail. Bản 2.
// Dán nguyên đoạn này vào Code.gs, không cần sửa gì.
const KEY = '__KEY__';
const QUERY = '(from:seasons OR from:sungroup OR from:"sun ponte" OR "sun ponte") newer_than:60d';

function doGet(e) {
  const p = (e && e.parameter) || {};
  if (p.key !== KEY) return out_({ ok: false, error: 'forbidden' });
  if (p.ping) return out_({ ok: true, v: 2, account: Session.getEffectiveUser().getEmail() });
  let q = QUERY;
  if (p.since && /^\d+$/.test(p.since)) q += ' after:' + p.since;
  const res = Gmail.Users.Messages.list('me', { q: q, maxResults: 30 });
  const items = [];
  (res.messages || []).forEach(function (r) {
    const m = Gmail.Users.Messages.get('me', r.id, { format: 'full' });
    const h = {};
    (m.payload.headers || []).forEach(function (x) { h[x.name.toLowerCase()] = x.value; });
    const acc = { plain: '', html: '', pdfs: [] };
    walk_(m.payload, acc);
    const body = acc.html || acc.plain;
    const parking = /(Số tiền|Amount)/i.test(body) && /(Số tài khoản|Account No)/i.test(body);
    const bill = acc.pdfs.length && /(phí|phi|debit)/i.test((h['subject'] || '') + ' ' + body);
    // Chỉ gửi đi email báo phí, bỏ qua mọi email khác.
    if (!parking && !bill) return;
    const pdfs = [];
    if (bill) acc.pdfs.forEach(function (a) {
      const d = Gmail.Users.Messages.Attachments.get('me', r.id, a.id).data;
      pdfs.push({ name: a.name, data: typeof d === 'string' ? d : Utilities.base64EncodeWebSafe(d) });
    });
    items.push({ messageId: h['message-id'] || r.id, date: Number(m.internalDate), from: h['from'] || '',
                 subject: h['subject'] || '', html: acc.html, text: acc.plain, pdfs: pdfs });
  });
  return out_({ ok: true, v: 2, messages: items });
}

function walk_(part, acc) {
  if (part.parts && part.parts.length) { part.parts.forEach(function (c) { walk_(c, acc); }); return; }
  const name = part.filename || '';
  if (name && (part.mimeType === 'application/pdf' || /\.pdf$/i.test(name))) {
    if (part.body && part.body.attachmentId && (part.body.size || 0) <= 5 * 1024 * 1024) acc.pdfs.push({ name: name, id: part.body.attachmentId });
    return;
  }
  if (name || !part.body || !part.body.data) return;
  if (part.mimeType !== 'text/html' && part.mimeType !== 'text/plain') return;
  let charset = 'UTF-8';
  (part.headers || []).forEach(function (x) {
    const m = /charset="?([^";]+)/i.exec(x.value || '');
    if (x.name.toLowerCase() === 'content-type' && m) charset = m[1];
  });
  const d = part.body.data;
  let bytes;
  if (typeof d === 'string') {
    const s = d.replace(/-/g, '+').replace(/_/g, '/');
    bytes = Utilities.base64Decode(s + '==='.slice((s.length + 3) % 4));
  } else {
    bytes = d;
  }
  let text;
  try { text = Utilities.newBlob(bytes).getDataAsString(charset); }
  catch (err) { text = Utilities.newBlob(bytes).getDataAsString('UTF-8'); }
  if (part.mimeType === 'text/html') acc.html += text; else acc.plain += text;
}

function out_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
"""
SCRIPT_V = 2


def _script_get(url: str, key: str, extra: str = ""):
    import json
    import urllib.parse
    import urllib.request
    full = f"{url}?key={urllib.parse.quote(key)}{extra}"
    req = urllib.request.Request(full, headers={"User-Agent": "MyBikenCar/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:  # Google chuyển hướng sang script.googleusercontent.com
        raw = r.read().decode("utf-8", "replace")
        ctype = r.headers.get("Content-Type", "")
    if "json" not in ctype and not raw.lstrip().startswith("{"):
        raise ScriptError("html")
    data = json.loads(raw)
    if not data.get("ok"):
        raise ScriptError(data.get("error") or "error")
    return data


class ScriptError(Exception):
    pass


def script_error(e: Exception) -> str:
    msg = str(e)
    if isinstance(e, ScriptError):
        if msg == "forbidden":
            return "Khóa bí mật không khớp: hãy chép lại đoạn mã Code.gs mới nhất rồi triển khai lại"
        if msg == "html":
            return "Link chưa dùng được: khi triển khai chọn 'Ai có quyền truy cập: Bất kỳ ai' và đã bấm Cấp quyền"
    if "HTTP Error 404" in msg:
        return "Không tìm thấy script: kiểm tra lại đường link /exec"
    if isinstance(e, OSError):
        return "Máy chủ OMV chưa kết nối được tới Google (mạng / DNS)"
    return msg[:200]


def script_ping(url: str, key: str) -> dict:
    return _script_get(url, key, "&ping=1")


def _b64(s: str) -> bytes:
    import base64
    s = (s or "").replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4))


def fetch_fees_script(url: str, key: str, seen: set, since: int = 0):
    """Gọi đoạn mã Google. Trả về ([(phí, PDF|None)], phiên bản đoạn mã)."""
    d = _script_get(url, key, f"&since={int(since)}" if since else "")
    found = []
    for m in d.get("messages") or []:
        mid = (m.get("messageId") or "").strip()
        if not mid:
            continue
        text = html_to_text(m["html"]) if m.get("html") else (m.get("text") or "")
        pdfs = []
        for a in m.get("pdfs") or []:
            try:
                data = _b64(a.get("data"))
            except ValueError:
                continue
            if data[:5] == b"%PDF-":
                pdfs.append((a.get("name") or "phi.pdf", data))
        found += fees_from_message(mid, m.get("subject", ""), m.get("from", ""), int(m.get("date") or 0), text, pdfs, seen)
    return found, int(d.get("v") or 1)
