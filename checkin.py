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

# 游戏签到：act_id 来自 MihoyoBBSTools setting.py，base 为官方签到活动域名
MI_GAMES = {
    "genshin": {"name": "原神", "biz": "hk4e_cn", "act_id": "e202311201442471",
                "base": "https://api-takumi.mihoyo.com/event/luna"},
    "honkaisr": {"name": "崩坏：星穹铁道", "biz": "hkrpg_cn", "act_id": "e202304121516551",
                 "base": "https://api-takumi.mihoyo.com/event/luna"},
    "zzz": {"name": "绝区零", "biz": "nap_cn", "act_id": "e202406242138391",
            "base": "https://act-nap-api.mihoyo.com/event/luna/zzz"},
    "honkai3rd": {"name": "崩坏3", "biz": "bh3_cn", "act_id": "e202306201626331",
                  "base": "https://api-takumi.mihoyo.com/event/luna"},
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


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    log_file = logging.FileHandler(LOG_DIR / f"checkin_{datetime.now():%Y-%m-%d}.log", encoding="utf-8")
    log_file.setFormatter(fmt)
    log.addHandler(console)
    log.addHandler(log_file)


def load_config() -> dict:
    default = {
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
    }
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        for section, values in default.items():
            saved.setdefault(section, {})
            for key, val in values.items():
                saved[section].setdefault(key, val)
        return saved
    return default


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def http_request(method: str, url: str, **kwargs) -> dict:
    """带重试的请求，返回 JSON dict；网络异常时返回 {"retcode": -1, "message": ...}"""
    kwargs.setdefault("timeout", TIMEOUT)
    for attempt in range(3):
        try:
            resp = requests.request(method, url, **kwargs)
            return resp.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            if attempt == 2:
                log.warning(f"请求失败 {url}：{e}")
                return {"retcode": -1, "message": str(e)}
            time.sleep(3)


# ════════════════════════════ 米游社部分 ════════════════════════════

def mi_stoken_cookie(m: dict) -> str:
    cookie = f"stoken={m['stoken']};stuid={m['stuid']}"
    if m["stoken"].startswith("v2_"):
        cookie += f";mid={m['mid']}"
    return cookie


def mi_web_cookie(m: dict) -> str:
    return f"account_id={m['account_id']};cookie_token={m['cookie_token']}"


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


def mi_refresh_cookie_token(m: dict) -> bool:
    """用 stoken 换新的 cookie_token"""
    if not m.get("stoken"):
        return False
    data = http_request("GET", MI_COOKIE_TOKEN_URL,
                        headers={"Accept": "application/json",
                                 "Cookie": mi_stoken_cookie(m)})
    if data.get("retcode") == 0 and data.get("data", {}).get("cookie_token"):
        m["cookie_token"] = data["data"]["cookie_token"]
        save_config(CONFIG)
        log.info("米游社 cookie_token 已自动刷新")
        return True
    log.warning("米游社 stoken 已失效，请重新执行 python checkin.py login 扫码登录")
    return False


def mi_qr_login() -> None:
    """米游社 App 扫码登录，获取 stoken 并换出 cookie_token"""
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


def mi_bbs_checkin(m: dict) -> bool:
    """米游币打卡：逐个分区调用签到接口"""
    all_ok = True
    for gid in m["bbs_gids"]:
        name = MI_BBS_PARTITIONS.get(str(gid), f"分区{gid}")
        body = json.dumps({"gids": str(gid)}, separators=(",", ":"))
        data = http_request("POST", MI_BBS_SIGN_URL,
                            headers=mi_app_headers(m, body), data=body)
        code = data.get("retcode")
        if code == 0:
            log.info(f"米游币打卡 [{name}]：成功")
        elif code == 1034:
            log.warning(f"米游币打卡 [{name}]：触发风控验证码，今天请在 App 里手动打卡一次")
            all_ok = False
        elif code == -100:
            log.error(f"米游币打卡 [{name}]：stoken 已失效，请重新扫码登录")
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

        roles_data = http_request("GET", MI_ROLES_URL,
                                  headers=mi_web_headers(m),
                                  params={"game_biz": game["biz"]})
        if roles_data.get("retcode") == -100:
            if mi_refresh_cookie_token(m):
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

        rewards = []
        home = http_request("GET", f"{game['base']}/home",
                            headers=mi_web_headers(m),
                            params={"lang": "zh-cn", "act_id": game["act_id"]})
        if home.get("retcode") == 0:
            rewards = home["data"].get("awards", [])

        for role in roles:
            uid, region, nick = role["game_uid"], role["region"], role["nickname"]
            time.sleep(random.randint(2, 6))
            info = http_request("GET", f"{game['base']}/info",
                                headers=mi_web_headers(m),
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
                                headers=mi_web_headers(m),
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
    if not m["cookie_token"] and not mi_refresh_cookie_token(m):
        return False

    ok = True
    if m["bbs_checkin"]:
        ok = mi_bbs_checkin(m) and ok
    ok = mi_game_sign(m) and ok
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


def kuro_sms_login() -> None:
    """库街区短信登录：官网登录弹窗里获取验证码，脚本用 手机号+验证码 换 token"""
    print("\n─── 库街区登录 ───")
    print("第一步：浏览器已打开库街区官网 https://www.kurobbs.com/")
    print("        → 点右上角「登录」→ 输入手机号 → 完成滑块验证 → 点「获取验证码」")
    print("        → 收到短信验证码即可，网页上的「登录」按钮不用点")
    try:
        os.startfile("https://www.kurobbs.com/")
    except Exception:
        print("        （浏览器未自动打开，请手动访问 https://www.kurobbs.com/ ）")
    mobile = input("第二步：输入手机号：").strip()
    code = input("第三步：输入收到的短信验证码：").strip()
    if not mobile or not code:
        log.error("手机号或验证码为空，取消库街区登录")
        return

    k = CONFIG["kuro"]
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
        log.info(f"库街区登录成功！用户 ID={k['user_id']}")
    else:
        log.error(f"库街区登录失败：{result.get('message', result)}（验证码可能已过期，请重试）")


def kuro_sign() -> bool:
    k = CONFIG["kuro"]
    if not k["enabled"]:
        log.info("库街区模块未启用，跳过")
        return True
    if not k["token"]:
        log.warning("库街区尚未登录（缺少 token），请先执行：python checkin.py login")
        return False

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


# ════════════════════════════ 主流程 ════════════════════════════

def cmd_run() -> int:
    log.info(f"════ 每日打卡签到开始（{datetime.now():%Y-%m-%d %H:%M:%S}）════")
    results = []
    results.append(("米游社", mihoyo_run()))
    results.append(("库街区", kuro_sign()))
    log.info("══════════════ 执行汇总 ══════════════")
    for name, ok in results:
        log.info(f"  {name}：{'完成' if ok else '存在失败项，详见上方日志'}")
    return 0 if all(ok for _, ok in results) else 1


def cmd_test() -> int:
    """只查询不执行：校验登录状态"""
    log.info("─── 登录状态检查（只查询，不执行任何签到）───")
    m = CONFIG["mihoyo"]
    if m["stoken"]:
        data = http_request("GET", MI_COOKIE_TOKEN_URL,
                            headers={"Accept": "application/json",
                                     "Cookie": mi_stoken_cookie(m)})
        state = "有效" if data.get("retcode") == 0 else "已失效，请重新扫码登录"
        log.info(f"米游社 stoken：{state}")
        if m["cookie_token"]:
            roles = http_request("GET", MI_ROLES_URL, headers=mi_web_headers(m),
                                 params={"game_biz": "hk4e_cn"})
            state = "有效" if roles.get("retcode") == 0 else "已失效"
            log.info(f"米游社 cookie_token：{state}")
        else:
            log.info("米游社 cookie_token：为空（执行签到时会自动刷新）")
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
        log.info("开始登录配置（米游社扫码 + 库街区短信，可随时 Ctrl+C 跳过某一项）")
        m = CONFIG["mihoyo"]
        if m["stoken"]:
            answer = input(f"米游社已登录（uid={m['stuid']}），是否重新扫码登录？(y/N)：").strip().lower()
            need_mi = answer == "y"
        else:
            need_mi = True
        if need_mi:
            try:
                mi_qr_login()
            except (KeyboardInterrupt, EOFError):
                log.info("已跳过米游社扫码登录")
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
        log.info("登录流程结束。可执行 python checkin.py test 校验登录状态")
        return 0
    if args.command == "test":
        return cmd_test()
    return cmd_run()


if __name__ == "__main__":
    CONFIG: dict = {}
    sys.exit(main())
