# -*- coding: utf-8 -*-
"""DailyCheckIn 图形界面

基于 CustomTkinter 的深色桌面界面：
  - 平台登录状态卡片（米游社 / 库街区）
  - 一键签到（复用命令行的签到逻辑与日志）
  - 短信验证码登录弹窗
  - 实时日志区、下次自动运行时间查询

启动：python gui.py  （或双击 启动界面.bat）
"""

import logging
import os
import queue
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

import checkin
from checkin import (
    CONFIG_FILE, LOG_DIR, ROOT, KURO_MINE_URL,
    load_config, setup_logging, log, today_done_summary,
    mihoyo_run, kuro_sign, cmd_run, weibo_run,
    mi_ensure_web_auth, kuro_request, kuro_user_headers,
    wb_verify_login, wb_verify_cookie, wb_browser_login,
    mi_sms_send, mi_sms_verify, kuro_sms_verify,
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ACCENT = "#3B82F6"
GREEN = "#22C55E"
RED = "#EF4444"
GRAY = "#9CA3AF"
AMBER = "#F59E0B"

KURO_HOME_URL = "https://www.kurobbs.com/"


class QueueLogHandler(logging.Handler):
    """把签到日志转发到 GUI 队列（线程安全）"""

    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        try:
            self.q.put(self.format(record))
        except Exception:
            pass


class SMSLoginDialog(ctk.CTkToplevel):
    """短信验证码登录弹窗（platform: mihoyo / kuro）"""

    def __init__(self, master, app: "App", platform: str):
        super().__init__(master)
        self.app = app
        self.platform = platform
        self.title("登录米游社" if platform == "mihoyo" else "登录库街区")
        self.geometry("480x420")
        self.resizable(False, False)
        self.grid_columnconfigure(0, weight=1)

        title = "米游社 · 短信验证码登录" if platform == "mihoyo" else "库街区 · 短信验证码登录"
        ctk.CTkLabel(self, text=title, font=("Microsoft YaHei UI", 18, "bold")).grid(
            row=0, column=0, padx=24, pady=(24, 4), sticky="w")

        if platform == "mihoyo":
            hint = ("输入手机号后点「发送验证码」。\n"
                    "若提示需要人机验证，会自动打开网页登录页，"
                    "在网页上获取验证码后回到这里输入（网页上的「登录」按钮不用点）。")
        else:
            hint = ("先点「打开库街区官网」，在官网右上角「登录」弹窗中输入手机号、"
                    "完成滑块并点「获取验证码」（网页上的「登录」按钮不用点），"
                    "然后把手机号和验证码填到这里。")
        ctk.CTkLabel(self, text=hint, wraplength=420, justify="left",
                     text_color=GRAY, font=("Microsoft YaHei UI", 12)).grid(
            row=1, column=0, padx=24, pady=(0, 12), sticky="w")

        self.phone = ctk.CTkEntry(self, placeholder_text="手机号", height=38,
                                  font=("Microsoft YaHei UI", 13))
        self.phone.grid(row=2, column=0, padx=24, pady=6, sticky="ew")

        if platform == "mihoyo":
            self.send_btn = ctk.CTkButton(self, text="📤 发送验证码", height=36,
                                          command=self.send_code)
            self.send_btn.grid(row=3, column=0, padx=24, pady=(2, 6), sticky="ew")
        else:
            ctk.CTkButton(self, text="🌐 打开库街区官网获取验证码", height=36,
                          fg_color="#4B5563", hover_color="#374151",
                          command=lambda: self._open(KURO_HOME_URL)).grid(
                row=3, column=0, padx=24, pady=(2, 6), sticky="ew")

        self.code = ctk.CTkEntry(self, placeholder_text="短信验证码", height=38,
                                 font=("Microsoft YaHei UI", 13))
        self.code.grid(row=4, column=0, padx=24, pady=6, sticky="ew")

        self.login_btn = ctk.CTkButton(self, text="✅ 登 录", height=42,
                                       font=("Microsoft YaHei UI", 14, "bold"),
                                       command=self.do_login)
        self.login_btn.grid(row=5, column=0, padx=24, pady=(10, 4), sticky="ew")

        self.status = ctk.CTkLabel(self, text="", wraplength=420, justify="left",
                                   font=("Microsoft YaHei UI", 12))
        self.status.grid(row=6, column=0, padx=24, pady=(4, 16), sticky="ew")

        self.transient(master)
        self.lift()
        self.after(200, self.grab_set)

    @staticmethod
    def _open(url: str):
        try:
            os.startfile(url)
        except Exception:
            pass

    def _ui(self, fn):
        """线程里更新界面：统一丢回主线程执行"""
        self.app.ui_queue.put(fn)

    def _set_status(self, text: str, color: str = GRAY):
        self._ui(lambda: (self.status.configure(text=text, text_color=color),
                          self.login_btn.configure(state="normal"),
                          self.send_btn.configure(state="normal")
                          if hasattr(self, "send_btn") else None))

    def send_code(self):
        mobile = self.phone.get()
        if not mobile.strip():
            self.status.configure(text="请先输入手机号", text_color=RED)
            return
        self.send_btn.configure(state="disabled")
        self.status.configure(text="正在发送验证码...", text_color=GRAY)

        def worker():
            ok, msg = mi_sms_send(mobile)
            if not ok and "user.mihoyo.com" in msg:
                self._open("https://user.mihoyo.com/#/login")
            self._set_status(msg, GREEN if ok else RED)

        threading.Thread(target=worker, daemon=True).start()

    def do_login(self):
        mobile, code = self.phone.get(), self.code.get()
        self.login_btn.configure(state="disabled")
        self.status.configure(text="正在登录...", text_color=GRAY)

        def worker():
            if self.platform == "mihoyo":
                ok, msg = mi_sms_verify(mobile, code)
            else:
                ok, msg = kuro_sms_verify(mobile, code)
            checkin.log.info(msg)
            self._set_status(msg, GREEN if ok else RED)
            if ok:
                self._ui(lambda: (self.app.refresh_status(),
                                  self.after(1500, self.destroy)))

        threading.Thread(target=worker, daemon=True).start()


class WeiboLoginDialog(ctk.CTkToplevel):
    """微博 Cookie 登录弹窗"""

    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        self.title("登录微博")
        self.geometry("540x480")
        self.resizable(False, False)
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="微博 · 超话签到登录",
                     font=("Microsoft YaHei UI", 18, "bold")).grid(
            row=0, column=0, padx=24, pady=(24, 4), sticky="w")
        hint = ("推荐：点「自动获取 Cookie」，会打开浏览器，在页面中登录微博后自动抓取保存。\n"
                "签到会覆盖你关注的全部超话（包括原神超话）。\n"
                "自动方式不可用时，可按下面步骤手动复制 Cookie 粘贴。")
        ctk.CTkLabel(self, text=hint, wraplength=470, justify="left",
                     text_color=GRAY, font=("Microsoft YaHei UI", 12)).grid(
            row=1, column=0, padx=24, pady=(0, 10), sticky="w")

        self.auto_btn = ctk.CTkButton(self, text="🤖 自动获取 Cookie（推荐）", height=44,
                                      font=("Microsoft YaHei UI", 14, "bold"),
                                      command=self.auto_grab)
        self.auto_btn.grid(row=2, column=0, padx=24, pady=(0, 8), sticky="ew")

        ctk.CTkLabel(self, text="—— 或手动粘贴 Cookie ——", text_color=GRAY,
                     font=("Microsoft YaHei UI", 11)).grid(
            row=3, column=0, padx=24, pady=(0, 6))

        ctk.CTkButton(self, text="打开微博网页版", height=32,
                      fg_color="#4B5563", hover_color="#374151",
                      command=lambda: self._open("https://m.weibo.cn")).grid(
            row=4, column=0, padx=24, pady=(0, 8), sticky="ew")

        self.cookie_box = ctk.CTkTextbox(self, height=70, font=("Consolas", 12))
        self.cookie_box.grid(row=5, column=0, padx=24, pady=(0, 8), sticky="ew")

        self.save_btn = ctk.CTkButton(self, text="保存并验证", height=38,
                                      font=("Microsoft YaHei UI", 13),
                                      command=self.do_save)
        self.save_btn.grid(row=6, column=0, padx=24, pady=(0, 4), sticky="ew")

        self.status = ctk.CTkLabel(self, text="", wraplength=470, justify="left",
                                   font=("Microsoft YaHei UI", 12))
        self.status.grid(row=7, column=0, padx=24, pady=(4, 16), sticky="ew")

        self.transient(master)
        self.lift()
        self.after(200, self.grab_set)

    @staticmethod
    def _open(url: str):
        try:
            os.startfile(url)
        except Exception:
            pass

    def auto_grab(self):
        """自动方式：打开浏览器让用户登录，登录后自动抓取 Cookie"""
        self.auto_btn.configure(state="disabled")
        self.status.configure(text="正在启动浏览器，请在打开的窗口中登录微博，登录后自动抓取...",
                              text_color=GRAY)

        def worker():
            ok, msg = wb_browser_login(headless=False, timeout=600)
            checkin.log.info(msg)

            def ui():
                self.auto_btn.configure(state="normal")
                self.status.configure(text=msg, text_color=GREEN if ok else RED)
                if ok:
                    self.app.refresh_status()
                    self.after(1500, self.destroy)

            self.app.ui_queue.put(ui)

        threading.Thread(target=worker, daemon=True).start()

    def do_save(self):
        raw = self.cookie_box.get("1.0", "end").strip()
        if not raw:
            self.status.configure(text="请先粘贴 Cookie", text_color=RED)
            return
        self.save_btn.configure(state="disabled")
        self.status.configure(text="正在验证 Cookie...", text_color=GRAY)

        def worker():
            ok, msg = wb_verify_cookie(raw)
            checkin.log.info(msg)

            def ui():
                self.status.configure(text=msg, text_color=GREEN if ok else RED)
                self.save_btn.configure(state="normal")
                if ok:
                    self.app.refresh_status()
                    self.after(1500, self.destroy)

            self.app.ui_queue.put(ui)

        threading.Thread(target=worker, daemon=True).start()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("DailyCheckIn · 每日自动签到")
        self.geometry("1020x765")
        self.minsize(880, 700)

        self.log_queue = queue.Queue()
        self.ui_queue = queue.Queue()
        self.running = False
        self.applying = False

        setup_logging(console=False)
        checkin.CONFIG = load_config()
        checkin.log.addHandler(QueueLogHandler(self.log_queue))

        self._build_header()
        self._build_body()
        self._build_log()

        self.after(120, self._poll)
        self.after(300, self.refresh_status)
        self.after(300, self.refresh_next_run)
        self.after(600, self.update_run_btn)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def update_run_btn(self):
        """今日启用模块全部签完时，按钮显示已完成状态（仍可点击，执行时只会跳过）"""
        try:
            summary = today_done_summary()
        except Exception:
            return
        if summary and all(summary.values()):
            self.run_btn.configure(text="✅ 今日已全部签到", fg_color="#16A34A")
        else:
            self.run_btn.configure(text="🚀 立即签到", fg_color=GREEN)

    # ────────── 界面构建 ──────────
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent", height=56)
        bar.pack(fill="x", padx=24, pady=(12, 4))
        ctk.CTkLabel(bar, text="🗓️ DailyCheckIn",
                     font=("Microsoft YaHei UI", 24, "bold")).pack(side="left")
        ctk.CTkLabel(bar, text="米游社 · 库街区 · 微博超话 每日自动签到",
                     font=("Microsoft YaHei UI", 12), text_color=GRAY).pack(side="left", padx=12, pady=(8, 0))

        self.theme_btn = ctk.CTkButton(bar, text="🌓", width=40, height=32,
                                       fg_color="#374151", hover_color="#4B5563",
                                       command=lambda: ctk.set_appearance_mode(
                                           "light" if ctk.get_appearance_mode().lower() == "dark" else "dark"))
        self.theme_btn.pack(side="right")

    def _card(self, parent, row, title, color):
        """紧凑状态卡：标题与状态点同行，说明一行，整体约 80px 高"""
        card = ctk.CTkFrame(parent, corner_radius=12)
        card.grid(row=row, column=0, sticky="ew", padx=2, pady=5)
        card.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(9, 0))
        ctk.CTkLabel(top, text=title, font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        dot = ctk.CTkLabel(top, text="● 检查中...", font=("Microsoft YaHei UI", 12),
                           text_color=GRAY)
        dot.pack(side="right")
        sub = ctk.CTkLabel(card, text="", font=("Microsoft YaHei UI", 11),
                           text_color=GRAY, anchor="w")
        sub.pack(fill="x", padx=14, pady=(0, 9))
        return {"dot": dot, "sub": sub, "color": color}

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # 左列：状态卡片
        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(3, weight=1)

        self.mi_card = self._card(left, 0, "米游社", ACCENT)
        self.kuro_card = self._card(left, 1, "库街区", GREEN)
        self.wb_card = self._card(left, 2, "微博超话", AMBER)

        # 每日自动执行 + 时间设置
        next_card = ctk.CTkFrame(left, corner_radius=12)
        next_card.grid(row=3, column=0, sticky="sew", padx=2, pady=5)
        ctk.CTkLabel(next_card, text="⏰ 每日自动执行",
                     font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", padx=16, pady=(10, 0))
        self.next_run_label = ctk.CTkLabel(next_card, text="查询中...",
                                           font=("Consolas", 14), text_color=ACCENT)
        self.next_run_label.pack(anchor="w", padx=16, pady=(2, 4))

        time_row = ctk.CTkFrame(next_card, fg_color="transparent")
        time_row.pack(fill="x", padx=16, pady=(0, 4))
        self.time_entry = ctk.CTkEntry(time_row, placeholder_text="00:02", width=96,
                                       height=30, justify="center", font=("Consolas", 14))
        self.time_entry.pack(side="left")
        self.sched_btn = ctk.CTkButton(time_row, text="应用到计划任务", width=124, height=30,
                                       font=("Microsoft YaHei UI", 12),
                                       command=self.apply_schedule)
        self.sched_btn.pack(side="right")

        self.sched_note = ctk.CTkLabel(next_card, text="错过自动补跑 · 签到幂等不重复领取",
                                       font=("Microsoft YaHei UI", 11), text_color=GRAY)
        self.sched_note.pack(anchor="w", padx=16, pady=(0, 10))

        # 右列：操作按钮
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        right.grid_columnconfigure(0, weight=1)

        self.run_btn = ctk.CTkButton(right, text="🚀 立即签到", height=48,
                                     font=("Microsoft YaHei UI", 16, "bold"),
                                     fg_color=GREEN, hover_color="#16A34A",
                                     command=self.start_checkin)
        self.run_btn.grid(row=0, column=0, sticky="ew", pady=6)

        ctk.CTkButton(right, text="登录米游社", height=40,
                      font=("Microsoft YaHei UI", 13),
                      command=lambda: SMSLoginDialog(self, self, "mihoyo")).grid(
            row=1, column=0, sticky="ew", pady=5)
        ctk.CTkButton(right, text="登录库街区", height=40,
                      font=("Microsoft YaHei UI", 13),
                      command=lambda: SMSLoginDialog(self, self, "kuro")).grid(
            row=2, column=0, sticky="ew", pady=5)
        ctk.CTkButton(right, text="登录微博", height=40,
                      font=("Microsoft YaHei UI", 13),
                      command=lambda: WeiboLoginDialog(self, self)).grid(
            row=3, column=0, sticky="ew", pady=5)

        ctk.CTkButton(right, text="🔄 刷新登录状态", height=36,
                      fg_color="#374151", hover_color="#4B5563",
                      command=self.refresh_status).grid(row=4, column=0, sticky="ew", pady=(12, 5))
        ctk.CTkButton(right, text="📂 打开日志文件夹", height=36,
                      fg_color="#374151", hover_color="#4B5563",
                      command=lambda: self._open(LOG_DIR)).grid(row=5, column=0, sticky="ew", pady=5)
        ctk.CTkButton(right, text="⚙️ 打开配置文件", height=36,
                      fg_color="#374151", hover_color="#4B5563",
                      command=lambda: self._open(CONFIG_FILE)).grid(row=6, column=0, sticky="ew", pady=5)

        right.grid_rowconfigure(7, weight=1)

    def _build_log(self):
        log_frame = ctk.CTkFrame(self, corner_radius=12, height=210)
        log_frame.pack(fill="x", padx=24, pady=(2, 14))
        log_frame.pack_propagate(False)   # 日志区固定高度，任何窗口尺寸下都可见
        self.log_box = ctk.CTkTextbox(log_frame, font=("Consolas", 12), wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_box.insert("end", "欢迎使用 DailyCheckIn！点击「立即签到」开始，或等待每日自动执行。\n")
        self.log_box.configure(state="disabled")

    # ────────── 工具方法 ──────────
    @staticmethod
    def _open(path):
        try:
            os.startfile(path)
        except Exception as e:
            log.error(f"打开失败：{e}")

    def _set_dot(self, card, ok, note=""):
        if ok:
            card["dot"].configure(text="● 已登录", text_color=GREEN)
        else:
            card["dot"].configure(text="● 未登录", text_color=RED)
        card["sub"].configure(text=note)

    def refresh_status(self):
        self.mi_card["dot"].configure(text="● 检查中...", text_color=GRAY)
        self.kuro_card["dot"].configure(text="● 检查中...", text_color=GRAY)

        def worker():
            cfg = checkin.CONFIG
            mi_ok = bool(cfg["mihoyo"]["stoken"]) and mi_ensure_web_auth(cfg["mihoyo"])
            mi_note = f"uid={cfg['mihoyo']['stuid']}" if cfg["mihoyo"]["stuid"] else "尚未登录，点击右侧按钮登录"
            kuro_ok = False
            kuro_note = "尚未登录，点击右侧按钮登录"
            k = cfg["kuro"]
            if k["token"]:
                mine = kuro_request(KURO_MINE_URL, kuro_user_headers(k), {})
                kuro_ok = mine.get("code") == 200
                kuro_note = (f"用户 ID={k.get('user_id', '?')}" if kuro_ok
                             else "token 已过期，请重新登录")
            wb_ok = False
            wb_note = "尚未登录，点击右侧按钮登录"
            w = cfg["weibo"]
            if w["cookie"]:
                wb_ok, who = wb_verify_login(w)
                wb_note = f"昵称：{who}" if wb_ok else "Cookie 已失效，请重新登录"
            self.ui_queue.put(lambda: (self._set_dot(self.mi_card, mi_ok, mi_note),
                                       self._set_dot(self.kuro_card, kuro_ok, kuro_note),
                                       self._set_dot(self.wb_card, wb_ok, wb_note)))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_next_run(self):
        def worker():
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            trigger, next_run = "", ""
            try:
                ps = ("$t = Get-ScheduledTask -TaskPath '\\DailyCheckIn\\' -TaskName 'daily' "
                      "-ErrorAction SilentlyContinue; "
                      "if ($t) { ([datetime]$t.Triggers[0].StartBoundary).ToString('HH:mm'); "
                      "(Get-ScheduledTaskInfo -TaskPath '\\DailyCheckIn\\' -TaskName 'daily').NextRunTime.ToString('yyyy-MM-dd HH:mm') }")
                r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                   capture_output=True, text=True, timeout=15, creationflags=flags)
                lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
                if len(lines) >= 2:
                    trigger, next_run = lines[0], lines[1]
            except Exception:
                pass

            def ui():
                if not next_run:
                    self.next_run_label.configure(text="未注册（输入时间后点「应用」即可开启）")
                    return
                self.next_run_label.configure(text=next_run)
                if trigger and not self.applying:
                    if self.time_entry.get() != trigger:
                        try:
                            if self.focus_get() is not self.time_entry:
                                self.time_entry.delete(0, "end")
                                self.time_entry.insert(0, trigger)
                        except Exception:
                            pass
                    self.sched_note.configure(text=f"每日 {trigger} 自动执行 · 错过开机补跑",
                                              text_color=GRAY)
                else:
                    self.sched_note.configure(text="错过自动补跑 · 签到幂等不重复领取",
                                              text_color=GRAY)

            self.ui_queue.put(ui)

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _pick_python() -> str:
        """计划任务优先用 pythonw（执行时不弹控制台窗口）"""
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        return str(pythonw) if pythonw.exists() else str(exe)

    def apply_schedule(self):
        """把界面输入的每日执行时间写入 Windows 计划任务（含错过补跑设置）"""
        if self.applying:
            return
        raw = self.time_entry.get().strip()
        m = re.match(r"^(\d{1,2})[:：](\d{1,2})$", raw)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            self.sched_note.configure(text="时间格式应为 HH:MM（如 08:30）", text_color=RED)
            return
        hhmm = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
        self.applying = True
        self.sched_btn.configure(state="disabled")
        log.info(f"正在设置每日自动执行时间为 {hhmm} ...")

        def worker():
            ok, msg = False, ""
            try:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                python_exe = self._pick_python()
                script = str(ROOT / "checkin.py")
                r = subprocess.run(
                    ["schtasks", "/Create", "/F", "/TN", "DailyCheckIn\\daily",
                     "/TR", f'"{python_exe}" "{script}" run',
                     "/SC", "DAILY", "/ST", hhmm],
                    capture_output=True, timeout=30, creationflags=flags)
                if r.returncode != 0:
                    detail = (r.stderr or r.stdout or b"").decode("gbk", "ignore").strip()
                    msg = f"设置失败：{detail or f'returncode={r.returncode}'}"
                else:
                    ps = ("$t = Get-ScheduledTask -TaskPath '\\DailyCheckIn\\' -TaskName 'daily'; "
                          "$t.Settings.StartWhenAvailable = $true; $t | Set-ScheduledTask")
                    r2 = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                        capture_output=True, timeout=60, creationflags=flags)
                    ok = r2.returncode == 0
                    msg = (f"已设置每天 {hhmm} 自动签到，错过开机补跑已开启"
                           if ok else
                           f"计划任务时间已改为 {hhmm}，但补跑设置未生效（可双击 install_task.bat 修复）")
            except Exception as e:
                msg = f"设置失败：{e}"

            def ui():
                self.applying = False
                self.sched_btn.configure(state="normal")
                (log.info if ok else log.error)(msg)
                self.refresh_next_run()

            self.ui_queue.put(ui)

        threading.Thread(target=worker, daemon=True).start()

    def start_checkin(self):
        if self.running:
            return
        self.running = True
        self.run_btn.configure(text="⏳ 签到执行中...", state="disabled", fg_color="#4B5563")

        def worker():
            try:
                cmd_run()
            except Exception as e:
                log.error(f"执行异常：{e}")
            finally:
                self.ui_queue.put(self._finish_checkin)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_checkin(self):
        self.running = False
        self.run_btn.configure(state="normal")
        self.update_run_btn()
        self.refresh_status()
        self.refresh_next_run()

    # ────────── 主线程轮询 ──────────
    def _poll(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_box.configure(state="normal")
                self.log_box.insert("end", line + "\n")
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
        except queue.Empty:
            pass
        try:
            while True:
                fn = self.ui_queue.get_nowait()
                fn()
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _on_close(self):
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
