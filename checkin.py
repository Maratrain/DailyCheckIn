# -*- coding: utf-8 -*-
"""每日打卡签到小工具

功能：
  1. 米游社：米游币打卡（分区打卡）+ 游戏签到领福利（原神/星穹铁道/绝区零/崩坏3）
  2. 库街区：每日补给签到（鸣潮/战双帕弥什）

用法：
  python checkin.py login          # 首次使用：米游社扫码登录 + 库街区短信登录
  python checkin.py                # 执行今日打卡签到（等价于 run）
  python checkin.py test           # 只查询不执行，校验登录状态是否有效
  双击 install_task.bat 可注册每天 08:05 及开机自动执行的 Windows 计划任务

接口参考（现网可用实现）：
  米游社  https://github.com/Womsxd/MihoyoBBSTools   （2026-06 仍在维护）
  库街区  https://github.com/mxyooR/Kuro-autosignin  +  https://github.com/mxyooR/Kuro_login
"""

import argparse
import base64
import hashlib
import io
import json
import logging
import os
import random
import re
import socket
import string
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
LOG_DIR = ROOT / "logs"

TIMEOUT = 20

# ──────────────────────────── 米游社常量 ────────────────────────────
# Salt 与版本相互对应，跟随米游社客户端更新（参考 MihoyoBBSTools 2026-06-30 更新）
MI_SALT_APP = "47f15f1b66bee46b816115d8e8e6ebb6"
MI_SALT_WEB = "d9200c846b10886e8c874fc33c8f308b"
MI_SALT_X6 = "t0qEgfub6cvueAPgR5m9aQWWVciEer7v"
MI_VERSION = "2.109.0"
MI_UA = (
    "Mozilla/5.0 (Linux; Android 12; Unspecified Device) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/103.0.5060.129 Mobile Safari/537.36 "
    f"miHoYoBBS/{MI_VERSION}"
)

MI_QR_CREATE_URL = "https://passport-api.mihoyo.com/account/ma-cn-passport/app/createQRLogin"
MI_QR_QUERY_URL = "https://passport-api.mihoyo.com/account/ma-cn-passport/app/queryQRLoginStatus"
MI_COOKIE_TOKEN_URL = "https://api-takumi.mihoyo.com/auth/api/getCookieAccountInfoBySToken"
MI_ROLES_URL = "https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie"
MI_TASKS_URL = "https://bbs-api.miyoushe.com/apihub/wapi/getUserMissionsState"
MI_BBS_SIGN_URL = "https://bbs-api.miyoushe.com/apihub/app/api/signIn"
MI_SMS_SEND_URL = "https://passport-api.mihoyo.com/account/ma-cn-verifier/verifier/createLoginCaptcha"
MI_SMS_LOGIN_URL = "https://passport-api.mihoyo.com/account/ma-cn-passport/app/loginByMobileCaptcha"
MI_COOKIE_TOKEN_V2_URL = "https://passport-api.mihoyo.com/account/auth/api/getCookieAccountInfoBySToken"
MI_PASSPORT_X4_SALT = "xV8v4Qu54lUKrEYFZkJhB8cuOh9Asafs"
MI_RSA_KEY_URLS = [
    "https://passport-api.mihoyo.com/account/ma-cn-passport/app/getRSAKey",
    "https://passport-api.mihoyo.com/account/ma-cn-passport/app/get_by_rsa_key",
    "https://passport-api.mihoyo.com/account/ma-cn-passport/web/rsa_public_key",
]

# 游戏签到：act_id 来自 MihoyoBBSTools setting.py，base 为官方签到活动域名
# extra_headers：米哈游对原神(hk4e)/绝区零(zzz)的签到接口校验专属请求头，缺失会报 -500001
MI_GAMES = {
    "genshin": {"name": "原神", "biz": "hk4e_cn", "act_id": "e202311201442471",
                "base": "https://api-takumi.mihoyo.com/event/luna",
                "extra_headers": {"x-rpc-signgame": "hk4e"}},
    "honkaisr": {"name": "崩坏：星穹铁道", "biz": "hkrpg_cn", "act_id": "e202304121516551",
                 "base": "https://api-takumi.mihoyo.com/event/luna",
                 "extra_headers": {}},
    "zzz": {"name": "绝区零", "biz": "nap_cn", "act_id": "e202406242138391",
            "base": "https://act-nap-api.mihoyo.com/event/luna/zzz",
            "extra_headers": {"x-rpc-signgame": "zzz"}},
    "honkai3rd": {"name": "崩坏3", "biz": "bh3_cn", "act_id": "e202306201626331",
                  "base": "https://api-takumi.mihoyo.com/event/luna",
                  "extra_headers": {}},
}

# 米游币打卡分区（id 与名称来自 MihoyoBBSTools mihoyobbs_List）
MI_BBS_PARTITIONS = {
    "1": "崩坏3", "2": "原神", "3": "崩坏2", "4": "未定事件簿",
    "5": "大别野", "6": "崩坏：星穹铁道", "8": "绝区零",
    "9": "崩坏：因缘精灵", "10": "星布谷地",
}

# ──────────────────────────── 库街区常量 ────────────────────────────
KURO_BASE = "https://api.kurobbs.com"
KURO_SDK_LOGIN_URL = f"{KURO_BASE}/user/sdkLogin"
KURO_MINE_URL = f"{KURO_BASE}/user/mineV2"
KURO_ROLE_LIST_URL = f"{KURO_BASE}/user/role/findRoleList"
KURO_GAME_SIGN_URL = f"{KURO_BASE}/encourage/signIn/v2"
KURO_GAME_SIGN_RECORD_URL = f"{KURO_BASE}/encourage/signIn/queryRecordV2"

KURO_GAMES = {
    "wuwa": {"name": "鸣潮", "game_id": "3", "server_id": "76402e5b20be2c39f095a152090afddc"},
    "pgr": {"name": "战双帕弥什", "game_id": "2", "server_id": "1000"},
}

KURO_GAME_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) KuroGameBox/2.2.0"
)

# ──────────────────────────── 微博超话常量 ────────────────────────────
# 走 m.weibo.cn 移动网页版容器接口（参考 swtmaxx/weibo-auto-checkin 2026-09 实现）
WB_BASE = "https://m.weibo.cn"
WB_FOLLOW_REFERER = f"{WB_BASE}/p/index?containerid=100803_-_followsuper"
WB_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

log = logging.getLogger("checkin")


# ════════════════════════════ 通用工具 ════════════════════════════

def md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def ds1() -> str:
    """米游社网页端 DS 签名（salt={web}&t={t}&r={r}）"""
    t = str(int(time.time()))
    r = "".join(random.sample(string.ascii_lowercase + string.digits, 6))
    return f"{t},{r},{md5(f'salt={MI_SALT_WEB}&t={t}&r={r}')}"


def ds2(body: str, query: str = "") -> str:
    """米游社 App 端 DS2 签名（salt={x6}&t={t}&r={r}&b={body}&q={query}）"""
    t = str(int(time.time()))
    r = str(random.randint(100001, 200000))
    return f"{t},{r},{md5(f'salt={MI_SALT_X6}&t={t}&r={r}&b={body}&q={query}')}"


def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "192.168.1.101"


def setup_logging(console: bool = True) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    log_file = logging.FileHandler(LOG_DIR / f"checkin_{datetime.now():%Y-%m-%d}.log", encoding="utf-8")
    log_file.setFormatter(fmt)
    if console:
        log.addHandler(console_handler)
    log.addHandler(log_file)


def load_config() -> dict:
    default = {
        "proxy": "",
        "mihoyo": {
            "enabled": True,
            "device_id": "",
            "stoken": "", "stuid": "", "mid": "",
            "cookie_token": "", "account_id": "",
            "bbs_checkin": True,
            "bbs_gids": ["2", "6", "8"],
            "game_sign": ["genshin", "honkaisr", "zzz"],
        },
        "kuro": {
            "enabled": True,
            "token": "", "user_id": "",
            "devcode": "", "distinct_id": "",
            "games": ["wuwa", "pgr"],
        },
        "weibo": {
            "enabled": True,
            "cookie": "",
        },
    }
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        for section, values in default.items():
            if not isinstance(values, dict):
                saved.setdefault(section, values)
                continue
            saved.setdefault(section, {})
            for key, val in values.items():
                saved[section].setdefault(key, val)
        return saved
    return default


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# 统一使用的会话：忽略系统/环境变量代理（米哈游与库街区均为国内接口，
# 走代理容易被墙外节点拖死导致长时间无响应）；如需代理可在 config.json 配 proxy 字段
SESSION = requests.Session()
SESSION.trust_env = False


def _current_proxy() -> str:
    try:
        return CONFIG.get("proxy", "") or ""
    except NameError:
        return ""


def http_request(method: str, url: str, **kwargs) -> dict:
    """带重试的请求，返回 JSON dict；网络异常时返回 {"retcode": -1, "message": ...}

    timeout 为 (连接, 读取) 二元组：连接 7 秒快速失败，避免网络异常时长时间静默。
    """
    kwargs.setdefault("timeout", (7, TIMEOUT))
    proxy = _current_proxy()
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    for attempt in range(1, 4):
        try:
            resp = SESSION.request(method, url, **kwargs)
            return resp.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            if attempt == 3:
                log.warning(f"请求失败（已重试 3 次）{url}：{e}")
                return {"retcode": -1, "message": str(e)}
            log.warning(f"请求异常（第 {attempt} 次），3 秒后重试：{type(e).__name__}: {e}")
            time.sleep(3)


# ════════════════════════════ 米游社部分 ════════════════════════════

def mi_stoken_cookie(m: dict) -> str:
    cookie = f"stoken={m['stoken']};stuid={m['stuid']}"
    if m["stoken"].startswith("v2_"):
        cookie += f";mid={m['mid']}"
    return cookie


def mi_web_cookie(m: dict) -> str:
    account_id = m.get("account_id") or m.get("stuid", "")
    return f"account_id={account_id};cookie_token={m['cookie_token']}"


def mi_web_headers(m: dict) -> dict:
    return {
        "Accept": "application/json, text/plain, */*",
        "DS": ds1(),
        "x-rpc-channel": "miyousheluodi",
        "Origin": "https://webstatic.mihoyo.com",
        "x-rpc-app_version": MI_VERSION,
        "User-Agent": MI_UA,
        "x-rpc-client_type": "5",
        "Referer": "https://act.mihoyo.com/",
        "Accept-Language": "zh-CN,en-US;q=0.8",
        "X-Requested-With": "com.mihoyo.hyperion",
        "Cookie": mi_web_cookie(m),
        "x-rpc-device_id": m["device_id"],
    }


def mi_app_headers(m: dict, body: str) -> dict:
    return {
        "DS": ds2(body),
        "cookie": mi_stoken_cookie(m),
        "x-rpc-client_type": "2",
        "x-rpc-app_version": MI_VERSION,
        "x-rpc-app_id": "bll8iq97cem8",
        "x-rpc-sys_version": "12",
        "x-rpc-channel": "miyousheluodi",
        "x-rpc-device_id": m["device_id"],
        "x-rpc-device_name": "ZCode-Tool",
        "x-rpc-device_model": "PC",
        "x-rpc-h265_supported": "1",
        "Referer": "https://app.mihoyo.com",
        "x-rpc-verify_key": "bll8iq97cem8",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept-Encoding": "gzip",
        "User-Agent": "okhttp/4.9.3",
    }


def ds_x4(query: str = "") -> str:
    """passport 接口专用 DS 签名（x4 salt，DS2 风格）"""
    t = str(int(time.time()))
    r = str(random.randint(100000, 200000))
    return f"{t},{r},{md5(f'salt={MI_PASSPORT_X4_SALT}&t={t}&r={r}&b=&q={query}')}"


def mi_exchange_cookie_token(m: dict) -> bool:
    """新登录的 stoken 换 cookie_token：走 passport 专用接口（对齐 MiyoQian 现网实现）。

    旧接口 api-takumi/auth/api 对刚签发的 stoken 会拒绝（2026-09 收紧），
    此接口要求 stoken 同时出现在查询参数与 cookie 中，并携带 DS(x4) 签名。
    """
    query = f"stoken={m['stoken']}"
    headers = {
        "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) miHoYoBBS/{MI_VERSION}",
        "x-rpc-app_version": MI_VERSION,
        "x-rpc-client_type": "2",
        "x-requested-with": "com.mihoyo.hyperion",
        "Referer": "https://webstatic.mihoyo.com",
        "x-rpc-device_id": m["device_id"],
        "x-rpc-device_fp": "38d814469b1e4",
        "cookie": f"mid={m['mid']};stoken={m['stoken']}",
        "ds": ds_x4(query),
        "x-rpc-aigis": "",
    }
    data = http_request("GET", MI_COOKIE_TOKEN_V2_URL, headers=headers,
                        params={"stoken": m["stoken"]})
    if data.get("retcode") == 0 and data.get("data", {}).get("cookie_token"):
        m["cookie_token"] = data["data"]["cookie_token"]
        if not m.get("account_id"):
            m["account_id"] = m.get("stuid", "")
        save_config(CONFIG)
        log.info("米游社 cookie_token 已通过 passport 接口兑换成功")
        return True
    log.warning(f"passport 兑换 cookie_token 失败：{data.get('retcode')} {data.get('message')}")
    return False


def mi_refresh_cookie_token(m: dict) -> bool:
    """用 stoken 换新的 cookie_token：先走 passport 兑换接口，失败回退旧接口"""
    if not m.get("stoken"):
        return False
    if mi_exchange_cookie_token(m):
        return True
    # 回退：旧接口（对已建立会话的 stoken 通常有效）
    data = http_request("GET", MI_COOKIE_TOKEN_URL,
                        headers={"Accept": "application/json",
                                 "Cookie": mi_stoken_cookie(m)})
    if data.get("retcode") == 0 and data.get("data", {}).get("cookie_token"):
        m["cookie_token"] = data["data"]["cookie_token"]
        if not m.get("account_id"):
            m["account_id"] = m.get("stuid", "")
        save_config(CONFIG)
        log.info("米游社 cookie_token 已自动刷新")
        return True
    log.warning(f"cookie_token 续期失败：{data.get('retcode')} {data.get('message')}")
    return False


def _der_read(data: bytes, idx: int):
    """读取单个 DER TLV 节点，返回 (tag, value, 下一节点偏移)"""
    tag = data[idx]
    idx += 1
    length = data[idx]
    idx += 1
    if length & 0x80:
        num = length & 0x7F
        length = int.from_bytes(data[idx:idx + num], "big")
        idx += num
    return tag, data[idx:idx + length], idx + length


# 米哈游 passport 体系通用 RSA 公钥（1024bit，社区通用值；服务端拉取接口已 404，故作为兜底）
MI_RSA_FALLBACK_KEY = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDDvekdPMHN3AYhm/vktJT+YJr7"
    "cI5DcsNKqdsx5DZX0gDuWFuIjzdwButrIYPNmRJ1G8ybDIF7oDW2eEpm5sMbL9zs"
    "9ExXCdvqrn51qELbqj0XxtMTIpaCHFSI50PfPpTFV9Xt/hmyVwokoOXFlAEgCn+Q"
    "CgGs52bFoYMtyi+xEQIDAQAB"
)


def mi_fetch_rsa_key() -> str:
    """获取 RSA 公钥（base64 DER）：先尝试服务端，失败用内置公钥"""
    for url in MI_RSA_KEY_URLS:
        try:
            resp = SESSION.get(url, timeout=10)
            j = resp.json()
            data = j.get("data") or {}
            key = data.get("public_key") or data.get("rsa_key") or j.get("public_key") or j.get("rsa_key")
            if key:
                return key
        except Exception:
            continue
    return MI_RSA_FALLBACK_KEY


def rsa_encrypt(data: str, b64_der: str) -> str:
    """RSA PKCS#1 v1.5 加密（米哈游 passport 短信登录用），纯标准库实现"""
    der = base64.b64decode(b64_der)
    _, spki, _ = _der_read(der, 0)
    _, _, off = _der_read(spki, 0)
    _, bitstr, _ = _der_read(spki, off)
    if not bitstr or bitstr[0] != 0:
        raise ValueError("RSA 公钥格式异常")
    _, seq, _ = _der_read(bitstr[1:], 0)
    _, n_bytes, off = _der_read(seq, 0)
    _, e_bytes, _ = _der_read(seq, off)
    n = int.from_bytes(n_bytes, "big")
    e = int.from_bytes(e_bytes, "big")
    k = (n.bit_length() + 7) // 8
    msg = data.encode()
    if len(msg) > k - 11:
        raise ValueError("待加密内容过长")
    padding = bytes(random.randint(1, 255) for _ in range(k - 3 - len(msg)))
    em = b"\x00\x02" + padding + b"\x00" + msg
    cipher = pow(int.from_bytes(em, "big"), e, n)
    return base64.b64encode(cipher.to_bytes(k, "big")).decode()


def mi_sms_headers(device_id: str) -> dict:
    """短信登录专用请求头。

    必须使用米游社渠道（x-rpc-app_id=bll8iq97cem8）：stoken 按签发渠道限定作用域，
    用其他渠道（如游戏 SDK）登录拿到的 token 米游社接口不认（对齐 starudream/sign-task 实现）。
    """
    return {
        "Content-Type": "application/json",
        "User-Agent": MI_UA,
        "Referer": "https://app.mihoyo.com",
        "x-rpc-app_version": MI_VERSION,
        "x-rpc-app_id": "bll8iq97cem8",
        "x-rpc-verify_key": "bll8iq97cem8",
        "x-rpc-device_id": device_id,
        "x-rpc-client_type": "2",
        "x-rpc-device_name": "LAPTOP-TOOL",
        "x-rpc-device_model": "PC",
        "x-rpc-sys_version": "Android 12",
        "x-rpc-channel": "miyousheluodi",
        "x-rpc-device_fp": "38d814469b1e4",
    }


def mi_sms_send(mobile: str) -> tuple:
    """发送米游社登录验证码，返回 (是否成功, 提示信息)。供 CLI 与 GUI 共用。"""
    m = CONFIG["mihoyo"]
    if not m["device_id"]:
        m["device_id"] = str(uuid.uuid4())
    mobile = (mobile or "").strip().replace("+86", "").strip()
    if not mobile:
        return False, "手机号为空"
    try:
        pub_key = mi_fetch_rsa_key()
    except Exception as e:
        return False, str(e)
    enc_phone = rsa_encrypt(mobile, pub_key)
    enc_area = rsa_encrypt("+86", pub_key)
    send = http_request("POST", MI_SMS_SEND_URL, headers=mi_sms_headers(m["device_id"]),
                        data=json.dumps({"area_code": enc_area, "mobile": enc_phone},
                                        separators=(",", ":")))
    rc = send.get("retcode")
    if rc == 0:
        return True, "验证码已发送，请查看手机短信"
    if rc == -3101:
        return False, "发送验证码需要人机验证：请在浏览器打开 https://user.mihoyo.com/#/login，" \
                      "输入同一手机号完成滑块并点「获取验证码」（不要点登录）"
    return False, f"发送失败（{rc} {send.get('message')}），可改在 user.mihoyo.com 网页端获取验证码"


def mi_sms_verify(mobile: str, code: str) -> tuple:
    """用 手机号+验证码 完成米游社登录，返回 (是否成功, 提示信息)。供 CLI 与 GUI 共用。"""
    m = CONFIG["mihoyo"]
    mobile = (mobile or "").strip().replace("+86", "").strip()
    code = (code or "").strip()
    if not mobile or not code:
        return False, "手机号或验证码为空"
    try:
        pub_key = mi_fetch_rsa_key()
    except Exception as e:
        return False, str(e)
    enc_phone = rsa_encrypt(mobile, pub_key)
    enc_area = rsa_encrypt("+86", pub_key)
    result = http_request("POST", MI_SMS_LOGIN_URL, headers=mi_sms_headers(m["device_id"]),
                          data=json.dumps({"area_code": enc_area, "action_type": "login_by_mobile_captcha",
                                           "captcha": code, "mobile": enc_phone},
                                          separators=(",", ":")))
    if result.get("retcode") == 0:
        info = result["data"]["user_info"]
        m["stoken"] = result["data"]["token"]["token"]
        m["stuid"] = str(info["aid"])
        m["mid"] = str(info["mid"])
        m["account_id"] = m["stuid"]
        # 先保存 stoken 再兑换 cookie_token，避免兑换失败时丢弃登录成果
        save_config(CONFIG)
        msg = f"米游社短信登录成功！stoken 已保存（uid={m['stuid']}）"
        if mi_refresh_cookie_token(m):
            msg += "，cookie_token 已兑换，米游币打卡与游戏签到全部可用"
        else:
            msg += "，cookie_token 稍后自动重试（米游币打卡不受影响）"
        return True, msg
    return False, (f"短信登录失败：{result.get('retcode')} {result.get('message')}"
                   f"（验证码 5 分钟内有效，可重试）")


def mi_sms_login() -> None:
    """米游社短信验证码登录（CLI 交互封装）"""
    mobile = input("输入米哈游账号手机号：")
    ok, msg = mi_sms_send(mobile)
    if ok:
        log.info(msg)
    else:
        log.warning(msg)
        try:
            os.startfile("https://user.mihoyo.com/#/login")
        except Exception:
            pass
    code = input("输入收到的短信验证码：")
    ok, msg = mi_sms_verify(mobile, code)
    (log.info if ok else log.error)(msg)


def mi_qr_login() -> None:
    """米游社 App 扫码登录，获取 stoken 并换出 cookie_token。

    注意：2026-09 起米哈游限制了扫码获取的 stoken（仅够换取 cookie_token 做游戏签到），
    米游币打卡需要完整权限 stoken，请优先使用短信验证码登录。
    """
    m = CONFIG["mihoyo"]
    if not m["device_id"]:
        m["device_id"] = str(uuid.uuid4())

    def passport_headers() -> dict:
        return {"Content-Type": "application/json",
                "x-rpc-app_id": "dw9y09jqjpxc",
                "x-rpc-device_id": m["device_id"]}

    def create_ticket():
        data = http_request("POST", MI_QR_CREATE_URL, headers=passport_headers(), json={})
        if data.get("retcode") == 0:
            return data["data"]["url"], data["data"]["ticket"]
        log.error(f"创建登录二维码失败：{data.get('message')}")
        return None

    def show_qr(url: str) -> None:
        print("\n请用「米游社 App」扫描二维码登录（App → 我 → 左上角扫一扫）：")
        try:
            import qrcode
            qr = qrcode.QRCode(border=1, box_size=1)
            qr.add_data(url)
            qr.make(fit=True)
            buf = io.StringIO()
            qr.print_ascii(out=buf, invert=True)
            print(buf.getvalue())
            html = ROOT / "login_qr.html"
            html.write_text(
                '<html><body style="text-align:center;margin-top:60px">'
                "<h3>米游社扫码登录</h3><pre style=\"line-height:1.1;font-size:8px\">"
                f"{buf.getvalue()}</pre><p>请用米游社 App 扫描</p></body></html>",
                encoding="utf-8")
            os.startfile(html)
            print(f"（终端二维码不清晰时，已用浏览器打开 {html.name}，扫那里的二维码）")
        except ImportError:
            print(f"未安装 qrcode 库，无法在终端出二维码（可执行 pip install qrcode 后重试）")
        print(f"也可以把这个链接发到手机上，用手机浏览器打开后确认登录：\n{url}\n")

    log.info("正在生成米游社登录二维码...")
    ticket_info = create_ticket()
    if not ticket_info:
        return
    qr_url, ticket = ticket_info
    show_qr(qr_url)

    scanned_hint = False
    deadline = time.time() + 300
    refresh_count = 0
    while time.time() < deadline:
        data = http_request("POST", MI_QR_QUERY_URL, headers=passport_headers(),
                            json={"ticket": ticket})
        status = data.get("data", {}).get("status", "")
        if status == "Confirmed":
            info = data["data"].get("user_info", {})
            tokens = data["data"].get("tokens", [])
            if not tokens:
                log.error("登录确认成功但未返回 stoken，请重试")
                return
            m["stuid"] = str(info.get("aid", ""))
            m["mid"] = str(info.get("mid", ""))
            m["stoken"] = tokens[0]["token"]
            if mi_refresh_cookie_token(m):
                save_config(CONFIG)
                log.info(f"米游社扫码登录成功！账号 uid={m['stuid']}")
            return
        if status == "Scanned" and not scanned_hint:
            log.info("已扫码，请在手机上确认登录...")
            scanned_hint = True
        if status == "Expired":
            refresh_count += 1
            if refresh_count > 3:
                log.error("二维码多次过期仍无人扫码，退出登录流程")
                return
            log.info("二维码已过期，正在重新生成...")
            ticket_info = create_ticket()
            if not ticket_info:
                return
            qr_url, ticket = ticket_info
            show_qr(qr_url)
            scanned_hint = False
        time.sleep(2.5)
    log.error("等待扫码超时（5 分钟），退出登录流程")


def mi_roles_call(m: dict) -> dict:
    return http_request("GET", MI_ROLES_URL, headers=mi_web_headers(m),
                        params={"game_biz": "hk4e_cn"})


def mi_ensure_web_auth(m: dict) -> bool:
    """确保网页端登录态可用；失效时用 stoken 续期一次并延迟重试。

    注意：每次换取 cookie_token 都会使旧 token 作废（服务端单活轮换），
    因此不要在无关流程里调用换取接口，续期后稍等片刻再重试。
    """
    log.info("正在检查米游社登录态...")
    if m.get("cookie_token") and mi_roles_call(m).get("retcode") == 0:
        return True
    log.info("cookie_token 无效，尝试用 stoken 续期...")
    if not mi_refresh_cookie_token(m):
        return False
    time.sleep(2)
    return mi_roles_call(m).get("retcode") == 0


def mi_bbs_checkin(m: dict) -> bool:
    """米游币打卡：逐个分区调用签到接口"""
    all_ok = True
    for gid in m["bbs_gids"]:
        name = MI_BBS_PARTITIONS.get(str(gid), f"分区{gid}")
        log.info(f"正在米游币打卡 [{name}]...")
        body = json.dumps({"gids": str(gid)}, separators=(",", ":"))
        data = http_request("POST", MI_BBS_SIGN_URL,
                            headers=mi_app_headers(m, body), data=body)
        code = data.get("retcode")
        if code == 0:
            log.info(f"米游币打卡 [{name}]：成功")
        elif code == 1008:
            log.info(f"米游币打卡 [{name}]：今日已打卡")
        elif code == 1034:
            log.warning(f"米游币打卡 [{name}]：触发风控验证码，今天请在 App 里手动打卡一次")
            all_ok = False
        elif code == -100:
            log.error(f"米游币打卡 [{name}]：stoken 无权限或已失效"
                      f"（扫码获取的 token 已被米哈游限制权限，请用短信验证码方式重新登录："
                      f"python checkin.py login）")
            return False
        else:
            msg = data.get("message", "")
            if "已" in msg:
                log.info(f"米游币打卡 [{name}]：今日已打卡")
            else:
                log.warning(f"米游币打卡 [{name}]：失败（{code} {msg}）")
                all_ok = False
        time.sleep(random.randint(2, 6))

    # 顺便汇报今日米游币获取情况（失败不影响主流程）
    tasks = http_request("GET", MI_TASKS_URL,
                         headers={"Accept": "application/json, text/plain, */*",
                                  "User-Agent": MI_UA,
                                  "Referer": "https://webstatic.mihoyo.com",
                                  "Cookie": mi_web_cookie(m)},
                         params={"point_sn": "myb"})
    if tasks.get("retcode") == 0:
        t = tasks["data"]
        log.info(f"米游币：今日已获得 {t.get('already_received_points', '?')}，"
                 f"还可获取 {t.get('can_get_points', '?')}，当前共 {t.get('total_points', '?')}")
    return all_ok


def mi_game_sign(m: dict) -> bool:
    """游戏签到领福利：原神/星穹铁道/绝区零/崩坏3"""
    all_ok = True
    for game_key in m["game_sign"]:
        game = MI_GAMES.get(game_key)
        if not game:
            log.warning(f"未知游戏配置项：{game_key}，已跳过")
            continue
        name = game["name"]
        log.info(f"正在检查 {name} 签到...")

        roles_data = http_request("GET", MI_ROLES_URL,
                                  headers=mi_web_headers(m),
                                  params={"game_biz": game["biz"]})
        if roles_data.get("retcode") == -100 and mi_ensure_web_auth(m):
            roles_data = http_request("GET", MI_ROLES_URL,
                                      headers=mi_web_headers(m),
                                      params={"game_biz": game["biz"]})
        if roles_data.get("retcode") != 0:
            log.warning(f"{name}：获取账号角色列表失败（{roles_data.get('retcode')} "
                        f"{roles_data.get('message')}），可能未绑定该游戏")
            all_ok = False
            continue
        roles = roles_data.get("data", {}).get("list", [])
        if not roles:
            log.info(f"{name}：账号未绑定该游戏，跳过")
            continue

        headers = mi_web_headers(m)
        headers.update(game.get("extra_headers", {}))
        rewards = []
        home = http_request("GET", f"{game['base']}/home",
                            headers=headers,
                            params={"lang": "zh-cn", "act_id": game["act_id"]})
        if home.get("retcode") == 0:
            rewards = home["data"].get("awards", [])

        for role in roles:
            uid, region, nick = role["game_uid"], role["region"], role["nickname"]
            time.sleep(random.randint(2, 6))
            info = http_request("GET", f"{game['base']}/info",
                                headers=headers,
                                params={"lang": "zh-cn", "act_id": game["act_id"],
                                        "region": region, "uid": uid})
            if info.get("retcode") != 0:
                log.warning(f"{name} [{nick}]：获取签到信息失败（{info.get('message')}）")
                all_ok = False
                continue
            info_data = info["data"]
            if info_data.get("first_bind"):
                log.warning(f"{name} [{nick}]：首次绑定米游社，请先手动签到一次")
                continue

            total_day = info_data.get("total_sign_day", 0)
            if info_data.get("is_sign"):
                award = rewards[total_day - 1] if rewards and total_day <= len(rewards) else {}
                log.info(f"{name} [{nick}]：今日已签到"
                         + (f"，奖励「{award.get('name')}」x{award.get('cnt')}" if award else ""))
                continue

            time.sleep(random.randint(2, 6))
            sign = http_request("POST", f"{game['base']}/sign",
                                headers=headers,
                                json={"act_id": game["act_id"], "region": region, "uid": uid})
            if sign.get("retcode") == 0 and sign.get("data", {}).get("success") == 1:
                award = rewards[total_day] if rewards and total_day < len(rewards) else {}
                log.info(f"{name} [{nick}]：签到成功（本月第 {total_day + 1} 天）"
                         + (f"，获得「{award.get('name')}」x{award.get('cnt')}" if award else ""))
            elif sign.get("retcode") == 0:
                log.info(f"{name} [{nick}]：签到完成")
            else:
                msg = sign.get("message", "")
                if "已" in msg:
                    log.info(f"{name} [{nick}]：今日已签到")
                else:
                    log.warning(f"{name} [{nick}]：签到失败（{sign.get('retcode')} {msg}）")
                    all_ok = False
    return all_ok


def mihoyo_run() -> bool:
    m = CONFIG["mihoyo"]
    if not m["enabled"]:
        log.info("米游社模块未启用，跳过")
        return True
    if not m["stoken"]:
        log.warning("米游社尚未登录（缺少 stoken），请先执行：python checkin.py login")
        return False

    ok = True
    # 米游币打卡只依赖 stoken，放在最前且不依赖网页端登录态
    if m["bbs_checkin"]:
        ok = mi_bbs_checkin(m) and ok
    if mi_ensure_web_auth(m):
        ok = mi_game_sign(m) and ok
    else:
        log.error("米游社网页端登录态不可用，本次跳过游戏签到（cookie_token 会在下次运行时自动重试）")
        ok = False
    return ok


# ════════════════════════════ 库街区部分 ════════════════════════════

def kuro_user_headers(k: dict) -> dict:
    return {
        "osversion": "Android",
        "countrycode": "CN",
        "ip": get_lan_ip(),
        "model": "2211133C",
        "source": "android",
        "lang": "zh-Hans",
        "version": "1.0.9",
        "versioncode": "1090",
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": "okhttp/3.10.0",
        "devcode": k["devcode"],
        "distinct_id": k["distinct_id"],
        "token": k["token"],
    }


def kuro_game_headers(k: dict) -> dict:
    return {
        "Accept": "application/json, text/plain, */*",
        "source": "ios",
        "Origin": "https://web-static.kurobbs.com",
        "User-Agent": KURO_GAME_UA,
        "devCode": f"{get_lan_ip()}, {KURO_GAME_UA}",
        "token": k["token"],
        "content-type": "application/x-www-form-urlencoded",
    }


def kuro_request(url: str, headers: dict, data: dict) -> dict:
    result = http_request("POST", url, headers=headers, data=data)
    if not isinstance(result, dict) or "code" not in result:
        return {"code": -1, "message": "响应解析失败", "data": None}
    return result


def kuro_sms_verify(mobile: str, code: str) -> tuple:
    """用 手机号+验证码 完成库街区登录，返回 (是否成功, 提示信息)。供 CLI 与 GUI 共用。

    验证码需先在库街区官网（https://www.kurobbs.com/）登录弹窗里获取。
    """
    k = CONFIG["kuro"]
    mobile = (mobile or "").strip()
    code = (code or "").strip()
    if not mobile or not code:
        return False, "手机号或验证码为空"
    if not k["devcode"]:
        k["devcode"] = uuid.uuid4().hex
    if not k["distinct_id"]:
        k["distinct_id"] = uuid.uuid4().hex

    headers = {
        "osversion": "Android",
        "devcode": k["devcode"],
        "distinct_id": k["distinct_id"],
        "countrycode": "CN",
        "ip": get_lan_ip(),
        "model": "2211133C",
        "source": "android",
        "lang": "zh-Hans",
        "version": "1.0.9",
        "versioncode": "1090",
        "content-type": "application/x-www-form-urlencoded",
        "accept-encoding": "gzip",
        "user-agent": "okhttp/3.10.0",
    }
    data = {"code": code, "devCode": k["devcode"], "gameList": "", "mobile": mobile}
    result = kuro_request(KURO_SDK_LOGIN_URL, headers, data)
    if result.get("code") == 200 and result.get("data", {}).get("token"):
        k["token"] = str(result["data"]["token"])
        k["user_id"] = str(result["data"].get("userId", ""))
        save_config(CONFIG)
        return True, f"库街区登录成功！用户 ID={k['user_id']}"
    return False, f"库街区登录失败：{result.get('message', result)}（验证码可能已过期，请重试）"


def kuro_sms_login() -> None:
    """库街区短信登录（CLI 交互封装）"""
    print("\n─── 库街区登录 ───")
    print("第一步：浏览器已打开库街区官网 https://www.kurobbs.com/")
    print("        → 点右上角「登录」→ 输入手机号 → 完成滑块验证 → 点「获取验证码」")
    print("        → 收到短信验证码即可，网页上的「登录」按钮不用点")
    try:
        os.startfile("https://www.kurobbs.com/")
    except Exception:
        print("        （浏览器未自动打开，请手动访问 https://www.kurobbs.com/ ）")
    mobile = input("第二步：输入手机号：")
    code = input("第三步：输入收到的短信验证码：")
    ok, msg = kuro_sms_verify(mobile, code)
    (log.info if ok else log.error)(msg)


def kuro_sign() -> bool:
    k = CONFIG["kuro"]
    if not k["enabled"]:
        log.info("库街区模块未启用，跳过")
        return True
    if not k["token"]:
        log.warning("库街区尚未登录（缺少 token），请先执行：python checkin.py login")
        return False

    log.info("正在检查库街区登录态...")
    mine = kuro_request(KURO_MINE_URL, kuro_user_headers(k), {})
    if mine.get("code") == 220:
        log.error("库街区 token 已过期，请重新执行：python checkin.py login")
        return False
    user_id = k["user_id"]
    if mine.get("code") == 200 and mine.get("data", {}).get("mine", {}).get("userId"):
        user_id = str(mine["data"]["mine"]["userId"])
        if user_id != k["user_id"]:
            k["user_id"] = user_id
            save_config(CONFIG)
    if not user_id:
        log.error("库街区：无法获取用户 ID，请重新登录")
        return False

    all_ok = True
    current_month = datetime.now().strftime("%m")
    for game_key in k["games"]:
        game = KURO_GAMES.get(game_key)
        if not game:
            log.warning(f"未知库街区游戏配置项：{game_key}，已跳过")
            continue
        name = game["name"]
        log.info(f"正在处理库街区 {name} 每日补给...")

        roles = kuro_request(KURO_ROLE_LIST_URL, kuro_user_headers(k),
                             {"gameId": game["game_id"]})
        role_list = roles.get("data") if roles.get("code") == 200 else None
        if not role_list:
            log.info(f"库街区 {name}：未查询到绑定角色，跳过")
            continue

        for role in role_list:
            role_id = str(role.get("roleId", ""))
            server_id = str(role.get("serverId", "")) or game["server_id"]
            role_name = role.get("roleName", role_id)
            time.sleep(random.randint(2, 6))

            sign_data = {
                "gameId": game["game_id"],
                "serverId": server_id,
                "roleId": role_id,
                "userId": user_id,
                "reqMonth": current_month,
            }
            result = kuro_request(KURO_GAME_SIGN_URL, kuro_game_headers(k), sign_data)
            code = result.get("code")
            if code == 200:
                reward = ""
                record = kuro_request(KURO_GAME_SIGN_RECORD_URL, kuro_game_headers(k),
                                      {key: val for key, val in sign_data.items()
                                       if key != "reqMonth"})
                if record.get("code") == 200 and isinstance(record.get("data"), list) and record["data"]:
                    reward = f"，奖励：{record['data'][0].get('goodsName', '未知')}"
                log.info(f"库街区 {name} [{role_name}]：每日补给签到成功{reward}")
            elif code == 1511:
                log.info(f"库街区 {name} [{role_name}]：今日已签到")
            elif code == 220:
                log.error("库街区 token 已过期，请重新执行：python checkin.py login")
                return False
            elif code == 1513:
                log.warning(f"库街区 {name} [{role_name}]：用户信息异常，请检查账号绑定")
                all_ok = False
            else:
                log.warning(f"库街区 {name} [{role_name}]：签到失败"
                            f"（{code} {result.get('message')}）")
                all_ok = False
    return all_ok


# ════════════════════════════ 微博超话部分 ════════════════════════════

def _wb_ok(payload: dict) -> bool:
    return payload.get("ok") in (True, 1, "1")


def wb_headers(w: dict) -> dict:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": WB_UA,
        "Referer": WB_FOLLOW_REFERER,
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": w["cookie"],
    }
    for part in w["cookie"].split(";"):
        name, _, value = part.strip().partition("=")
        if name == "XSRF-TOKEN" and value:
            headers["X-XSRF-TOKEN"] = value
            break
    return headers


def wb_get(path: str, w: dict, params: dict = None) -> dict:
    url = path if path.startswith("http") else f"{WB_BASE}{path}"
    return http_request("GET", url, headers=wb_headers(w), params=params)


def wb_normalize_cookie(raw: str) -> str:
    """规范化粘贴内容：支持整行 Cookie 请求头（cookie: xxx）或原始键值对"""
    raw = (raw or "").strip()
    for line in raw.splitlines():
        if re.match(r"^\s*cookie\s*:", line, flags=re.IGNORECASE):
            raw = line.split(":", 1)[1].strip()
            break
    pairs, seen = [], set()
    for part in raw.replace("\n", ";").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if name and name not in seen:
            seen.add(name)
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def wb_verify_login(w: dict) -> tuple:
    """校验微博 Cookie，返回 (是否有效, 昵称或说明)"""
    data = wb_get("/api/config", w)
    d = data.get("data") or {}
    if d.get("login") in (True, 1, "1"):
        name = d.get("screen_name") or str(d.get("uid") or "未知用户")
        return True, name
    return False, "Cookie 已失效或未登录"


def wb_fetch_st(w: dict) -> str:
    data = wb_get("/api/config", w)
    return str((data.get("data") or {}).get("st") or "")


def wb_list_topics(w: dict) -> list:
    """拉取关注的所有超话及其签到状态，返回 [{name, status, scheme}]"""
    topics, seen, since_id = [], set(), None
    for _ in range(100):
        params = {"containerid": "100803_-_followsuper"}
        if since_id:
            params["since_id"] = since_id
        payload = wb_get("/api/container/getIndex", w, params=params)
        if not _wb_ok(payload):
            raise ValueError(payload.get("message") or payload.get("msg") or "接口返回异常")
        cards = (payload.get("data") or {}).get("cards") or []
        for card in cards:
            for item in (card.get("card_group") or []) if isinstance(card, dict) else []:
                name = str(item.get("title_sub") or item.get("title") or "").strip()
                if not name:
                    continue
                status, scheme = "unknown", None
                for b in (item.get("buttons") or []):
                    bname = str(b.get("name") or "").strip()
                    if bname == "签到":
                        sch = str(b.get("scheme") or "").strip()
                        if sch.startswith("/api/container/button"):
                            status, scheme = "available", sch
                        break
                    if "已签" in bname or bname == "明日再来":
                        status = "signed"
                        break
                key = str(item.get("oid") or item.get("topic_id") or item.get("id") or name)
                if key not in seen:
                    seen.add(key)
                    topics.append({"name": name, "status": status, "scheme": scheme})
        info = (payload.get("data") or {}).get("cardlistInfo") or {}
        nxt = str(info.get("since_id") or "")
        if not nxt or nxt == (since_id or ""):
            break
        since_id = nxt
    return topics


def wb_do_checkin(w: dict, scheme: str) -> tuple:
    """执行单个超话签到，返回 (结果状态, 提示信息)；状态：success/already/failed"""
    payload = wb_get(scheme, w)
    data = payload.get("data") or {}
    message = str(data.get("msg") or data.get("tipMessage")
                  or payload.get("msg") or payload.get("message") or "")
    if str(payload.get("errno") or "") == "100015" or "验签" in message:
        st = wb_fetch_st(w)
        if st:
            payload = wb_get(scheme, w, params={"st": st})
            data = payload.get("data") or {}
            message = str(data.get("msg") or data.get("tipMessage")
                          or payload.get("msg") or payload.get("message") or "")
    code = str(payload.get("code") or "")
    if _wb_ok(payload) or data.get("ok") in (True, 1, "1") or code in ("100000", "382010"):
        return "success", message or "签到成功"
    if code == "382004" or "已签" in message or "明日再来" in message:
        return "already", message or "今日已签到"
    return "failed", message or "签到失败"


def wb_verify_cookie(raw: str) -> tuple:
    """规范化并校验粘贴的 Cookie，有效则保存，返回 (是否成功, 提示信息)"""
    cookie = wb_normalize_cookie(raw)
    if "SUB=" not in cookie:
        return False, "Cookie 中未找到 SUB 字段，请确认从已登录的微博网页复制"
    w = CONFIG["weibo"]
    w["cookie"] = cookie
    ok, who = wb_verify_login(w)
    if ok:
        save_config(CONFIG)
        return True, f"微博登录成功！昵称：{who}"
    return False, "Cookie 无效或已失效，请重新复制"


def wb_cookie_login() -> None:
    """微博 Cookie 登录（CLI 交互封装）"""
    print("\n─── 微博超话登录 ───")
    print("第一步：浏览器打开 https://m.weibo.cn 并登录")
    print("        （签到会覆盖你关注的全部超话，包括原神超话）")
    try:
        os.startfile("https://m.weibo.cn")
    except Exception:
        pass
    print("第二步：登录后按 F12 →「网络/Network」→ 刷新页面 → 点任意请求 →")
    print("        在「请求标头」里找到 Cookie 一行，复制整行值")
    raw = input("第三步：粘贴 Cookie：")
    ok, msg = wb_verify_cookie(raw)
    (log.info if ok else log.error)(msg)


def weibo_run() -> bool:
    """微博超话签到：自动签到所有关注且可签的超话（含原神超话）"""
    w = CONFIG["weibo"]
    if not w["enabled"]:
        log.info("微博超话模块未启用，跳过")
        return True
    if not w["cookie"]:
        log.warning("微博尚未登录（缺少 Cookie），请先执行：python checkin.py login")
        return False

    log.info("正在检查微博登录态...")
    ok, who = wb_verify_login(w)
    if not ok:
        log.error("微博 Cookie 已失效，请重新登录（python checkin.py login 或图形界面）")
        return False
    log.info(f"微博登录有效：{who}")

    try:
        topics = wb_list_topics(w)
    except Exception as e:
        log.error(f"获取超话列表失败：{e}")
        return False

    todo = [t for t in topics if t["status"] == "available"]
    already = [t for t in topics if t["status"] == "signed"]
    log.info(f"共关注 {len(topics)} 个超话：待签 {len(todo)}，今日已签 {len(already)}")

    all_ok, success_count = True, 0
    for t in todo:
        try:
            status, msg = wb_do_checkin(w, t["scheme"])
            if status in ("success", "already"):
                log.info(f"超话签到 [{t['name']}]：{msg}")
                success_count += status == "success"
            else:
                log.warning(f"超话签到 [{t['name']}]：失败（{msg}）")
                all_ok = False
        except Exception as e:
            log.warning(f"超话签到 [{t['name']}]：异常（{e}）")
            all_ok = False
        time.sleep(random.randint(2, 5))
    log.info(f"微博超话：本次成功 {success_count}，之前已签 {len(already)}")
    return all_ok


# ════════════════════════════ 主流程 ════════════════════════════

def cmd_run() -> int:
    log.info(f"════ 每日打卡签到开始（{datetime.now():%Y-%m-%d %H:%M:%S}）════")
    results = []
    results.append(("米游社", mihoyo_run()))
    results.append(("库街区", kuro_sign()))
    results.append(("微博超话", weibo_run()))
    log.info("══════════════ 执行汇总 ══════════════")
    for name, ok in results:
        log.info(f"  {name}：{'完成' if ok else '存在失败项，详见上方日志'}")
    return 0 if all(ok for _, ok in results) else 1


def cmd_test() -> int:
    """只查询不执行：校验登录状态"""
    log.info("─── 登录状态检查（只查询，不执行任何签到）───")
    m = CONFIG["mihoyo"]
    if m["stoken"]:
        if mi_ensure_web_auth(m):
            log.info("米游社登录态：有效（stoken 与 cookie_token 均正常）")
        else:
            log.info("米游社登录态：已失效，请重新执行 python checkin.py login 扫码登录")
    else:
        log.info("米游社：未登录")

    k = CONFIG["kuro"]
    if k["token"]:
        mine = kuro_request(KURO_MINE_URL, kuro_user_headers(k), {})
        if mine.get("code") == 200:
            log.info("库街区 token：有效")
        elif mine.get("code") == 220:
            log.info("库街区 token：已过期，请重新短信登录")
        else:
            log.info(f"库街区 token：状态异常（{mine.get('code')} {mine.get('message')}）")
    else:
        log.info("库街区：未登录")

    w = CONFIG["weibo"]
    if w["cookie"]:
        ok, who = wb_verify_login(w)
        log.info(f"微博 Cookie：{'有效，昵称：' + who if ok else '已失效，请重新复制'}")
    else:
        log.info("微博：未登录")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="米游社 + 库街区 每日打卡签到小工具")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "login", "test"],
                        help="run=执行签到（默认）；login=扫码/短信登录；test=只查询登录状态")
    args = parser.parse_args()

    setup_logging()
    global CONFIG
    CONFIG = load_config()

    if args.command == "login":
        log.info("开始登录配置")
        print("\n─── 米游社登录（二选一）───")
        print("  1. 短信验证码登录（推荐：米游币打卡 + 游戏签到全部可用）")
        print("  2. App 扫码登录（2026-09 起米哈游限制了其权限，仅游戏签到可用）")
        choice = input("选择 [1]: ").strip() or "1"
        m = CONFIG["mihoyo"]
        if m["stoken"]:
            print(f"（当前已登录 uid={m['stuid']}，重新登录将覆盖）")
        try:
            if choice == "2":
                mi_qr_login()
            else:
                mi_sms_login()
        except (KeyboardInterrupt, EOFError):
            log.info("已跳过米游社登录")
        k = CONFIG["kuro"]
        if k["token"]:
            answer = input(f"\n库街区已登录（用户ID={k['user_id']}），是否重新登录？(y/N)：").strip().lower()
            need_kuro = answer == "y"
        else:
            answer = input("\n是否现在配置库街区登录？(Y/n)：").strip().lower()
            need_kuro = answer != "n"
        if need_kuro:
            try:
                kuro_sms_login()
            except (KeyboardInterrupt, EOFError):
                log.info("已跳过库街区登录")
        try:
            answer = input("\n是否配置微博超话签到？(Y/n)：").strip().lower()
            if answer != "n":
                wb_cookie_login()
        except (KeyboardInterrupt, EOFError):
            log.info("已跳过微博登录")
        log.info("登录流程结束。可执行 python checkin.py test 校验登录状态")
        return 0
    if args.command == "test":
        return cmd_test()
    return cmd_run()


if __name__ == "__main__":
    CONFIG: dict = {}
    sys.exit(main())
