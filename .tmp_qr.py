import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

from bilibili_harvester.cookies import read_cookie_file

header, _ = read_cookie_file("cookie.txt")
r = requests.get(
    "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Cookie": header, "Referer": "https://www.bilibili.com/"},
    timeout=15,
)
j = r.json()
print("generate code:", j.get("code"))
data = j.get("data") or {}
print("qrcode_key:", str(data.get("qrcode_key"))[:24], "...")
print("url:", str(data.get("url"))[:80])
try:
    import qrcode

    print("qrcode lib: OK", qrcode.__version__ if hasattr(qrcode, "__version__") else "")
except ImportError:
    print("qrcode lib: MISSING")
try:
    import PIL

    print("PIL: OK")
except ImportError:
    print("PIL: MISSING")
