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


def fetch_fees(user: str, password: str, keyword: str, skip_ids: set, days: int = 45):
    """Đăng nhập Gmail (chỉ đọc), tìm email từ người gửi có chữ `keyword`, trả về danh sách phí mới."""
    found = []
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
    try:
        M.login(user, password)
        M.select(_all_mail_folder(M), readonly=True)
        since = date.today() - timedelta(days=days)
        crit = ["SINCE", f"{since.day:02d}-{MONTHS[since.month - 1]}-{since.year}"]
        if keyword:
            crit += ["FROM", f'"{keyword}"']
        typ, data = M.search(None, *crit)
        for num in (data[0].split() if data and data[0] else [])[-40:]:
            typ, d = M.fetch(num, "(BODY.PEEK[])")
            raw = next((x[1] for x in d if isinstance(x, tuple)), None)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            mid = (msg.get("Message-ID") or "").strip() or hashlib.sha1(raw[:4000]).hexdigest()
            fid = "fee" + hashlib.sha1(mid.encode()).hexdigest()[:16]
            if fid in skip_ids:
                continue
            fee = parse_fee(body_text(msg))
            if not fee:
                continue
            try:
                received = int(parsedate_to_datetime(msg.get("Date")).timestamp() * 1000)
            except Exception:  # noqa: BLE001
                received = 0
            fee.update(id=fid, subject=header(msg, "Subject"), sender=header(msg, "From"), received=received, paidAt=None)
            found.append(fee)
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
    note = no_accent(note)[:90]
    if note:
        s += _tlv("62", _tlv("08", note))
    s += "6304"
    return s + _crc16(s)
