from __future__ import annotations

from pathlib import Path

import pytest

from bilibili_opencli import cookies as cs


# ---------- pure parsing ----------

def test_parse_cookie_header() -> None:
    raw = 'buvid3=abc; SESSDATA=x%2Cy; bili_jct=j'
    d = cs.parse_cookie_header(raw)
    assert d == {"buvid3": "abc", "SESSDATA": "x%2Cy", "bili_jct": "j"}


def test_extract_login_cookies_cookie_info_shape() -> None:
    body = {"data": {"cookie_info": {"cookies": [
        {"name": "SESSDATA", "value": "s%2C1"},
        {"name": "bili_jct", "value": "jct"},
    ]}}}
    assert cs.extract_login_cookies(body) == {"SESSDATA": "s%2C1", "bili_jct": "jct"}


def test_extract_login_cookies_url_shape() -> None:
    body = {"data": {"url": "https://passport.biligame.com/crossDomain?DedeUserID=42&SESSDATA=a%2Cb&bili_jct=j&gourl=x"}}
    out = cs.extract_login_cookies(body)
    assert out["SESSDATA"] == "a%2Cb" and out["bili_jct"] == "j" and out["DedeUserID"] == "42"


def test_extract_login_cookies_empty_on_unsuccessful() -> None:
    assert cs.extract_login_cookies({"data": {"code": 86101}}) == {}
    assert cs.extract_login_cookies(None) == {}


def test_merge_cookies_fresh_wins() -> None:
    merged = cs.merge_cookies({"SESSDATA": "old", "buvid3": "b1"}, {"buvid3": "b2", "empty": ""})
    assert merged == {"SESSDATA": "old", "buvid3": "b2"}  # empty value never overrides


# ---------- netscape writer / reader ----------

def test_write_netscape_roundtrip_and_backup(tmp_path: Path) -> None:
    p = tmp_path / "cookie.txt"
    p.write_text("# old file\n.bilibili.com\tTRUE\t/\tTRUE\t1\tSESSDATA\told\n", encoding="utf-8")
    bak = cs.write_netscape(p, {"SESSDATA": "s1", "buvid3": "b"})
    assert bak is not None and bak.exists()
    raw = p.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")  # no BOM
    text = p.read_text(encoding="utf-8")
    assert ".bilibili.com\tTRUE\t/\tTRUE\t" in text and "\tSESSDATA\ts1\n" in text
    assert "\tbuvid3\tb\n" in text
    # reader understands our own output
    assert cs.read_existing(p)["SESSDATA"] == "s1"


def test_write_netscape_skips_tab_values(tmp_path: Path) -> None:
    p = tmp_path / "cookie.txt"
    cs.write_netscape(p, {"good": "v", "evil": "a\tb"}, backup=False)
    assert "evil" not in cs.read_existing(p)


def test_read_existing_header_style(tmp_path: Path) -> None:
    p = tmp_path / "cookie.txt"
    p.write_text("SESSDATA=h1; buvid3=b\n", encoding="utf-8")
    assert cs.read_existing(p) == {"SESSDATA": "h1", "buvid3": "b"}


# ---------- QR login flow (pure Python, mocked passport transport) ----------

def _mock_transport(monkeypatch: pytest.MonkeyPatch, poll_sequence: list) -> dict:
    calls = {"generate": 0, "poll": 0}

    def fake_get(path, params, cookie_header):
        if path == "qrcode/generate":
            calls["generate"] += 1
            return {"code": 0, "data": {"qrcode_key": f"key{calls['generate']}", "url": f"https://qr/{calls['generate']}"}}
        idx = calls["poll"]
        calls["poll"] += 1
        return poll_sequence[min(idx, len(poll_sequence) - 1)]

    monkeypatch.setattr(cs, "_passport_get", fake_get)
    monkeypatch.setattr(cs, "_render_qr_png", lambda url, log: None)
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    return calls


def test_qr_login_flow_success(monkeypatch: pytest.MonkeyPatch) -> None:
    polls = [
        {"code": 86101, "data": {"code": 86101, "message": "未扫码"}},
        {"code": 86090, "data": {"code": 86090, "message": "已扫待确认"}},
        {"code": 0, "data": {"code": 0,
                             "url": "https://passport.biligame.com/crossDomain?SESSDATA=new%2C1&bili_jct=j",
                             "cookie_info": {"cookies": [{"name": "SESSDATA", "value": "new%2C1"}]}}},
    ]
    calls = _mock_transport(monkeypatch, polls)
    out = cs.qr_login_flow(timeout=30)
    assert out["SESSDATA"] == "new%2C1"
    assert calls["poll"] == 3


def test_qr_login_flow_regenerates_expired_qr(monkeypatch: pytest.MonkeyPatch) -> None:
    polls = [
        {"code": 86038, "data": {"code": 86038, "message": "二维码已失效"}},
        {"code": 0, "data": {"code": 0, "cookie_info": {"cookies": [{"name": "SESSDATA", "value": "fresh"}]}}},
    ]
    calls = _mock_transport(monkeypatch, polls)
    out = cs.qr_login_flow(timeout=30)
    assert out["SESSDATA"] == "fresh" and calls["generate"] == 2  # expired -> re-issued


def test_qr_login_flow_redeems_cross_domain_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-09 实测形状: poll 成功但 data.url 只有 ticket（SESSDATA 不在 URL），
    必须请求 crossDomain 地址从 Set-Cookie 兑换。"""
    polls = [
        {"code": 0, "data": {"code": 0,
                             "url": "https://passport.biligame.com/x/passport-login/web/crossDomain?ticket=7d01ff&gourl=x"}},
    ]
    _mock_transport(monkeypatch, polls)

    redeemed: list = []

    def fake_redeem(url, cookie_header):
        redeemed.append(url)
        return {"SESSDATA": "via-setcookie%2C1", "bili_jct": "j3"}

    monkeypatch.setattr(cs, "_redeem_cross_domain", fake_redeem)
    out = cs.qr_login_flow(timeout=30)
    assert out["SESSDATA"] == "via-setcookie%2C1" and out["bili_jct"] == "j3"
    assert redeemed and "ticket=7d01ff" in redeemed[0]


def test_qr_login_flow_timeout_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_transport(monkeypatch, [{"code": 86101, "data": {"code": 86101, "message": "未扫码"}}])
    assert cs.qr_login_flow(timeout=0.05) == {}


# ---------- CLI tier-1 happy path ----------

def test_cookie_refresh_tier1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from bilibili_get import cli
    from bilibili_opencli import bridge

    p = tmp_path / "cookie.txt"
    p.write_text(".bilibili.com\tTRUE\t/\tTRUE\t9999999999\tSESSDATA\tcarry\n", encoding="utf-8")

    monkeypatch.setattr(bridge, "_AVAILABLE_CACHE", {})
    monkeypatch.setattr(bridge, "available", lambda: True)
    monkeypatch.setattr(cs, "browser_cookies", lambda session="x": {"buvid3": "fresh", "bili_jct": "j"})
    monkeypatch.setattr(cs, "validate_login", lambda path, proxy=None: True)
    monkeypatch.setattr(cs, "qr_login_flow", lambda *a, **k: pytest.fail("tier1 ok must not invoke QR"))

    args = cli.build_parser().parse_args(["cookie-refresh", "--cookies", str(p)])
    assert cli._cookie_refresh_cmd(args) == 0
    got = cs.read_existing(p)
    assert got["SESSDATA"] == "carry" and got["buvid3"] == "fresh" and got["bili_jct"] == "j"
    baks = list(tmp_path.glob("cookie.txt.bak-*"))
    assert len(baks) == 1 and baks[0].read_text(encoding="utf-8").startswith(".bilibili.com")
