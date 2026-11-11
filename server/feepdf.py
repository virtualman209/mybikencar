"""Đọc file PDF báo phí đính kèm email (v1.8.0):
- Seasons Avenue (BQL PMC): "PHIẾU THÔNG BÁO (Lần n) / DEBIT NOTE", tổng tiền, hạn, số tài khoản Techcombank, nội dung chuyển khoản.
- Sun Ponte Residence (Sungroup): "THÔNG BÁO THU PHÍ (DEBIT NOTE)", tổng còn phải trả, hạn, mã QR có sẵn trong PDF (đọc ra chuỗi VietQR).
Đọc chữ + vẽ trang bằng pypdfium2, đọc mã QR bằng zxing-cpp. Không đọc được thì trả None (email đó bị bỏ qua)."""
import re
import unicodedata

BANK_NAMES = {  # tên trong PDF -> khóa của feemail.BANKS
    "techcombank": "techcombank", "vietcombank": "vietcombank", "vietinbank": "vietinbank", "bidv": "bidv",
    "agribank": "agribank", "mb bank": "mbbank", "mbbank": "mbbank", "quân đội": "mbbank", "acb": "acb", "vpbank": "vpbank",
    "tpbank": "tpbank", "sacombank": "sacombank", "hdbank": "hdbank", "vib": "vib", "shb": "shb", "ocb": "ocb", "msb": "msb",
    "seabank": "seabank", "eximbank": "eximbank",
}
BIN_NAMES = {"970407": "Techcombank", "970436": "Vietcombank", "970415": "VietinBank", "970418": "BIDV", "970405": "Agribank",
             "970422": "MB Bank", "970416": "ACB", "970432": "VPBank", "970423": "TPBank", "970403": "Sacombank",
             "970437": "HDBank", "970441": "VIB", "970443": "SHB", "970448": "OCB", "970426": "MSB", "970440": "SeABank",
             "970431": "Eximbank"}
PLACES = {"seasons": "Seasons Avenue", "sunponte": "Sun Ponte"}


def pdf_text(data: bytes) -> str:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)
    try:
        out = []
        for i in range(min(len(doc), 4)):
            tp = doc[i].get_textpage()
            out.append(tp.get_text_range())
            tp.close()
        return "\n".join(out)
    finally:
        doc.close()


def pdf_qr(data: bytes) -> str:
    """Chuỗi trong mã QR đầu tiên tìm thấy trên 2 trang đầu ("" nếu không có)."""
    import pypdfium2 as pdfium
    import zxingcpp
    doc = pdfium.PdfDocument(data)
    try:
        for i in range(min(len(doc), 2)):
            for scale in (3, 5):
                img = doc[i].render(scale=scale).to_pil().convert("L")
                for b in zxingcpp.read_barcodes(img, formats=zxingcpp.BarcodeFormat.QRCode):
                    if b.text:
                        return b.text
    finally:
        doc.close()
    return ""


def parse_vietqr(s: str) -> dict | None:
    """Tách chuỗi VietQR (EMVCo): ngân hàng, số tài khoản, số tiền, nội dung."""
    def tlv(x):
        out, i = {}, 0
        while i + 4 <= len(x):
            tag, n = x[i:i + 2], x[i + 2:i + 4]
            if not n.isdigit():
                break
            out[tag] = x[i + 4:i + 4 + int(n)]
            i += 4 + int(n)
        return out
    if not s or not s.startswith("0002"):
        return None
    top = tlv(s)
    acct = tlv(top.get("38", ""))
    if acct.get("00") != "A000000727":
        return None
    ben = tlv(acct.get("01", ""))
    if not ben.get("00") or not ben.get("01"):
        return None
    amt = top.get("54", "")
    return {"bin": ben["00"], "accountNo": ben["01"], "amount": int(float(amt)) if re.fullmatch(r"\d+(\.\d+)?", amt) else 0,
            "note": tlv(top.get("62", "")).get("08", "")}


def _money(s: str) -> int:
    s = s.strip().split(",")[0]          # 1.329.270 -> 1329270 ; 14.619,05 -> 14619
    return int(re.sub(r"\D", "", s) or 0)


def _date(m):
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else ""


def _bank(text: str):
    from feemail import BANKS
    low = text.lower()
    for name, key in BANK_NAMES.items():
        if name in low:
            b, code = BANKS[key]
            return BIN_NAMES.get(b, name.title()), b, code
    return "", "", ""


def _round(*texts) -> int:
    for t in texts:
        m = re.search(r"\(?\s*l[aầ]n\s*0*(\d{1,2})\s*\)?", t or "", re.I)
        if m:
            return int(m.group(1))
    return 1


def which(text: str) -> str:
    up = text.upper()
    if "PONTE" in up:
        return "sunponte"
    if "SEASONS" in up and ("DEBIT NOTE" in up or "THÔNG BÁO" in up):
        return "seasons"
    return ""


def parse_seasons(text: str, subject: str = "") -> dict | None:
    flat = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text))
    m = re.search(r"Tổng tiền cần thanh toán\s*\(?\s*Total amount\s*\)?\s*:?\s*([\d.,]+)", flat, re.I) or \
        re.search(r"Total amount\)?\s*:?\s*([\d][\d.,]*\d)", flat, re.I)
    acc = re.search(r"Account No\s*:?\s*(\d[\d .]{5,}\d)", flat, re.I)
    if not m or not acc:
        return None
    month = re.search(r"Tháng\s+(\d{1,2})\s*[-/]\s*(\d{4})", flat, re.I)
    apt = re.search(r"Căn hộ\s*\(?\s*Apartment\s*\)?\s*:\s*([A-Z0-9.\-]+)", flat, re.I)
    tower = re.search(r"Tòa\s*\(?\s*Tower\s*\)?\s*:\s*(?:TÒA\s*)?([A-Z0-9]+)", flat, re.I)
    name = re.search(r"Account Name\s*:?\s*(.+?)\s*Số tài khoản", flat, re.I)
    bank_m = re.search(r"At Bank\s*:?\s*(.+?)\s*Nội dung", flat, re.I)
    tpl = re.search(r"Nội dung chuyển khoản\s*/\s*Description\s*:?\s*(.+?)\s*(?:Lưu ý|Note\b)", flat, re.I)
    bank, bin_, code = _bank(bank_m.group(1) if bank_m else flat[acc.end():acc.end() + 120])
    mm = f"{int(month.group(1)):02d}/{month.group(2)}" if month else ""
    a, t = (apt.group(1) if apt else ""), (tower.group(1) if tower else "")
    note = " ".join(x for x in (t, a, "Phi quan ly Phi xe", mm.replace("/", " ")) if x)
    rnd = re.search(r"PHIẾU THÔNG BÁO\s*\(Lần\s*\d+\)", flat, re.I)
    items = []
    for it in re.finditer(r"(?:^|\s)([1-9])\s+(Phí quản lý|Nước sinh hoạt|Phí khác|Phí gửi xe[^:]*|Điện[^:]*|Phí [^:(]{2,30})"
                          r"\s*(?:\([^)]*\))?\s*:?[^\d]*?(?:\d{2}/\d{2}/\d{4}\s*-\s*\d{2}/\d{2}/\d{4})?\s*([\d]{1,3}(?:\.\d{3})+)", flat):
        items.append({"name": it.group(2).strip(), "amount": _money(it.group(3))})
    return {"kind": "service", "place": PLACES["seasons"], "amount": _money(m.group(1)), "accountNo": re.sub(r"\D", "", acc.group(1)),
            "accountName": (name.group(1).strip(" .") if name else "BQT TNCC Seasons Avenue"), "bank": bank, "bin": bin_, "bankCode": code,
            "month": mm, "due": _date(re.search(r"trước ngày\s*(\d{1,2})/(\d{1,2})/(\d{4})", flat, re.I)),
            "apartment": a, "round": _round(rnd.group(0) if rnd else "", subject),
            "note": note, "descTemplate": tpl.group(1).strip() if tpl else "", "items": items[:8], "qrPayload": ""}


def parse_sunponte(text: str, qr: str, subject: str = "", body: str = "") -> dict | None:
    flat = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text))
    m = re.search(r"Closing payable balance\s*:?\s*([\d][\d.,]*)", flat, re.I) or \
        re.search(r"Payable amount\s*:?\s*([\d][\d.,]*)", flat, re.I)
    v = parse_vietqr(qr)
    if not m and not (v and v["amount"]):
        return None
    month = re.search(r"Payment period\)?\s*:?\s*(\d{1,2})\s*/\s*(\d{4})", flat, re.I) or \
        re.search(r"tháng\s+(\d{1,2})\s*/\s*(\d{4})", subject + " " + body, re.I)
    apt = re.search(r"Apartment\s*:?\s*([A-Z0-9.\-]+)", flat, re.I)
    amount = _money(m.group(1)) if m else v["amount"]
    items = [{"name": x.group(1).strip(), "amount": _money(x.group(2))}
             for x in re.finditer(r"(?:^|\s)[1-9]\s+((?:Phí|Thoát|Tiền|Dịch vụ)[^/]{2,40})/\s*[A-Za-z ]+?\s+([\d]{1,3}(?:\.\d{3})+)\b", flat)]
    fee = {"kind": "sunponte", "place": PLACES["sunponte"], "amount": amount, "accountNo": "", "accountName": "BQL tòa nhà The Ponte",
           "bank": "", "bin": "", "bankCode": "", "month": f"{int(month.group(1)):02d}/{month.group(2)}" if month else "",
           "due": _date(re.search(r"trước ngày\s*(\d{1,2})/(\d{1,2})/(\d{4})", flat, re.I)),
           "apartment": apt.group(1) if apt else "", "round": _round(subject, body), "note": "", "descTemplate": "",
           "items": items[:8], "qrPayload": qr or ""}
    if v:
        fee.update(accountNo=v["accountNo"], bin=v["bin"], note=v["note"], bank=BIN_NAMES.get(v["bin"], ""))
        from feemail import BANKS
        fee["bankCode"] = next((c for b, c in BANKS.values() if b == v["bin"]), "")
        if v["amount"] and v["amount"] != amount:
            fee["qrAmount"] = v["amount"]
    return fee


def parse_pdf_why(data: bytes, subject: str = "", body: str = ""):
    """(phí, "") hoặc (None, lý do không đọc được) — lý do hiện cho chủ nhà xem."""
    try:
        text = pdf_text(data)
    except Exception as e:  # noqa: BLE001 — PDF hỏng / có mật khẩu
        print("pdf error:", e)
        return None, "không mở được file PDF"
    if len(re.sub(r"\s", "", text)) < 30:
        return None, "PDF chỉ có hình (không có chữ để đọc)"
    kind = which(text)
    if not kind:
        low = (subject + " " + body).lower()
        if "seasons" in low:
            kind = "seasons"
        elif "ponte" in low or "sungroup" in low:
            kind = "sunponte"
        else:
            return None, "không phải phiếu báo phí Seasons Avenue / Sun Ponte"
    if kind == "seasons":
        fee = parse_seasons(text, subject)
        return (fee, "") if fee else (None, "không thấy tổng tiền / số tài khoản trong phiếu Seasons Avenue")
    try:
        qr = pdf_qr(data)
    except Exception as e:  # noqa: BLE001
        print("qr error:", e)
        qr = ""
    fee = parse_sunponte(text, qr, subject, body)
    return (fee, "") if fee else (None, "không thấy số tiền trong phiếu Sun Ponte")


def parse_pdf(data: bytes, subject: str = "", body: str = "") -> dict | None:
    return parse_pdf_why(data, subject, body)[0]
