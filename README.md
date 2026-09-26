# DailyCheckIn —— 米游社 & 库街区每日打卡签到小工具

一个零配置界面、纯命令行的 Python 小工具，每天自动完成：

| 平台 | 功能 |
|------|------|
| 米游社 | 米游币打卡（分区打卡）+ 游戏签到领福利（原神 / 崩坏：星穹铁道 / 绝区零 / 崩坏3，可配置） |
| 库街区 | 每日补给签到（鸣潮 / 战双帕弥什，自动识别绑定角色） |

- 登录**不需要抓包**：米游社用 App 扫码，库街区用手机号 + 短信验证码
- 只依赖 `requests`（可选 `qrcode` 用于终端显示登录二维码）
- 接口实现参考两个仍在活跃维护的开源项目：
  - 米游社：[Womsxd/MihoyoBBSTools](https://github.com/Womsxd/MihoyoBBSTools)
  - 库街区：[mxyooR/Kuro-autosignin](https://github.com/mxyooR/Kuro-autosignin) / [mxyooR/Kuro_login](https://github.com/mxyooR/Kuro_login)

> 仅供个人学习交流使用，请合理控制请求频率。请勿用于商业用途或批量账号操作。

---

## 环境要求

- Windows + Python 3.9+（已在 Python 3.14 上验证）
- 依赖安装：

```bash
pip install requests qrcode
```

（`qrcode` 只用于扫码登录时在终端/浏览器显示二维码，不装也能用链接方式登录）

## 快速开始

### 1. 登录（首次使用，只需一次）

```bash
python checkin.py login
```

流程：

1. **米游社**（二选一，推荐短信登录）：
   - **短信验证码登录（推荐）**：输入手机号，工具直接下发短信验证码（失败时会自动打开网页登录页让你手动获取），输入验证码后获得完整权限 stoken——**米游币打卡和游戏签到全部可用**。
   - **App 扫码登录**：终端/浏览器出二维码，米游社 App 扫一扫。⚠️ 2026-09 起米哈游限制了扫码 token 的权限，仅够游戏签到，米游币打卡会提示无权限。
2. **库街区**：工具会自动打开库街区官网 https://www.kurobbs.com/ → 点右上角「**登录**」→ 输入手机号 → 完成滑块验证 → 点「**获取验证码**」（网页上的「登录」按钮不用点），把手机号和收到的短信验证码输进命令行，工具换取 token 存入 `config.json`。

### 2. 手动执行一次

```bash
python checkin.py        # 执行今日打卡签到
python checkin.py test   # 只查询登录状态是否有效，不执行任何签到
```

日志同时写入 `logs/checkin_日期.log`。

### 3. 每天自动执行

双击 `install_task.bat`，会注册两个计划：

- 每天 **08:05** 执行（想改时间就编辑 bat 里的 `/ST 08:05` 后重新双击）
- 每次开机登录时执行（电脑 8 点没开机也能补上；签到接口本身幂等，重复执行只会提示"已签到"）

卸载：双击 `uninstall_task.bat`。

## 配置说明（config.json）

首次运行自动生成，一般不需要手动改：

```jsonc
{
  "mihoyo": {
    "enabled": true,
    "bbs_checkin": true,            // 是否做米游币打卡
    "bbs_gids": ["2", "6", "8"],    // 打卡分区：1崩坏3 2原神 6星穹铁道 8绝区零 ...
    "game_sign": ["genshin", "honkaisr", "zzz"]  // 游戏签到：可加 "honkai3rd"
  },
  "kuro": {
    "enabled": true,
    "games": ["wuwa", "pgr"]        // 每日补给：鸣潮 / 战双帕弥什
  }
}
```

登录凭证（stoken / cookie_token / 库街区 token）都由 `login` 命令自动写入并保存，其中
米游社 cookie_token 过期时工具会用 stoken 自动续期。

## 常见问题

- **运行时卡住不动**
  先检查是否在 cmd 窗口里点过鼠标：Windows 的"快速编辑"模式会因选中文字而冻结程序输出，按 `Esc` 或回车即可恢复。其次确认是否开了代理/加速器（TUN、Clash 系统代理等）——本工具默认忽略系统代理直连国内接口；如你的网络必须走代理，在 `config.json` 里加 `"proxy": "http://127.0.0.1:端口"`。
- **登录库街区时网页显示 404**
  库街区官网已改版，登录改为全站弹窗形式：打开 https://www.kurobbs.com/ 后点右上角「登录」，在弹窗里获取短信验证码即可（旧地址 /mc/home/ 已失效）。
- **米游币打卡提示 stoken 无权限**
  2026-09 起米哈游限制了扫码登录 token 的米游社接口权限（参考 MihoyoBBSTools issue #284/#285），用短信验证码方式重新登录一次即可（`python checkin.py login` 选 1）。
- **原神签到报"网络出小差了"（-500001）**
  米哈游要求原神签到接口携带 `x-rpc-signgame: hk4e` 请求头，本工具已内置处理；若仍出现多为风控临时拦截，稍后重试。
- **提示"触发风控验证码"（米游币打卡 retcode 1034）**
  米哈游对部分环境的风控较严，遇到时当天在米游社 App 里手动点一次打卡即可，游戏签到一般不受影响。
- **提示 token / stoken 已过期**
  重新执行 `python checkin.py login` 扫码/短信登录一次即可。
- **某游戏提示"未绑定该游戏"**
  先在米游社 App / 库街区 App 里手动绑定一次该游戏角色，工具会自动识别所有已绑定角色（多角色会全部签到）。
- **电脑 8 点没开机**
  开机登录时会自动补跑一次；错过当天也能事后手动 `python checkin.py`，米游社游戏签到当月内补签由官方页面处理，库街区当天内补跑有效。

## 项目结构

```
DailyCheckIn/
├── checkin.py          # 主程序（单文件）
├── config.json         # 配置与登录凭证（首次运行生成，已加入 .gitignore）
├── install_task.bat    # 注册计划任务（每天 08:05 + 开机）
├── uninstall_task.bat  # 移除计划任务
└── logs/               # 运行日志（按天）
```
