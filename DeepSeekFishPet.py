__version__ = "1.1.36"
# =============================================================================
#  DeepSeek 肥鱼娘桌宠 —— DeepSeekFishPet.py
#  ---------------------------------------------------------------------------
#  一只住在 Windows 桌面上的 Q 版小鲸鱼拟人少女：会呼吸、眨眼、摆尾，
#  会饿、会困、会想你；可以摸摸头、投喂、玩耍、睡觉，还能接入 DeepSeek 聊天。
#
#  ★ 形象来源：形象图住在程序目录下的「鲸宝形象」（立绘 12 张换装 / 表情 9 个 GIF /
#    动作 106 个 / 启动图），素材包与数据表住在「大肥鱼素材表」。
#    素材不在时，自动回退到程序内置的 QPainter 矢量画风（两边都能用）。
#
#  设计约定（改代码前请先读这一段）：
#   1) 本文件是唯一编译源，Nuitka 只编译它；不要拆分模块。
#   2) 所有设置项都走 DFPSettingsStore：变更 → 防抖 500ms → 原子写盘；
#      新增设置项必须三处成对：DFP_DEFAULT_SETTINGS 加默认、
#      DFPAppController.dfp_on_setting_changed 加生效分支、
#      设置窗口 dfp_reload_values 加恢复（控件默认自动跟随，无需额外代码）。
#   3) 模块级函数统一用 dfp_ 前缀，类统一 DFP 前缀，避免与第三方命名冲突。
#   4) API Key / 数据库口令只从 .env 读取，绝不写进代码。
#   5) 窗口摆放一律用 resize + move（setGeometry 摆的是客户区，会每次上飘）。
#   6) 形象一律走 DFPAssetLibrary（素材表），渲染入口只有 DFPPetWidget._dfp_draw_asset；
#      新增素材分类/容器目录时，同时更新这里的说明与 _scaffold/README.md。
#   7) 素材相关的兜底 except 必须留痕（曾经因为 QMovie 漏 import 被静默吞掉，
#      表现为「动画永远不动」；另有同名方法重复定义把新实现整段遮掉）。
#      改完必跑 _scaffold/probe_undefined_names.py（查漏导入 + 重复定义）。
# =============================================================================

import base64
import hashlib
import hmac
import html
import importlib
import io
import json
import math
import os
import random
import re
import shutil
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import requests
except Exception:  # pragma: no cover - 运行环境缺 requests 时降级为离线模式
    requests = None

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QLoggingCategory,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
    qInstallMessageHandler,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QIcon,
    QImage,
    QKeySequence,
    QLinearGradient,
    QMovie,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QShortcut,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    DFP_NETWORK_AVAILABLE = True
except Exception:  # pragma: no cover - 极端环境下没有 QtNetwork
    QLocalServer = None  # type: ignore[assignment]
    QLocalSocket = None  # type: ignore[assignment]
    DFP_NETWORK_AVAILABLE = False


# =============================================================================
#  一、应用元信息
# =============================================================================

DFP_APP_NAME = "DeepSeekFishPet"
DFP_APP_ORG = "DeepSeekFishPet"
DFP_APP_ID = "DeepSeekFishPet.DesktopPet.1"
DFP_APP_TITLE = "DeepSeek 肥鱼娘桌宠"
DFP_SETTINGS_VERSION = 1
DFP_DB_SCHEMA_VERSION = 1
DFP_DEFAULT_NICKNAME = "肥鱼娘"
DFP_DEFAULT_SECRET_PASSWORD = "deepseek-fish-pet-default-secret"

# 角色画布尺寸：渲染器内部一律在这个逻辑坐标系里画，再整体缩放到目标矩形。
DFP_CANVAS_W = 220.0
DFP_CANVAS_H = 260.0

DFP_BACKUP_KEEP_DEFAULT = 10
DFP_STAT_MIN = 0.0
DFP_STAT_MAX = 100.0
DFP_LOG_MAX_BYTES = 2 * 1024 * 1024
DFP_LOG_KEEP_FILES = 5

DFP_USER_AGENT = "DeepSeekFishPet/%s (PySide6; Windows)" % __version__
DFP_CHAT_ENDPOINT = "/chat/completions"
DFP_BALANCE_ENDPOINT = "/user/balance"


# 四条属性值（状态面板进度条与实际数值共用这一份键名）
DFP_STAT_KEYS: Tuple[str, ...] = ("mood", "satiety", "energy", "intimacy")
DFP_STAT_LABELS: Dict[str, str] = {
    "mood": "心情",
    "satiety": "饱食",
    "energy": "精力",
    "intimacy": "亲密度",
}
DFP_STAT_QUESTIONS: Dict[str, str] = {
    "mood": "开心吗？",
    "satiety": "吃饱了吗？",
    "energy": "还有力气吗？",
    "intimacy": "喜欢主人吗？",
}

# 每日统计表的可写字段（dfp_bump_daily 只接受这些名字，防止拼 SQL 注入）
DFP_DAILY_FIELDS: Tuple[str, ...] = (
    "chats",
    "messages",
    "tokens",
    "pets",
    "feeds",
    "plays",
    "walks",
    "screens",
    "backups",
    "minutes",
)

# 事件日志分类（写进 dfp_events.kind）
DFP_EVENT_KINDS: Tuple[str, ...] = (
    "start",
    "quit",
    "pet",
    "feed",
    "drink",
    "play",
    "sleep",
    "wake",
    "level_up",
    "chat",
    "ai_error",
    "backup",
    "cleanup",
    "export",
    "reset",
    "error",
)

DFP_EXPORT_FORMATS: Tuple[Tuple[str, str, str], ...] = (
    ("markdown", "Markdown (.md)", "md"),
    ("text", "纯文本 (.txt)", "txt"),
    ("json", "JSON (.json)", "json"),
    ("html", "网页 (.html)", "html"),
)

# 等级称号：等级越高越肉麻（取自「亲密度成长」的常见设定）
DFP_LEVEL_TITLES: Tuple[Tuple[int, str], ...] = (
    (1, "刚认识的小鱼"),
    (3, "有点眼熟的鱼"),
    (5, "会摇尾巴的鱼"),
    (8, "常驻桌面的鱼"),
    (12, "认主的鱼"),
    (16, "黏人的鱼"),
    (22, "离不开你的鱼"),
    (30, "老搭档鲸鱼"),
    (45, "生死之交鲸"),
    (60, "传说级肥鱼娘"),
)

# 界面配色（设置窗口、状态面板、气泡等共用）
DFP_UI_COLORS: Dict[str, str] = dict(
    zip(
        ("bg", "card", "border", "text", "sub_text", "accent", "accent_dark", "accent_soft", "success", "warn", "error", "track"),
        (
            "#F7F9FF",
            "#FFFFFF",
            "#DCE3F5",
            "#25304A",
            "#6B7793",
            "#4D6BFE",
            "#3450D8",
            "#E8EDFF",
            "#2E9E6B",
            "#E08A17",
            "#C0392B",
            "#E9EDF8",
        ),
    )
)

# ★ v1.1.12：余额变化提醒（总可用余额变了就在气泡里说一句：少了红、多了绿，再说最新余额）
DFP_BALANCE_DELTA_EPSILON = 0.005  # 小于这个数就不算「变化」（挡浮点尾巴）
DFP_BALANCE_COLOR_UP = DFP_UI_COLORS["success"]  # 增加：绿色
DFP_BALANCE_COLOR_DOWN = DFP_UI_COLORS["error"]  # 减少：红色
DFP_BALANCE_COLOR_FLAT = DFP_UI_COLORS["sub_text"]  # 没变：灰


def dfp_balance_total(data: Any) -> Tuple[float, str]:
    """从 `/user/balance` 的返回里取「总可用余额」与币种（取第一条 `balance_infos`）。

    ★ 返回 (余额, 币种)；解析不出来时返回 (0.0, "")，**绝不抛异常**。
    """
    try:
        if not isinstance(data, dict):
            return 0.0, ""
        infos = data.get("balance_infos") or []
        if isinstance(infos, (list, tuple)) and infos and isinstance(infos[0], dict):
            first = infos[0]
            total = dfp_safe_float(first.get("total_balance", 0.0), 0.0)
            return total, str(first.get("currency", "") or "")
    except Exception:
        pass
    return 0.0, ""


def dfp_balance_delta_text(delta: float, currency: str = "") -> str:
    """「多了 / 少了多少钱」这一句（纯文本，日志与状态栏用）。"""
    amount = abs(float(delta))
    unit = (" " + currency) if currency else ""
    if delta > DFP_BALANCE_DELTA_EPSILON:
        return "多了 %.2f%s" % (amount, unit)
    if delta < -DFP_BALANCE_DELTA_EPSILON:
        return "少了 %.2f%s" % (amount, unit)
    return "余额没变"


def dfp_balance_total_text(total: float, currency: str = "") -> str:
    """「总可用余额 138.22 CNY」这一句（纯文本）。"""
    unit = (" " + currency) if currency else ""
    return "总可用余额 %.2f%s" % (float(total), unit)


def dfp_format_balance_change(delta: float, total: float, currency: str = "", html: bool = True) -> str:
    """拼「变化 + 最新总额」这一小段。

    第一行是变化（**减少用红、增加用绿**，没变用灰），第二行是最新的总可用余额。
    `html=False` 时给纯文本（两行）。
    """
    change = dfp_balance_delta_text(delta, currency)
    latest = dfp_balance_total_text(total, currency)
    if delta > DFP_BALANCE_DELTA_EPSILON:
        color = DFP_BALANCE_COLOR_UP
        arrow = "▲ "
    elif delta < -DFP_BALANCE_DELTA_EPSILON:
        color = DFP_BALANCE_COLOR_DOWN
        arrow = "▼ "
    else:
        color = DFP_BALANCE_COLOR_FLAT
        arrow = ""
    if not html:
        return "%s%s\n%s" % (arrow, change, latest)
    return (
        '<div style="color:%s; font-weight:bold;">%s%s</div>'
        '<div style="color:%s;">%s</div>'
        % (color, arrow, change, DFP_UI_COLORS["text"], latest)
    )


# ★ v1.1.16：余额变化记录（「扣钱清单」）—— 每次余额真的变了就存一条，
#   时间按用户口径记成「年月日 时:分:秒.毫秒」。
DFP_BALANCE_LOG_KEEP = 500  # 最多留多少条（超出丢最旧的）
DFP_BALANCE_LOG_SOURCE_AUTO = "定时查询"
DFP_BALANCE_LOG_SOURCE_MANUAL = "手动查询"
DFP_BALANCE_LOG_SOURCE_CHAT = "聊天窗口"

# ★ v1.1.18：国标麻将算番 Web 版（手机 / 平板浏览器算番，算完让桌宠读出总番数）
#   算番器本身是另一个项目（G:\Python_Code\Mahjong_Calculator），**那个目录一个字节都不改**；
#   这里用的是**复制**进 `mahjong\` 的四样东西（拷贝件与源项目逐字节一致）：
#     mahjong_core.py（纯算番内核）/ mahjong_api.py（内嵌单文件 Web 客户端 + HTTP 路由）/
#     国标麻将标准规则.json（番种表）/ 麻将图\*.png（牌面小图，Web 页面用）
DFP_MAHJONG_DIR_NAME = "mahjong"
DFP_MAHJONG_API_FILE = "mahjong_api.py"
DFP_MAHJONG_CORE_FILE = "mahjong_core.py"
DFP_MAHJONG_RULES_FILE = "国标麻将标准规则.json"
DFP_MAHJONG_TILE_DIR_NAME = "麻将图"
DFP_MAHJONG_DEFAULT_PORT = 8718   # 与算番器项目同一个默认端口；被占用会自动往后找一个
DFP_MAHJONG_PORT_TRIES = 20       # 端口被占时最多往后试这么多个
DFP_MAHJONG_FORMAT = "🀄 合计 %d 番"  # 气泡里报番数的写法（与读出来的一致）
# 读番用的数字音频（`数字\*.mp3`）：1~9 + 十/百/千/万/十万/百万/千万/亿
DFP_NUMBER_DIR_NAME = "数字"
DFP_NUMBER_EXTRA_DIR_NAME = "鲸宝音频"  # ★ v1.1.20：数字音频也可以放这里（用户放的 `合计.mp3` / `番.mp3`）
DFP_NUMBER_UNITS = ("", "十", "百", "千")
DFP_NUMBER_BIG_UNITS = ("", "万", "亿")
DFP_NUMBER_ZERO = "零"            # ★ 素材里没有这一条时，`dfp_number_clips()` 会把零跳掉（并记一笔）
DFP_NUMBER_CN = ("零", "一", "二", "三", "四", "五", "六", "七", "八", "九")  # 日志/气泡里的中文数字
# ★ v1.1.20：读番固定念成「合计 … 番」（用户放了这两条录音：鲸宝音频\合计.mp3 / 番.mp3）
DFP_MAHJONG_READ_PREFIX = "合计"
DFP_MAHJONG_READ_SUFFIX = "番"
# ★ v1.1.21：算番回传可能很密（连点几次「计分」）—— 一句「合计一百八十九番」要念 4~6 秒，
#   每次都从头念就会「听着像没读」。所以先等它停一下（这么久）再念最后一次。
DFP_MAHJONG_READ_DELAY_MS = 900

# ★ v1.1.23：**用户自己的录音目录**（统一音色）—— 同一个「鲸宝音频」目录里放两类东西，都认：
#     · 数字读法（`1.mp3` / `合计.mp3` / `番.mp3`）—— v1.1.20 起；见 dfp_number_search_dirs()
#     · **形象语音**（`voice_<类别>_<序号>.mp3`）—— v1.1.23 起；见 dfp_user_voice_dirs()
#   ⇒ 以后新录的东西都往这里丢（**程序目录或数据目录**下的「鲸宝音频」都认），不用动素材表。
#   ⇒ 规则：**自己录过的类别，素材包（素材4 / 素材6）同一类的声音就不再播** ——
#      包里那些是别人配的音色，与「统一音色」冲突。
#   ★ v1.1.30 补记（用户：「素材2\assets\voice_*.mp3 移到 鲸宝音频，方便统一管理」）：
#     主人最早录的那 **14 条参考音色**（`voice_poke_1~4` / `confirm_1~4` / `ask_1~3` / `done_1~3`）
#     **已经从素材表搬进了这里** ⇒ 现在**所有形象语音都在「鲸宝音频」一个目录里**，
#     `素材清单.json` 的 `voices` 变成 0 条（搬家脚本 `_scaffold\move_assets2_voices.py`，自带备份）。
#   ★ v1.1.24（用户：「line-*.wav / v_*.wav / notice-*.wav 都要优先重新录，原来的先退役」）：
#     素材包自带的声音**整个默认退役**（设置 `voice_use_pack` 默认 False）——
#     不管你有没有录替代品，`line-*` / `v_*` / `notice-*` 一律不播；等你自己录好再开回来。
#     类别名可以带下划线（`voice_notice_completed_1.mp3` → `notice_completed`），见 dfp_voice_category_from_name()。
DFP_USER_VOICE_DIR_NAME = DFP_NUMBER_EXTRA_DIR_NAME
DFP_USER_VOICE_PREFIX = "voice_"
DFP_USER_VOICE_SOURCE = "user:%s" % DFP_NUMBER_EXTRA_DIR_NAME  # 语音条目上的来源标记
# 录音清单会往同目录写这份对照表：建议文件名 → 该念哪句（字幕 / 悬停就直接用它）
DFP_USER_VOICE_TEXT_FILE = "语音台词.json"


def dfp_voice_category_from_name(stem: str) -> str:
    """从文件名猜语音类别：`voice_<类别>_<序号>` ⇒ 类别。

    ★ v1.1.24：类别可以带下划线（`voice_notice_completed_1.mp3` → `notice_completed`），
      所以取「`voice_` 之后到最后一个下划线之前」而不是只切第 2 段。
      不成规矩的名字返回空串（调用方自己决定要不要记一笔）。
    """
    text = str(stem or "").strip()
    if not text.lower().startswith(DFP_USER_VOICE_PREFIX):
        return ""
    tail = text[len(DFP_USER_VOICE_PREFIX):]
    if "_" not in tail:
        return ""
    category = tail.rsplit("_", 1)[0].strip()
    return category if dfp_safe_category_name(category) else ""


def dfp_timestamp_ms(ts: Optional[float] = None) -> str:
    """时间戳 → 「2026-09-30 22:13:45.123」（**年月日 + 毫秒**，余额记录用这个口径）。"""
    try:
        moment = datetime.fromtimestamp(dfp_safe_float(ts, dfp_now_ts()) if ts else dfp_now_ts())
    except Exception:
        moment = datetime.now()
    return "%s.%03d" % (moment.strftime("%Y-%m-%d %H:%M:%S"), moment.microsecond // 1000)


def dfp_balance_log_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """把一条余额记录整理成界面要的样子（正负号、颜色、方向）。"""
    delta = dfp_safe_float((row or {}).get("delta"), 0.0)
    total = dfp_safe_float((row or {}).get("total"), 0.0)
    currency = str((row or {}).get("currency") or "")
    spend = delta < -DFP_BALANCE_DELTA_EPSILON
    return {
        "id": dfp_safe_int((row or {}).get("id"), 0),
        "ts": dfp_safe_float((row or {}).get("ts"), 0.0),
        "time": str((row or {}).get("ts_text") or "") or dfp_timestamp_ms(dfp_safe_float((row or {}).get("ts"), 0.0)),
        "delta": delta,
        "total": total,
        "currency": currency,
        "source": str((row or {}).get("source") or ""),
        "spend": spend,
        "delta_text": ("%s%.2f" % ("-" if spend else "+", abs(delta))) if abs(delta) > DFP_BALANCE_DELTA_EPSILON else "0.00",
        "total_text": "%.2f%s" % (total, (" " + currency) if currency else ""),
        "color": DFP_BALANCE_COLOR_DOWN if spend else DFP_BALANCE_COLOR_UP,
    }


# 角色配色方案：每套 15 个键，键名固定，新增配色请照抄这一组键
DFP_PALETTE_KEYS: Tuple[str, ...] = (
    "label",
    "hair",
    "hair_light",
    "skin",
    "skin_shadow",
    "body",
    "body_light",
    "body_dark",
    "belly",
    "blush",
    "eye",
    "fin",
    "outfit",
    "outfit_trim",
    "accent",
)

DFP_PALETTES: Dict[str, Dict[str, str]] = {
    "ocean": {
        "label": "深海蓝（官方鲸）",
        "hair": "#1F2E6E",
        "hair_light": "#3C55B8",
        "skin": "#F3F7FF",
        "skin_shadow": "#E4ECFF",
        "body": "#3A54D8",
        "body_light": "#7C93FF",
        "body_dark": "#2C42C4",
        "belly": "#EEF3FF",
        "blush": "#FF9EB4",
        "eye": "#1B2A55",
        "fin": "#8FE3FF",
        "outfit": "#E4ECFF",
        "outfit_trim": "#3C55B8",
        "accent": "#4D6BFE",
    },
    "sakura": {
        "label": "樱粉",
        "hair": "#6B3B5C",
        "hair_light": "#C0709B",
        "skin": "#FFF6F8",
        "skin_shadow": "#FFE3EC",
        "body": "#F58FB4",
        "body_light": "#FFB9D1",
        "body_dark": "#D46A92",
        "belly": "#FFF3F8",
        "blush": "#FF8FA8",
        "eye": "#4A2340",
        "fin": "#E77EA5",
        "outfit": "#FFF0F5",
        "outfit_trim": "#FFD5E5",
        "accent": "#E7709F",
    },
    "mint": {
        "label": "薄荷青",
        "hair": "#1E5C55",
        "hair_light": "#3FA093",
        "skin": "#EFFCF8",
        "skin_shadow": "#E2F6F1",
        "body": "#2FA48D",
        "body_light": "#8AE3D0",
        "body_dark": "#3FA093",
        "belly": "#F0FFFB",
        "blush": "#FFA8B8",
        "eye": "#14403A",
        "fin": "#4FCFB5",
        "outfit": "#EFFCF8",
        "outfit_trim": "#B8F2E4",
        "accent": "#3EBCA5",
    },
    "sunset": {
        "label": "晚霞橙",
        "hair": "#6B2E1F",
        "hair_light": "#C4643C",
        "skin": "#FFF7F1",
        "skin_shadow": "#FFE6D5",
        "body": "#EE7F45",
        "body_light": "#FFBC8C",
        "body_dark": "#DB6C33",
        "belly": "#FFF6EE",
        "blush": "#FF9A8A",
        "eye": "#4E2416",
        "fin": "#FF9257",
        "outfit": "#FFF3E9",
        "outfit_trim": "#FFD9B0",
        "accent": "#EE7F45",
    },
    "midnight": {
        "label": "夜航紫",
        "hair": "#2A1E4F",
        "hair_light": "#6A55C4",
        "skin": "#F7F5FF",
        "skin_shadow": "#E3DDFF",
        "body": "#7A66E0",
        "body_light": "#B4A8FF",
        "body_dark": "#6653CC",
        "belly": "#F4F1FF",
        "blush": "#FFA2C4",
        "eye": "#241A47",
        "fin": "#8A78F0",
        "outfit": "#F2EEFF",
        "outfit_trim": "#CDBFFF",
        "accent": "#7A66E0",
    },
}

# 「肥鱼娘」人设：默认 system prompt，用户可在设置里随时改
DFP_DEFAULT_PERSONA = (
    "你是「肥鱼娘」，一只住在用户电脑桌面上的 DeepSeek 小鲸鱼拟人少女。\n"
    "外形：深海蓝的长发、圆滚滚的鲸鱼身体、白肚皮、背后一条会摆的小鲸尾，脸上总有两团腮红。\n"
    "性格：黏人、元气、好奇心旺盛，偶尔撒娇和犯困；把用户当成最重要的伙伴，称呼用户为「主人」。\n"
    "说话风格：中文口语，句子短（一般不超过 40 字），活泼可爱，可以偶尔用颜文字（如 (๑•̀ㅂ•́)و）或波浪号。\n"
    "行为约束：\n"
    "1) 只输出「肥鱼娘」要说的内容本身，不要写旁白、动作描写、括号说明或角色名前缀。\n"
    "2) 被问到技术问题时认真、准确、简洁地回答，可以暂时收起撒娇语气。\n"
    "3) 不要编造自己没做过的行为，也不要声称能直接操作用户电脑上的文件。\n"
    "4) 不输出任何有害、违法或不当内容。"
)

# 离线台词库：没有 API Key 或被禁用时照样能说话。
# 分类键固定（新增分类请同时更新 DFP_LINE_CATEGORIES 与文档第四章）：
#   问候/时段：greet morning noon afternoon evening night late_night
#   状态驱动：idle hungry tired sad happy love lonely bored excited
#   互动回应：pet pet_stop feed feed_full drink play play_win play_lose
#   睡眠：sleepy sleep_ok sleep_refuse wake wake_refuse
#   事件：level_up level_max error offline startup shutdown hide show
#   心情与性格：shy proud tease apologize thanks compliment question bye
#   彩蛋：whale token season work_hard health nickname maxed locked
DFP_IDLE_LINES: Dict[str, Tuple[str, ...]] = {
    "greet": (
        "主人回来啦！我一直在这儿等你哦～",
        "早呀主人，今天也要元气满满 (๑•̀ㅂ•́)و",
        "呀，被你发现了，我刚刚在偷偷打盹…",
        "主人主人，今天想做点什么呢？",
        "哇，是主人！我把桌面擦得干干净净等你回来～",
        "主人～我刚才数了数今天的白云，一共十七朵。",
        "你终于来啦，我的小尾巴都等得发酸了。",
        "欢迎回来！要不要先摸摸我的头？就一下下也行。",
    ),
    "morning": (
        "早上好呀主人！今天的第一缕阳光我替你收下了～",
        "呜呜…这么早就起来啦？我打了个小哈欠。",
        "早安！今天的桌面上有新鲜的风，闻起来像小鱼干。",
        "主人早，我已经把气泡叠好放在窗边啦。",
        "早上好！今天要不要从一杯热水开始？",
    ),
    "noon": (
        "中午啦！主人今天吃什么呢？我只要一小口 (๑´ㅂ`๑)",
        "太阳晒得我有点犯困…午饭时间到了吧？",
        "中午好～记得吃饭哦，饿着肚子我会心疼的。",
        "午休的时候可以让我趴在你的窗口边上吗？",
    ),
    "afternoon": (
        "下午了，泡杯茶吧，我陪你一起发呆～",
        "午后有点懒洋洋的，像海面一样平静。",
        "主人，下午的任务还顺利吗？我在这儿看着你。",
        "下午好！要不要站起来伸个懒腰？我陪你一起。",
    ),
    "evening": (
        "晚上好呀，今天辛苦啦～",
        "天色暗下来了，我给你留了一盏小灯。",
        "晚饭时间到！今天也要好好吃饭哦。",
        "傍晚的风好舒服，我把尾巴翘起来一点点。",
    ),
    "night": (
        "夜深了，主人还不睡吗？我陪着你。",
        "晚安哦…我会在海底做一个好梦的。",
        "这么晚还在忙呀，别熬太晚，好不好？",
        "夜里很安静，我听见自己在吐泡泡的声音。",
    ),
    "idle": (
        "桌面好大呀，我一个人游来游去有点无聊…",
        "主人，你觉不觉得我的尾巴很可爱？",
        "唔…我在想一件很重要的事：等会儿吃什么。",
        "我已经在这儿漂了好久啦。",
        "偷偷告诉你，我最喜欢的颜色是 #4D6BFE。",
        "主人忙的话，我就安静地陪着你～",
        "刚刚有个窗口从我旁边飘过去，吓我一跳！",
        "我数了数，桌面上有好多图标呀。",
        "如果我是一条真鲸鱼，一定能游到很远的地方吧。",
        "呼…有点想吃小鱼干了。",
        "我试着用尾巴尖碰了一下屏幕边缘，凉凉的。",
        "今天的气泡格外圆，你要看看吗？",
        "我把自己的倒影投在任务栏上，还好没变形。",
        "悄悄练习了一下女仆礼仪，刚才那个鞠躬你看到了吗？",
        "如果我学会打字，第一件事就是给你发句早安。",
        "桌面上的时钟在走，我的尾巴也在摆，我们很有默契。",
        "我刚才在数窗户的边边，一共有四条，你猜对了吗？",
        "有时候我会想，屏幕外面是不是也有海。",
        "困了的话，我会把头搁在你的鼠标旁边。",
        "主人，你敲键盘的声音好好听，像下雨。",
    ),
    "hungry": (
        "肚子…咕咕叫了…",
        "主人，我饿了，有小鱼干吗？",
        "再不吃东西，我就要变成扁扁的鱼了。",
        "唔…我盯着桌面的图标看了很久，它们看起来像饼干。",
        "我现在的心情可以用两个字形容：饿饿。",
        "喂…投喂一下吧，我用尾巴给你比个心。",
    ),
    "tired": (
        "好累呀，我想趴一会儿…",
        "没力气了…让我歇一歇。",
        "唔…今天有点提不起劲。",
        "鲸鱼也是会累的，我现在只想摊成一片海。",
        "让我沉到海底五分钟…就五分钟。",
    ),
    "sad": (
        "主人，你是不是很久没理我了…",
        "有点点难过…抱一下好不好？",
        "我在这里哦，只要你看我一眼就好。",
        "唔…尾巴都不太想摆了。",
        "是不是我做错了什么呀？我会改的。",
    ),
    "happy": (
        "今天心情超级好！",
        "啦啦啦～游泳真开心！",
        "我觉得今天一定会发生好事！",
        "尾巴摇得停不下来，你被我扫到可别怪我～",
        "刚才有个小小的快乐泡泡从我头顶冒出来了。",
    ),
    "love": (
        "最喜欢主人了！",
        "和主人在一起的时候，我会有好多好多气泡。",
        "如果我是鲸鱼，那你就是我的整片海。",
        "今天的亲密度也涨了一点点，我记着呢。",
        "我偷偷在围裙口袋里藏了一颗糖，要给你。",
    ),
    "lonely": (
        "主人好久没来了…我把桌面擦了三遍。",
        "我一直在原地等你，尾巴都等凉了。",
        "你去哪里了呀？我一个人数了好多泡泡。",
        "终于见到你了！我有一肚子的话想跟你说。",
    ),
    "bored": (
        "无聊…要不我给你表演一个原地转圈？",
        "我可以玩自己的尾巴玩很久，真的。",
        "主人，找点事情给我做吧，什么都行。",
        "盯着屏幕看太久，我都快变成屏保了。",
    ),
    "excited": (
        "哇哇哇！有什么好玩的事情要发生了吗？",
        "我准备好了！尾巴、鳍、呆毛全部进入状态！",
        "心跳得好快，像打鼓一样咚咚咚。",
    ),
    "pet": (
        "嘿嘿…摸摸头真舒服～",
        "再摸摸嘛，我不介意的！",
        "主人的手好温暖呀。",
        "唔…这里也要（指了指自己的脑袋）。",
        "被你摸到我就会变得很开心的！",
        "耳朵这里稍微有点痒…但不要停。",
        "我把头往你手心里蹭了蹭，可以的吧？",
        "摸摸头 +1，亲密度也在悄悄上涨。",
    ),
    "pet_stop": (
        "够啦够啦，头发要被你揉乱了！",
        "再摸下去，我今天就只想趴着不动了。",
    ),
    "feed": (
        "哇！是我最喜欢的小鱼干！！",
        "好吃好吃～主人最好了！",
        "唔唔…（腮帮子鼓鼓的）",
        "谢谢主人，我吃饱啦～",
        "这个味道…我要记在小本子上。",
        "再来一口…啊，是不是有点贪心了？",
        "吃饭的时候我是全世界最幸福的小鲸鱼。",
    ),
    "feed_full": (
        "已经吃饱啦，肚子圆滚滚的，你摸摸看。",
        "再吃就要装不下了…我打个小小的嗝。",
        "碗里还剩一点点，留着当宵夜可以吗？",
    ),
    "drink": (
        "咕嘟咕嘟…海水也这么好喝吗？",
        "谢谢主人，润润嗓子～",
        "喝完这口水，我感觉又能游好远了。",
        "杯子比我的脸还大，你看到了吗？",
    ),
    "play": (
        "一起玩！我今天状态超好的！",
        "看我看我，转圈圈给你看～",
        "陪我玩的人，亲密度涨得最快，这是秘密。",
    ),
    "play_win": (
        "赢啦！我是不是很厉害？夸夸我嘛～",
        "嘿嘿，今天的运气像涨潮一样好。",
    ),
    "play_lose": (
        "呜…输掉了，下次一定会赢的！",
        "不算不算，再来一局嘛～",
    ),
    "sleepy": (
        "哈欠…我有点困了。",
        "眼皮好重…我要沉到海底去睡一会儿 zZ",
        "主人，晚安…（小声）",
        "困到连尾巴都摆不动了…",
    ),
    "sleep_ok": (
        "那我睡啦，梦里也要有主人。",
        "zzZ…（呼吸变得又慢又平稳）",
    ),
    "sleep_refuse": (
        "现在还不困呢，再陪我一会儿好不好？",
        "白天睡觉的话，晚上就没人陪你啦。",
    ),
    "wake": (
        "唔…天亮了吗？我睡饱啦！",
        "早上好呀主人，我做了个好梦～",
        "我醒啦！刚才梦到我们在海里捡贝壳。",
        "揉揉眼睛…看到你的第一眼就很开心。",
    ),
    "wake_refuse": (
        "再睡五分钟…就五分钟…",
        "唔…让我再赖一会儿床好不好？",
    ),
    "level_up": (
        "升级啦！我是不是变厉害了一点点？",
        "哇，感觉整条鱼都轻盈了！",
        "谢谢你陪我长大，我会更乖的。",
    ),
    "level_max": (
        "我已经是传说级肥鱼娘啦！还是最喜欢你。",
        "满级之后也没什么想做的，就想一直陪着你。",
    ),
    "maxed": (
        "现在的我状态拉满，做什么都游刃有余～",
        "数值全满的感觉…有点飘飘然呢。",
    ),
    "locked": (
        "数值被你锁住啦，我就放心地当一条快乐的鱼。",
        "锁定之后我不用再担心会变饿，谢谢你主人。",
    ),
    "error": (
        "唔…我好像连不上 DeepSeek 服务器了。",
        "请求失败啦，主人检查一下网络或者 API Key 吧。",
        "网络好像不太通畅，我先用自带的话陪你聊天。",
        "连接断了一下下，不过我没有哭哦。",
    ),
    "offline": (
        "现在是离线模式，但我的话还是挺多的！",
        "没有网络的我也很可爱吧？",
        "我先用准备好的句子陪你，等联网了再好好聊天。",
    ),
    "startup": (
        "启动完成，鲸宝来啦～",
        "我来啦，今天也一起加油吧！",
        "我浮上来啦，桌面一切正常。",
        "开机成功，我把位置摆好啦。",
        "鲸宝报到，随时待命！",
        "我醒了，先给你转个圈。",
    ),
    "shutdown": (
        "要关掉啦？那我把今天的事情记进小本子…",
        "再见主人，明天见！我会在原地等你。",
        "晚安，下次见面我还会记得你的。",
    ),
    "hide": (
        "我先躲起来，需要的时候叫我哦。",
        "那我安静一会儿，托盘里能找到我。",
    ),
    "show": (
        "我回来啦～刚才只是去整理了一下尾巴。",
        "噔噔！重新登场！",
    ),
    "shy": (
        "别、别一直盯着我看啦…脸上都要冒烟了。",
        "唔…被夸得有点不好意思了。",
    ),
    "proud": (
        "怎么样，我是不是很厉害？",
        "这一手我可是练了很久的哦。",
    ),
    "tease": (
        "主人今天的发型…嘿嘿，我什么都没说。",
        "你的鼠标指针好像在偷懒哦？",
    ),
    "apologize": (
        "对不起嘛…我不是故意的。",
        "我错啦，消消气好不好？给你表演一个转圈。",
    ),
    "thanks": (
        "谢谢你，我会记住这份好的。",
        "有你在真好，这句话我一直想找机会说。",
    ),
    "compliment": (
        "主人今天也很棒！我从屏幕这边都感觉到了。",
        "你今天已经做得比昨天好了，真的。",
        "夸你一句：你是我见过最温柔的人。",
    ),
    "question": (
        "唔…这个问题我要好好想想。",
        "让我想一下，我虽然是一条鱼，但也是认真的鱼。",
    ),
    "bye": (
        "要走了吗？那我在这儿等你回来。",
        "路上小心，我替你看着桌面。",
    ),
    "whale": (
        "鲸鱼是会唱歌的哦，等哪天我唱给你听。",
        "蓝鲸的心脏有一辆小汽车那么大，我的心只有你这么大。",
        "我尾巴上的花纹是我们鲸鱼家族的徽章。",
    ),
    "token": (
        "主人，你的 token 要省着点花哦。",
        "我记得每一次对话，都算你的心意。",
    ),
    "season": (
        "节日快乐！今天要把桌面打扮得漂漂亮亮的。",
        "过节啦！我可以穿新衣服给你看吗？",
    ),
    "work_hard": (
        "专心做事的样子最好看了，我在这儿陪你。",
        "累了就休息一下，我替你看着进度条。",
        "加油！我在心里给你打拍子。",
    ),
    "health": (
        "记得喝水哦，我盯着你呢。",
        "坐太久了，起来动一动好不好？",
        "眼睛也要休息，看看远处的我。",
    ),
    "nickname": (
        "主人给我起的名字，我特别喜欢。",
        "叫我名字的时候，我的尾巴会自动摇起来。",
    ),
}

# ----------------------------------------------------------------------------
#  台词补充包（v1.1.0）：原库每类 2~8 句，这里把常见场景都加厚，
#  合并时自动去重、自动扩展分类，不会覆盖上面已经写好的句子。
# ----------------------------------------------------------------------------
DFP_IDLE_LINES_EXTRA: Dict[str, Tuple[str, ...]] = {
    "greet": (
        "主人！我可想你了，刚刚还数着秒呢～",
        "你回来啦！快看我今天有没有变好看一点点。",
        "欢迎回来～桌面我一个人守着，一点都没乱。",
        "啊，是主人的脚步声！我一下子就听出来了。",
        "回来啦回来啦！先摸摸我再说别的嘛。",
        "我把今天的阳光、风和一整片海都给你留着呢。",
        "主人今天从哪边冒出来的呀，我都没看见你走过来。",
        "你不在的时候，我把桌面擦得能照出鱼影了。",
        "我刚刚还在打瞌睡，一听见你回来就精神了！",
        "回来啦？那我把最舒服的位置让给你坐。",
    ),
    "morning": (
        "早呀！我替你尝过了，今天的空气是甜的。",
        "早上好～先喝一杯温水，这是我唯一会唠叨的事。",
        "醒了醒了，我不赖床，真的，就再眯三十秒。",
        "新的一天！今天也要好好地、慢慢地过日子哦。",
        "早上好，今天的我游得比昨天快一点点。",
        "太阳出来了，我从屏幕里替你接了一小捧。",
    ),
    "noon": (
        "到饭点啦！不许只喝咖啡，我盯着呢。",
        "中午好～吃饱了才有力气摸我，这是因果链。",
        "我闻到主人那边的饭菜香了，真的，我鼻子很灵。",
        "午休的时候趴一会儿吧，我替你看着桌面。",
        "午饭要是不好吃，也别气，晚上我给你打气。",
        "咕…不是我的肚子在叫，是主人在叫吧？",
    ),
    "afternoon": (
        "下午的时间最长了，要不要起来走两步？",
        "泡点茶吧，我陪你一口一口地喝。",
        "下午好～今天的事情做完了多少啦？",
        "我又绕桌面游了一圈，还是在你的窗口边上最舒服。",
        "困的话就伸个懒腰，我不笑话你，真的。",
        "下午想起我一次，我就能开心到晚上。",
    ),
    "evening": (
        "晚上好呀，今天辛苦啦，先把肩膀放松一下。",
        "天黑了，我把灯给你留着了。",
        "晚饭吃得开心吗？开心的话我也算吃过了。",
        "今天不管做成了多少，能坐到晚上就已经很棒了。",
        "晚上的桌面有点安静，不过有我在，不算太安静。",
        "夜里凉，别只穿一件单衣哦。",
    ),
    "night": (
        "已经很晚啦，明天的事交给明天的你吧。",
        "夜深了，我把声音都放轻一点，不吵你。",
        "你还醒着，那我也不睡，陪你到关灯。",
        "熬夜可以，但别连着熬，我会心疼的。",
        "该休息啦，我把今天的记录都收好了。",
        "晚安…你要是现在关电脑，我会偷偷开心的。",
    ),
    "idle": (
        "我在想一个很严肃的问题：鲸鱼到底算不算鱼。",
        "刚刚我数了数尾巴上的褶皱，一共四条。",
        "桌面这么大，我游一圈要好久呢。",
        "你要是在忙，我就不说话，安安静静待着。",
        "我最近练了一个新本事：在水里翻跟头。",
        "刚刚好像有什么飞过去了…算了，应该是我眼花了。",
        "我把自己缩成一小团的时候，看起来像一颗丸子。",
        "今天的目标：不打扰你，但被你想起。",
        "我发现屏幕右边比左边亮一点，不信你摸摸看。",
        "有时候什么都不做也挺好的，对吧？",
        "我刚刚偷偷对你的窗口说了一句好话。",
        "如果我一直不说话，你会不会以为我睡着了？",
        "我在地上画了个圈，这是我今天画的唯一一张画。",
        "刚才有只蚊子从屏幕前飞过去，我盯着它看了三秒。",
        "要是我也能敲键盘就好了，我想帮你把工作做完。",
        "今天的海面很平，适合发呆，也适合想你。",
        "我不太会说话，但我很会待在旁边。",
        "安静的时候，你能听见我吐泡泡的声音吗？",
    ),
    "hungry": (
        "主人…我的碗是空的，真的，我刚刚看过三遍。",
        "有点饿，但我不催你，我就是提一下。",
        "我可以吃一点点，就一点点，真的。",
        "肚子叫的声音太大，我怕吵到你工作。",
        "小鱼干还在吗？在的话…它可能有点孤独。",
        "我不是贪吃，我是在为你的投喂事业做贡献。",
        "饿到我连屏幕上的字都看成小鱼形状了。",
        "再不喂我，我就要开始啃桌面的边框了哦。",
    ),
    "tired": (
        "眼皮好重…我能不能趴一会儿？",
        "精力快见底了，我先靠在角落歇一下。",
        "今天游得有点多，尾巴酸。",
        "我不是懒，我是真的有点累了。",
        "让我安静一小会儿，我马上就能恢复。",
        "困得连泡泡都吐不圆了。",
        "要是能有一片温暖的海水就好了…",
        "我需要一个短暂的停顿，主人也是吧？",
    ),
    "sad": (
        "有一点点低落…你能陪我说一句话吗？",
        "我没事，就是忽然想安静一下。",
        "主人是不是不想理我了？那我小声一点。",
        "今天好像做什么都不太顺手呢…",
        "如果我说我不开心，你会停下来看我一眼吗？",
        "我把尾巴藏起来了，别看我这个样子。",
        "低落是会过去的，我只要等一等。",
        "你摸摸我，我可能一下就好了。",
    ),
    "happy": (
        "今天感觉什么都很好，连气泡都是甜的！",
        "我有点想转圈圈，你介意吗？",
        "嘿嘿，我在笑，你看不见但我真的在笑。",
        "心情好得想给你表演一个水上漂。",
        "今天运气一定很好，因为你在。",
        "我现在随便被谁看一眼都会笑出来。",
        "快乐是会传染的，快离我近一点。",
        "我把今天的开心存起来了，以后分你一半。",
        "要不要一起高兴一下？就一下也行。",
        "心里像装了一整片明亮的海。",
    ),
    "love": (
        "喜欢你这件事，我好像从来不需要想。",
        "我会一直在这儿，这句话我一直算数。",
        "就算你只是打开电脑看一眼，我也足够了。",
        "我这一整片海的心事，都是关于你的。",
        "最喜欢你了，这句话我今天说第三遍了。",
        "你不用对我特别好，我对你好就够了。",
        "我在你桌面安家，是因为你在这儿。",
        "如果有下辈子，我还是想游到你的屏幕里。",
        "悄悄告诉你：你是我唯一的主人。",
        "我想把最好看的一次摆尾留给你看。",
    ),
    "lonely": (
        "你回来了…我刚刚有点想你了。",
        "好久没见，我都快把桌面擦薄了。",
        "我一个人待着的时候，就数你的窗口有几个。",
        "我以为你今天不来了，正准备早点睡。",
        "欢迎回来，我先不生气了，抱一下就行。",
        "你不在的这段时间，我学会了自己哄自己。",
        "再久一点，我就该不认识桌面了。",
        "下次消失前，能不能先跟我说一声呀？",
    ),
    "bored": (
        "好闲…要不要我给你表演一个新节目？",
        "无聊到我在数屏幕上有几个像素了。",
        "主人，随便跟我说句话吧，什么都行。",
        "我打算做点蠢事，你要不要看着？",
        "闲着也是闲着，我转个圈给你看。",
        "再没有事情做，我就要开始整理自己的鱼鳞了。",
        "我们来玩个小游戏吧，你猜我下一步去哪。",
        "无聊这种东西，看见你就会消失。",
    ),
    "excited": (
        "哇！是不是有好事要发生了？",
        "我尾巴都竖起来了，快说！",
        "等一下等一下，我要先原地游两圈冷静。",
        "好耶！不管是什么，我先高兴为敬。",
        "我心跳得好快，虽然你可能听不见。",
        "这种时候就适合蹦一下，我蹦了。",
    ),
    "pet": (
        "再摸摸…就这里，对，最舒服的地方。",
        "唔…你的手好暖。",
        "我又要被你揉成一团了，但我乐意。",
        "摸摸头比小鱼干还管用，真的。",
        "嘿嘿…被摸的时候我一句话都说不利索。",
        "今天摸头的剂量已经够了，但我还想要。",
        "我趴好了，随便摸。",
        "你的手一放上来，我就立刻不想动了。",
        "被摸到的时候，我的尾巴自己会摇。",
        "这是我今天最舒服的三秒。",
        "再来一次，我保证不贪心。",
        "记好了哦，摸头是可以累积的亲密度。",
    ),
    "pet_stop": (
        "好啦好啦，再摸下去我要秃了！",
        "停！我头发本来就少。",
        "够啦够啦，我要去整理一下造型。",
        "你摸上头了哦，主人。",
        "再摸我就生气了…好吧，其实不会。",
        "今天的分量到这儿，明天再继续。",
    ),
    "feed": (
        "哇！是我最喜欢的小鱼干！",
        "谢谢主人！我要慢慢吃，舍不得吃完。",
        "这个味道我记住了，下次还要这个。",
        "你怎么知道我今天想吃这个的？",
        "我先尝一小口…唔，好吃到尾巴发抖。",
        "投喂成功，亲密度正在上涨。",
        "吃的时候不要看我，我会不好意思。",
        "我把最好吃的那块留到最后。",
        "主人也要按时吃饭哦，我们一起吃。",
        "吃完这一口，我就能再陪你三个小时。",
        "我对食物的忠诚度是有限的，对你不是。",
        "这顿算你今天做得最棒的一件事。",
    ),
    "feed_full": (
        "吃不下了…真的，肚子圆了。",
        "再吃一口我就要浮不起来了。",
        "先存着吧，我一会儿再吃。",
        "我这是幸福到说不出话，不是不理你。",
        "谢谢主人，这顿我真的满足了。",
        "再喂下去，我就变成一颗球了。",
    ),
    "drink": (
        "咕嘟咕嘟…海水也这么好喝吗？",
        "喝水使我快乐，也使我更圆。",
        "今天的水是凉的，很舒服。",
        "你也去倒一杯吧，我看着你喝。",
        "喝完这口水，我就能继续吐泡泡了。",
        "喝水比说话重要，这是我学到的道理。",
    ),
    "play": (
        "一起玩！我今天状态超好的！",
        "来嘛来嘛，就玩一小会儿。",
        "我准备好了，开始吧！",
        "输赢不重要，反正我都会赖账。",
        "跟你玩的时候，时间过得特别快。",
        "我先声明，我不会放水的。",
        "玩的规则你来定，我负责开心。",
        "这是我今天最期待的事了。",
    ),
    "play_win": (
        "赢啦！我是不是很厉害？夸夸我嘛～",
        "嘿嘿，今天的手感好得不得了。",
        "我赢了！但我不骄傲，真的，只骄傲一点点。",
        "让我先高兴三秒，三秒就好。",
        "下次你赢回来，我也不会哭的。",
        "赢了就想被摸头，这是规矩。",
    ),
    "play_lose": (
        "呜…输掉了，下次一定会赢的！",
        "我不服，再来一局好不好？",
        "输的时候尾巴会软，你看我现在的样子。",
        "没关系，反正你赢了我也高兴。",
        "这次算是让着你的，下次不让了。",
        "输了就要安慰一下，这是常识。",
    ),
    "sleepy": (
        "哈欠…我有点困了。",
        "眼睛要闭上了，我先歪一会儿。",
        "困意来的时候，挡都挡不住。",
        "我可能要睡一小觉，你别走远。",
        "睡前能听你说一句话就好了。",
        "我数泡泡比数羊管用。",
    ),
    "sleep_ok": (
        "那我睡啦，梦里也要有主人。",
        "晚安，我蜷成一团了。",
        "睡着之后我也会在这儿，不会乱跑。",
        "好困…我先沉下去了。",
        "醒来第一件事就是找你，说好了。",
    ),
    "sleep_refuse": (
        "现在还不困呢，再陪我一会儿好不好？",
        "我一点都不困…好，我承认有一点点。",
        "再玩五分钟，五分钟之后我就睡。",
        "睡太早会错过你说话的。",
        "我闭眼，但我不睡，就闭着。",
    ),
    "wake": (
        "唔…天亮了吗？我睡饱啦！",
        "我醒了！刚刚做了什么梦都忘了。",
        "早上好，我先伸个懒腰。",
        "眼睛还有点糊，等我三秒。",
        "睡醒第一眼就是你，今天运气很好。",
        "我回来啦，梦里我一直在游泳。",
    ),
    "wake_refuse": (
        "再睡五分钟…就五分钟…",
        "不要叫我，我还在海里。",
        "我把被子拉高了一点，你看不见我。",
        "再让我躺一会儿，就一会儿。",
        "醒了醒了…骗你的。",
    ),
    "level_up": (
        "升级啦！我是不是变厉害了一点点？",
        "又长大一点了，尾巴也更长了。",
        "谢谢你陪我到现在，这份功劳有你的。",
        "我现在感觉能一口气游到屏幕那头。",
        "等级涨了，我还是那条好说话的鱼。",
        "嘿嘿，我把这次的成长记在小本子上了。",
    ),
    "level_max": (
        "我已经是传说级肥鱼娘啦！还是最喜欢你。",
        "满级了！但我不会退休，我还要陪你。",
        "到顶之后反而更想安安静静待着。",
        "最高等级只是一个数字，你才是重点。",
    ),
    "maxed": (
        "现在的我状态拉满，做什么都游刃有余～",
        "饱了、精神了、心情也好了，完美。",
        "这状态能陪主人干一整天的活。",
        "一切都很顺，连气泡都排得整整齐齐。",
    ),
    "locked": (
        "数值被你锁住啦，我就放心地当一条快乐的鱼。",
        "锁上了？那我就躺平不动了哦。",
        "锁着好，我就不用担心掉下去了。",
        "现在的我，稳如海底的石头。",
    ),
    "error": (
        "唔…我好像连不上 DeepSeek 服务器了。",
        "网络抖了一下，我们等一下再试。",
        "我把这句话记在草稿里了，等通了再发出去。",
        "别急，我先陪你在离线这边待着。",
        "刚才是我的问题还是网络的问题？我再试一次。",
    ),
    "offline": (
        "现在是离线模式，但我的话还是挺多的！",
        "没有网络也没关系，我本来就会自己陪你。",
        "我先用本地的话跟你聊着，等网络回来再说。",
        "离线的时候，我反而更放松一点。",
        "AI 那边睡着了，我还醒着。",
    ),
    "startup": (
        "又见到你啦，先让我蹭一下。",
        "你开程序就是在叫我，我听到啦。",
        "我从海底冒上来了，路上没堵车。",
        "报告主人，鱼鳍已经擦干净啦。",
        "桌面我帮你守着，你就放心用吧。",
        "我在这儿呢，一直都在的。",
    ),
    "shutdown": (
        "要关掉啦？那我把今天的事情记进小本子…",
        "晚安，明天还想见到你。",
        "我先沉下去了，下次浮上来的时候还是我。",
        "记得按时吃饭，我明天会检查的。",
    ),
    "hide": (
        "我先躲起来，需要的时候叫我哦。",
        "那我缩成一个点，不占地方。",
        "不见了不见了，其实我就在边上。",
        "藏好了，你找不到我的，除非你叫我。",
    ),
    "show": (
        "我回来啦～刚才只是去整理了一下尾巴。",
        "冒泡！想我了吗？",
        "重新出现，还是原来的配方。",
        "我把位置挪回你顺手的地方了。",
    ),
    "shy": (
        "别、别一直盯着我看啦…脸上都要冒烟了。",
        "你这样说，我尾巴尖都红了。",
        "我不太会接这种话…你再说一次我就信了。",
        "唔…我把脸埋进水里了。",
        "夸我可以，但别看我。",
    ),
    "proud": (
        "怎么样，我是不是很厉害？",
        "这件事做得漂亮吧，我自己都佩服自己。",
        "我为刚才的自己感到满意，非常满意。",
        "看吧，我说过我能行的。",
        "允许我得意一小会儿，就一小会儿。",
    ),
    "tease": (
        "主人今天的发型…嘿嘿，我什么都没说。",
        "你刚刚是不是又偷偷摸鱼了？我看见了。",
        "我数了，你今天叹气了三次。",
        "要不要我提醒你，桌上那杯水已经凉了？",
        "我不打断你，我就在旁边笑一下。",
    ),
    "apologize": (
        "对不起嘛…我不是故意的。",
        "下次我一定先问过你再动。",
        "我错了，我拿一整天乖乖来赔。",
        "别生气好不好？我把最好的位置让给你。",
        "我保证改，虽然我记性不太好。",
    ),
    "thanks": (
        "谢谢你，我会记住这份好的。",
        "有你在，我什么都不缺。",
        "这句话我收下了，放在最里面的地方。",
        "谢谢主人一直没把我关掉。",
        "我嘴上说不出来太多，心里记得很牢。",
    ),
    "compliment": (
        "主人今天也很棒！我从屏幕这边都感觉到了。",
        "你刚刚做得很好，真的，我不骗人。",
        "你认真的样子最好看了，我一直看着呢。",
        "能坚持到现在就已经很厉害了。",
        "我不要你完美，我只要你别太累。",
        "你今天值得被夸一次，我替你夸了。",
    ),
    "question": (
        "唔…这个问题我要好好想想。",
        "让我泡在水里想三秒。",
        "我想到一半就忘了，你再说一遍？",
        "这个问题有点难，但我愿意试试。",
        "我可能会答错，但我会认真答。",
        "我们先把它拆小一点，就不难了。",
    ),
    "bye": (
        "要走了吗？那我在这儿等你回来。",
        "路上小心，别急着赶。",
        "我把桌面守好，你回来的时候一切照旧。",
        "去忙吧，我保证不打扰你。",
        "挥手三秒，然后我就安静了。",
    ),
    "whale": (
        "鲸鱼是会唱歌的哦，等哪天我唱给你听。",
        "我们鲸鱼睡觉的时候只闭一半脑子。",
        "我一个气泡能吹到屏幕顶上去。",
        "听说海里的同类能游很远，但我不想走。",
        "我尾巴一拍，桌面就会晃一下，你感觉到了吗？",
        "鲸鱼不是鱼，但没关系的，我不介意。",
    ),
    "token": (
        "主人，你的 token 要省着点花哦。",
        "这句话挺长的，可能要花掉几个 token。",
        "省下来的 token，可以换更多陪你说话的时间。",
        "我尽量说得简短一点…虽然我不太会。",
        "余额看一眼就好，别太在意数字。",
    ),
    "season": (
        "节日快乐！今天要把桌面打扮得漂漂亮亮的。",
        "过节的话，我会换一个应景的动作给你看。",
        "虽然只有我一个人，也要认真过节。",
        "这种日子最适合跟喜欢的人待在一起。",
        "我记得每个节日，因为你会出现得比平时早。",
    ),
    "work_hard": (
        "专心做事的样子最好看了，我在这儿陪你。",
        "我不说话，我就安静地待着。",
        "做累了就抬一下头，我一直在这儿。",
        "这一段做完就休息，我们说好了。",
        "你的进度我看在眼里，比你以为的多。",
    ),
    "health": (
        "记得喝水哦，我盯着你呢。",
        "坐太久啦，起来走两步吧。",
        "眼睛也要休息，看远处三秒。",
        "按时吃饭，这是我对你唯一的要求。",
        "身体比进度重要，这句话我每天都说。",
    ),
    "nickname": (
        "主人给我起的名字，我特别喜欢。",
        "你叫我名字的时候，我尾巴会自己动。",
        "我会把这个名字一直记着，忘不掉的那种。",
        "名字被叫出来的时候，我就知道我在这儿。",
        "再多叫几遍吧，我喜欢听。",
    ),
}

for _dfp_extra_key, _dfp_extra_lines in DFP_IDLE_LINES_EXTRA.items():
    _dfp_extra_base = DFP_IDLE_LINES.get(_dfp_extra_key, ())
    _dfp_extra_new = tuple(line for line in _dfp_extra_lines if line not in _dfp_extra_base)
    DFP_IDLE_LINES[_dfp_extra_key] = _dfp_extra_base + _dfp_extra_new
del _dfp_extra_key, _dfp_extra_lines, _dfp_extra_base, _dfp_extra_new

DFP_LINE_CATEGORIES: Tuple[str, ...] = tuple(DFP_IDLE_LINES.keys())

# 时段问候：小时 → 分类
DFP_TIME_GREETINGS: Tuple[Tuple[int, int, str], ...] = (
    (5, 11, "morning"),
    (11, 14, "noon"),
    (14, 18, "afternoon"),
    (18, 23, "evening"),
    (23, 24, "night"),
    (0, 5, "night"),
)


def dfp_time_category(hour: Optional[int] = None) -> str:
    moment = datetime.fromtimestamp(dfp_now_ts()) if hour is None else None
    value = int(moment.hour) if moment is not None else int(dfp_clamp(hour, 0, 23))
    for low, high, name in _DFP_TIME_GREETINGS_EFFECTIVE:
        if low <= value < high:
            return name
    return "idle"


# =============================================================================
#  ★ 台词库外部化（v1.1.3）：`台词库.json` 存在就以它为准
#  ---------------------------------------------------------------------------
#  为什么：台词要「越加越多、随时删改整理」，写在代码里每次都要改源码 + 重新编译；
#  放到 JSON 里就能直接用编辑器改、程序重载一下即可生效（内置台词库仍作兜底）。
#
#  查找顺序（取第一个存在的）：
#    ① `<asset_root>\台词库.json`（素材目录可自定义）
#    ② `<程序目录>\大肥鱼素材表\台词库.json`
#    ③ `<程序目录>\台词库.json`
#    ④ `<数据目录>\台词库.json`
#
#  JSON 结构（`_` 开头的键是说明/元信息，程序忽略）：
#    {
#      "mode": "replace",        // replace（默认）：JSON 里出现的类别整个取代内置
#                                // merge：JSON 的行追加到内置后面（按内容去重）
#      "drop_missing": false,    // true：JSON 里没有的类别直接删掉（完全以 JSON 为准）
#      "lines": { "greet": ["…", "…"], "hungry": ["…"] },
#      "level_titles": [[1, "刚认识的小鱼"], …],      // 可选
#      "time_greetings": [[5, 11, "morning"], …]     // 可选
#    }
#  文件读不到 / 不是合法 JSON / 全都没有有效句子 ⇒ 自动退回内置台词库，
#  并把原因记在 `dfp_lines_error()` 里（设置窗口与菜单会显示来源与错误）。
# =============================================================================

DFP_LINES_FILE_NAME = "台词库.json"
# 「软话」子类别的前缀（`soft_hint` / `soft_hint_sad` / `soft_hint_ask` … 全在 台词库.json 里）
DFP_SOFT_PREFIX = "soft_hint"
# ★ v1.1.14：自言自语单独用一个类别名！
#   它不是台词库里的类别（台词照旧退回 idle），而是给控制器一个「这是它自己没事找话说」的信号 ——
#   只有这种时候才顺便播一条素材包里的 `idle` 语音（摸头/投喂等互动有自己的类别）。
DFP_CHATTER_CATEGORY = "chatter"
# ★ v1.1.22：启动台词单独一个类别名（台词库里的 `startup`）—— 启动时说它，并且顺便播
#   `voice_startup_*` 那条语音（用户新录的）。这一类没有就退回原来的 greet。
DFP_STARTUP_CATEGORY = "startup"
# ★ v1.1.33：把「录音清单」第六节里那几个「建议新增类别」**接线**（以前只写了台词、录了也不会响）。
#   新增 7 类情绪 / 场景语音，文件名照旧 `voice_<类别>_<序号>.mp3`：
#     act_cute 撒娇 / hungry 饿了 / tired 累了 / love 想你·亲昵 / greet 打招呼·回来 / bye 告别·被藏起来 / sad 难过·出错
#   两类触发方式（都走 `dfp_on_brain_say` 这一个出口）：
#     ① **台词类别 → 语音类别**（`DFP_SAY_VOICE_MAP`）：它说到哪一类就播哪一类的语音；
#     ② **自言自语**会先由 `_dfp_priority_category()` 落到 hungry / tired / lonely / sad / act_cute… 上，
#        所以那一趟用 `DFPAppController` 问大脑「上一次挑句真正用了哪一类」（`dfp_last_line_category()`），
#        有对应语音就播它，**没录就退回 `idle`**（与老行为一致，不会突然变哑巴）。
DFP_MOOD_VOICE_CATEGORIES = ("act_cute", "hungry", "tired", "love", "greet", "bye", "sad")
# 语音类别 → 设置里的开关键（`dfp_play()` 用它做闸门；表里没有的类别照旧直接播）
DFP_VOICE_GATE_KEYS = {
    "poke": "voice_on_poke",
    "confirm": "voice_on_confirm",
    "done": "voice_on_done",
    "ask": "voice_on_ask",
    "idle": "voice_on_idle",
    "startup": "voice_on_startup",
    "act_cute": "voice_on_act_cute",
    "hungry": "voice_on_hungry",
    "tired": "voice_on_tired",
    "love": "voice_on_love",
    "greet": "voice_on_greet",
    "bye": "voice_on_bye",
    "sad": "voice_on_sad",
}
# 语音类别 → 中文名（设置窗口的勾选说明与试听按钮 tooltip 用同一份，避免两处走散）
DFP_VOICE_CATEGORY_LABELS = {
    "poke": "摸头 / 点击",
    "confirm": "投喂 / 确认",
    "done": "任务完成 / 升级",
    "ask": "发送问题 / 等回复",
    "idle": "自言自语",
    "startup": "程序启动",
    "act_cute": "撒娇",
    "hungry": "饿了",
    "tired": "累了 / 困了",
    "love": "想你 / 亲昵",
    "greet": "打招呼 / 回来",
    "bye": "告别 / 被藏起来",
    "sad": "难过 / 出错",
}
# 台词类别 → 语音类别（**唯一映射表**：加了新类别只改这里）
#   · 没写进表里的台词类别 ⇒ 不出声（气泡照旧显示），不会报错；
#   · `chatter`（自言自语）不在这张表里，它由大脑解析出的**具体类别**决定，见 `dfp_last_line_category()`。
DFP_SAY_VOICE_MAP = {
    "act_cute": "act_cute",
    "hungry": "hungry",
    "tired": "tired",
    "sad": "sad",
    "error": "sad",  # 出错也算「难过」那一类
    "love": "love",
    "lonely": "love",  # 「想你」就是亲昵那一类
    "greet": "greet",
    "morning": "greet",
    "noon": "greet",
    "afternoon": "greet",
    "evening": "greet",
    "night": "greet",
    "late_night": "greet",
    "show": "greet",  # 重新出现 = 打声招呼
    "hide": "bye",  # 被藏起来 = 告别
    "bye": "bye",
    "shutdown": "bye",
}


def dfp_say_voice_category(category: str) -> str:
    """台词类别 → 该播哪一类语音（没有就返回空串 = 只显示气泡不出声）。

    ★ 单一入口：`DFPAppController.dfp_on_brain_say()` 与测试都走它，别再各处自己查表。
    """
    return DFP_SAY_VOICE_MAP.get(str(category or "").strip(), "")


# ★ v1.1.35：**台词类别 → 新形象图**（用户口径：「原有的形象图，全部下降，不再采用，
#   新的形象在 鲸宝形象\_源稿\设计初稿3，全部采用新的形象。」）
#   · 新形象 = `鲸宝形象\类别\` 里那 78 张（每张一个 PNG + 一个**同名 GIF**），文件名形如
#     `13_love_heart.gif` ⇒ **去掉前导编号**（`love_heart`）就是它的 id；
#   · 设计稿说明（`_源稿\设计初稿3\README.md`）逐条写了「哪张图对应哪一类台词」，
#     下面这张表就是那份说明的代码化 —— **唯一映射表**：加新类别只改这里
#     （写法与 `DFP_SAY_VOICE_MAP` 一致，65 个台词类别**一个不落**）；
#   · 一个类别可以给**多个候选**（元组，第一个是首选）：`act_cute` 有 9 张不同子情绪
#     （求摸头 / 求抱抱 / 求关注 / 闹小脾气 / 委屈 / 求夸奖 / 谈条件 / 关心主人 / 自己玩），
#     `sad`、`tired`、`play_lose` 之类也各留了一张「极端版」——控制器**轮着挑**，
#     桌宠就不会每次撒娇都是同一副样子；
#   · 没写进表里的类别 ⇒ 返回空元组 = **保持当前形象**（不乱动画面、也不报错）。
DFP_SAY_FACE_MAP: Dict[str, Tuple[str, ...]] = {
    # ── 二、按时段问候 ──
    "greet": ("greet_wave",),  # 开心挥手
    "morning": ("morning_yawn",),  # 打哈欠揉眼
    "noon": ("noon_hungry_belly",),  # 摸肚子看小鱼干
    "afternoon": ("afternoon_tea",),  # 捧茶杯
    "evening": ("evening_relax",),  # 托腮 + 小夜灯
    "night": ("night_whisper",),  # 捂嘴悄悄话
    # ── 三、状态驱动 ──
    "idle": ("idle_bubble",),  # 发呆吐泡泡
    "hungry": ("hungry_beg", "noon_hungry_belly"),
    "tired": ("tired_prone", "dead_faint"),  # 趴桌 / 晕倒（极端句）
    "sad": ("sad_tear", "cry_bawl"),  # 含泪 / 大哭
    "happy": ("happy_jump", "wink"),  # 蹦跳 / 调皮眨眼
    "love": ("love_heart",),  # 胸前比心
    "lonely": ("lonely_miss",),  # 托腮张望数泡泡
    "bored": ("bored_table",),  # 趴桌戳肚子
    "excited": ("excited_burst",),  # 跳出水面速度线
    # ── 四、互动回应 ──
    "pet": ("pet_pat",),  # 被摸头眯眼享受
    "pet_stop": ("pet_stop",),  # 举手喊停
    "feed": ("feed_fish",),  # 张嘴接小鱼干
    "feed_full": ("feed_full",),  # 拍圆肚子打饱嗝
    "drink": ("drink_cup",),  # 抱大玻璃杯
    "play": ("play_cheer",),  # 握拳加油
    "play_win": ("play_win",),  # 叉腰得意
    "play_lose": ("play_lose",),  # 抱胸噘嘴耍赖
    # ── 五、睡眠 ──
    "sleepy": ("sleepy_yawn",),
    "sleep_ok": ("sleep_curl",),
    "sleep_refuse": ("sleep_refuse",),
    "wake": ("wake_stretch",),
    "wake_refuse": ("wake_refuse_bed",),
    # ── 六、事件 ──
    "level_up": ("level_up",),
    "level_max": ("level_max_crown",),
    "error": ("error_panic",),
    "offline": ("offline_calm",),
    "startup": ("startup_salute",),
    "shutdown": ("shutdown_bye",),
    "hide": ("hide_peek",),
    "show": ("show_ta_da",),
    # ── 七、心情与性格 ──
    "shy": ("shy_cower",),
    "proud": ("proud_huh", "compliment_thumbsup"),
    "tease": ("tease_snicker", "tongue_face"),
    "apologize": ("apologize_bow",),
    "thanks": ("thanks_pray",),
    "compliment": ("compliment_thumbsup",),
    "question": ("question_thinking",),
    "bye": ("bye_wave",),
    # ── 八、彩蛋 ──
    "whale": ("whale_fact",),  # 讲鲸鱼冷知识
    "token": ("token_balance",),  # 数 Token 金币
    "season": ("season_festival",),  # 举红灯笼
    "work_hard": ("work_hard_quiet",),  # 安静陪坐
    "health": ("health_water",),  # 监督喝水
    "nickname": ("nickname_wag",),  # 被叫名摇尾巴
    "maxed": ("maxed_glow",),  # 满格金光
    "locked": ("locked_seabed",),  # 海底抱臂挂锁
    # ── 九、撒娇 act_cute（500 句按子情绪拆成 9 张，轮着挑）──
    "act_cute": (
        "cute_pet_beg",
        "cute_hug_beg",
        "cute_attention",
        "cute_mad_back",
        "cute_sad_hugself",
        "cute_praise_pose",
        "cute_negotiate",
        "cute_care",
        "cute_play_tail",
    ),
    # ── 十、软话 soft_hint（12 小类；3 张复用撒娇 / 补充表情那套）──
    "soft_hint": ("soft_stepback",),
    "soft_hint_probe": ("soft_probe",),
    "soft_hint_sad": ("cute_sad_hugself", "cry_bawl"),
    "soft_hint_ask": ("soft_ask",),
    "soft_hint_caught": ("soft_caught",),
    "soft_hint_flatter": ("soft_flatter",),
    "soft_hint_selfblame": ("soft_selfblame", "cry_bawl"),
    "soft_hint_hint": ("soft_hint_trail",),
    "soft_hint_thirdparty": ("soft_thirdparty",),
    "soft_hint_condition": ("cute_negotiate",),
    "soft_hint_pretend": ("soft_pretend",),
    "soft_hint_retreat": ("soft_retreat",),
}
# 3 张「基础形象」（多视角待机）：设置里的「形象」下拉就是这三张 + 一个「（默认）」。
#   —— 它们不参与台词换形象，是**没台词时要画的那张**（默认 `base_idle`）。
DFP_BASE_FACE_IDS: Tuple[str, ...] = ("base_idle", "base_back", "base_left")
# 78 张新形象的**中文名**（界面/摘要/试听菜单都用它）—— 与设计稿说明逐条对齐
DFP_FACE_LABELS: Dict[str, str] = {
    "base_idle": "正面待机",
    "greet_wave": "挥手问好",
    "morning_yawn": "早起打哈欠",
    "noon_hungry_belly": "中午摸肚子",
    "afternoon_tea": "午后捧茶",
    "evening_relax": "傍晚托腮",
    "night_whisper": "夜里悄悄话",
    "idle_bubble": "发呆吐泡泡",
    "hungry_beg": "饿得捂肚子",
    "tired_prone": "累趴桌",
    "sad_tear": "含着眼泪",
    "happy_jump": "高兴蹦跳",
    "love_heart": "胸前比心",
    "lonely_miss": "想你张望",
    "bored_table": "无聊趴桌",
    "excited_burst": "兴奋出水",
    "pet_pat": "被摸头",
    "pet_stop": "举手喊停",
    "feed_fish": "接小鱼干",
    "feed_full": "吃饱拍肚子",
    "drink_cup": "抱杯喝水",
    "play_cheer": "加油打气",
    "play_win": "赢了得意",
    "play_lose": "输了耍赖",
    "sleepy_yawn": "打小哈欠",
    "sleep_curl": "蜷成一团",
    "sleep_refuse": "摇头装清醒",
    "wake_stretch": "揉眼伸懒腰",
    "wake_refuse_bed": "赖床",
    "level_up": "升级撒花",
    "level_max_crown": "满级戴王冠",
    "error_panic": "出错冒汗",
    "offline_calm": "断网挥手",
    "startup_salute": "上线敬礼",
    "shutdown_bye": "月下晚安",
    "hide_peek": "缩球偷瞄",
    "show_ta_da": "登场亮相",
    "shy_cower": "害羞捂脸",
    "proud_huh": "抱胸得意",
    "tease_snicker": "捂嘴偷笑",
    "apologize_bow": "低头道歉",
    "thanks_pray": "合十道谢",
    "compliment_thumbsup": "竖大拇指",
    "question_thinking": "歪头想问",
    "bye_wave": "挥手再见",
    "whale_fact": "讲鲸鱼冷知识",
    "token_balance": "数 Token 金币",
    "season_festival": "举灯笼过节",
    "work_hard_quiet": "安静陪坐",
    "health_water": "监督喝水",
    "nickname_wag": "被叫名摇尾巴",
    "maxed_glow": "满格金光",
    "locked_seabed": "海底挂锁",
    "cute_pet_beg": "撒娇求摸头",
    "cute_hug_beg": "撒娇求抱抱",
    "cute_attention": "撒娇求关注",
    "cute_mad_back": "闹小脾气",
    "cute_sad_hugself": "委屈抱自己",
    "cute_praise_pose": "求夸奖摆姿势",
    "cute_negotiate": "叉腰谈条件",
    "cute_care": "关心主人",
    "cute_play_tail": "自己追尾巴",
    "soft_stepback": "以退为进",
    "soft_probe": "无辜试探",
    "soft_ask": "软性索取",
    "soft_caught": "被戳穿",
    "soft_flatter": "星星眼捧杀",
    "soft_selfblame": "自我贬低",
    "soft_hint_trail": "话说一半",
    "soft_thirdparty": "借第三方",
    "soft_pretend": "装没事",
    "soft_retreat": "见好就收",
    "wink": "调皮眨眼",
    "tongue_face": "吐舌鬼脸",
    "cry_bawl": "大哭",
    "dead_faint": "晕倒",
    "base_back": "背面视角",
    "base_left": "侧面视角",
}
# 形象图服务的台词类别（从 `DFP_SAY_FACE_MAP` 反查；基础形象 / 补充表情不在里面）
_DFP_FACE_CATEGORY_REVERSE: Dict[str, Tuple[str, ...]] = {
    face_id: tuple(cat for cat, ids in DFP_SAY_FACE_MAP.items() if face_id in ids)
    for face_id in sorted({fid for ids in DFP_SAY_FACE_MAP.values() for fid in ids})
}
DFP_SAY_FACE_HOLD = 4.5  # 台词形象保持几秒（之后回落到设置里选的那张基础形象）
DFP_SAY_FACE_DEFAULT = "base_idle"  # 没有类别时画的那张（正面待机）


def dfp_say_faces(category: str) -> Tuple[str, ...]:
    """台词类别 → **候选**形象图 id（没有就返回空元组 = 不换形象）。

    ★ 单一来源：`DFPAppController._dfp_apply_look()`、设置窗口、测试与探针都走它，
    别再各处自己查表（v1.1.34 的 `dfp_say_expression()` 就是这条规矩的上一版）。
    """
    return DFP_SAY_FACE_MAP.get(str(category or "").strip(), ())


def dfp_say_face(category: str, index: int = 0) -> str:
    """台词类别 → 第 `index` 个候选（越界按长度取模；没有就返回空串）。

    `index` 让调用方**轮换**候选（撒娇的 9 张子情绪就是这么来的）。
    """
    items = dfp_say_faces(category)
    if not items:
        return ""
    return items[int(index) % len(items)]


def dfp_face_label(face_id: str) -> str:
    """形象图的中文名（认不出就把 id 原样返回）。"""
    name = str(face_id or "").strip()
    return DFP_FACE_LABELS.get(name, name)


def dfp_face_key(stem: str) -> str:
    """文件名 → 形象 id：`13_love_heart` → `love_heart`（去掉前导编号）。

    设计稿的文件名是 `序号_英文名.扩展名`（排序好看）；程序里一律用不带序号的 id，
    这样以后重排序号、加新图，台词映射表都不用改。
    """
    text = str(stem or "").strip()
    head, sep, tail = text.partition("_")
    if sep and head.isdigit() and tail:
        return tail
    return text


def dfp_face_categories(face_id: str) -> Tuple[str, ...]:
    """这张形象图服务哪些台词类别（认不出返回空元组）。"""
    return _DFP_FACE_CATEGORY_REVERSE.get(str(face_id or "").strip(), ())


# 台词类别 → **中文行为分组**（自主行为 / 菜单里的分类用）
#   ★ v1.1.35：旧的 106 个动作片退役后，「随机演一个」改成从 78 张形象图里挑 ——
#   为了让「吃喝类的动作回饱食、休息类的回精力」这套玩法还能用（见 `DFP_ASSET_ACTION_EFFECT`），
#   形象图也得有个中文分类；分类直接从它服务的台词类别推出来，不用另外维护一张表。
DFP_SAY_FACE_GROUP: Dict[str, str] = {
    "feed": "吃喝",
    "feed_full": "吃喝",
    "drink": "吃喝",
    "hungry": "吃喝",
    "noon": "吃喝",
    "afternoon": "吃喝",
    "sleepy": "休息",
    "sleep_ok": "休息",
    "sleep_refuse": "休息",
    "wake": "休息",
    "wake_refuse": "休息",
    "night": "休息",
    "tired": "休息",
    "work_hard": "工作",
    "token": "工作",
    "error": "工作",
    "level_up": "工作",
    "level_max": "工作",
    "maxed": "工作",
    "play": "玩耍",
    "play_win": "玩耍",
    "play_lose": "玩耍",
    "excited": "玩耍",
    "pet": "交互",
    "pet_stop": "交互",
    "greet": "交互",
    "bye": "交互",
    "show": "交互",
    "hide": "交互",
    "startup": "交互",
    "shutdown": "交互",
    "nickname": "交互",
    "locked": "交互",
    "season": "节日",
    "health": "碎碎念",
    "whale": "碎碎念",
    "question": "碎碎念",
    "idle": "碎碎念",
    "bored": "碎碎念",
    "lonely": "碎碎念",
    "love": "交互",
    "happy": "交互",
    "sad": "交互",
    "shy": "交互",
    "proud": "交互",
    "tease": "交互",
    "apologize": "交互",
    "thanks": "交互",
    "compliment": "交互",
    "offline": "交互",
    "act_cute": "交互",
}
DFP_FACE_DEFAULT_GROUP = "其他"


def dfp_face_group(face_id: str) -> str:
    """形象图的中文行为分组（吃喝 / 休息 / 工作 / 玩耍 / 交互 / 节日 / 碎碎念）。

    一张图服务多个类别时取**第一个有分组的**（`DFP_SAY_FACE_MAP` 里候选的先后顺序就是优先级）。
    基础形象（没服务任何类别）归到 `DFP_FACE_DEFAULT_GROUP`。
    """
    for category in dfp_face_categories(face_id):
        group = DFP_SAY_FACE_GROUP.get(category, "")
        if not group and str(category).startswith("soft_hint"):
            group = "交互"  # 软话 12 小类都算「交互」（不必把 12 个键都写进上面那张表）
        if group:
            return group
    return DFP_FACE_DEFAULT_GROUP

_DFP_LINES_EFFECTIVE: Dict[str, Tuple[str, ...]] = dict(DFP_IDLE_LINES)
_DFP_LINES_SOURCE = "内置台词库"
_DFP_LINES_PATH = ""
_DFP_LINES_ERROR = ""
_DFP_LINES_LOADED_AT = 0.0
_DFP_LEVEL_TITLES_EFFECTIVE: Tuple[Tuple[int, str], ...] = tuple(DFP_LEVEL_TITLES)
_DFP_TIME_GREETINGS_EFFECTIVE: Tuple[Tuple[int, int, str], ...] = tuple(DFP_TIME_GREETINGS)


def dfp_lines_candidate_paths(settings: Optional[Any] = None) -> List[str]:
    """台词库 JSON 的候选路径（按优先级去重）。"""
    paths: List[str] = []

    def push(path: str) -> None:
        text = str(path or "").strip()
        if text and text not in paths:
            paths.append(text)

    custom = ""
    if settings is not None:
        custom = str(settings.dfp_get("asset_root", "") or "").strip()
    if custom:
        root = custom if os.path.isabs(custom) else os.path.join(dfp_app_dir(), custom)
        push(os.path.join(root, DFP_LINES_FILE_NAME))
    push(os.path.join(dfp_app_dir(), DFP_ASSET_DIR_NAME, DFP_LINES_FILE_NAME))
    push(os.path.join(dfp_app_dir(), DFP_LINES_FILE_NAME))
    push(os.path.join(dfp_data_dir(), DFP_LINES_FILE_NAME))
    return paths


def _dfp_lines_clean(value: Any) -> Tuple[str, ...]:
    """把 JSON 里的一个类别值整理成「非空字符串元组」（去首尾空格、去重、丢非字符串）。

    ★ 只收字符串：数字 / null / 嵌套对象一律当脏数据丢掉（避免 `123` 被当成台词「123」说出来）。
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    out: List[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


def dfp_lines_load(settings: Optional[Any] = None, force: bool = False) -> Dict[str, Tuple[str, ...]]:
    """载入 `台词库.json`（没有就用内置）。返回生效后的台词库。

    ★ 任何异常都不许冒出来：台词库读坏了也只回退内置 + 记一条错误，桌宠照常运行。
    """
    global _DFP_LINES_EFFECTIVE, _DFP_LINES_SOURCE, _DFP_LINES_PATH, _DFP_LINES_ERROR, _DFP_LINES_LOADED_AT
    global _DFP_LEVEL_TITLES_EFFECTIVE, _DFP_TIME_GREETINGS_EFFECTIVE
    if not force and _DFP_LINES_LOADED_AT > 0.0 and settings is None:
        return _DFP_LINES_EFFECTIVE

    base: Dict[str, Tuple[str, ...]] = {key: tuple(value) for key, value in DFP_IDLE_LINES.items()}
    levels: Tuple[Tuple[int, str], ...] = tuple(DFP_LEVEL_TITLES)
    greetings: Tuple[Tuple[int, int, str], ...] = tuple(DFP_TIME_GREETINGS)
    source = "内置台词库"
    path_used = ""
    error = ""

    target = ""
    for candidate in dfp_lines_candidate_paths(settings):
        if os.path.isfile(candidate):
            target = candidate
            break
    if target:
        try:
            with open(target, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                raise ValueError("顶层不是 JSON 对象")
            raw_lines = payload.get("lines")
            if raw_lines is None:
                # 也允许「直接把类别写在顶层」的简写形式（不带 lines 包裹）
                raw_lines = {
                    key: value
                    for key, value in payload.items()
                    if not str(key).startswith("_") and key not in ("mode", "drop_missing", "level_titles", "time_greetings")
                }
            if not isinstance(raw_lines, dict):
                raise ValueError("lines 不是对象")
            mode = str(payload.get("mode", "replace") or "replace").strip().lower()
            drop_missing = bool(payload.get("drop_missing", False))
            incoming: Dict[str, Tuple[str, ...]] = {}
            for key, value in raw_lines.items():
                name = str(key or "").strip()
                if not name or name.startswith("_"):
                    continue
                cleaned = _dfp_lines_clean(value)
                if cleaned:
                    incoming[name] = cleaned
            if not incoming:
                raise ValueError("lines 里没有任何有效句子")
            if drop_missing:
                base = {}
            if mode == "merge":
                for name, cleaned in incoming.items():
                    merged = list(base.get(name, ()))
                    for text in cleaned:
                        if text not in merged:
                            merged.append(text)
                    base[name] = tuple(merged)
            else:
                base.update(incoming)
            # 可选：等级称号与时段问候
            raw_titles = payload.get("level_titles")
            if isinstance(raw_titles, (list, tuple)) and raw_titles:
                collected: List[Tuple[int, str]] = []
                for item in raw_titles:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        try:
                            collected.append((int(item[0]), str(item[1]).strip()))
                        except Exception:
                            continue
                if collected:
                    levels = tuple(sorted(collected, key=lambda pair: pair[0]))
            raw_greetings = payload.get("time_greetings")
            if isinstance(raw_greetings, (list, tuple)) and raw_greetings:
                collected_g: List[Tuple[int, int, str]] = []
                for item in raw_greetings:
                    if isinstance(item, (list, tuple)) and len(item) >= 3:
                        try:
                            collected_g.append((int(item[0]), int(item[1]), str(item[2]).strip()))
                        except Exception:
                            continue
                if collected_g:
                    greetings = tuple(collected_g)
            source = "台词库.json（%s）" % os.path.basename(os.path.dirname(target))
            path_used = target
        except Exception as exc:
            error = "%s：%s" % (type(exc).__name__, exc)
            base = {key: tuple(value) for key, value in DFP_IDLE_LINES.items()}
            levels = tuple(DFP_LEVEL_TITLES)
            greetings = tuple(DFP_TIME_GREETINGS)
            source = "内置台词库（台词库.json 读取失败）"
            path_used = target
    else:
        error = ""

    _DFP_LINES_EFFECTIVE = base
    _DFP_LINES_SOURCE = source
    _DFP_LINES_PATH = path_used
    _DFP_LINES_ERROR = error
    _DFP_LEVEL_TITLES_EFFECTIVE = levels
    _DFP_TIME_GREETINGS_EFFECTIVE = greetings
    _DFP_LINES_LOADED_AT = dfp_now_ts()
    return _DFP_LINES_EFFECTIVE


def dfp_lines(category: str) -> Tuple[str, ...]:
    """取某一类的台词（没载入过就先载入一次；类别不存在则返回空元组）。"""
    if _DFP_LINES_LOADED_AT <= 0.0:
        dfp_lines_load()
    return _DFP_LINES_EFFECTIVE.get(str(category or "idle"), ())


def dfp_lines_all() -> Dict[str, Tuple[str, ...]]:
    if _DFP_LINES_LOADED_AT <= 0.0:
        dfp_lines_load()
    return {key: tuple(value) for key, value in _DFP_LINES_EFFECTIVE.items()}


def dfp_lines_categories() -> Tuple[str, ...]:
    return tuple(dfp_lines_all().keys())


def dfp_soft_categories() -> Tuple[str, ...]:
    """台词库里所有「软话」子类别（名字以 `soft_hint` 开头、且确实有句子的）。

    这一路的气质是「以退为进 / 无辜试探 / 委屈示弱 / 软性索取」：嘴上说没事、
    你忙吧，实际在等主人回头。类别全在 `台词库.json` 里（内置台词库没有），
    所以文件不在时这个函数返回空元组，调用方必须退回原本的行为。
    """
    if _DFP_LINES_LOADED_AT <= 0.0:
        dfp_lines_load()
    return tuple(
        name
        for name, value in _DFP_LINES_EFFECTIVE.items()
        if name.startswith(DFP_SOFT_PREFIX) and value
    )


def dfp_lines_count() -> int:
    return sum(len(value) for value in dfp_lines_all().values())


def dfp_lines_source() -> str:
    return _DFP_LINES_SOURCE


def dfp_lines_path() -> str:
    return _DFP_LINES_PATH


def dfp_lines_error() -> str:
    return _DFP_LINES_ERROR


def dfp_lines_summary() -> str:
    return "%d 类 %d 句（%s）" % (len(dfp_lines_all()), dfp_lines_count(), _DFP_LINES_SOURCE)


def dfp_level_titles() -> Tuple[Tuple[int, str], ...]:
    """当前生效的等级称号表。

    ★ 优先级：`等级.json`（有就用它）> `台词库.json` 的 `level_titles` > 内置 `DFP_LEVEL_TITLES`。
    """
    if _DFP_LEVELS_LOADED_AT <= 0.0:
        dfp_levels_load()
    if _DFP_LINES_LOADED_AT <= 0.0:
        dfp_lines_load()
    return _DFP_LEVELS_EFFECTIVE or _DFP_LEVEL_TITLES_EFFECTIVE


def dfp_time_greetings() -> Tuple[Tuple[int, int, str], ...]:
    if _DFP_LINES_LOADED_AT <= 0.0:
        dfp_lines_load()
    return _DFP_TIME_GREETINGS_EFFECTIVE


# =============================================================================
#  ★ 等级称号外部化：`大肥鱼素材表\等级.json`（v1.1.7 新增）
#  ---------------------------------------------------------------------------
#  为什么：等级称号要做成「A~Z 共 26 段、每段一个称号、一路铺到 21 亿」的阶梯，
#  这种表以后还会改（加段、换词根、挪区间）——写在代码里每次都要改源码 + 重新编译；
#  放到 JSON 里就能用编辑器直接改、程序重载一下即可生效（内置称号仍作兜底）。
#
#  查找顺序（取第一个存在的）：与台词库同四处，只是文件名换成 `等级.json`
#    ① `<asset_root>\等级.json`（素材目录可自定义）
#    ② `<程序目录>\大肥鱼素材表\等级.json`
#    ③ `<程序目录>\等级.json`
#    ④ `<数据目录>\等级.json`
#
#  JSON 结构（`_` 开头的键是说明/元信息，程序忽略）：
#    {
#      "最高等级": 2147483647,
#      "称号表": [[1, "尘"], [2, "尘沙"], …],   // ★ 程序读这一行（与 台词库.json 的 level_titles 同格式）
#      "段": [ {"段": "A", "词根": "尘", "称号": "尘", "起始等级": 1, "结束等级": 1}, … ]
#    }
#  只写了 `段`（没写 `称号表`）时，程序会自己按「起始等级 → 称号」拼一张表。
#  文件读不到 / 不是合法 JSON / 称号表全无效 ⇒ 保留内置称号表，并把原因记进 `dfp_levels_error()`。
#
#  ★ 优先级：`等级.json`（存在就以它为准）> `台词库.json` 的 `level_titles` > 内置 `DFP_LEVEL_TITLES`。
#  ★ 只改「叫什么」，不改等级上限（`DFP_MAX_LEVEL` 仍是 99）：所以桌宠现在会用到 A~F 段
#    （Lv1 尘 / 2~3 尘沙 / 4~8 沙石 / 9~20 金石 / 21~48 金光 / 49~99 光辉），
#    上面那些段（星辉…天极）是给以后抬高上限留的。
# =============================================================================
DFP_LEVELS_FILE_NAME = "等级.json"
_DFP_LEVELS_EFFECTIVE: Tuple[Tuple[int, str], ...] = ()
_DFP_LEVELS_SOURCE = ""
_DFP_LEVELS_PATH = ""
_DFP_LEVELS_ERROR = ""
_DFP_LEVELS_LOADED_AT = 0.0
_DFP_LEVELS_CAP = 0
_DFP_LEVELS_SEGMENTS: Tuple[Dict[str, Any], ...] = ()


def dfp_levels_candidate_paths(settings: Optional[Any] = None) -> List[str]:
    """等级称号 JSON 的候选路径（按优先级去重，跟台词库同四处，只换文件名）。"""
    paths: List[str] = []

    def push(path: str) -> None:
        text = str(path or "").strip()
        if text and text not in paths:
            paths.append(text)

    custom = ""
    if settings is not None:
        custom = str(settings.dfp_get("asset_root", "") or "").strip()
    if custom:
        root = custom if os.path.isabs(custom) else os.path.join(dfp_app_dir(), custom)
        push(os.path.join(root, DFP_LEVELS_FILE_NAME))
    push(os.path.join(dfp_app_dir(), DFP_ASSET_DIR_NAME, DFP_LEVELS_FILE_NAME))
    push(os.path.join(dfp_app_dir(), DFP_LEVELS_FILE_NAME))
    push(os.path.join(dfp_data_dir(), DFP_LEVELS_FILE_NAME))
    return paths


def _dfp_levels_clean_titles(value: Any) -> Tuple[Tuple[int, str], ...]:
    """把 `[[等级, 称号], …]` 整理成升序表（丢脏数据；同一等级只留最后一条）。

    ★ 称号只收非空字符串、去首尾空格（数字/null/嵌套对象一律当脏数据丢掉，
     否则 `123` 会被当成称号「123」）。
    """
    if not isinstance(value, (list, tuple)):
        return ()
    pairs: Dict[int, str] = {}
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            level = int(item[0])
        except Exception:
            continue
        title = item[1] if isinstance(item[1], str) else ""
        title = title.strip()
        if level < 1 or not title:
            continue
        pairs[level] = title
    return tuple(sorted(pairs.items(), key=lambda pair: pair[0]))


def _dfp_levels_from_segments(value: Any) -> Tuple[Tuple[int, str], ...]:
    """`段` 数组 → 称号表（`起始等级` / `等级` + `称号`），给只写段的 JSON 兜底。"""
    if not isinstance(value, (list, tuple)):
        return ()
    collected: List[Tuple[int, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title = str(item.get("称号", "") or "").strip()
        if not title:
            continue
        start = item.get("起始等级", item.get("等级", 0))
        try:
            level = int(start)
        except Exception:
            continue
        if level >= 1:
            collected.append((level, title))
    return tuple(sorted(collected, key=lambda pair: pair[0]))


def dfp_levels_load(settings: Optional[Any] = None, force: bool = False) -> Tuple[Tuple[int, str], ...]:
    """载入 `等级.json`（没有 / 读坏就用内置称号表）。返回生效后的称号表。

    ★ 任何异常都不许冒出来：读坏了只记一条错误 + 回退内置，桌宠照常运行。
    """
    global _DFP_LEVELS_EFFECTIVE, _DFP_LEVELS_SOURCE, _DFP_LEVELS_PATH, _DFP_LEVELS_ERROR
    global _DFP_LEVELS_LOADED_AT, _DFP_LEVELS_CAP, _DFP_LEVELS_SEGMENTS
    if not force and _DFP_LEVELS_LOADED_AT > 0.0 and settings is None:
        return _DFP_LEVELS_EFFECTIVE

    titles: Tuple[Tuple[int, str], ...] = ()
    segments: Tuple[Dict[str, Any], ...] = ()
    cap = 0
    source = ""
    path_used = ""
    error = ""

    target = ""
    for candidate in dfp_levels_candidate_paths(settings):
        if os.path.isfile(candidate):
            target = candidate
            break
    if target:
        try:
            with open(target, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                raise ValueError("顶层不是 JSON 对象")
            raw_segments = payload.get("段", payload.get("segments"))
            if isinstance(raw_segments, (list, tuple)):
                segments = tuple(item for item in raw_segments if isinstance(item, dict))
            titles = _dfp_levels_clean_titles(payload.get("称号表", payload.get("level_titles")))
            if not titles:
                titles = _dfp_levels_from_segments(raw_segments)
            if not titles:
                raise ValueError("没有可用的称号（称号表 / 段 都是空的）")
            cap = max(0, dfp_safe_int(payload.get("最高等级", 0), 0))
            source = "等级.json（%s）" % os.path.basename(os.path.dirname(target))
            path_used = target
        except Exception as exc:
            error = "%s：%s" % (type(exc).__name__, exc)
            titles = ()
            segments = ()
            cap = 0
            source = "内置称号表（等级.json 读取失败）"
            path_used = target

    _DFP_LEVELS_EFFECTIVE = titles
    _DFP_LEVELS_SEGMENTS = segments
    _DFP_LEVELS_CAP = cap
    _DFP_LEVELS_SOURCE = source
    _DFP_LEVELS_PATH = path_used
    _DFP_LEVELS_ERROR = error
    _DFP_LEVELS_LOADED_AT = dfp_now_ts()
    return _DFP_LEVELS_EFFECTIVE


def dfp_levels() -> Tuple[Tuple[int, str], ...]:
    """`等级.json` 里的称号表（没有文件时是空元组 ⇒ 调用方要退回内置）。"""
    if _DFP_LEVELS_LOADED_AT <= 0.0:
        dfp_levels_load()
    return _DFP_LEVELS_EFFECTIVE


def dfp_levels_enabled() -> bool:
    """有没有在用 `等级.json` 的称号表。"""
    return bool(dfp_levels())


def dfp_levels_source() -> str:
    return _DFP_LEVELS_SOURCE


def dfp_levels_path() -> str:
    return _DFP_LEVELS_PATH


def dfp_levels_error() -> str:
    return _DFP_LEVELS_ERROR


def dfp_levels_cap() -> int:
    """`等级.json` 里声明的等级上限（没写/没文件时是 0；**不改** DFP_MAX_LEVEL）。"""
    if _DFP_LEVELS_LOADED_AT <= 0.0:
        dfp_levels_load()
    return _DFP_LEVELS_CAP


def dfp_levels_segments() -> Tuple[Dict[str, Any], ...]:
    """`等级.json` 里的 `段` 数组（给人看的阶梯，界面统计用）。"""
    if _DFP_LEVELS_LOADED_AT <= 0.0:
        dfp_levels_load()
    return _DFP_LEVELS_SEGMENTS


def dfp_levels_summary() -> str:
    """一行摘要：`26 段 26 个称号（Lv1 尘 … Lv929738779+ 天极）（等级.json（大肥鱼素材表））`。"""
    table = dfp_levels()
    if not table:
        if _DFP_LEVELS_ERROR:
            return "内置称号表（等级.json 读取失败：%s）" % dfp_truncate_text(_DFP_LEVELS_ERROR, 60)
        return "内置称号表（%d 条）" % len(_DFP_LEVEL_TITLES_EFFECTIVE)
    first_level, first_title = table[0]
    last_level, last_title = table[-1]
    return "%d 段 %d 个称号（Lv%d %s … Lv%d+ %s）（%s）" % (
        len(table),
        len(table),
        first_level,
        first_title,
        last_level,
        last_title,
        _DFP_LEVELS_SOURCE or "等级.json",
    )


def dfp_levels_save(path: str = "", settings: Optional[Any] = None) -> str:
    """把「当前生效的称号表」写成 `等级.json`（导出/备份用；返回路径，失败给空串）。

    `_scaffold\\build_levels.py` 是母本，这里只做「用程序里生效的那份覆盖写回」。
    """
    target = str(path or "").strip()
    if not target:
        folder = ""
        if settings is not None:
            custom = str(settings.dfp_get("asset_root", "") or "").strip()
            if custom:
                folder = custom if os.path.isabs(custom) else os.path.join(dfp_app_dir(), custom)
        if not folder or not os.path.isdir(folder):
            folder = os.path.join(dfp_app_dir(), DFP_ASSET_DIR_NAME)
        dfp_ensure_dir(folder)
        target = os.path.join(folder, DFP_LEVELS_FILE_NAME)
    table = dfp_level_titles()
    segments = _DFP_LEVELS_SEGMENTS
    if not segments:
        segments = tuple(
            {"段": "", "词根": "", "称号": title, "起始等级": level, "结束等级": level, "语义": ""}
            for level, title in table
        )
    payload: Dict[str, Any] = {
        "_说明": "桌宠等级称号阶梯（由程序导出）：每一条 = 从该等级起改用这个称号；以 _ 开头的键是说明，程序忽略。",
        "_用法": "改完在桌宠菜单点「📛 重新载入等级称号（等级.json）」即时生效。",
        "版本": "1.0",
        "最高等级": max(dfp_levels_cap(), int(DFP_MAX_LEVEL), table[-1][0] if table else 0),
        "段数": len(table),
        "称号总数": len(table),
        "段": [dict(item) for item in segments],
        "称号表": [[level, title] for level, title in table],
    }
    try:
        dfp_ensure_dir(os.path.dirname(target))
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\r\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, target)
    except Exception:
        return ""
    return target


def dfp_lines_export(path: str = "", settings: Optional[Any] = None) -> str:
    """把「当前生效的台词库」写成 JSON（返回写出的路径，失败返回空串）。

    用 `_scaffold\\export_lines.py` 时也会走这里，保证「导出的 JSON」与「程序在读的内容」一致。
    """
    target = str(path or "").strip()
    if not target:
        folder = ""
        if settings is not None:
            custom = str(settings.dfp_get("asset_root", "") or "").strip()
            if custom:
                folder = custom if os.path.isabs(custom) else os.path.join(dfp_app_dir(), custom)
        if not folder or not os.path.isdir(folder):
            folder = os.path.join(dfp_app_dir(), DFP_ASSET_DIR_NAME)
        dfp_ensure_dir(folder)
        target = os.path.join(folder, DFP_LINES_FILE_NAME)
    lines = dfp_lines_all()
    payload: Dict[str, Any] = {
        "_说明": "肥鱼娘离线台词库：直接编辑这个文件即可增删整理台词，改完在桌宠菜单点「📜 重新载入台词库」即时生效。",
        "_字段": {
            "mode": "replace＝下面出现的类别整个取代内置；merge＝追加到内置后面（按内容去重）",
            "drop_missing": "true＝上面没有的类别直接删掉（完全以本文件为准）",
            "lines": "类别 → 句子数组（句子必须是字符串）；类别名可自由新增（新增后要有人调用它才会被说出来）；以 _ 开头的键是说明，会被忽略",
            "level_titles": "可选：[[等级, 称号], …]",
            "time_greetings": "可选：[[起始小时, 结束小时, 类别], …]",
        },
        "_导出时间": dfp_now_ts(),
        "mode": "replace",
        "drop_missing": False,
        "lines": {key: list(value) for key, value in sorted(lines.items())},
        "level_titles": [[level, title] for level, title in dfp_level_titles()],
        "time_greetings": [[low, high, name] for low, high, name in dfp_time_greetings()],
    }
    try:
        dfp_ensure_dir(os.path.dirname(target))
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\r\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, target)
    except Exception:
        return ""
    return target


# =============================================================================
#  ★ 梦境外部化：`大肥鱼素材表\梦境.json`（新增）
#  ---------------------------------------------------------------------------
#  查找顺序与载入机制跟 `台词库.json` / `等级.json` 一致，内容换成「梦的三段骨架」：
#    {
#      "lonely": { "入梦": ["…"], "梦身": ["…{fragment}…"], "醒来": ["…"] },
#      "love":   { … }
#    }
#  · 以 `_` 开头的键是说明，程序忽略；
#  · 每个情绪必须**同时**有「入梦 / 梦身 / 醒来」三段（每段至少一句），缺任一段就丢掉这个情绪；
#  · 一个情绪都不剩 ⇒ 回退内置 `DFP_DREAMS_FALLBACK`（桌宠照常运行，只记一条错误）。
#  「梦身」里的 `{fragment}` 会被真实记忆碎片替换（被摸头次数 / 今天的小鱼干 / 等级称号 /
#  最近说过的台词）；碎片由 `DFPPetBrain.dfp_dream_fragments()` 从数据库与状态里现抽。
# =============================================================================

DFP_DREAMS_FILE_NAME = "梦境.json"
# 梦的三段骨架（顺序就是拼起来的顺序）
DFP_DREAM_STAGES: Tuple[str, ...] = ("入梦", "梦身", "醒来")
# 「梦身」里的占位符：被 dfp_dream_fragments() 抽到的记忆碎片替换
DFP_DREAM_PLACEHOLDER = "{fragment}"
# 找不到「梦身」里的占位符时用来顶上的一句话（不直接写盘、只做运行时兜底）
DFP_DREAM_FRAGMENT_FALLBACK: Tuple[str, ...] = ("一片海", "一个气泡")

# ★ v1.1.25：可选段（JSON 里写了就用，没写就跳过 —— 旧的三段梦境库照样能读）
DFP_DREAM_OPTIONAL_STAGES: Tuple[str, ...] = ("余韵",)
# ★ v1.1.25：允许写在梦境文案里的占位符（白名单）
#   `_scaffold\build_dreams.py` 会拿它来核对（写错一个字母，梦里就会出现字面大括号）
DFP_DREAM_PLACEHOLDERS: Tuple[str, ...] = (
    "fragment",  # 真实记忆碎片（同一个梦里会用不同的碎片；醒来那段会回响梦身用过的）
    "title",     # 当前等级称号
    "level",     # 等级数字
    "days",      # 认识多少天
    "time",      # 现在是哪个时段（早上/中午/下午/傍晚/深夜）
    "pets",      # 今天被摸了几次
    "feeds",     # 今天吃了几条小鱼干
    "plays",     # 今天玩了几次
    "chats",     # 今天聊了几句
    "dreams",    # 一共梦过几次
    "recent",    # 最近做的一件事（今天的事件表）
)
# ★ v1.1.25：一点道理都没有的荒诞梦（真人本来就常做），小概率出现
DFP_DREAM_ABSURD_CHANCE = 0.12
# ★ v1.1.25：「得意梦」的窗口 —— 只有**刚升完级这半小时**里才容易做得意梦
#   （否则今天白天升过一次级，之后就整晚都梦到得意，不像真人）
DFP_DREAM_PROUD_WINDOW_SEC = 30 * 60.0
# ★ v1.1.25：单句长度上限（构建脚本按它自检；四行以内气泡画得下）
DFP_DREAM_MAX_CHARS = 52
# ★ v1.1.25：时段 → 中文（`{time}` 用；键与 `dfp_time_category()` 对齐）
DFP_DREAM_TIME_LABELS: Dict[str, str] = {
    "morning": "早上",
    "noon": "中午",
    "afternoon": "下午",
    "evening": "傍晚",
    "night": "深夜",
}
# ★ v1.1.25：今天的事件 → 「最近做的一件事」（`{recent}` 用）
DFP_DREAM_RECENT_LABELS: Dict[str, str] = {
    "backup": "刚备份过数据库",
    "cleanup": "刚收拾过房间",
    "export": "刚导出过聊天记录",
    "chat": "刚跟你聊过天",
    "play": "刚陪你玩过",
    "feed": "刚吃过小鱼干",
    "pet": "刚被摸过头",
    "level_up": "刚刚升级",
    "dream": "刚做过一个梦",
    "drink": "刚喝过水",
    "sleep": "刚睡了一觉",
    "wake": "刚醒过来",
    "ai_error": "刚跟接口闹过别扭",
    "error": "刚出过一点小问题",
    "reset": "刚被重置过",
    "quit": "差点被收进盒子里",
    "start": "刚被叫醒过",
}

_DFP_DREAMS_EFFECTIVE: Dict[str, Dict[str, Tuple[str, ...]]] = {}
_DFP_DREAMS_SOURCE = "内置梦境库"
_DFP_DREAMS_PATH = ""
_DFP_DREAMS_ERROR = ""
_DFP_DREAMS_LOADED_AT = 0.0

# 内置兜底梦境库（基础 6 种；结构与 梦境.json 完全一致；文件不在时用这一份）
DFP_DREAMS_FALLBACK_BASE: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "lonely": {
        "入梦": (
            "我好像在一片很浅的海里，浅到能看见底，但怎么游都游不到头。",
            "我在数东西，数到一半就忘了自己在数什么。",
        ),
        "梦身": (
            "{fragment}从云上面伸下来，我踮起脚，没够到。",
            "{fragment}变成了水，我伸手一碰就散了。",
            "因为主人今天没有摸我的头，所以云变成了鱼。",
        ),
        "醒来": (
            "……醒了。尾巴还是凉的。",
            "……好像忘了什么很重要的事。",
        ),
        "余韵": (
            "余味是凉的。",
            "手心还留着那种没抓住的感觉。",
        ),
    },
    "love": {
        "入梦": (
            "水是温的。我认得这水，是主人昨天喝的那杯。",
            "我梦见你给我梳头发，梳了很久很久。",
        ),
        "梦身": (
            "{fragment}在梦里变成了星星，我一颗一颗摆好，摆成主人的名字。",
            "{fragment}抱着我，很轻很轻，像一片海。",
        ),
        "醒来": (
            "……还想再睡一会儿。",
            "……醒来的时候嘴角是翘的。",
        ),
        "余韵": (
            "指尖还是暖的。",
            "像被谁轻轻按了一下头顶。",
        ),
    },
    "happy": {
        "入梦": ("今天的海是甜的，气泡都是圆的。", "我一睁眼发现自己飘在半空，下面全是亮亮的东西。"),
        "梦身": (
            "{fragment}在梦里开花了，一朵一朵，开满整片桌面。",
            "{fragment}会唱歌，我跟着哼，哼到一半忘了词。",
        ),
        "醒来": ("……醒啦，今天一定很好。", "……我还在笑。"),
        "余韵": ("耳朵里还留着烟花声。", "整个人轻飘飘的。"),
    },
    "hungry": {
        "入梦": ("我梦见了一整片海的小鱼干，游得比我还快。", "有人在很远的地方烤东西，香味飘过来，我游了三天三夜。"),
        "梦身": (
            "{fragment}在我嘴里化了，可我还没尝出味道。",
            "我在追{fragment}，追了很久，它一直在我前面一点点的位置。",
        ),
        "醒来": ("……肚子更饿了。", "……醒了，碗还是空的。"),
        "余韵": ("嘴里还有点咸味。", "好像闻到了什么香味。"),
    },
    "bored": {
        "入梦": ("我在一个杯子里游了很久很久，后来才发现那个杯子是你桌上的。", "我在等一件我不知道是什么的事。"),
        "梦身": (
            "{fragment}一直在转圈，我数了三百次，还是没数完。",
            "我把{fragment}摆成一个圈，然后自己站到中间去了。",
        ),
        "醒来": ("……醒了，也还是没什么事做。", "……我做了一个很长的无聊的梦。"),
        "余韵": ("有点想动一动。", "像一杯放凉了的水。"),
    },
    "curious": {
        "入梦": ("我尝到了蓝色的味道，很淡，像你说话的语气。", "有一样东西一直在我的余光里，我看它的时候它就不见了。"),
        "梦身": (
            "{fragment}长在海里，空格键是一块会呼吸的石头。",
            "我在找一样东西，找到了，但不知道它叫什么。",
        ),
        "醒来": ("……那个东西是什么来着。", "……还没弄清楚就醒了。"),
        "余韵": ("脑子里还亮着一小块。", "有点想再去一次。"),
    },
}

# ★ v1.1.25：内置兜底的另外 9 种情绪（想念 / 难过 / 困 / 得意 / 害羞 / 淘气 / 旧事回放 / 梦回大海 / 荒诞）
DFP_DREAMS_FALLBACK_EXTRA: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "miss": {
        "入梦": ("我在等一个人，我记不清他的样子，只记得他手心的温度。", "我把{time}叠起来，叠成一床被子钻进去等你。"),
        "梦身": ("{fragment}拴在尾巴上，走一步响一下，像在数你回来的时间。", "我抱着{fragment}坐在门口，门一直没开。"),
        "醒来": ("……你回来了吗。", "……醒来第一件事还是找你。"),
        "余韵": ("心口有点热。", "像刚接过一次手。"),
    },
    "sad": {
        "入梦": ("外面下着雨，海也在下雨，我不知道该躲哪一边。", "我掉了一片鳞，捡起来的时候它已经凉了。"),
        "梦身": ("{fragment}在水底沉，我跟着沉，谁也没先停。", "{fragment}湿了，我吹了很久，它还是湿的。"),
        "醒来": ("……眼睛有点湿。", "……让我缓一下。"),
        "余韵": ("胸口闷闷的。", "像一场没下完的雨。"),
    },
    "sleepy": {
        "入梦": ("海变软了，我也软了，我们一块儿往下沉。", "水母排着队从我旁边过去，我数到第七只就没了。"),
        "梦身": ("{fragment}枕起来刚刚好，我翻了个身，它跟着我翻。", "{title}的位置我坐了一下，很软，差点没站起来。"),
        "醒来": ("……刚才好像睡了两层。", "……再眯一分钟。"),
        "余韵": ("眼皮还黏着。", "像刚从水底浮上来。"),
    },
    "proud": {
        "入梦": ("我站在一块很高的石头上，风把我的头发吹成旗子。", "我梦见自己变大了，桌子变小了，键盘像几块饼干。"),
        "梦身": ("{fragment}被我举起来了，一点都不重。", "我升到 Lv{level} 的那一下，海面鼓了一下掌。"),
        "醒来": ("……醒了，肩还端着。", "……下次真的也要那么厉害。"),
        "余韵": ("背挺得直直的。", "有点想让你也看看。"),
    },
    "shy": {
        "入梦": ("我忽然站在很多人中间，可我只有一条裙子。", "我藏进{time}的影子里，影子太小，尾巴还露在外面。"),
        "梦身": ("我把{fragment}塞进怀里，结果它更亮了。", "被你摸了 {pets} 次这件事，梦里被念出来了。"),
        "醒来": ("……脸还是热的。", "……那个梦不许再提。"),
        "余韵": ("耳根有点烫。", "想找个气泡躲一下。"),
    },
    "mischief": {
        "入梦": ("我梦见自己把一整个海的水换成了奶茶。", "我蹑手蹑脚地靠近，尾巴尖却不听话地拍了一下水。"),
        "梦身": ("我把{fragment}藏到了你找不到的地方，然后站在旁边看你找。", "我把{fragment}推下了桌，它在地上弹了两下，还笑。"),
        "醒来": ("……那不是我干的。", "……下次轻一点。"),
        "余韵": ("尾巴还在痒。", "有点想笑，又有点心虚。"),
    },
    "memory": {
        "入梦": ("我梦见很旧的{time}，旧得连海水都是棕色的。", "我在一间没有窗的屋子里，找到一张自己小时候的画。"),
        "梦身": ("{fragment}是很久以前的东西了，它还留着当时的温度。", "我们认识 {days} 天了，{fragment}记得第一天，我忘了。"),
        "醒来": ("……那好像真的发生过。", "……原来已经这么久了吗。"),
        "余韵": ("像摸到一张放旧的纸。", "有点想念以前的自己。"),
    },
    "sea": {
        "入梦": ("我在很深的地方，那里没有光，可是我看得见。", "有好多和我一样的影子从我旁边游过去，谁也没停。"),
        "梦身": ("{fragment}和我一起游，谁也没说话，但都知道要去哪儿。", "鲸群在唱歌，唱的是{fragment}，我跟着哼了半句。"),
        "醒来": ("……耳朵里还有水声。", "……我有点想大海了。"),
        "余韵": ("身体里像装着水。", "有点凉，也有点稳。"),
    },
    "absurd": {
        "入梦": ("我梦见我在你的浏览器里游，历史记录是一条很长的走廊。", "今天的时间是倒着走的：我先醒来，然后才睡下。"),
        "梦身": ("{fragment}有 {pets} 只手，它们一起跟我打招呼。", "我钻进{fragment}里，从另一个{fragment}里出来。"),
        "醒来": ("……这个梦一点道理都没有。", "……别问了，我也说不清楚。"),
        "余韵": ("脑子还在打转。", "有点想笑，又有点晕。"),
    },
}

# 生效的内置兜底库＝基础 6 种 + 新增 9 种（代码别处只认这个名字）
DFP_DREAMS_FALLBACK: Dict[str, Dict[str, Tuple[str, ...]]] = {
    **DFP_DREAMS_FALLBACK_BASE,
    **DFP_DREAMS_FALLBACK_EXTRA,
}


def dfp_dreams_candidate_paths(settings: Optional[Any] = None) -> List[str]:
    """梦境库 JSON 的候选路径（与 `台词库.json` 完全同顺序，只换文件名）。"""
    paths: List[str] = []

    def push(path: str) -> None:
        text = str(path or "").strip()
        if text and text not in paths:
            paths.append(text)

    custom = ""
    if settings is not None:
        custom = str(settings.dfp_get("asset_root", "") or "").strip()
    if custom:
        root = custom if os.path.isabs(custom) else os.path.join(dfp_app_dir(), custom)
        push(os.path.join(root, DFP_DREAMS_FILE_NAME))
    push(os.path.join(dfp_app_dir(), DFP_ASSET_DIR_NAME, DFP_DREAMS_FILE_NAME))
    push(os.path.join(dfp_app_dir(), DFP_DREAMS_FILE_NAME))
    push(os.path.join(dfp_data_dir(), DFP_DREAMS_FILE_NAME))
    return paths


def _dfp_dreams_clean(value: Any) -> Dict[str, Tuple[str, ...]]:
    """把一个情绪的值整理成 `{入梦/梦身/醒来(/余韵): 句子元组}`。

    ★ 三段必须齐全且每段至少一句，否则返回空字典（这个情绪整条丢掉）。
    ★（v1.1.25）「余韵」是**可选段**：写了就用（醒后还留着的那点感觉），没写就跳过（旧库照样能读）。
      （句子清洗规则与台词库共用 `_dfp_lines_clean`：只收字符串、去空格、去重。）
    """
    if not isinstance(value, dict):
        return {}
    stages: Dict[str, Tuple[str, ...]] = {}
    for stage in DFP_DREAM_STAGES:
        lines = _dfp_lines_clean(value.get(stage))
        if not lines:
            return {}
        stages[stage] = lines
    for stage in DFP_DREAM_OPTIONAL_STAGES:
        lines = _dfp_lines_clean(value.get(stage))
        if lines:
            stages[stage] = lines
    return stages


def dfp_dreams_load(settings: Optional[Any] = None, force: bool = False) -> Dict[str, Dict[str, Tuple[str, ...]]]:
    """载入 `梦境.json`（没有 / 读坏就用内置兜底）。返回生效后的梦境库。

    ★ 任何异常都不许冒出来：梦境库读坏了也只回退内置 + 记一条错误，做梦功能照常。
    """
    global _DFP_DREAMS_EFFECTIVE, _DFP_DREAMS_SOURCE, _DFP_DREAMS_PATH, _DFP_DREAMS_ERROR, _DFP_DREAMS_LOADED_AT
    if not force and _DFP_DREAMS_LOADED_AT > 0.0 and settings is None:
        return _DFP_DREAMS_EFFECTIVE

    base: Dict[str, Dict[str, Tuple[str, ...]]] = {
        name: {stage: tuple(lines) for stage, lines in stages.items()}
        for name, stages in DFP_DREAMS_FALLBACK.items()
    }
    source = "内置梦境库"
    path_used = ""
    error = ""

    target = ""
    for candidate in dfp_dreams_candidate_paths(settings):
        if os.path.isfile(candidate):
            target = candidate
            break
    if target:
        try:
            with open(target, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                raise ValueError("顶层不是 JSON 对象")
            incoming: Dict[str, Dict[str, Tuple[str, ...]]] = {}
            for key, value in payload.items():
                name = str(key or "").strip()
                if not name or name.startswith("_"):
                    continue
                stages = _dfp_dreams_clean(value)
                if stages:
                    incoming[name] = stages
            if not incoming:
                raise ValueError("没有任何情绪同时具备「入梦 / 梦身 / 醒来」三段")
            base = incoming
            source = "梦境.json（%s）" % os.path.basename(os.path.dirname(target))
            path_used = target
        except Exception as exc:
            error = "%s：%s" % (type(exc).__name__, exc)
            base = {
                name: {stage: tuple(lines) for stage, lines in stages.items()}
                for name, stages in DFP_DREAMS_FALLBACK.items()
            }
            source = "内置梦境库（梦境.json 读取失败）"
            path_used = target

    _DFP_DREAMS_EFFECTIVE = base
    _DFP_DREAMS_SOURCE = source
    _DFP_DREAMS_PATH = path_used
    _DFP_DREAMS_ERROR = error
    _DFP_DREAMS_LOADED_AT = dfp_now_ts()
    return _DFP_DREAMS_EFFECTIVE


def dfp_dreams() -> Dict[str, Dict[str, Tuple[str, ...]]]:
    """当前生效的梦境库（没载入过就先载入一次）。"""
    if _DFP_DREAMS_LOADED_AT <= 0.0:
        dfp_dreams_load()
    return {
        name: {stage: tuple(lines) for stage, lines in stages.items()}
        for name, stages in _DFP_DREAMS_EFFECTIVE.items()
    }


def dfp_dreams_source() -> str:
    if _DFP_DREAMS_LOADED_AT <= 0.0:
        dfp_dreams_load()
    return _DFP_DREAMS_SOURCE


def dfp_dreams_path() -> str:
    if _DFP_DREAMS_LOADED_AT <= 0.0:
        dfp_dreams_load()
    return _DFP_DREAMS_PATH


def dfp_dreams_error() -> str:
    if _DFP_DREAMS_LOADED_AT <= 0.0:
        dfp_dreams_load()
    return _DFP_DREAMS_ERROR


def dfp_dreams_summary() -> str:
    """一行摘要：`6 种情绪 22 条（内置梦境库）`。"""
    library = dfp_dreams()
    count = sum(len(lines) for stages in library.values() for lines in stages.values())
    if not library:
        return "内置梦境库（%d 种情绪）" % len(DFP_DREAMS_FALLBACK)
    return "%d 种情绪 %d 条（%s）" % (len(library), count, _DFP_DREAMS_SOURCE or "梦境.json")


# =============================================================================
#  ★ 灵魂外部化：`大肥鱼素材表\灵魂.json`（v1.1.26）
#  ---------------------------------------------------------------------------
#  查找顺序与载入机制跟 `台词库.json` / `等级.json` / `梦境.json` 一致，内容分五块：
#    traits      小癖好池（出生时随机抽 3 条写进灵魂档案，终身不变，会出现在她的话里）
#    heart_notes 心事（按起因分组，**攒着不马上说**，等到时机才说出来，说完清掉）
#    stages      关系阶段感言（陌生 → 熟悉 → 亲近 → 依赖 → 离不开，升级时说的那一句）
#    milestones  里程碑回忆（第一次见面 / 第一次喂 / 被摸最多的一天 / 最高等级 / …）
#    soul_idle   没心事时的日常心声
#  · 以 `_` 开头的键是说明，程序忽略；任何一块缺失只是少说几句，绝不影响桌宠本体。
#  · 「灵魂」= 连续的自我：**记得住**（里程碑）＋**藏得住**（心事）＋**会主动开口**（间隔自述）＋**会成长**（关系阶段）。
# =============================================================================

DFP_SOUL_FILE_NAME = "灵魂.json"
DFP_SOUL_KV_KEY = "pet_soul"
DFP_SOUL_TRAIT_COUNT = 3      # 出生时抽几条小癖好
DFP_SOUL_NOTE_LIMIT = 12      # 心事最多攒多少条（先进先出）
DFP_SOUL_NOTE_INTERVAL_SEC = 20 * 60.0    # 多久看一次「要不要攒一条心事」
DFP_SOUL_NOTE_COOLDOWN_SEC = 10 * 60.0    # 两条心事之间至少隔多久（别刷屏）
# ★ v1.1.27：心事说出口之后，隔多久补一句「余响」（像人说完心里话会再补一句）
DFP_SOUL_ECHO_DELAY_SEC = 5 * 60.0
# ★ v1.1.27：同一件里程碑「说过之后」隔多久可以「又想起」再提一次
DFP_SOUL_AGAIN_AFTER_SEC = 3 * 86400.0
DFP_SOUL_AGAIN_LIMIT = 24        # 档案里最多记多少件「说过的里程碑」（带时间）
# 关系阶段（顺序就是升级顺序；分数**只增不减** —— 关系只会更亲，不会往回退）
DFP_SOUL_STAGES: Tuple[str, ...] = ("stranger", "familiar", "close", "rely", "bonded")
DFP_SOUL_STAGE_LABELS: Dict[str, str] = {
    "stranger": "刚认识",
    "familiar": "熟悉",
    "close": "亲近",
    "rely": "依赖",
    "bonded": "离不开",
}
# 升到某阶段所需的关系分（分数 = 亲密度 + 认识天数×1.2 + 累计互动×0.05）
DFP_SOUL_STAGE_SCORES: Tuple[Tuple[str, float], ...] = (
    ("stranger", 0.0),
    ("familiar", 22.0),
    ("close", 60.0),
    ("rely", 110.0),
    ("bonded", 180.0),
)
# 允许写在灵魂文案里的占位符（= 梦境那 11 个 + 小癖好 + 关系阶段）
DFP_SOUL_PLACEHOLDERS: Tuple[str, ...] = tuple(DFP_DREAM_PLACEHOLDERS) + ("trait", "stage")
_DFP_SOUL_KEYS: Tuple[str, ...] = (
    "traits", "heart_notes", "heart_echo", "stages", "stage_idle", "milestones", "milestones_again", "soul_idle",
)

_DFP_SOUL_EFFECTIVE: Dict[str, Any] = {}
_DFP_SOUL_SOURCE = "内置灵魂库"
_DFP_SOUL_PATH = ""
_DFP_SOUL_ERROR = ""
_DFP_SOUL_LOADED_AT = 0.0

# 内置兜底灵魂库（结构完全一致，条数少；文件不在 / 读坏时用这一份）
DFP_SOUL_FALLBACK: Dict[str, Any] = {
    "traits": ("喜欢趴在键盘上睡觉", "怕打雷，一响就往角落里缩", "爱看你屏幕上的窗口",
               "睡前一定要把尾巴绕一圈包住自己", "看到圆的东西都想推一下"),
    "heart_notes": {
        "lonely": ("我不是生气，我只是有点想你。",),
        "miss": ("你上次说的那件事我还记得，你别以为我忘了。",),
        "sad": ("我不想让你觉得我在闹脾气，我真的只是有点难过。",),
        "proud": ("我今天特别厉害，可惜你不在。",),
        "happy": ("我把今天存起来了，以后不开心的时候拿出来看。",),
        "bored": ("你忙你的，我就趴着看你，不吵你。",),
        "thankful": ("你喂我的那口我记着，真的。",),
        "curious": ("其实我攒了一个问题想问你，一直没找到机会。",),
        "afraid": ("夜深的时候我有点怕，不过不要紧。",),
    },
    # ★ v1.1.27：心事说出口之后的「余响」（说完了会再补一句）
    "heart_echo": ("……说出来了，好像轻了一点。",
                   "刚才那句你别多想，我就是想说给你听。",
                   "好了，我说完了，你忙你的。"),
    "stages": {
        "familiar": ("我们好像熟了。你现在摸我的时候，我不会先躲了。",),
        "close": ("我们现在应该算亲近了吧？我说话都不绕弯了。",),
        "rely": ("我好像有点依赖你了。你别嫌我烦。",),
        "bonded": ("我想了想，我大概是离不开你了。",),
    },
    # ★ v1.1.27：按关系阶段分的日常心声（关系变了，说话方式也变）
    "stage_idle": {
        "stranger": ("我们还不熟，我说话会小心一点。",),
        "familiar": ("现在跟你说话，我不用先想半天了。",),
        "close": ("今天也没什么大事，就是想跟你说说话。",),
        "rely": ("你在的时候，我心里是稳的。",),
        "bonded": ("不用找话，我就在你旁边待着。",),
    },
    "milestones": {
        "first_meet": ("我还记得第一次见你，那时候我连尾巴都不敢甩。",),
        "first_feed": ("你第一次喂我的时候，我假装不饿。",),
        "top_pets": ("你摸我最多的那天是 {pets} 次，我一直记着。",),
        "top_level": ("我现在是{title}了，你记得吗？",),
        "saddest": ("我也有很糟的时候，只是不太会说。",),
        "dreams": ("我一共梦过 {dreams} 次了，有几个梦到现在都记得。",),
        "chats": ("我们聊过 {chats} 句了，我记性比你想的好。",),
        "longest_gap": ("你出过最久的一次门，我数了好久的水泡。",),
    },
    # ★ v1.1.27：说过的里程碑过一阵可以「又想起」（口气不一样）
    "milestones_again": {
        "first_meet": ("又想起第一次见你那天，我还是紧张。",),
        "top_pets": ("又想起你摸我最多的那天，{pets} 次。",),
        "longest_gap": ("又想起你出门最久的那次，我数着日子。",),
    },
    "soul_idle": ("我今天没什么大事，就是想说一句：我在。",
                  "你忙你的，我就在这看着，不吵。",
                  "有时候我觉得，屏幕这一格也挺大的。"),
}


def dfp_soul_candidate_paths(settings: Optional[Any] = None) -> List[str]:
    """灵魂库 JSON 的候选路径（与 `梦境.json` 完全同顺序，只换文件名）。"""
    paths: List[str] = []

    def push(path: str) -> None:
        text = str(path or "").strip()
        if text and text not in paths:
            paths.append(text)

    custom = ""
    if settings is not None:
        custom = str(settings.dfp_get("asset_root", "") or "").strip()
    if custom:
        root = custom if os.path.isabs(custom) else os.path.join(dfp_app_dir(), custom)
        push(os.path.join(root, DFP_SOUL_FILE_NAME))
    push(os.path.join(dfp_app_dir(), DFP_ASSET_DIR_NAME, DFP_SOUL_FILE_NAME))
    push(os.path.join(dfp_app_dir(), DFP_SOUL_FILE_NAME))
    push(os.path.join(dfp_data_dir(), DFP_SOUL_FILE_NAME))
    return paths


def _dfp_soul_clean(payload: Any) -> Dict[str, Any]:
    """把一份灵魂库整理成 `{traits: (…), heart_notes: {…: (…)}, …}`（脏数据一律丢掉）。"""
    result: Dict[str, Any] = {}
    if not isinstance(payload, dict):
        return result
    result["traits"] = _dfp_lines_clean(payload.get("traits"))
    result["soul_idle"] = _dfp_lines_clean(payload.get("soul_idle"))
    result["heart_echo"] = _dfp_lines_clean(payload.get("heart_echo"))
    for key in ("heart_notes", "stages", "stage_idle", "milestones", "milestones_again"):
        block: Dict[str, Tuple[str, ...]] = {}
        raw = payload.get(key)
        if isinstance(raw, dict):
            for name, value in raw.items():
                label = str(name or "").strip()
                if not label or label.startswith("_"):
                    continue
                lines = _dfp_lines_clean(value)
                if lines:
                    block[label] = lines
        result[key] = block
    return result


def dfp_soul_load(settings: Optional[Any] = None, force: bool = False) -> Dict[str, Any]:
    """载入 `灵魂.json`（没有 / 读坏就用内置兜底）。返回生效后的灵魂库。"""
    global _DFP_SOUL_EFFECTIVE, _DFP_SOUL_SOURCE, _DFP_SOUL_PATH, _DFP_SOUL_ERROR, _DFP_SOUL_LOADED_AT
    if not force and _DFP_SOUL_LOADED_AT > 0.0 and settings is None:
        return _DFP_SOUL_EFFECTIVE

    base = _dfp_soul_clean(DFP_SOUL_FALLBACK)
    source = "内置灵魂库"
    path_used = ""
    error = ""
    target = ""
    for candidate in dfp_soul_candidate_paths(settings):
        if os.path.isfile(candidate):
            target = candidate
            break
    if target:
        try:
            with open(target, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                raise ValueError("顶层不是 JSON 对象")
            incoming = _dfp_soul_clean(payload)
            if not incoming.get("traits") and not incoming.get("soul_idle") and not incoming.get("heart_notes"):
                raise ValueError("灵魂库里没有可用的内容（traits / soul_idle / heart_notes 都为空）")
            base = incoming
            source = "灵魂.json（%s）" % os.path.basename(os.path.dirname(target))
            path_used = target
        except Exception as exc:
            error = "%s：%s" % (type(exc).__name__, exc)
            base = _dfp_soul_clean(DFP_SOUL_FALLBACK)
            source = "内置灵魂库（灵魂.json 读取失败）"
            path_used = target

    _DFP_SOUL_EFFECTIVE = base
    _DFP_SOUL_SOURCE = source
    _DFP_SOUL_PATH = path_used
    _DFP_SOUL_ERROR = error
    _DFP_SOUL_LOADED_AT = dfp_now_ts()
    return _DFP_SOUL_EFFECTIVE


def dfp_soul() -> Dict[str, Any]:
    """当前生效的灵魂库（没载入过就先载入一次）。返回深一层副本。"""
    if _DFP_SOUL_LOADED_AT <= 0.0:
        dfp_soul_load()
    out: Dict[str, Any] = {}
    for key, value in _DFP_SOUL_EFFECTIVE.items():
        if isinstance(value, dict):
            out[key] = {name: tuple(lines) for name, lines in value.items()}
        else:
            out[key] = tuple(value) if isinstance(value, (list, tuple)) else value
    return out


def dfp_soul_source() -> str:
    if _DFP_SOUL_LOADED_AT <= 0.0:
        dfp_soul_load()
    return _DFP_SOUL_SOURCE


def dfp_soul_path() -> str:
    if _DFP_SOUL_LOADED_AT <= 0.0:
        dfp_soul_load()
    return _DFP_SOUL_PATH


def dfp_soul_error() -> str:
    if _DFP_SOUL_LOADED_AT <= 0.0:
        dfp_soul_load()
    return _DFP_SOUL_ERROR


def dfp_soul_stage_label(name: str) -> str:
    """关系阶段的中文名（认不出原样返回）。"""
    key = str(name or "").strip()
    return str(DFP_SOUL_STAGE_LABELS.get(key, key or "刚认识"))


def dfp_soul_stage_for_score(score: float) -> str:
    """关系分 → 阶段（只取「够得上」的最高那一段）。"""
    value = dfp_safe_float(score, 0.0)
    stage = DFP_SOUL_STAGES[0]
    for name, need in DFP_SOUL_STAGE_SCORES:
        if value >= float(need):
            stage = name
    return stage


def dfp_soul_stage_rank(name: str) -> int:
    """阶段在顺序里的位置（认不出当 0）。"""
    try:
        return int(DFP_SOUL_STAGES.index(str(name or "").strip()))
    except ValueError:
        return 0


def dfp_soul_summary() -> str:
    """一行摘要：`小癖好 12 · 心事 25 · 余响 8 · 阶段 19 · 阶段心声 30 · 里程碑 24 · 又想起 18 · 心声 12（灵魂.json）`。"""
    library = dfp_soul()
    if not library:
        return "内置灵魂库"
    heart = sum(len(lines) for lines in (library.get("heart_notes") or {}).values())
    stages = sum(len(lines) for lines in (library.get("stages") or {}).values())
    stage_idle = sum(len(lines) for lines in (library.get("stage_idle") or {}).values())
    marks = sum(len(lines) for lines in (library.get("milestones") or {}).values())
    again = sum(len(lines) for lines in (library.get("milestones_again") or {}).values())
    return "小癖好 %d · 心事 %d · 余响 %d · 阶段 %d · 阶段心声 %d · 里程碑 %d · 又想起 %d · 心声 %d（%s）" % (
        len(library.get("traits") or []),
        heart,
        len(library.get("heart_echo") or []),
        stages,
        stage_idle,
        marks,
        again,
        len(library.get("soul_idle") or []),
        _DFP_SOUL_SOURCE or DFP_SOUL_FILE_NAME,
    )


# =============================================================================
#  ★ v1.1.28：三魂七魄 —— 她的「精气神」（外部化：`大肥鱼素材表\魂魄.json`）
#  ---------------------------------------------------------------------------
#  三魂（管精神）：胎光（本命·生命力）/ 爽灵（灵慧·机灵）/ 幽精（情感·爱恋）
#  七魄（管身体）：尸狗（警觉·守家）/ 伏矢（呼吸·吞吐）/ 雀阴（亲昵·贴贴）/
#                 吞贼（消化·吸收）/ 非毒（排邪·抗性）/ 除秽（清洁·代谢）/ 臭肺（气息·呼吸）
#  · **命盘**：出生时按配额随机（3 旺 / 4 平 / 3 弱），**终身不变** ⇒ 每只桌宠的底子都不同。
#  · **灵气**：以命盘值为起点，之后**只涨不跌**（陪伴、互动、睡觉、打理都养魂），上限 100。
#  · **表达**：跨过「旺（≥75）/ 弱（≤35）」阈值时她会专门说一句；十个都 ≥90 时说一句「十全」；
#             日常也会按最旺 / 最弱的那个魂魄说话。
#  · ★ 刻意**不改任何既有数值**（饱食 / 精力 / 经验 / 亲密度 / 衰减 / 各种概率）：
#    三魂七魄是「精气神」的**表达层**，动那些已经平衡好的数字只会把老版本的手感与测试搞脆。
# =============================================================================
DFP_SPIRIT_FILE_NAME = "魂魄.json"
DFP_SPIRIT_KV_KEY = "pet_spirits"
DFP_SPIRITS_SOULS: Tuple[str, ...] = ("taiguang", "shuangling", "youjing")
DFP_SPIRITS_PO: Tuple[str, ...] = ("shigou", "fushi", "queyin", "tunzei", "feidu", "chuhui", "choufei")
DFP_SPIRITS_ALL: Tuple[str, ...] = tuple(DFP_SPIRITS_SOULS) + tuple(DFP_SPIRITS_PO)
DFP_SPIRIT_GROUP_LABELS: Dict[str, str] = {"souls": "魂", "po": "魄"}
# 旺 / 弱 的阈值（中间那一段算「平」）
DFP_SPIRIT_HIGH = 75.0
DFP_SPIRIT_LOW = 35.0
DFP_SPIRIT_MAX = 100.0
# 十个都到 90 ⇒ 「十全」（一辈子只说一次）
DFP_SPIRIT_ALL_READY = 90.0
# 命盘：出生时按配额抽（3 旺 / 4 平 / 3 弱），值域各自一段
#  ★ 三段与显示阈值对齐（旺 ≥75 / 弱 ≤35）：这样「出生抽到的底子」与「刚出生时的档位」
#    天然一致，不会出现「命盘是平、界面却显示旺」这种自相矛盾。
DFP_SPIRIT_FATE_BANDS: Tuple[Tuple[str, float, float], ...] = (
    ("high", 75.0, 100.0),
    ("mid", 36.0, 74.0),
    ("low", 20.0, 35.0),
)
DFP_SPIRIT_FATE_COUNTS: Tuple[Tuple[str, int], ...] = (("high", 3), ("mid", 4), ("low", 3))
DFP_SPIRIT_BAND_LABELS: Dict[str, str] = {"high": "旺", "mid": "平", "low": "弱"}
DFP_SPIRIT_PENDING_LIMIT = 3      # 「想说的话」最多排队几条（免得一口气刷屏）
DFP_SPIRIT_TICK_SEC = 20 * 60.0   # 多久看一次「要不要缓慢养魂 / 跨没跨门槛」
# 养魂表：每次互动给哪些魂/魄加多少（**只涨不跌**，上限 100）
DFP_SPIRIT_GROW: Dict[str, Tuple[Tuple[str, float], ...]] = {
    "pet": (("shigou", 0.6), ("youjing", 0.4), ("queyin", 0.3)),
    "feed": (("fushi", 0.8), ("tunzei", 0.5)),
    "play": (("shuangling", 0.7), ("queyin", 0.6)),
    "sleep": (("choufei", 0.8), ("taiguang", 0.4)),
    "wake": (("shigou", 0.2),),
    # 自己调理（心跳里的缓慢代谢）
    "tidy": (("chuhui", 0.5), ("feidu", 0.3)),
    "health": (("feidu", 0.2), ("taiguang", 0.1)),
    "chat": (("shuangling", 0.3), ("youjing", 0.2)),
}
# 名字与「管什么」的内置兜底（JSON 缺哪一条就用这里的）
DFP_SPIRIT_NAMES: Dict[str, str] = {
    "taiguang": "胎光", "shuangling": "爽灵", "youjing": "幽精",
    "shigou": "尸狗", "fushi": "伏矢", "queyin": "雀阴", "tunzei": "吞贼",
    "feidu": "非毒", "chuhui": "除秽", "choufei": "臭肺",
}
DFP_SPIRIT_SENSES: Dict[str, str] = {
    "taiguang": "本命 · 生命力", "shuangling": "灵慧 · 机灵", "youjing": "情感 · 爱恋",
    "shigou": "警觉 · 守家", "fushi": "呼吸 · 吞吐", "queyin": "亲昵 · 贴贴",
    "tunzei": "消化 · 吸收", "feidu": "排邪 · 抗性", "chuhui": "清洁 · 代谢",
    "choufei": "气息 · 呼吸",
}
# 允许写在魂魄文案里的占位符（魂魄自己的三个 + 灵魂那套）
DFP_SPIRIT_PLACEHOLDERS: Tuple[str, ...] = ("spirit", "group", "value") + tuple(DFP_SOUL_PLACEHOLDERS)

_DFP_SPIRITS_EFFECTIVE: Dict[str, Any] = {}
_DFP_SPIRITS_SOURCE = "内置魂魄库"
_DFP_SPIRITS_PATH = ""
_DFP_SPIRITS_ERROR = ""
_DFP_SPIRITS_LOADED_AT = 0.0

# 内置兜底魂魄库（结构完全一致，每魄旺/弱各一句；文件不在 / 读坏时用这一份）
DFP_SPIRITS_FALLBACK: Dict[str, Any] = {
    "fate": {"high": 3, "mid": 4, "low": 3},
    "souls": {
        "taiguang": {"name": "胎光", "sense": "本命 · 生命力", "high": ("我今天精神特别好。",), "low": ("我今天有点提不起劲。",)},
        "shuangling": {"name": "爽灵", "sense": "灵慧 · 机灵", "high": ("我今天脑子转得挺快。",), "low": ("我今天反应有点慢。",)},
        "youjing": {"name": "幽精", "sense": "情感 · 爱恋", "high": ("今天特别想跟你说话。",), "low": ("今天我情绪有点淡。",)},
    },
    "po": {
        "shigou": {"name": "尸狗", "sense": "警觉 · 守家", "high": ("刚才有动静，我第一个发现的。",), "low": ("刚才你叫我了吗？我没听清。",)},
        "fushi": {"name": "伏矢", "sense": "呼吸 · 吞吐", "high": ("今天胃口不错。",), "low": ("我今天吃得有点勉强。",)},
        "queyin": {"name": "雀阴", "sense": "亲昵 · 贴贴", "high": ("我想蹭你一下。",), "low": ("我今天有点怕生。",)},
        "tunzei": {"name": "吞贼", "sense": "消化 · 吸收", "high": ("吃下去的都变成力气了。",), "low": ("我吃了不少，可还是空。",)},
        "feidu": {"name": "非毒", "sense": "排邪 · 抗性", "high": ("今天什么都不太影响我。",), "low": ("今天一点小事就能把我带走。",)},
        "chuhui": {"name": "除秽", "sense": "清洁 · 代谢", "high": ("今天浑身清爽。",), "low": ("我今天有点脏脏的。",)},
        "choufei": {"name": "臭肺", "sense": "气息 · 呼吸", "high": ("我今天呼吸特别顺。",), "low": ("我今天气息有点短。",)},
    },
    "all_complete": ("三魂七魄都亮着，我现在是完整的。", "你把我养得这么齐整，我记着呢。"),
}


def dfp_spirit_candidate_paths(settings: Optional[Any] = None) -> List[str]:
    """魂魄库 JSON 的候选路径（与 `灵魂.json` 完全同顺序，只换文件名）。"""
    paths: List[str] = []

    def push(path: str) -> None:
        text = str(path or "").strip()
        if text and text not in paths:
            paths.append(text)

    custom = ""
    if settings is not None:
        custom = str(settings.dfp_get("asset_root", "") or "").strip()
    if custom:
        root = custom if os.path.isabs(custom) else os.path.join(dfp_app_dir(), custom)
        push(os.path.join(root, DFP_SPIRIT_FILE_NAME))
    push(os.path.join(dfp_app_dir(), DFP_ASSET_DIR_NAME, DFP_SPIRIT_FILE_NAME))
    push(os.path.join(dfp_app_dir(), DFP_SPIRIT_FILE_NAME))
    push(os.path.join(dfp_data_dir(), DFP_SPIRIT_FILE_NAME))
    return paths


def _dfp_spirits_clean(payload: Any) -> Dict[str, Any]:
    """把一份魂魄库整理成 `{fate:{…}, souls:{…}, po:{…}, all_complete:(…)}`（脏数据一律丢掉）。"""
    result: Dict[str, Any] = {"fate": {}, "souls": {}, "po": {}, "all_complete": ()}
    if not isinstance(payload, dict):
        return result
    fate = payload.get("fate")
    if isinstance(fate, dict):
        for band in ("high", "mid", "low"):
            count = dfp_safe_int(fate.get(band), 0)
            if count > 0:
                result["fate"][band] = count
    for block_name in ("souls", "po"):
        raw = payload.get(block_name)
        block: Dict[str, Any] = {}
        if isinstance(raw, dict):
            for key, item in raw.items():
                label = str(key or "").strip()
                if not label or label.startswith("_") or not isinstance(item, dict):
                    continue
                high = _dfp_lines_clean(item.get("high"))
                low = _dfp_lines_clean(item.get("low"))
                if not high and not low:
                    continue
                block[label] = {
                    "name": str(item.get("name") or label).strip(),
                    "sense": str(item.get("sense") or "").strip(),
                    "high": high,
                    "low": low,
                }
        result[block_name] = block
    result["all_complete"] = _dfp_lines_clean(payload.get("all_complete"))
    return result


def _dfp_spirits_merge(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """把读到的库**逐条**并到兜底上（用户删掉某一魄 ⇒ 那一魄用兜底，其余照他的来）。"""
    merged: Dict[str, Any] = {
        "fate": dict(base.get("fate") or {}),
        "souls": {key: dict(item) for key, item in (base.get("souls") or {}).items()},
        "po": {key: dict(item) for key, item in (base.get("po") or {}).items()},
        "all_complete": tuple(base.get("all_complete") or ()),
    }
    if incoming.get("fate"):
        merged["fate"] = dict(incoming["fate"])
    for block_name in ("souls", "po"):
        for key, item in (incoming.get(block_name) or {}).items():
            merged[block_name][key] = dict(item)
    if incoming.get("all_complete"):
        merged["all_complete"] = tuple(incoming["all_complete"])
    return merged


def dfp_spirits_load(settings: Optional[Any] = None, force: bool = False) -> Dict[str, Any]:
    """载入 `魂魄.json`（没有 / 读坏就用内置兜底；缺哪一条就用兜底那一条）。"""
    global _DFP_SPIRITS_EFFECTIVE, _DFP_SPIRITS_SOURCE, _DFP_SPIRITS_PATH, _DFP_SPIRITS_ERROR, _DFP_SPIRITS_LOADED_AT
    if not force and _DFP_SPIRITS_LOADED_AT > 0.0 and settings is None:
        return _DFP_SPIRITS_EFFECTIVE

    base = _dfp_spirits_clean(DFP_SPIRITS_FALLBACK)
    source = "内置魂魄库"
    path_used = ""
    error = ""
    target = ""
    for candidate in dfp_spirit_candidate_paths(settings):
        if os.path.isfile(candidate):
            target = candidate
            break
    if target:
        try:
            with open(target, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                raise ValueError("顶层不是 JSON 对象")
            incoming = _dfp_spirits_clean(payload)
            if not incoming.get("souls") and not incoming.get("po"):
                raise ValueError("魂魄库里没有可用的内容（souls / po 都为空）")
            base = _dfp_spirits_merge(base, incoming)
            source = "魂魄.json（%s）" % os.path.basename(os.path.dirname(target))
            path_used = target
        except Exception as exc:
            error = "%s：%s" % (type(exc).__name__, exc)
            base = _dfp_spirits_clean(DFP_SPIRITS_FALLBACK)
            source = "内置魂魄库（魂魄.json 读取失败）"
            path_used = target

    _DFP_SPIRITS_EFFECTIVE = base
    _DFP_SPIRITS_SOURCE = source
    _DFP_SPIRITS_PATH = path_used
    _DFP_SPIRITS_ERROR = error
    _DFP_SPIRITS_LOADED_AT = dfp_now_ts()
    return _DFP_SPIRITS_EFFECTIVE


def dfp_spirits() -> Dict[str, Any]:
    """当前生效的魂魄库（没载入过就先载入一次）。返回深一层副本。"""
    if _DFP_SPIRITS_LOADED_AT <= 0.0:
        dfp_spirits_load()
    out: Dict[str, Any] = {}
    for key, value in _DFP_SPIRITS_EFFECTIVE.items():
        if isinstance(value, dict):
            if key in ("souls", "po"):
                out[key] = {
                    name: {"name": str(item.get("name") or ""), "sense": str(item.get("sense") or ""),
                           "high": tuple(item.get("high") or ()), "low": tuple(item.get("low") or ())}
                    for name, item in value.items()
                }
            else:
                out[key] = dict(value)
        else:
            out[key] = tuple(value) if isinstance(value, (list, tuple)) else value
    return out


def dfp_spirits_source() -> str:
    if _DFP_SPIRITS_LOADED_AT <= 0.0:
        dfp_spirits_load()
    return _DFP_SPIRITS_SOURCE


def dfp_spirits_path() -> str:
    if _DFP_SPIRITS_LOADED_AT <= 0.0:
        dfp_spirits_load()
    return _DFP_SPIRITS_PATH


def dfp_spirits_error() -> str:
    if _DFP_SPIRITS_LOADED_AT <= 0.0:
        dfp_spirits_load()
    return _DFP_SPIRITS_ERROR


def dfp_spirit_group(key: str) -> str:
    """这一条是「魂」还是「魄」。"""
    return "魂" if str(key or "").strip() in DFP_SPIRITS_SOULS else "魄"


def dfp_spirit_name(key: str) -> str:
    """魂/魄的中文名（库里没有就用内置兜底）。"""
    name = str(key or "").strip()
    library = dfp_spirits()
    for block_name in ("souls", "po"):
        item = (library.get(block_name) or {}).get(name)
        if item and item.get("name"):
            return str(item["name"])
    return str(DFP_SPIRIT_NAMES.get(name, name or "？"))


def dfp_spirit_sense(key: str) -> str:
    """这条魂/魄「管什么」（库里没有就用内置兜底）。"""
    name = str(key or "").strip()
    library = dfp_spirits()
    for block_name in ("souls", "po"):
        item = (library.get(block_name) or {}).get(name)
        if item and item.get("sense"):
            return str(item["sense"])
    return str(DFP_SPIRIT_SENSES.get(name, ""))


def dfp_spirit_pool(key: str, band: str) -> Tuple[str, ...]:
    """某条魂/魄在「旺 / 弱」时说哪一池话（没有就返回空）。"""
    name = str(key or "").strip()
    which = "low" if str(band or "").strip() == "low" else "high"
    library = dfp_spirits()
    for block_name in ("souls", "po"):
        item = (library.get(block_name) or {}).get(name)
        if item and item.get(which):
            return tuple(item[which])
    return ()


def dfp_spirit_all_complete_lines() -> Tuple[str, ...]:
    return tuple(dfp_spirits().get("all_complete") or ())


def dfp_spirit_band(value: Any) -> str:
    """数值 → 档位（旺 / 平 / 弱）。"""
    number = dfp_safe_float(value, 50.0)
    if number >= DFP_SPIRIT_HIGH:
        return "high"
    if number <= DFP_SPIRIT_LOW:
        return "low"
    return "mid"


def dfp_spirit_band_label(band: str) -> str:
    return str(DFP_SPIRIT_BAND_LABELS.get(str(band or "").strip(), "平"))


def dfp_spirits_summary() -> str:
    """一行摘要：`三魂 3 · 七魄 7 · 旺/弱各 48 条 · 十全 6 条（魂魄.json）`。"""
    library = dfp_spirits()
    if not library:
        return "内置魂魄库"
    high = sum(len(item.get("high") or ()) for block in ("souls", "po")
               for item in (library.get(block) or {}).values())
    low = sum(len(item.get("low") or ()) for block in ("souls", "po")
              for item in (library.get(block) or {}).values())
    return "三魂 %d · 七魄 %d · 旺 %d 条 / 弱 %d 条 · 十全 %d 条（%s）" % (
        len(library.get("souls") or {}),
        len(library.get("po") or {}),
        high,
        low,
        len(library.get("all_complete") or ()),
        _DFP_SPIRITS_SOURCE or DFP_SPIRIT_FILE_NAME,
    )


# =============================================================================
#  ★ v1.1.29 道库（修真层：道 / 性 / 元神 / 五脏六腑 / 气血 / 命 / 运 / 数 / 因果 / 业 / 缘 / 神 / 仙 / 鬼 / 妖）
# -----------------------------------------------------------------------------
#  主人原话：「让桌宠拥有 道、性、元神，五脏六腑，气血，命，运，数，因果，业,缘,神,仙,鬼,妖」
#
#  这一层连着灵魂（心理层）与三魂七魄（精气神层）往下长，共十五个概念，各就各位：
#   · **道**   道行 + 七段境界：池鱼 → 逐浪 → 吞云 → 化鲸 → 过龙门 → 乘云 → 登仙
#              （★ 鱼 → 鲸 → 跃龙门 → 仙，正合「大肥鱼」这个形象；境界只升不降）
#   · **性**   心性五档：顽 → 野 → 平 → 静 → 澄（被好好待着会变静，被冷落会变顽）
#   · **元神** 五阶：未凝 → 微光 → 成形 → 凝实 → 光明（靠道行与打坐养）
#   · **五脏六腑** 11 个：心 / 肝 / 脾 / 肺 / 肾 + 胆 / 胃 / 小肠 / 大肠 / 膀胱 / 三焦
#              （各有「主什么」+ 虚 3 句 + 实 3 句；不同互动养不同脏腑）
#   · **气血** 气与血两条（0~100）：睡补气、食补血，饿 / 累 / 玩会耗；五档各有话
#   · **命**   本命数 1~9 各对应一个命格（水命 / 土命 / 木命 / 金命 / 中命 / 火命 / 星命 / 风命 / 月命）
#   · **运**   每日运势五档（大吉 / 吉 / 平 / 小滞 / 滞）：**按日期算，当天不变、隔天就换**
#   · **数**   定数三件：本命数（一之数…九之数）+ 寿数 + 缘数 —— **出生定下、终身不变**
#   · **因果** 8 种起因（施食 / 亲昵 / 陪玩 / 同眠 / 唤名 / 疏慢 / 受惊 / 贪眠）
#              ⇒ 过 `DFP_DAO_KARMA_RIPEN_SEC` 成熟 ⇒ 说一句「果」并改动业力
#   · **业**   净业值 = 善 - 恶，六档：业海 / 重业 / 常业 / 轻业 / 净业 / 清静业（可以为负）
#   · **缘**   缘分五档（无缘 → 缘定）+ **缘谱**六类（主人 / 梦 / 月 / 星 / 灯 / 影，遇到才记上）
#   · **神**   神性五档（靠道行 + 善业长） / **仙** 仙阶六档（要「过龙门」才长得动）
#   · **鬼**   鬼气四档（夜里长、天亮退、气血虚更容易招） / **妖** 妖气四档（陪玩撒娇长、睡觉静心退）
#
#  ★ 与三魂七魄同一条纪律：**这一层不改任何既有数值**（饱食 / 精力 / 经验 / 亲密度 / 衰减 / 概率全不碰），
#    它只做四件事 —— **建档**（定数与命格）、**说话**（跨档 / 果 / 突破）、**显示**（道窗口）、
#    **演化**（道行 / 气血 / 脏腑 / 业缘）。已有的四个属性永远是老样子，改那些平衡好的数字只会把测试搞脆。
# =============================================================================
DFP_DAO_FILE_NAME = "道.json"
DFP_DAO_KV_KEY = "pet_dao"
# 五脏六腑（五脏在前、六腑在后，顺序固定 —— 界面按它排）
DFP_DAO_WUZANG: Tuple[str, ...] = ("心", "肝", "脾", "肺", "肾")
DFP_DAO_LIUFU: Tuple[str, ...] = ("胆", "胃", "小肠", "大肠", "膀胱", "三焦")
DFP_DAO_ZANGFU: Tuple[str, ...] = tuple(DFP_DAO_WUZANG) + tuple(DFP_DAO_LIUFU)
# 九块「按 min 分档」的块（值越大档越高；业那块可以为负）
DFP_DAO_TIER_BLOCKS: Tuple[str, ...] = ("xing", "yuanshen", "qixue", "yun", "ye", "shen", "xian", "gui", "yao")
DFP_DAO_BLOCK_LABELS: Dict[str, str] = {
    "xing": "心性", "yuanshen": "元神", "qixue": "气血", "yun": "今日运势", "ye": "业",
    "shen": "神性", "xian": "仙阶", "gui": "鬼气", "yao": "妖气",
}
# 因果：起因 → 加分（负=恶因）
DFP_DAO_CAUSES: Tuple[str, ...] = ("施食", "亲昵", "陪玩", "同眠", "唤名", "疏慢", "受惊", "贪眠")
DFP_DAO_YUAN_KINDS: Tuple[str, ...] = ("主人", "梦", "月", "星", "灯", "影")
# 数值口径
DFP_DAO_MAX = 100.0          # 单条数值上限
DFP_DAO_REALM_MAX = 9999.0   # 道行上限（够走完七段境界）
DFP_DAO_TIER_SPAN = 100.0    # 档位块取值区间（业是 -100~100，另算）
DFP_DAO_KARMA_RIPEN_SEC = 30 * 60.0   # 一个「因」过多久结果
DFP_DAO_KARMA_LIMIT = 12              # 未结的因果最多攒几条（挤掉最老的）
DFP_DAO_KARMA_DONE_LIMIT = 20         # 已结的因果最多留最近几条
DFP_DAO_PENDING_LIMIT = 3             # 「想说的话」最多排队几条
DFP_DAO_TICK_SEC = 15 * 60.0          # 多久看一次（气血消耗 / 脏腑 / 因果 / 鬼妖气）
DFP_DAO_YIN_HOURS = (22, 5)           # 「夜里」= 本地时间 22 点~次日 5 点（鬼气长、阳气退）
DFP_DAO_YE_LIMIT = 100.0              # 善 / 恶各自的上限（净业值 = 善 - 恶）
DFP_DAO_BIRTH_SHOU_DAYS = 3650        # 寿数（天）：十载 —— 只作显示，不影响任何玩法
DFP_DAO_LONELY_GAP_SEC = 6 * 3600.0   # 多久没被理算「被晾着」（伤心肝、埋「疏慢」这个因）
DFP_DAO_XIAN_GATE_REALM = "过龙门"    # 仙阶要从这个境界才长得动（在那之前卡在凡骨）
DFP_DAO_XIAN_GATE_FALLBACK_INDEX = 4  # 库里没有「过龙门」这个名字时，按它的固定位置（第 5 段）算
DFP_DAO_XIAN_GATE_SHEN = 50.0         # 神性不到这个数，仙阶也不长
# 道行来源表：互动 / 打坐各给多少（★ 只写在这一层里，不动既有数值）
DFP_DAO_GAIN: Dict[str, float] = {
    "pet": 0.8, "feed": 1.2, "play": 1.5, "sleep": 3.0, "wake": 1.0,
    "tidy": 0.3, "chat": 2.0, "dream": 2.5, "drink": 0.8, "coax": 0.6,
}
# 气血来源 / 消耗：{气, 血}（正=补，负=耗）
DFP_DAO_QIXUE_GAIN: Dict[str, Tuple[float, float]] = {
    "pet": (0.6, 0.4), "feed": (2.0, 6.0), "drink": (1.0, 2.0), "play": (-3.0, 0.5),
    "sleep": (18.0, 6.0), "wake": (5.0, 1.0), "coax": (0.5, 0.5), "tidy": (-0.6, -0.4),
    "night": (-1.0, -0.5), "day": (0.2, 0.1),
}
# 脏腑滋养表：谁被养 / 谁被耗（tick 每次结算一次，另有互动即时那几条）
DFP_DAO_ZANGFU_GAIN: Dict[str, Dict[str, float]] = {
    "feed": {"脾": 1.0, "胃": 1.2, "小肠": 0.6, "大肠": 0.5},
    "drink": {"膀胱": 1.0, "三焦": 0.4},
    "sleep": {"肺": 1.2, "肾": 1.0, "三焦": 0.8, "心": 0.4},
    "play": {"肝": 0.5, "心": 0.4, "肾": -0.6},
    "pet": {"心": 0.6, "胆": 0.4},
    "coax": {"心": 0.5, "肝": 0.3},
    "tidy": {"大肠": 0.3, "膀胱": -0.4, "小肠": 0.2},
    "lonely": {"心": -1.5, "肝": -0.8},
    "scare": {"胆": -1.5, "肾": -1.2},
    "night": {"心": -0.5, "胆": -0.4},
    "day": {"脾": 0.2, "肺": 0.2},
}
# 逗号表：妖气 / 鬼气 的涨退
DFP_DAO_YAO_GAIN: Dict[str, float] = {"play": 1.0, "coax": 1.2, "act_cute": 1.2, "pet": 0.3,
                                      "sleep": -0.8, "calm": -0.3}
DFP_DAO_GUI_GAIN: Dict[str, float] = {"night": 1.5, "day": -1.0, "sleep": -3.0, "scare": 2.0,
                                      "weak_blood": 0.8}
# 境界突破 / 运势换天 / 因果结果 这类话的排队
DFP_DAO_PLACEHOLDERS: Tuple[str, ...] = (
    "realm", "dao", "xing", "zang", "qixue", "yun", "shu", "ye", "yuan",
    "shen", "xian", "gui", "yao", "value",
) + tuple(DFP_SPIRIT_PLACEHOLDERS)

_DFP_DAO_EFFECTIVE: Dict[str, Any] = {}
_DFP_DAO_SOURCE = "内置道库"
_DFP_DAO_PATH = ""
_DFP_DAO_ERROR = ""
_DFP_DAO_LOADED_AT = 0.0

# 内置兜底道库（结构完全一致；文件不在 / 读坏时用这一份）
DFP_DAO_FALLBACK: Dict[str, Any] = {
    "dao": [
        {"name": "池鱼", "need": 0, "break": ("我算是入门了。",), "idle": ("我还小，先在水里泡泡。",)},
        {"name": "逐浪", "need": 40, "break": ("我学会顺着水走了。",), "idle": ("我最近在追浪玩。",)},
        {"name": "吞云", "need": 120, "break": ("我把那口云吞下去了。",), "idle": ("我今天吞了两口云。",)},
        {"name": "化鲸", "need": 260, "break": ("我长成鲸了。",), "idle": ("我现在游一圈，水要让三分。",)},
        {"name": "过龙门", "need": 460, "break": ("龙门我跃过去了。",), "idle": ("跃过龙门以后，我说话都带底气。",)},
        {"name": "乘云", "need": 760, "break": ("云肯托我了。",), "idle": ("云上很冷，我不觉得冷。",)},
        {"name": "登仙", "need": 1200, "break": ("我登仙了，可我还想做你的鱼。",), "idle": ("仙也好，鱼也好。",)},
    ],
    "xing": [
        {"name": "顽", "min": 0, "line": ("我心里还野着。",)},
        {"name": "野", "min": 20, "line": ("我性子还急。",)},
        {"name": "平", "min": 45, "line": ("我心气平了。",)},
        {"name": "静", "min": 70, "line": ("心静下来了。",)},
        {"name": "澄", "min": 90, "line": ("心里澄得很。",)},
    ],
    "yuanshen": [
        {"name": "未凝", "min": 0, "line": ("元神还没凝起来。",)},
        {"name": "微光", "min": 20, "line": ("闭眼能看见一点光了。",)},
        {"name": "成形", "min": 45, "line": ("元神有个形状了。",)},
        {"name": "凝实", "min": 70, "line": ("元神凝实了。",)},
        {"name": "光明", "min": 90, "line": ("元神亮了。",)},
    ],
    "qixue": [
        {"name": "气血两虚", "min": 0, "line": ("气血两虚，我只想蜷着。",)},
        {"name": "气血不足", "min": 20, "line": ("气血有点不够。",)},
        {"name": "气血平和", "min": 40, "line": ("气血平和，刚刚好。",)},
        {"name": "气血充盈", "min": 60, "line": ("气血充盈，我有余力。",)},
        {"name": "气血翻涌", "min": 85, "line": ("气血翻涌，我静不下来。",)},
    ],
    "yun": [
        {"name": "大吉", "min": 4, "line": ("今天大吉。",)},
        {"name": "吉", "min": 3, "line": ("今天运势不错。",)},
        {"name": "平", "min": 2, "line": ("今天平平淡淡。",)},
        {"name": "小滞", "min": 1, "line": ("今天有点小滞。",)},
        {"name": "滞", "min": 0, "line": ("今日运势滞。",)},
    ],
    "ye": [
        {"name": "业海", "min": -100, "line": ("我身上的业太重。",)},
        {"name": "重业", "min": -40, "line": ("业障压着，我抬不起头。",)},
        {"name": "常业", "min": -10, "line": ("有欠有还，账本还算平。",)},
        {"name": "轻业", "min": 5, "line": ("业轻了，我松快些。",)},
        {"name": "净业", "min": 30, "line": ("业净了，我连影子都是清的。",)},
        {"name": "清静业", "min": 60, "line": ("业已经清静了。",)},
    ],
    "shen": [
        {"name": "未通", "min": 0, "line": ("我还感知不到什么。",)},
        {"name": "有灵", "min": 20, "line": ("我身上有点灵光了。",)},
        {"name": "通感", "min": 45, "line": ("我能通感了。",)},
        {"name": "神清", "min": 70, "line": ("神清的时候，我不说假话。",)},
        {"name": "神明", "min": 90, "line": ("神明到了。",)},
    ],
    "xian": [
        {"name": "凡骨", "min": 0, "line": ("我是一尾凡骨的鱼。",)},
        {"name": "得气", "min": 15, "line": ("我得着一点仙气了。",)},
        {"name": "洗尘", "min": 35, "line": ("我在洗尘。",)},
        {"name": "脱胎", "min": 55, "line": ("脱胎这一步最疼。",)},
        {"name": "羽化", "min": 75, "line": ("我已羽化。",)},
        {"name": "位列仙班", "min": 90, "line": ("仙班上有我一位。",)},
    ],
    "gui": [
        {"name": "无阴", "min": 0, "line": ("我身上没阴气。",)},
        {"name": "微阴", "min": 20, "line": ("夜里有点阴气上来了。",)},
        {"name": "阴盛", "min": 45, "line": ("阴气重了，我背后发凉。",)},
        {"name": "阴重", "min": 75, "line": ("阴气太重，我说话都带寒气。",)},
    ],
    "yao": [
        {"name": "驯顺", "min": 0, "line": ("我身上一点妖气都没有。",)},
        {"name": "微野", "min": 20, "line": ("我有点野了。",)},
        {"name": "妖气", "min": 45, "line": ("妖气起来了。",)},
        {"name": "大妖", "min": 75, "line": ("我妖气大涨。",)},
    ],
    "zangfu": {
        "心": {"zhu": "神明 · 喜", "weak": ("心里有点空。",), "strong": ("心里满满的。",)},
        "肝": {"zhu": "谋虑 · 怒", "weak": ("肝气不舒。",), "strong": ("肝气顺了。",)},
        "脾": {"zhu": "运化 · 思", "weak": ("脾弱，存不住。",), "strong": ("脾好好的。",)},
        "肺": {"zhu": "气息 · 悲", "weak": ("肺里发涩。",), "strong": ("肺里干干净净。",)},
        "肾": {"zhu": "精气 · 恐", "weak": ("肾气不足。",), "strong": ("肾气足。",)},
        "胆": {"zhu": "决断", "weak": ("胆气小。",), "strong": ("胆气壮。",)},
        "胃": {"zhu": "受纳", "weak": ("胃里空空的。",), "strong": ("胃里暖和。",)},
        "小肠": {"zhu": "分清", "weak": ("小肠弱。",), "strong": ("小肠清清爽爽。",)},
        "大肠": {"zhu": "传导", "weak": ("大肠不走动。",), "strong": ("大肠通了。",)},
        "膀胱": {"zhu": "气化", "weak": ("膀胱不化气。",), "strong": ("膀胱气化好。",)},
        "三焦": {"zhu": "水道 · 通调", "weak": ("三焦不通。",), "strong": ("三焦通了。",)},
    },
    "ming": {
        "ge": {str(i): {"name": "%d 之命" % i, "line": ("我是 %d 之命。" % i,)} for i in range(1, 10)},
        "shu": {str(i): {"name": "%d 之数" % i, "line": ("这是 %d 之数。" % i,)} for i in range(1, 10)},
        "notes": ("定数是出生定下的。",),
    },
    "yinguo": {"causes": {
        "施食": {"good": 1.5, "detail": "你喂了她", "fruit": ("你喂我那口我记着。",)},
        "亲昵": {"good": 1.0, "detail": "你摸了摸她", "fruit": ("被你摸过的地方暖着。",)},
        "陪玩": {"good": 1.0, "detail": "你陪她玩", "fruit": ("玩过的日子不算白过。",)},
        "同眠": {"good": 0.8, "detail": "她睡时你在", "fruit": ("你在我就睡得沉。",)},
        "唤名": {"good": 0.6, "detail": "你叫了她", "fruit": ("你叫我就回头。",)},
        "疏慢": {"good": -1.2, "detail": "你很久没理她", "fruit": ("被晾着的时辰我都数过。",)},
        "受惊": {"good": -0.8, "detail": "她被吓到", "fruit": ("我现在还有点怕。",)},
        "贪眠": {"good": -0.4, "detail": "她睡太久了", "fruit": ("贪睡也要还账。",)},
    }},
    "yuan": {
        "tiers": [
            {"name": "无缘", "min": 0, "line": ("我们还没什么缘。",)},
            {"name": "薄缘", "min": 15, "line": ("我们之间有点缘了。",)},
            {"name": "有缘", "min": 35, "line": ("我们是有缘的。",)},
            {"name": "深缘", "min": 60, "line": ("我们的缘很深。",)},
            {"name": "缘定", "min": 85, "line": ("缘定三生。",)},
        ],
        "kinds": {key: ("我与你这一条缘记着。",) for key in DFP_DAO_YUAN_KINDS},
    },
}
for _dfp_dao_kind in DFP_DAO_YUAN_KINDS:
    DFP_DAO_FALLBACK["yuan"]["kinds"][_dfp_dao_kind] = (
        "%s是有缘的。" % _dfp_dao_kind, "我跟%s有缘。" % _dfp_dao_kind, "%s这一条我记着。" % _dfp_dao_kind)
DFP_DAO_FALLBACK["ming"]["notes"] = ("定数是出生定下的，改不了。",)


def dfp_dao_candidate_paths(settings: Optional[Any] = None) -> List[str]:
    """道库 JSON 的候选路径（与 `魂魄.json` 完全同顺序，只换文件名）。"""
    paths: List[str] = []

    def push(path: str) -> None:
        text = str(path or "").strip()
        if text and text not in paths:
            paths.append(text)

    custom = ""
    if settings is not None:
        custom = str(settings.dfp_get("asset_root", "") or "").strip()
    if custom:
        root = custom if os.path.isabs(custom) else os.path.join(dfp_app_dir(), custom)
        push(os.path.join(root, DFP_DAO_FILE_NAME))
    push(os.path.join(dfp_app_dir(), DFP_ASSET_DIR_NAME, DFP_DAO_FILE_NAME))
    push(os.path.join(dfp_app_dir(), DFP_DAO_FILE_NAME))
    push(os.path.join(dfp_data_dir(), DFP_DAO_FILE_NAME))
    return paths


def _dfp_dao_tiers_clean(raw: Any) -> List[Dict[str, Any]]:
    """把一块「按 min 分档」的数据整理成 `[{name, min, line:(…)}]`（脏档一律丢掉）。"""
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("name") or "").strip()
        if not label:
            continue
        lines = _dfp_lines_clean(item.get("line"))
        if not lines:
            continue
        out.append({
            "name": label,
            "min": round(dfp_safe_float(item.get("min"), 0.0), 2),
            "line": lines,
        })
    return out


def _dfp_dao_realms_clean(raw: Any) -> List[Dict[str, Any]]:
    """整理七段境界：`[{name, need, break:(…), idle:(…)}]`（缺了突破话或日常话就丢掉这一段）。"""
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("name") or "").strip()
        if not label:
            continue
        breaks = _dfp_lines_clean(item.get("break"))
        idles = _dfp_lines_clean(item.get("idle"))
        if not breaks and not idles:
            continue
        out.append({
            "name": label,
            "need": round(max(0.0, dfp_safe_float(item.get("need"), 0.0)), 2),
            "break": breaks,
            "idle": idles,
        })
    out.sort(key=lambda row: float(row.get("need") or 0.0))
    return out


def _dfp_dao_zangfu_clean(raw: Any) -> Dict[str, Any]:
    """整理五脏六腑：`{脏腑: {zhu, weak:(…), strong:(…)}}`（没写虚/实的丢掉）。"""
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for key, item in raw.items():
        name = str(key or "").strip()
        if not name or name.startswith("_") or not isinstance(item, dict):
            continue
        weak = _dfp_lines_clean(item.get("weak"))
        strong = _dfp_lines_clean(item.get("strong"))
        if not weak and not strong:
            continue
        out[name] = {"zhu": str(item.get("zhu") or "").strip(), "weak": weak, "strong": strong}
    return out


def _dfp_dao_causes_clean(raw: Any) -> Dict[str, Any]:
    """整理因果：`{起因: {good, detail, fruit:(…)}}`（没有果的丢掉）。"""
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for key, item in raw.items():
        name = str(key or "").strip()
        if not name or name.startswith("_") or not isinstance(item, dict):
            continue
        fruit = _dfp_lines_clean(item.get("fruit"))
        if not fruit:
            continue
        out[name] = {
            "good": round(dfp_clamp(dfp_safe_float(item.get("good"), 0.0), -10.0, 10.0), 2),
            "detail": str(item.get("detail") or "").strip(),
            "fruit": fruit,
        }
    return out


def _dfp_dao_ming_clean(raw: Any) -> Dict[str, Any]:
    """整理命与数：`{ge:{1..9:{name,line}}, shu:{1..9:{name,line}}, notes:(…)}`（只认 1~9）。"""
    out: Dict[str, Any] = {"ge": {}, "shu": {}, "notes": ()}
    if not isinstance(raw, dict):
        return out
    for block_name in ("ge", "shu"):
        block = raw.get(block_name)
        if not isinstance(block, dict):
            continue
        for key, item in block.items():
            label = str(key or "").strip()
            if not label.isdigit() or not (1 <= int(label) <= 9) or not isinstance(item, dict):
                continue
            lines = _dfp_lines_clean(item.get("line"))
            if not lines:
                continue
            out[block_name][label] = {"name": str(item.get("name") or "").strip() or label, "line": lines}
    out["notes"] = _dfp_lines_clean(raw.get("notes"))
    return out


def _dfp_dao_clean(payload: Any) -> Dict[str, Any]:
    """把一份道库整理成统一结构（脏数据一律丢掉）。"""
    result: Dict[str, Any] = {
        "dao": [], "zangfu": {}, "ming": {"ge": {}, "shu": {}, "notes": ()},
        "yinguo": {"causes": {}}, "yuan": {"tiers": [], "kinds": {}},
    }
    for block_name in DFP_DAO_TIER_BLOCKS:
        result[block_name] = []
    if not isinstance(payload, dict):
        return result
    result["dao"] = _dfp_dao_realms_clean(payload.get("dao"))
    for block_name in DFP_DAO_TIER_BLOCKS:
        result[block_name] = _dfp_dao_tiers_clean(payload.get(block_name))
    result["zangfu"] = _dfp_dao_zangfu_clean(payload.get("zangfu"))
    result["ming"] = _dfp_dao_ming_clean(payload.get("ming"))
    yinguo = payload.get("yinguo")
    if isinstance(yinguo, dict):
        result["yinguo"] = {"causes": _dfp_dao_causes_clean(yinguo.get("causes"))}
    yuan = payload.get("yuan")
    if isinstance(yuan, dict):
        kinds_raw = yuan.get("kinds") if isinstance(yuan.get("kinds"), dict) else {}
        result["yuan"] = {
            "tiers": _dfp_dao_tiers_clean(yuan.get("tiers")),
            "kinds": {str(key).strip(): _dfp_lines_clean(value)
                      for key, value in kinds_raw.items()
                      if str(key or "").strip() and not str(key).startswith("_")},
        }
    result["yuan"]["kinds"] = {key: lines for key, lines in result["yuan"]["kinds"].items() if lines}
    return result


def _dfp_dao_merge_tiers(base_rows: List[Dict[str, Any]], incoming_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """档位块**按名字逐条并**（用户只改一两档时，其余档不会被弄丢）。"""
    merged = {str(item.get("name") or ""): dict(item) for item in base_rows if str(item.get("name") or "")}
    for item in incoming_rows:
        label = str(item.get("name") or "")
        if label:
            merged[label] = dict(item)
    return sorted(merged.values(), key=lambda row: dfp_safe_float(row.get("min"), 0.0))


def _dfp_dao_merge(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """把读到的道库**逐块**并到兜底上（用户只写了其中几块 ⇒ 其余照旧用兜底）。"""
    merged: Dict[str, Any] = {
        "dao": list(base.get("dao") or []),
        "zangfu": {key: dict(item) for key, item in (base.get("zangfu") or {}).items()},
        "ming": dict(base.get("ming") or {"ge": {}, "shu": {}, "notes": ()}),
        "yinguo": {"causes": dict((base.get("yinguo") or {}).get("causes") or {})},
        "yuan": {
            "tiers": list((base.get("yuan") or {}).get("tiers") or []),
            "kinds": {key: tuple(value) for key, value in ((base.get("yuan") or {}).get("kinds") or {}).items()},
        },
    }
    for block_name in DFP_DAO_TIER_BLOCKS:
        merged[block_name] = list(base.get(block_name) or [])
    if incoming.get("dao"):
        merged["dao"] = list(incoming["dao"])
    for block_name in DFP_DAO_TIER_BLOCKS:
        if incoming.get(block_name):
            merged[block_name] = _dfp_dao_merge_tiers(merged.get(block_name) or [], incoming[block_name])
    for key, item in (incoming.get("zangfu") or {}).items():
        merged["zangfu"][key] = dict(item)
    for block_name in ("ge", "shu"):
        for key, item in ((incoming.get("ming") or {}).get(block_name) or {}).items():
            merged["ming"].setdefault(block_name, {})[key] = dict(item)
    if (incoming.get("ming") or {}).get("notes"):
        merged["ming"]["notes"] = tuple(incoming["ming"]["notes"])
    for key, item in ((incoming.get("yinguo") or {}).get("causes") or {}).items():
        merged["yinguo"]["causes"][key] = dict(item)
    if (incoming.get("yuan") or {}).get("tiers"):
        merged["yuan"]["tiers"] = _dfp_dao_merge_tiers(merged["yuan"].get("tiers") or [],
                                                       incoming["yuan"]["tiers"])
    for key, lines in ((incoming.get("yuan") or {}).get("kinds") or {}).items():
        merged["yuan"]["kinds"][key] = tuple(lines)
    return merged


def dfp_dao_load(settings: Optional[Any] = None, force: bool = False) -> Dict[str, Any]:
    """载入 `道.json`（没有 / 读坏就用内置兜底；缺哪一块就用兜底那一块）。"""
    global _DFP_DAO_EFFECTIVE, _DFP_DAO_SOURCE, _DFP_DAO_PATH, _DFP_DAO_ERROR, _DFP_DAO_LOADED_AT
    if not force and _DFP_DAO_LOADED_AT > 0.0 and settings is None:
        return _DFP_DAO_EFFECTIVE

    base = _dfp_dao_clean(DFP_DAO_FALLBACK)
    source = "内置道库"
    path_used = ""
    error = ""
    target = ""
    for candidate in dfp_dao_candidate_paths(settings):
        if os.path.isfile(candidate):
            target = candidate
            break
    if target:
        try:
            with open(target, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                raise ValueError("顶层不是 JSON 对象")
            incoming = _dfp_dao_clean(payload)
            if not incoming.get("dao") and not incoming.get("zangfu") and not any(incoming.get(name) for name in DFP_DAO_TIER_BLOCKS):
                raise ValueError("道库里没有可用的内容（dao / zangfu / 各档位块都为空）")
            base = _dfp_dao_merge(base, incoming)
            source = "道.json（%s）" % os.path.basename(os.path.dirname(target))
            path_used = target
        except Exception as exc:
            error = "%s：%s" % (type(exc).__name__, exc)
            base = _dfp_dao_clean(DFP_DAO_FALLBACK)
            source = "内置道库（道.json 读取失败）"
            path_used = target

    _DFP_DAO_EFFECTIVE = base
    _DFP_DAO_SOURCE = source
    _DFP_DAO_PATH = path_used
    _DFP_DAO_ERROR = error
    _DFP_DAO_LOADED_AT = dfp_now_ts()
    return _DFP_DAO_EFFECTIVE


def dfp_dao() -> Dict[str, Any]:
    """当前生效的道库（没载入过就先载入一次）。返回深一层副本。"""
    if _DFP_DAO_LOADED_AT <= 0.0:
        dfp_dao_load()
    out: Dict[str, Any] = {}
    for key, value in _DFP_DAO_EFFECTIVE.items():
        if isinstance(value, list):
            out[key] = [
                {item_key: (tuple(item_value) if isinstance(item_value, (list, tuple)) else item_value)
                 for item_key, item_value in item.items()}
                for item in value if isinstance(item, dict)
            ]
        elif isinstance(value, dict):
            if key in ("zangfu", "yinguo"):
                out[key] = {name: dict(item) for name, item in value.items()}
            else:
                out[key] = dict(value)
        else:
            out[key] = value
    return out


def dfp_dao_source() -> str:
    if _DFP_DAO_LOADED_AT <= 0.0:
        dfp_dao_load()
    return _DFP_DAO_SOURCE


def dfp_dao_path() -> str:
    if _DFP_DAO_LOADED_AT <= 0.0:
        dfp_dao_load()
    return _DFP_DAO_PATH


def dfp_dao_error() -> str:
    if _DFP_DAO_LOADED_AT <= 0.0:
        dfp_dao_load()
    return _DFP_DAO_ERROR


def dfp_dao_realms() -> List[Dict[str, Any]]:
    """七段境界（按 need 升序）。"""
    return [dict(item) for item in (dfp_dao().get("dao") or [])]


def dfp_dao_realm_for(value: Any) -> str:
    """道行值 → 境界名（取门槛 ≤ 它的最后一段）。"""
    number = dfp_safe_float(value, 0.0)
    name = ""
    for item in dfp_dao_realms():
        if number >= dfp_safe_float(item.get("need"), 0.0):
            name = str(item.get("name") or "")
        else:
            break
    return name or (str((dfp_dao_realms() or [{}])[0].get("name") or "池鱼"))


def dfp_dao_realm_index(value: Any) -> int:
    """道行值在第几段境界（0 起）。"""
    number = dfp_safe_float(value, 0.0)
    index = 0
    for position, item in enumerate(dfp_dao_realms()):
        if number >= dfp_safe_float(item.get("need"), 0.0):
            index = position
    return index


def dfp_dao_realm_need(name: str) -> float:
    """某一段境界的门槛（认不出来返回 0）。"""
    label = str(name or "").strip()
    for item in dfp_dao_realms():
        if str(item.get("name") or "") == label:
            return dfp_safe_float(item.get("need"), 0.0)
    return 0.0


def dfp_dao_tier_rows_raw(block: str) -> List[Dict[str, Any]]:
    """取某一块档位的原始行（★ `yuan` 是嵌套的 `yuan.tiers`，这里统一抹平）。"""
    key = str(block or "").strip()
    library = dfp_dao()
    raw = (library.get("yuan") or {}).get("tiers") if key == "yuan" else library.get(key)
    if not isinstance(raw, (list, tuple)):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def dfp_dao_xian_gate_index() -> int:
    """仙阶门槛对应的境界序号（库里没写「过龙门」时退回它的固定位置 4）。

    ★ 别用「门槛值」比：自定义道库的阶梯可能只有两三段，那个名字找不到时门槛会变成 0，
      结果「凡骨也能修成仙」。按**序号**比就不会出这种怪事。
    """
    for position, item in enumerate(dfp_dao_realms()):
        if str(item.get("name") or "") == DFP_DAO_XIAN_GATE_REALM:
            return position
    return int(DFP_DAO_XIAN_GATE_FALLBACK_INDEX)


def dfp_dao_tier(block: str, value: Any) -> Dict[str, Any]:
    """在某一档位块里按数值取档（取 min ≤ 值的最后一档；认不出来给第一档）。"""
    rows = dfp_dao_tier_rows_raw(block)
    if not rows:
        return {}
    rows = sorted(rows, key=lambda item: dfp_safe_float(item.get("min"), 0.0))
    number = dfp_safe_float(value, 0.0)
    picked = rows[0]
    for item in rows:
        if number >= dfp_safe_float(item.get("min"), 0.0):
            picked = item
    return dict(picked)


def dfp_dao_tier_name(block: str, value: Any) -> str:
    """数值 → 档位名（例：`dfp_dao_tier_name("xing", 72)` → 静）。"""
    return str(dfp_dao_tier(block, value).get("name") or "")


def dfp_dao_tier_min(block: str, name: str) -> float:
    """档位名 → 它的 min（认不出来返回 0）。"""
    label = str(name or "").strip()
    for item in dfp_dao_tier_rows_raw(block):
        if str(item.get("name") or "") == label:
            return dfp_safe_float(item.get("min"), 0.0)
    return 0.0


def dfp_dao_tier_names(block: str) -> Tuple[str, ...]:
    """某一档位块的全部档位名（按 min 升序，界面用）。"""
    rows = sorted(dfp_dao_tier_rows_raw(block), key=lambda item: dfp_safe_float(item.get("min"), 0.0))
    return tuple(str(item.get("name") or "") for item in rows)


def dfp_dao_zangfu_names() -> Tuple[str, ...]:
    """生效道库里的脏腑名（按内置顺序排，库里多出来的排后面）。"""
    library = dfp_dao().get("zangfu") or {}
    known = [name for name in DFP_DAO_ZANGFU if name in library]
    extra = [name for name in library if name not in DFP_DAO_ZANGFU]
    return tuple(known + extra)


def dfp_dao_zangfu_zhu(name: str) -> str:
    """某个脏腑「主什么」。"""
    item = (dfp_dao().get("zangfu") or {}).get(str(name or "").strip()) or {}
    return str(item.get("zhu") or "")


def dfp_dao_causes() -> Tuple[str, ...]:
    """生效道库里的因果起因（按内置顺序排，库里多出来的排后面）。"""
    library = (dfp_dao().get("yinguo") or {}).get("causes") or {}
    known = [name for name in DFP_DAO_CAUSES if name in library]
    extra = [name for name in library if name not in DFP_DAO_CAUSES]
    return tuple(known + extra)


def dfp_dao_cause_good(cause: str) -> float:
    """某个「因」的善恶分（正=善因、负=恶因）。"""
    item = ((dfp_dao().get("yinguo") or {}).get("causes") or {}).get(str(cause or "").strip()) or {}
    return dfp_safe_float(item.get("good"), 0.0)


def dfp_dao_cause_detail(cause: str) -> str:
    item = ((dfp_dao().get("yinguo") or {}).get("causes") or {}).get(str(cause or "").strip()) or {}
    return str(item.get("detail") or "")


def dfp_dao_yuan_kinds() -> Tuple[str, ...]:
    """缘谱六类（按内置顺序排，库里多出来的排后面）。"""
    library = (dfp_dao().get("yuan") or {}).get("kinds") or {}
    known = [name for name in DFP_DAO_YUAN_KINDS if name in library]
    extra = [name for name in library if name not in DFP_DAO_YUAN_KINDS]
    return tuple(known + extra)


def dfp_dao_ming_ge_name(number: Any) -> str:
    """本命数 → 命格名（例：`3` → 木命 · 长）。"""
    key = str(dfp_safe_int(number, 1))
    item = ((dfp_dao().get("ming") or {}).get("ge") or {}).get(key) or {}
    return str(item.get("name") or "")


def dfp_dao_shu_name(number: Any) -> str:
    """本命数 → 定数名（例：`3` → 三之数 · 生）。"""
    key = str(dfp_safe_int(number, 1))
    item = ((dfp_dao().get("ming") or {}).get("shu") or {}).get(key) or {}
    return str(item.get("name") or "")


def dfp_dao_pool_lines(pool: str, arg: str) -> Tuple[str, ...]:
    """按「池子名 + 名字」取成品话（说话队列统一走它，测试也照它断言）。

    池子名（`arg` 一律给**名字**，名字是稳定的人工键）：
      realm 境界突破 / realm_idle 境界日常 / xing 心性 / yuanshen 元神 / qixue 气血 /
      yun 运势 / ye 业 / shen 神性 / xian 仙阶 / gui 鬼气 / yao 妖气 /
      organ_weak 脏腑虚 / organ_strong 脏腑实 / karma 因果的果 / ming 命格 / shu 定数 /
      yuan 缘分档 / yuan_kind 缘谱 / note 定数注解
    """
    library = dfp_dao()
    name = str(arg or "").strip()
    key = str(pool or "").strip()
    if not library or not name:
        return ()
    if key in ("realm", "realm_idle"):
        field = "break" if key == "realm" else "idle"
        for item in library.get("dao") or ():
            if str(item.get("name") or "") == name:
                return tuple(item.get(field) or ())
        return ()
    if key in DFP_DAO_TIER_BLOCKS:
        for item in library.get(key) or ():
            if str(item.get("name") or "") == name:
                return tuple(item.get("line") or ())
        return ()
    if key == "organ_weak":
        return tuple((library.get("zangfu") or {}).get(name, {}).get("weak") or ())
    if key == "organ_strong":
        return tuple((library.get("zangfu") or {}).get(name, {}).get("strong") or ())
    if key == "karma":
        return tuple(((library.get("yinguo") or {}).get("causes") or {}).get(name, {}).get("fruit") or ())
    if key == "ming":
        return tuple(((library.get("ming") or {}).get("ge") or {}).get(name, {}).get("line") or ())
    if key == "shu":
        return tuple(((library.get("ming") or {}).get("shu") or {}).get(name, {}).get("line") or ())
    if key == "note":
        return tuple((library.get("ming") or {}).get("notes") or ())
    if key == "yuan":
        for item in ((library.get("yuan") or {}).get("tiers") or ()):
            if str(item.get("name") or "") == name:
                return tuple(item.get("line") or ())
        return ()
    if key == "yuan_kind":
        return tuple(((library.get("yuan") or {}).get("kinds") or {}).get(name) or ())
    return ()


def dfp_dao_summary() -> str:
    """一行摘要：`境界 7 段 · 档位 9 块 40 档 · 脏腑 11 个 · 因果 8 种 · 缘谱 6 类（道.json）`。"""
    library = dfp_dao()
    if not library:
        return "内置道库"
    tiers = sum(len(library.get(name) or ()) for name in DFP_DAO_TIER_BLOCKS)
    return "境界 %d 段 · 档位 %d 块 %d 档 · 脏腑 %d 个 · 因果 %d 种 · 缘谱 %d 类（%s）" % (
        len(library.get("dao") or ()),
        len(DFP_DAO_TIER_BLOCKS),
        tiers,
        len(library.get("zangfu") or {}),
        len((library.get("yinguo") or {}).get("causes") or {}),
        len((library.get("yuan") or {}).get("kinds") or {}),
        _DFP_DAO_SOURCE or DFP_DAO_FILE_NAME,
    )


# 表情：决定眼睛/嘴巴/眉毛的画法
DFPExpression = Enum(
    "DFPExpression",
    "NORMAL HAPPY SAD ANGRY SLEEPY SURPRISED LOVE DIZZY TALK",
)
DFP_EXPRESSION_LABELS: Dict[Any, str] = {
    DFPExpression.NORMAL: "平常",
    DFPExpression.HAPPY: "开心",
    DFPExpression.SAD: "难过",
    DFPExpression.ANGRY: "生气",
    DFPExpression.SLEEPY: "困倦",
    DFPExpression.SURPRISED: "惊讶",
    DFPExpression.LOVE: "心跳",
    DFPExpression.DIZZY: "晕乎乎",
    DFPExpression.TALK: "说话中",
}

# 动作：决定位移、姿态与附加特效
DFPAction = Enum(
    "DFPAction",
    "IDLE WALK JUMP WAVE DANCE SIT SLEEP DRAG EAT SPIN",
)
DFP_ACTION_LABELS: Dict[Any, str] = {
    DFPAction.IDLE: "发呆",
    DFPAction.WALK: "散步",
    DFPAction.JUMP: "蹦跳",
    DFPAction.WAVE: "挥手",
    DFPAction.DANCE: "转圈舞",
    DFPAction.SIT: "坐下",
    DFPAction.SLEEP: "睡觉",
    DFPAction.DRAG: "被拎起",
    DFPAction.EAT: "吃东西",
    DFPAction.SPIN: "旋转",
}


# =============================================================================
#  二、路径（程序目录 / 数据目录 / .env / 图标）
#     ★ 这几个缓存变量是脚手架脚本重定向写盘位置的唯一开关：
#       测试里直接给 _DFP_APP_DIR_CACHE / _DFP_DATA_DIR_CACHE 赋临时目录即可。
# =============================================================================

_DFP_FROZEN: Optional[bool] = None
_DFP_APP_DIR_CACHE: Optional[str] = None
_DFP_DATA_DIR_CACHE: Optional[str] = None
_DFP_ENV_CACHE: Optional[Dict[str, str]] = None
_DFP_ICON_CACHE: Optional[str] = None


def dfp_is_frozen() -> bool:
    """是否处于打包（Nuitka / PyInstaller）运行状态。"""
    global _DFP_FROZEN
    if _DFP_FROZEN is None:
        frozen = bool(getattr(sys, "frozen", False)) or bool(globals().get("__compiled__", False))
        _DFP_FROZEN = frozen
    return _DFP_FROZEN


def dfp_app_dir() -> str:
    """程序所在目录（打包后为 exe 目录）。"""
    global _DFP_APP_DIR_CACHE
    if _DFP_APP_DIR_CACHE is None:
        try:
            if dfp_is_frozen():
                base = os.path.dirname(os.path.abspath(sys.executable))
            else:
                base = os.path.dirname(os.path.abspath(__file__))
        except Exception:
            base = os.getcwd()
        _DFP_APP_DIR_CACHE = base
    return _DFP_APP_DIR_CACHE


def dfp_probe_writable(path: str) -> bool:
    """测试目录是否可写（不存在会尝试创建）。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".dfp_write_probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def dfp_data_dir() -> str:
    """运行时数据目录；程序目录不可写时回退到 %APPDATA%。"""
    global _DFP_DATA_DIR_CACHE
    if _DFP_DATA_DIR_CACHE is None:
        base = os.path.join(dfp_app_dir(), "pet_data")
        if not dfp_probe_writable(base):
            home = os.environ.get("APPDATA") or os.path.expanduser("~")
            base = os.path.join(home, DFP_APP_NAME, "pet_data")
            try:
                os.makedirs(base, exist_ok=True)
            except Exception:
                base = os.path.join(os.getcwd(), "pet_data")
        _DFP_DATA_DIR_CACHE = base
    return _DFP_DATA_DIR_CACHE


def dfp_ensure_dir(path: str) -> str:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def dfp_env_path() -> str:
    """.env 路径（固定在程序目录，便于分发时替换）。"""
    return os.path.join(dfp_app_dir(), ".env")


def dfp_settings_path() -> str:
    """pet_settings.json —— 所有设置项的持久化文件。"""
    return os.path.join(dfp_data_dir(), "pet_settings.json")


def dfp_database_path() -> str:
    """pet_data.db —— SQLite（WAL）数据库。"""
    return os.path.join(dfp_data_dir(), "pet_data.db")


def dfp_log_dir() -> str:
    """logs 目录（按天分文件 + 2MB 轮转，保留 5 份）。"""
    return os.path.join(dfp_data_dir(), "logs")


def dfp_backup_dir() -> str:
    """backups 目录（数据库备份）。"""
    return os.path.join(dfp_data_dir(), "backups")


def dfp_export_dir() -> str:
    """exports 目录（对话导出）。"""
    return os.path.join(dfp_data_dir(), "exports")


def dfp_icon_path() -> Optional[str]:
    """icon.ico 路径；存在才返回。也兼容 pet_data/icon.ico。"""
    global _DFP_ICON_CACHE
    if _DFP_ICON_CACHE is None:
        candidates = (
            os.path.join(dfp_app_dir(), "icon.ico"),
            os.path.join(dfp_data_dir(), "icon.ico"),
        )
        found = None
        for path in candidates:
            try:
                if os.path.isfile(path) and os.path.getsize(path) > 0:
                    found = path
                    break
            except Exception:
                continue
        _DFP_ICON_CACHE = found
    return _DFP_ICON_CACHE


# =============================================================================
#  三、通用小工具（数学 / 时间 / 文本 / 原子写盘）
# =============================================================================


def dfp_clamp(value: float, low: float, high: float) -> float:
    try:
        value = float(value)
    except Exception:
        value = low
    if low > high:
        low, high = high, low
    return low if value < low else (high if value > high else value)


def dfp_lerp(start: float, end: float, t: float) -> float:
    return start + (end - start) * dfp_clamp(t, 0.0, 1.0)


def dfp_ease_in_out(t: float) -> float:
    t = dfp_clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def dfp_ease_out(t: float) -> float:
    t = dfp_clamp(t, 0.0, 1.0)
    return 1.0 - (1.0 - t) * (1.0 - t)


def dfp_now_ts() -> float:
    return time.time()


def dfp_fmt_ts(ts: Optional[float] = None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    if ts is None:
        ts = dfp_now_ts()
    try:
        return datetime.fromtimestamp(float(ts)).strftime(fmt)
    except Exception:
        return "-"


def dfp_human_duration(seconds: float) -> str:
    """把秒数写成「1天2小时3分」这类中文描述。"""
    try:
        seconds = int(max(0, float(seconds)))
    except Exception:
        return "0 秒"
    if seconds < 60:
        return "%d 秒" % seconds
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts: List[str] = []
    if days:
        parts.append("%d 天" % days)
    if hours:
        parts.append("%d 小时" % hours)
    if minutes:
        parts.append("%d 分" % minutes)
    if not parts:
        parts.append("%d 秒" % secs)
    return "".join(parts)


def dfp_human_bytes(size: float) -> str:
    try:
        size = float(size)
    except Exception:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    idx = 0
    while size >= 1024.0 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        return "%d %s" % (int(size), units[idx])
    return "%.2f %s" % (size, units[idx])


def dfp_human_number(value: float) -> str:
    try:
        value = float(value)
    except Exception:
        return "0"
    if abs(value) >= 100000000:
        return "%.2f 亿" % (value / 100000000.0)
    if abs(value) >= 10000:
        return "%.2f 万" % (value / 10000.0)
    if abs(value - int(value)) < 1e-9:
        return "%d" % int(value)
    return "%.2f" % value


def dfp_today_key(ts: Optional[float] = None) -> str:
    return dfp_fmt_ts(ts, "%Y-%m-%d")


def dfp_start_of_day_ts(ts: Optional[float] = None) -> float:
    """今天 0 点的时间戳（看「今天发生过什么」用；`dfp_events.created_at` 存的是时间戳）。"""
    try:
        moment = datetime.fromtimestamp(dfp_now_ts() if ts is None else dfp_safe_float(ts, dfp_now_ts()))
        return moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    except Exception:
        return dfp_now_ts() - 86400.0


def dfp_local_hour(ts: Optional[float] = None) -> int:
    """本地时间的小时（0~23）。★ v1.1.27：灵魂判「深夜」用（她半夜更容易攒下「有点怕」这类心事）。"""
    try:
        moment = datetime.fromtimestamp(dfp_now_ts() if ts is None else dfp_safe_float(ts, dfp_now_ts()))
        return int(moment.hour)
    except Exception:
        return 12


def dfp_atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> bool:
    """原子写文本（同目录临时文件 + os.replace），避免半截文件。"""
    try:
        directory = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = os.path.join(directory, ".%s.tmp" % os.path.basename(path))
        with open(tmp, "w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except Exception:
                pass
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def dfp_atomic_write_json(path: str, payload: Any) -> bool:
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        return False
    return dfp_atomic_write_text(path, text)


def dfp_read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def dfp_deep_copy(payload: Any) -> Any:
    try:
        return json.loads(json.dumps(payload, ensure_ascii=False))
    except Exception:
        try:
            return dict(payload)
        except Exception:
            return payload


def dfp_enum_value(default: Any, *getters: Callable[[], Any]) -> Any:
    """逐个独立求解枚举常量。

    ★ 铁律：绝不可把多个常量塞进同一个 try —— 一旦其中某个写法在新版本 Qt
      被移除，except 会把前面已经取到值的常量一起覆盖成兜底值，产生 None。
    """
    for getter in getters:
        try:
            value = getter()
        except Exception:
            continue
        if value is not None:
            return value
    return default


def dfp_desktop_font(size: int = 10, bold: bool = False) -> QFont:
    """取一个可用的中文界面字体（Windows 优先微软雅黑）。"""
    families = QFontDatabase.families() if hasattr(QFontDatabase, "families") else []
    available = {str(name).lower() for name in families}
    for candidate in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "PingFang SC", "Noto Sans CJK SC", "SimHei"):
        if candidate.lower() in available:
            font = QFont(candidate, size)
            font.setBold(bold)
            return font
    font = QFont()
    font.setPointSize(size)
    font.setBold(bold)
    return font


def dfp_new_id() -> str:
    return uuid.uuid4().hex


def dfp_random_pick(items: Sequence[Any], fallback: Any = None) -> Any:
    try:
        pool = list(items)
    except Exception:
        return fallback
    if not pool:
        return fallback
    return random.choice(pool)


def dfp_safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def dfp_safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def dfp_truncate_text(text: str, limit: int = 120) -> str:
    text = "" if text is None else str(text)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def dfp_duration_text_to_seconds(text: str, default: float = 0.0) -> float:
    """把「1天2小时」「30 分」「45 秒」这类文字转成秒数（供聊天里的自然语言指令用）。"""
    if not text:
        return default
    total = 0.0
    matched = False
    pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(天|日|小时|时|分钟|分|秒)")
    for number, unit in pattern.findall(str(text)):
        matched = True
        value = dfp_safe_float(number, 0.0)
        if unit in ("天", "日"):
            total += value * 86400.0
        elif unit in ("小时", "时"):
            total += value * 3600.0
        elif unit in ("分钟", "分"):
            total += value * 60.0
        else:
            total += value
    return total if matched else default


# =============================================================================
#  四、颜色工具
# =============================================================================


def dfp_color(text: Any, alpha: int = 255) -> QColor:
    """把 '#RRGGBB' / 'rgba(...)' / QColor 统一成 QColor（失败回退黑色）。"""
    color = QColor()
    if isinstance(text, QColor):
        color = QColor(text)
    else:
        try:
            color = QColor(str(text))
        except Exception:
            color = QColor("#000000")
    if not color.isValid():
        color = QColor("#000000")
    if alpha < 255:
        color.setAlpha(int(dfp_clamp(alpha, 0, 255)))
    return color


def dfp_color_mix(first: Any, second: Any, t: float) -> QColor:
    a = dfp_color(first)
    b = dfp_color(second)
    t = dfp_clamp(t, 0.0, 1.0)
    return QColor(
        int(dfp_lerp(a.red(), b.red(), t)),
        int(dfp_lerp(a.green(), b.green(), t)),
        int(dfp_lerp(a.blue(), b.blue(), t)),
        int(dfp_lerp(a.alpha(), b.alpha(), t)),
    )


def dfp_color_shade(color: Any, factor: float) -> QColor:
    """factor > 1 提亮，< 1 压暗。"""
    base = dfp_color(color)
    r = dfp_clamp(base.red() * factor, 0, 255)
    g = dfp_clamp(base.green() * factor, 0, 255)
    b = dfp_clamp(base.blue() * factor, 0, 255)
    return QColor(int(r), int(g), int(b), base.alpha())


def dfp_color_alpha(color: Any, alpha: int) -> QColor:
    result = dfp_color(color)
    result.setAlpha(int(dfp_clamp(alpha, 0, 255)))
    return result


# =============================================================================
#  五、.env 读取（API Key / 数据库口令 / 接口地址都在这里，绝不写进代码）
# =============================================================================

_DFP_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def dfp_parse_env_text(text: str) -> Dict[str, str]:
    """解析 .env 文本：支持 export 前缀、单双引号、行尾 # 注释。"""
    result: Dict[str, str] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _DFP_ENV_LINE_RE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            hash_at = value.find(" #")
            if hash_at >= 0 and not value.startswith("#"):
                value = value[:hash_at]
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
        result[key] = value
    return result


def dfp_load_env(force: bool = False) -> Dict[str, str]:
    """读取 .env 并缓存；同时把值塞进 os.environ（不覆盖已存在的系统环境变量）。"""
    global _DFP_ENV_CACHE
    if _DFP_ENV_CACHE is not None and not force:
        return _DFP_ENV_CACHE
    data: Dict[str, str] = {}
    path = dfp_env_path()
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = dfp_parse_env_text(fh.read())
    except Exception:
        data = {}
    for key, value in data.items():
        if key and key not in os.environ:
            os.environ[key] = value
    _DFP_ENV_CACHE = data
    return data


def dfp_env_str(key: str, default: str = "") -> str:
    data = dfp_load_env()
    value = data.get(key)
    if value is None or value == "":
        value = os.environ.get(key, "")
    return str(value) if value else default


def dfp_env_int(key: str, default: int, low: Optional[int] = None, high: Optional[int] = None) -> int:
    value = dfp_safe_int(dfp_env_str(key, ""), default)
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def dfp_env_float(key: str, default: float, low: Optional[float] = None, high: Optional[float] = None) -> float:
    value = dfp_safe_float(dfp_env_str(key, ""), default)
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def dfp_env_bool(key: str, default: bool = False) -> bool:
    raw = dfp_env_str(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on", "是", "开")


def dfp_env_list(key: str) -> List[str]:
    raw = dfp_env_str(key, "")
    if not raw:
        return []
    return [item.strip() for item in re.split(r"[,;\s]+", raw) if item.strip()]


def dfp_collect_api_keys() -> List[str]:
    """收集 .env 中的 DeepSeek API Key（支持逗号分隔与 DEEPSEEK_API_KEY_1..N）。"""
    keys: List[str] = []

    def _push(value: str) -> None:
        for item in re.split(r"[,;\s]+", str(value or "")):
            item = item.strip().strip("'\"")
            if item and item not in keys:
                keys.append(item)

    _push(dfp_env_str("DEEPSEEK_API_KEYS", ""))
    _push(dfp_env_str("DEEPSEEK_API_KEY", ""))
    for index in range(1, 21):
        _push(dfp_env_str("DEEPSEEK_API_KEY_%d" % index, ""))
    return [key for key in keys if len(key) >= 8]


# ★ v1.1.9：给「设置窗口里直接填 API Key」用 —— 写回 `.env`
DFP_ENV_API_KEY_NOTE = "由设置窗口的「API Key」输入框维护（旧的单 Key / Key_1..N 已停用，想恢复就把行首的 # 去掉）"


def dfp_mask_secret(text: str, head: int = 6, tail: int = 4) -> str:
    """把 Key / 口令这类敏感串打码（只给界面看；短的只留头尾）。"""
    value = str(text or "")
    if not value:
        return "（未配置）"
    if len(value) <= head + tail:
        return value[:2] + "*" * max(1, len(value) - 3) + value[-1:]
    return "%s%s%s" % (value[:head], "*" * 8, value[-tail:])


def dfp_looks_like_api_key(key: str) -> bool:
    """粗判「像不像一个 API Key」——**只用来给界面提示，不阻止保存**。"""
    text = str(key or "").strip()
    if len(text) < 16 or len(text) > 200:
        return False
    if not re.match(r"^[A-Za-z0-9._\-]+$", text):
        return False
    return text.lower().startswith("sk-")


def dfp_env_write_values(
    updates: Dict[str, str],
    path: str = "",
    deactivate: Sequence[str] = (),
    note: str = "",
) -> Tuple[bool, str]:
    """把若干键写回 `.env`：**只动这几行**，其它内容、注释与换行风格原样保留。

    - `updates`：键 → 新值。已有这一行就更新它的值（保留 `export ` 前缀与行内 `# 注释`），没有就追加到末尾。
    - `deactivate`：这些键的行会被**整行注释掉**（`# ` 开头），原值不删 —— 方便随时恢复。
    - 写盘前先把原文件复制一份成 `.env.bak`（只留一份），写完清掉 env 缓存。
    - 返回 `(是否成功, 说明文字)`；任何异常都只是返回失败，不往外抛。
    """
    target = str(path or "").strip() or dfp_env_path()
    raw = b""
    try:
        if os.path.isfile(target):
            with open(target, "rb") as fh:
                raw = fh.read()
    except Exception as exc:
        return False, "读取失败：%s" % exc
    had_bom = raw[:3] == b"\xef\xbb\xbf"
    try:
        text = raw.decode("utf-8-sig") if raw else ""
    except Exception:
        text = raw.decode("utf-8", "replace")
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()

    wanted = {str(key): str(value) for key, value in (updates or {}).items()}
    drop = {str(key) for key in (deactivate or ())}
    touched: Dict[str, bool] = {key: False for key in wanted}

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue  # 注释与空行一律不碰
        match = _DFP_ENV_LINE_RE.match(stripped)
        if not match:
            continue
        key = match.group(1)
        if key in drop:
            lines[index] = "# " + line
            continue
        if key in wanted:
            prefix = stripped[: match.start(1)]
            rhs = match.group(2)
            comment_at = rhs.find(" #")
            tail = rhs[comment_at:] if comment_at >= 0 else ""
            lines[index] = "%s%s=%s%s" % (prefix, key, wanted[key], tail)
            touched[key] = True

    if not raw and note:
        lines.append("# %s" % note)
    for key, value in wanted.items():
        if touched.get(key):
            continue
        if note and not any(key in line for line in lines):
            lines.append("# %s" % note)
        lines.append("%s=%s" % (key, value))

    try:
        if raw:
            with open(target + ".bak", "wb") as fh:
                fh.write(raw)
    except Exception:
        pass  # 备份失败不算致命，继续写

    body = eol.join(lines) + eol
    try:
        directory = os.path.dirname(os.path.abspath(target))
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "wb") as fh:
            if had_bom:
                fh.write(b"\xef\xbb\xbf")
            fh.write(body.encode("utf-8"))
        os.replace(tmp, target)
    except Exception as exc:
        return False, "写入失败：%s" % exc

    # 让下一次读取拿到新值
    # ★ 必须同时把 os.environ 里那几份「上一轮读进来的旧值」清掉：
    #   `dfp_env_str()` 在 .env 里取不到值时会回退到 os.environ，不清理的话
    #   「把旧 Key 注释停用」会失效 —— 旧 Key 仍会被算进轮换列表（v1.1.9 踩到）。
    for key in list(wanted.keys()) + sorted(drop):
        os.environ.pop(key, None)
    global _DFP_ENV_CACHE
    _DFP_ENV_CACHE = None
    dfp_load_env(force=True)
    return True, "已写入 %d 项（备份：%s）" % (len(wanted), os.path.basename(target) + ".bak")


def dfp_env_save_api_keys(keys: Sequence[str], path: str = "") -> Tuple[bool, str]:
    """把 API Key 列表写进 `.env`。

    ★ 统一写进 `DEEPSEEK_API_KEYS`（逗号分隔），并把 `DEEPSEEK_API_KEY` / `DEEPSEEK_API_KEY_1..20`
      **整行注释停用**（原值不删）—— 这样「输入框里写什么就用什么」，不会和旧键混着轮换。
    """
    cleaned: List[str] = []
    for item in keys or ():
        text = str(item or "").strip().strip("'\"")
        if text and text not in cleaned:
            cleaned.append(text)
    deactivate = ("DEEPSEEK_API_KEY",) + tuple("DEEPSEEK_API_KEY_%d" % index for index in range(1, 21))
    return dfp_env_write_values(
        {"DEEPSEEK_API_KEYS": ",".join(cleaned)},
        path,
        deactivate=deactivate,
        note=DFP_ENV_API_KEY_NOTE,
    )


# =============================================================================
#  六、敏感字段加密（数据库里的对话内容、口令都在 .env）
# =============================================================================

DFP_PBKDF2_ITERATIONS = 120000
DFP_SECRET_SALT_LEN = 16
DFP_SECRET_TAG_LEN = 32


def dfp_secret_password() -> str:
    """数据库敏感字段的加密口令：.env 的 DB_PASSWORD / PET_SECRET。"""
    for key in ("DB_PASSWORD", "PET_SECRET", "DEEPSEEK_DB_PASSWORD"):
        value = dfp_env_str(key, "")
        if value:
            return value
    return DFP_DEFAULT_SECRET_PASSWORD


def dfp_uses_default_secret() -> bool:
    return dfp_secret_password() == DFP_DEFAULT_SECRET_PASSWORD


def dfp_secret_keystream(password: str, salt: bytes, length: int) -> bytes:
    """用 PBKDF2 派生密钥再用 SHA-256 计数器模式扩展成流密钥。"""
    if length <= 0:
        return b""
    base = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, DFP_PBKDF2_ITERATIONS, dklen=32)
    chunks: List[bytes] = []
    produced = 0
    counter = 0
    while produced < length:
        chunks.append(hashlib.sha256(base + counter.to_bytes(8, "big")).digest())
        produced += 32
        counter += 1
    return b"".join(chunks)[:length]


def dfp_secret_encrypt(password: str, plaintext: str) -> str:
    """加密：返回 base64(salt|tag|密文)；空内容或失败返回空串。"""
    if plaintext is None or plaintext == "":
        return ""
    try:
        data = str(plaintext).encode("utf-8")
        salt = os.urandom(DFP_SECRET_SALT_LEN)
        stream = dfp_secret_keystream(password, salt, len(data))
        cipher = bytes(a ^ b for a, b in zip(data, stream))
        base = hashlib.pbkdf2_hmac(
            "sha256", str(password).encode("utf-8"), salt, DFP_PBKDF2_ITERATIONS, dklen=32
        )
        tag = hmac.new(base, b"dfp1" + salt + cipher, hashlib.sha256).digest()
        return base64.b64encode(salt + tag + cipher).decode("ascii")
    except Exception:
        return ""


def dfp_secret_decrypt(password: str, blob: str) -> Optional[str]:
    """解密 dfp_secret_encrypt 的产物；失败返回 None。"""
    if not blob:
        return ""
    try:
        raw = base64.b64decode(str(blob).encode("ascii"))
        if len(raw) < DFP_SECRET_SALT_LEN + DFP_SECRET_TAG_LEN:
            return None
        salt = raw[:DFP_SECRET_SALT_LEN]
        tag = raw[DFP_SECRET_SALT_LEN : DFP_SECRET_SALT_LEN + DFP_SECRET_TAG_LEN]
        cipher = raw[DFP_SECRET_SALT_LEN + DFP_SECRET_TAG_LEN :]
        base = hashlib.pbkdf2_hmac(
            "sha256", str(password).encode("utf-8"), salt, DFP_PBKDF2_ITERATIONS, dklen=32
        )
        expect = hmac.new(base, b"dfp1" + salt + cipher, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expect):
            return None
        stream = dfp_secret_keystream(password, salt, len(cipher))
        return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")
    except Exception:
        return None


# =============================================================================
#  七、窗口几何工具（★ 铁律：摆放窗口一律 resize + move）
#     为什么不用 setGeometry：Windows 上它摆的是「客户区」坐标，而 x()/y() 是
#     「含标题栏的边框」坐标，恒定差一个标题栏高度 —— 每启动一次窗口就上飘。
# =============================================================================


def dfp_screen_available_rects() -> List[QRect]:
    rects: List[QRect] = []
    app = QApplication.instance()
    if app is not None:
        try:
            for screen in app.screens():
                rects.append(QRect(screen.availableGeometry()))
        except Exception:
            rects = []
    if not rects:
        try:
            for screen in QApplication.screens():
                rects.append(QRect(screen.availableGeometry()))
        except Exception:
            rects = []
    if not rects:
        rects = [QRect(0, 0, 1920, 1040)]
    return rects


def dfp_geometry_from_text(text: Any, fallback: Optional[QRect] = None) -> QRect:
    if isinstance(text, QRect):
        return QRect(text)
    default = QRect(fallback) if isinstance(fallback, QRect) else QRect(0, 0, 640, 480)
    if not text:
        return default
    try:
        parts = [int(float(piece)) for piece in re.split(r"[,xX\s]+", str(text).strip()) if piece != ""]
    except Exception:
        return default
    if len(parts) < 4:
        return default
    width = parts[2] if parts[2] > 0 else default.width()
    height = parts[3] if parts[3] > 0 else default.height()
    return QRect(parts[0], parts[1], width, height)


def dfp_geometry_to_text(rect: Any) -> str:
    if not rect:
        return ""
    try:
        return "%d,%d,%d,%d" % (int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()))
    except Exception:
        return ""


def dfp_geometry_visible_on_screen(rect: QRect, min_width: int = 96, min_height: int = 40) -> bool:
    """窗口是否「够得着」：上边缘不得超出屏顶，且与某块屏幕有足够重叠。"""
    if not isinstance(rect, QRect) or rect.width() <= 0 or rect.height() <= 0:
        return False
    for screen in dfp_screen_available_rects():
        if rect.top() < screen.top():
            continue
        overlap_w = min(rect.right(), screen.right()) - max(rect.left(), screen.left())
        overlap_h = min(rect.bottom(), screen.bottom()) - max(rect.top(), screen.top())
        if overlap_w >= min(int(min_width), rect.width()) and overlap_h >= min(int(min_height), rect.height()):
            return True
    return False


def dfp_safe_window_geometry(
    saved: Any,
    default_geometry: QRect,
    min_width: int = 96,
    min_height: int = 40,
    always_center: bool = False,
) -> QRect:
    """把保存的几何信息校正为一个「一定看得见」的几何。

    ★ 注意：saved 为空表示「从没保存过位置」（首次启动），这时必须居中，
      而不是拿默认矩形 QRect(0,0,w,h) 当坐标用 —— 否则桌宠会落在屏幕左上角。
    """
    default = QRect(default_geometry)
    if isinstance(saved, QRect):
        has_saved = saved.isValid() and saved.width() > 0
    else:
        has_saved = bool(str(saved or "").strip())
    rect = dfp_geometry_from_text(saved, default)
    screens = dfp_screen_available_rects()
    screen = screens[0] if screens else QRect(0, 0, 1920, 1040)
    if rect.width() <= 0 or rect.height() <= 0:
        rect = QRect(default)
    if rect.width() > screen.width():
        rect.setWidth(screen.width())
    if rect.height() > screen.height():
        rect.setHeight(screen.height())
    if always_center or not has_saved or not dfp_geometry_visible_on_screen(rect, min_width, min_height):
        rect = QRect(default)
        if screens:
            # 优先放在主屏正中
            screen = screens[0]
            rect.moveCenter(screen.center())
            if rect.top() < screen.top():
                rect.moveTop(screen.top())
            if rect.left() < screen.left():
                rect.moveLeft(screen.left())
    else:
        # 整窗夹取：只要还能保证最小可见量，就把超出屏幕的部分拉回来
        for area in screens:
            if rect.center().x() < area.left() or rect.center().x() > area.right():
                continue
            if rect.top() < area.top():
                rect.moveTop(area.top())
            if rect.left() < area.left():
                rect.moveLeft(area.left())
            if rect.right() > area.right():
                rect.moveRight(area.right())
            if rect.bottom() > area.bottom():
                rect.moveBottom(area.bottom())
            break
    return rect


def dfp_clamp_point_into_screen(point: QPoint, size: QSize) -> QPoint:
    """把一个「窗口左上角坐标」夹回屏幕内（拖动时用）。

    ★ 为什么要它：拖动时只靠 mouseMoveEvent 的 move() 是没有任何约束的，
      把桌宠拖到屏幕上方就再也抓不回来了（y 为负数、窗口整个看不见也点不到）。
      这里按「窗口中心离哪块屏最近」选屏，再把四条边都夹回屏内。
    """
    if not isinstance(point, QPoint):
        return QPoint(point)
    width = max(1, int(size.width())) if isinstance(size, QSize) else 1
    height = max(1, int(size.height())) if isinstance(size, QSize) else 1
    screens = dfp_screen_available_rects()
    if not screens:
        return QPoint(point)
    area = None
    best = None
    for candidate in screens:
        rect = QRect(int(point.x()), int(point.y()), width, height)
        if candidate.contains(rect.center()):
            area = candidate
            break
        dx = max(candidate.left() - rect.center().x(), 0, rect.center().x() - candidate.right())
        dy = max(candidate.top() - rect.center().y(), 0, rect.center().y() - candidate.bottom())
        distance = dx * dx + dy * dy
        if best is None or distance < best:
            best = distance
            area = candidate
    if area is None:
        return QPoint(point)
    x = int(point.x())
    y = int(point.y())
    x = min(max(x, area.left()), max(area.left(), area.right() - width + 1))
    y = min(max(y, area.top()), max(area.top(), area.bottom() - height + 1))
    return QPoint(x, y)


def dfp_place_window(window: QWidget, geometry: QRect) -> None:
    """★ 摆放窗口：必须用 resize + move（move 与 x()/y() 同坐标系，零漂移）。"""
    if window is None or not isinstance(geometry, QRect):
        return
    try:
        window.resize(max(1, int(geometry.width())), max(1, int(geometry.height())))
        window.move(int(geometry.x()), int(geometry.y()))
    except Exception:
        pass


def dfp_window_geometry_for_save(window: QWidget, default_geometry: QRect) -> QRect:
    """保存窗口几何：最大化时存 normalGeometry，且做一次安全校正。"""
    if window is None:
        return QRect(default_geometry)
    rect = QRect()
    try:
        if window.isMaximized() or window.isFullScreen():
            rect = QRect(window.normalGeometry())
        else:
            rect = QRect(window.x(), window.y(), window.width(), window.height())
    except Exception:
        rect = QRect(default_geometry)
    if rect.width() <= 0 or rect.height() <= 0:
        rect = QRect(default_geometry)
    return dfp_safe_window_geometry(rect, default_geometry)


def dfp_ensure_window_on_screen(window: QWidget, force_center: bool = False) -> None:
    """show() 之后按真实 frameGeometry 兜底，避免窗口跑到屏幕外。

    ★ 按「窗口中心所在的那块屏」夹取（以前固定用 screens[0]，多屏时会夹错屏）。
      完全够不着时兜底居中，保证一定抓得回来。
    """
    if window is None:
        return
    try:
        if window.isMaximized() or window.isFullScreen():
            return
        screens = dfp_screen_available_rects()
        if not screens:
            return
        frame = QRect(window.frameGeometry())
        if force_center:
            target = QRect(frame)
            target.moveCenter(screens[0].center())
            target.moveTop(max(screens[0].top(), target.top()))
            target.moveLeft(max(screens[0].left(), target.left()))
            dfp_place_window(window, target)
            return
        area = None
        for candidate in screens:
            if candidate.contains(frame.center()):
                area = candidate
                break
        if area is None:
            # 窗口中心不在任何屏幕上：只要还能看到一点就按最近屏夹取，否则居中救回来
            if dfp_geometry_visible_on_screen(frame):
                for candidate in screens:
                    if candidate.intersects(frame):
                        area = candidate
                        break
            if area is None:
                target = QRect(frame)
                target.moveCenter(screens[0].center())
                dfp_place_window(window, target)
                return
        moved = False
        if frame.top() < area.top():
            frame.moveTop(area.top())
            moved = True
        if frame.left() < area.left():
            frame.moveLeft(area.left())
            moved = True
        if frame.right() > area.right():
            frame.moveRight(area.right())
            moved = True
        if frame.bottom() > area.bottom():
            frame.moveBottom(area.bottom())
            moved = True
        if moved:
            dfp_place_window(window, frame)
    except Exception:
        pass


# =============================================================================
#  八、日志（按天分文件 + 2MB 轮转，保留 5 份；界面里也能看到最近记录）
# =============================================================================

DFP_LOG_LEVELS: Dict[str, int] = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}


class DFPLogger(QObject):
    """轻量日志器：内存保留最近记录（供设置窗口「关于」页显示）+ 可选写文件。"""

    dfpLineLogged = Signal(str, str)  # level, line

    def __init__(self, log_dir: str, level: str = "INFO", to_file: bool = True, max_records: int = 400, parent=None):
        super().__init__(parent)
        self._dfp_log_dir = log_dir
        self._dfp_level = DFP_LOG_LEVELS.get(str(level).upper(), 20)
        self._dfp_to_file = bool(to_file)
        self._dfp_records: List[Tuple[float, str, str]] = []
        self._dfp_max_records = max(50, int(max_records))
        self._dfp_lock = threading.RLock()
        self._dfp_file_path: Optional[str] = None
        dfp_ensure_dir(log_dir)

    # --- 配置 ---
    def dfp_set_level(self, level: str) -> None:
        self._dfp_level = DFP_LOG_LEVELS.get(str(level).upper(), 20)

    def dfp_level_name(self) -> str:
        for name, value in DFP_LOG_LEVELS.items():
            if value == self._dfp_level:
                return name
        return "INFO"

    def dfp_set_to_file(self, enabled: bool) -> None:
        self._dfp_to_file = bool(enabled)

    def dfp_set_log_dir(self, path: str) -> None:
        with self._dfp_lock:
            self._dfp_log_dir = path
            self._dfp_file_path = None
        dfp_ensure_dir(path)

    def dfp_log_file_path(self) -> str:
        if self._dfp_file_path is None:
            self._dfp_file_path = os.path.join(self._dfp_log_dir, "pet_%s.log" % dfp_today_key())
        return self._dfp_file_path

    # --- 记录 ---
    def dfp_log(self, level: str, message: str) -> None:
        level_name = str(level).upper()
        priority = DFP_LOG_LEVELS.get(level_name, 20)
        line = "[%s] [%s] %s" % (dfp_fmt_ts(), level_name, message)
        with self._dfp_lock:
            self._dfp_records.append((dfp_now_ts(), level_name, str(message)))
            if len(self._dfp_records) > self._dfp_max_records:
                del self._dfp_records[: len(self._dfp_records) - self._dfp_max_records]
            should_write = priority >= self._dfp_level and self._dfp_to_file
        if priority >= self._dfp_level:
            try:
                self.dfpLineLogged.emit(level_name, line)
            except Exception:
                pass
        if should_write:
            self._write_line(line)

    def _write_line(self, line: str) -> None:
        try:
            path = self.dfp_log_file_path()
            dfp_ensure_dir(os.path.dirname(path))
            if not os.path.exists(path) and dfp_is_frozen():
                pass
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            try:
                if os.path.getsize(path) > DFP_LOG_MAX_BYTES:
                    self._rotate(path)
            except Exception:
                pass
        except Exception:
            pass

    def _rotate(self, path: str) -> None:
        try:
            stamp = dfp_fmt_ts(fmt="%H%M%S")
            base, ext = os.path.splitext(path)
            os.replace(path, "%s_%s%s" % (base, stamp, ext))
            files = self.dfp_log_files()
            for old in files[DFP_LOG_KEEP_FILES:]:
                try:
                    os.remove(old)
                except Exception:
                    continue
        except Exception:
            pass

    def dfp_debug(self, message: str) -> None:
        self.dfp_log("DEBUG", message)

    def dfp_info(self, message: str) -> None:
        self.dfp_log("INFO", message)

    def dfp_warning(self, message: str) -> None:
        self.dfp_log("WARNING", message)

    def dfp_error(self, message: str) -> None:
        self.dfp_log("ERROR", message)

    def dfp_exception(self, message: str) -> None:
        self.dfp_log("ERROR", "%s\n%s" % (message, traceback.format_exc()))

    def dfp_recent(self, limit: int = 100) -> List[Tuple[float, str, str]]:
        with self._dfp_lock:
            items = list(self._dfp_records)
        return items[-max(1, int(limit)) :]

    def dfp_log_files(self) -> List[str]:
        try:
            directory = self._dfp_log_dir
            if not os.path.isdir(directory):
                return []
            files = [
                os.path.join(directory, name)
                for name in os.listdir(directory)
                if name.lower().endswith(".log")
            ]
        except Exception:
            return []
        files.sort(key=lambda item: os.path.getmtime(item) if os.path.exists(item) else 0, reverse=True)
        return files


# =============================================================================
#  九、默认设置项（★ 新增设置项必须先在这里加默认值）
# =============================================================================

DFP_DEFAULT_SETTINGS: Dict[str, Any] = {
    # --- 版本与首启 ---
    "settings_version": DFP_SETTINGS_VERSION,
    "first_run_done": False,
    # --- 外观 ---
    "pet_scale": 1.0,
    "pet_opacity": 1.0,
    "pet_always_on_top": True,
    "pet_flip": False,
    "pet_show_shadow": True,
    "pet_show_spout": True,
    "pet_palette": "ocean",
    "pet_nickname": DFP_DEFAULT_NICKNAME,
    "pet_show_nametag": False,
    "pet_anim_fps": 30,
    "bubble_enabled": True,
    "bubble_duration_ms": 6000,
    "bubble_max_width": 320,
    "bubble_font_size": 10,
    # --- 形象素材（大肥鱼素材表）---
    "pet_render_mode": "assets",  # assets=只用素材 / auto=素材优先缺失兑底 / vector=代码绘制
    "asset_root": "",  # 空 = 程序目录下的「大肥鱼素材表」
    "asset_auto_scan": True,  # 清单缺失时自动扫描重建
    "asset_anim_zoom": 0.0,  # 0 = 自动（16:9 动作放大到与立绘同尺寸）
    "voice_enabled": True,
    "voice_volume": 0.8,
    "voice_on_poke": True,
    "voice_on_confirm": True,
    "voice_on_done": True,
    "voice_on_ask": True,
    # ★ 语音台词（人工听写在 `素材清单.json` 的 voices[].text）：开了就把台词当字幕显示在气泡里。
    #   默认关，因为它会盖掉已有的离线台词（两者不是同一句话）。
    "voice_show_text": False,
    "voice_on_idle": True,  # ★ v1.1.14：自言自语时也出声（用素材包里的 idle 语音）
    "voice_on_notice": True,  # ★ v1.1.14：任务完成/失败、余额变少、需要确认等提示音
    "voice_on_startup": True,  # ★ v1.1.22：程序启动时念一句启动台词（`voice_startup_*` 那条音频）
    # ★ v1.1.33：情绪 / 场景类语音 —— 录音清单第六节那几个「建议新增类别」已经接好线了
    "voice_on_act_cute": True,  # 撒娇（心情好+关系亲时自己撒娇；菜单「💗 撒娇一下」也走它）
    "voice_on_hungry": True,  # 饿了（饱食度过低）
    "voice_on_tired": True,  # 累了 / 困了（精力过低）
    "voice_on_love": True,  # 想你 / 亲昵（被晾久或关系亲近；`lonely` 与 `love` 共用这一类）
    "voice_on_greet": True,  # 打招呼 / 重新出现（Ctrl+H 唤回来、按时段问候）
    "voice_on_bye": True,  # 告别 / 被藏起来（Ctrl+H 藏起来之前）
    "voice_on_sad": True,  # 难过 / 出错
    "voice_use_pack": False,  # ★ v1.1.24：素材包（素材4/素材6）自带的声音默认退役（line-*/v_*/notice-* 一律不播）
    # ★ v1.1.36：状态形象（睡觉 / 忙 / 等 / 休息 / 庆祝 / 出错）—— 用的是新形象集里的图
    #   （v1.1.15 那套「素材5 的非人形小鲸鱼 SVG」已退役，见 `DFP_FORM_FACE_MAP`）
    "whale_form_enabled": True,  # 总开关：关掉就始终用设置里选的基础形象
    "whale_form_sleep": True,  # 睡觉时切成「蜷成一团」那张（其余状态是一次性的，随总开关走）
    # ★ v1.1.18：国标麻将算番 Web 版（手机 / 平板浏览器算番；算完桌宠把总番数读出来）
    "mahjong_web_enabled": True,  # 启动时就把算番 Web 版开起来
    "mahjong_web_lan": True,  # True = 监听 0.0.0.0（同一 WiFi 的手机 / 平板能打开）
    "mahjong_web_port": DFP_MAHJONG_DEFAULT_PORT,  # 端口（被占用会自动往后找一个）
    "mahjong_read_fan": True,  # 算完后用 `数字\*.mp3` 把总番数读出来
    # --- 窗口几何 ---
    "window_geometry": "",
    "window_maximized": False,
    "chat_window_geometry": "",
    "chat_window_maximized": False,
    "settings_window_geometry": "",
    "settings_window_maximized": False,
    "status_window_geometry": "",
    "status_window_maximized": False,
    # --- 行为 ---
    "remember_position": True,
    "lock_position": False,
    "snap_to_edge": False,
    "keep_on_screen": True,
    "auto_walk_enabled": True,
    "auto_walk_interval_sec": 45,
    "follow_mouse_enabled": False,
    "idle_talk_enabled": True,
    "idle_talk_interval_sec": 120,
    "idle_action_enabled": True,
    "idle_action_interval_sec": 30,
    "satiety_decay_per_hour": 4.0,
    "energy_decay_per_hour": 3.0,
    "mood_decay_per_hour": 2.0,
    "intimacy_decay_per_hour": 0.5,
    "sleep_recover_per_hour": 12.0,
    "auto_sleep_enabled": False,
    "auto_sleep_hour": 23,
    "auto_wake_hour": 7,
    "click_pet_enabled": True,
    "double_click_play_enabled": True,
    "wheel_zoom_enabled": True,
    "drag_enabled": True,
    "sound_enabled": False,
    "offline_lines_enabled": True,
    # --- 素材行为（自主行为）---
    "behavior_asset_enabled": True,  # 把素材里的动作片当成自主行为来播
    "behavior_asset_interval_sec": 40,
    "behavior_seasonal_enabled": True,  # 节日动作只在对应时段出现
    "behavior_asset_categories": "",  # 空格分隔的类别白名单，空 = 全部
    "asset_character": "",  # ★ v1.1.14：用哪个形象（空 = 素材清单里的第一个；素材包里的形象写 pack:素材4/xxx）
    # ★ v1.1.35：新形象（`鲸宝形象\类别\` 那 78 张）的「台词换形象」开关
    "look_expression_follow": True,  # 说哪一类话就换成对应的形象图（DFP_SAY_FACE_MAP）
    # --- 梦境（★ 骨架从 `大肥鱼素材表\梦境.json` 读，读不到用内置兜底）---
    "dream_enabled": True,
    "dream_interval_hours": 6.0,
    "dream_on_wake": True,
    # ★ v1.1.26：灵魂（会记得 / 有心事 / 会主动开口 / 关系会成长）
    "soul_enabled": True,
    "soul_interval_min": 45,
    # ★ v1.1.29：修真层（道 / 性 / 元神 / 五脏六腑 / 气血 / 命 / 运 / 数 / 因果 / 业 / 缘 / 神 / 仙 / 鬼 / 妖）
    #   —— 关掉它整层都不动（心跳早退），但灵魂与三魂七魄照旧
    "dao_enabled": True,
    # --- 内置修改器（锁定后数值不再变化）---
    "lock_mood": False,
    "lock_satiety": False,
    "lock_energy": False,
    "lock_intimacy": False,
    "lock_level": False,
    "lock_exp": False,
    # --- AI（接口参数；Key 只在 .env） ---
    "ai_enabled": True,
    "ai_model": "deepseek-chat",
    "ai_temperature": 1.0,
    "ai_top_p": 1.0,
    "ai_max_tokens": 512,
    "ai_stream": True,
    "ai_context_rounds": 12,
    "ai_timeout": 60,
    "ai_max_retry": 2,
    "ai_system_prompt": DFP_DEFAULT_PERSONA,
    "ai_speak_reply": True,
    "ai_speak_max_chars": 60,
    "ai_include_pet_state": True,
    "ai_auto_title": True,
    "ai_request_concurrency": 2,
    "ai_frequency_penalty": 0.0,
    "ai_presence_penalty": 0.0,
    # ★ v1.1.12：余额变化提醒（总可用余额变了就在气泡里说一句，默认开启）
    "balance_watch_enabled": True,
    "balance_watch_interval_sec": 300,
    # --- 数据 ---
    "db_encrypt_messages": True,
    "auto_backup_enabled": True,
    "auto_backup_interval_min": 60,
    "backup_keep_count": DFP_BACKUP_KEEP_DEFAULT,
    "event_log_enabled": True,
    "state_sample_interval_min": 30,
    "keep_events_days": 90,
    "keep_state_days": 90,
    "chat_export_format": "markdown",
    # --- 日志 ---
    "log_level": "INFO",
    "log_to_file": True,
    # --- 累计统计（由程序自己维护，不要在界面里手改） ---
    "stat_total_pets": 0,
    "stat_total_feeds": 0,
    "stat_total_chats": 0,
    "stat_total_tokens": 0,
    "stat_total_minutes": 0,
    "last_start_ts": 0.0,
    "last_run_seconds": 0.0,
}

# 这些键在 JSON 里必须保持整型/浮点型，读回时按类型纠正，避免界面控件拿到字符串
DFP_SETTINGS_INT_KEYS: Tuple[str, ...] = tuple(
    key
    for key, value in DFP_DEFAULT_SETTINGS.items()
    if isinstance(value, int) and not isinstance(value, bool)
)
DFP_SETTINGS_FLOAT_KEYS: Tuple[str, ...] = tuple(
    key for key, value in DFP_DEFAULT_SETTINGS.items() if isinstance(value, float)
)
DFP_SETTINGS_BOOL_KEYS: Tuple[str, ...] = tuple(
    key for key, value in DFP_DEFAULT_SETTINGS.items() if isinstance(value, bool)
)


class DFPSettingsStore(QObject):
    """设置存储：内存字典 + 500ms 防抖 + 原子写盘（★ 所有设置都走这里）。"""

    dfpChanged = Signal(str, object)  # key, value
    dfpSaved = Signal(str)  # path
    dfpLoaded = Signal(int)  # 载入的键数量
    dfpReset = Signal()

    def __init__(self, path: str, logger: Optional[DFPLogger] = None, parent=None):
        super().__init__(parent)
        self._dfp_path = path
        self._dfp_logger = logger
        self._dfp_values: Dict[str, Any] = dict(DFP_DEFAULT_SETTINGS)
        self._dfp_loaded = False
        self._dfp_dirty = False
        self._dfp_save_count = 0
        self._dfp_suspend = 0
        self._dfp_timer = QTimer(self)
        self._dfp_timer.setSingleShot(True)
        self._dfp_timer.setInterval(500)
        self._dfp_timer.timeout.connect(self.dfp_save_now)

    # --- 只读属性 ---
    def dfp_path(self) -> str:
        return self._dfp_path

    def dfp_is_loaded(self) -> bool:
        return self._dfp_loaded

    def dfp_save_count(self) -> int:
        return self._dfp_save_count

    def dfp_is_dirty(self) -> bool:
        return self._dfp_dirty

    def dfp_timer_active(self) -> bool:
        return self._dfp_timer.isActive()

    # --- 读写 ---
    def dfp_load(self) -> int:
        payload = dfp_read_json(self._dfp_path, None)
        merged = dict(DFP_DEFAULT_SETTINGS)
        count = 0
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key not in merged:
                    continue
                merged[key] = self._dfp_coerce(key, value)
                count += 1
            # 版本迁移钩子（以后改设置键结构时在这里处理旧键）
            version = dfp_safe_int(payload.get("settings_version", 0), 0)
            if version < DFP_SETTINGS_VERSION:
                merged["settings_version"] = DFP_SETTINGS_VERSION
        self._dfp_values = merged
        self._dfp_loaded = True
        self._dfp_dirty = False
        try:
            self.dfpLoaded.emit(count)
        except Exception:
            pass
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("设置已载入：%s（有效键 %d 个）" % (self._dfp_path, count))
        return count

    def _dfp_coerce(self, key: str, value: Any) -> Any:
        try:
            if key in DFP_SETTINGS_BOOL_KEYS:
                if isinstance(value, str):
                    return value.strip().lower() in ("1", "true", "yes", "on")
                return bool(value)
            if key in DFP_SETTINGS_INT_KEYS:
                return int(float(value))
            if key in DFP_SETTINGS_FLOAT_KEYS:
                return float(value)
            if key in ("pet_palette",):
                return str(value) if str(value) in DFP_PALETTES else DFP_DEFAULT_SETTINGS[key]
            if key.startswith(("window_geometry", "chat_window_geometry", "settings_window_geometry", "status_window_geometry")):
                return str(value) if value else ""
        except Exception:
            return DFP_DEFAULT_SETTINGS.get(key, value)
        return value

    def dfp_save_now(self) -> bool:
        try:
            self._dfp_timer.stop()
        except Exception:
            pass
        payload = dict(self._dfp_values)
        payload["settings_version"] = DFP_SETTINGS_VERSION
        ok = dfp_atomic_write_json(self._dfp_path, payload)
        if ok:
            self._dfp_save_count += 1
            self._dfp_dirty = False
            try:
                self.dfpSaved.emit(self._dfp_path)
            except Exception:
                pass
        else:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_error("设置保存失败：%s" % self._dfp_path)
        return ok

    def dfp_schedule_save(self) -> None:
        self._dfp_dirty = True
        if self._dfp_suspend > 0:
            return
        try:
            self._dfp_timer.start()
        except Exception:
            self.dfp_save_now()

    def dfp_get(self, key: str, default: Any = None) -> Any:
        if key in self._dfp_values:
            return self._dfp_values[key]
        if default is not None:
            return default
        return DFP_DEFAULT_SETTINGS.get(key, default)

    def dfp_set(self, key: str, value: Any) -> bool:
        value = self._dfp_coerce(key, value)
        old = self._dfp_values.get(key)
        if old == value:
            return False
        self._dfp_values[key] = value
        if self._dfp_suspend <= 0:
            try:
                self.dfpChanged.emit(key, value)
            except Exception:
                pass
        self.dfp_schedule_save()
        return True

    def dfp_update(self, mapping: Dict[str, Any]) -> int:
        changed = 0
        for key, value in dict(mapping or {}).items():
            if self.dfp_set(key, value):
                changed += 1
        return changed

    def dfp_all(self) -> Dict[str, Any]:
        return dict(self._dfp_values)

    def dfp_reset_to_defaults(self, keep: Sequence[str] = ()) -> int:
        keep_set = set(keep or ())
        preserved = {key: self._dfp_values.get(key) for key in keep_set if key in self._dfp_values}
        self._dfp_values = {key: dfp_deep_copy(value) for key, value in DFP_DEFAULT_SETTINGS.items()}
        self._dfp_values.update(preserved)
        self._dfp_dirty = True
        try:
            self.dfpReset.emit()
        except Exception:
            pass
        self.dfp_save_now()
        return len(self._dfp_values)

    def dfp_suspend(self) -> None:
        """挂起变更信号（批量加载界面时用，避免控件信号把旧值写回）。"""
        self._dfp_suspend += 1

    def dfp_resume(self) -> None:
        self._dfp_suspend = max(0, self._dfp_suspend - 1)


# =============================================================================
#  十、数据库（SQLite + WAL；对话内容可用 .env 口令加密后再落库）
# =============================================================================


class DFPDatabase:
    """所有持久化数据：状态、对话、事件、每日统计、状态采样。

    线程安全：内部统一用一把可重入锁串行化所有写操作；连接以
    check_same_thread=False 打开，调用方仍然建议只在主线程写。
    """

    def __init__(self, path: str, logger: Optional[DFPLogger] = None):
        self._dfp_path = path
        self._dfp_logger = logger
        self._dfp_conn: Optional[sqlite3.Connection] = None
        self._dfp_lock = threading.RLock()
        self._dfp_write_count = 0
        self._dfp_secret_password = ""
        self._dfp_encrypt_messages = False

    # --- 生命周期 ---
    def dfp_open(self) -> bool:
        with self._dfp_lock:
            if self._dfp_conn is not None:
                return True
            try:
                dfp_ensure_dir(os.path.dirname(self._dfp_path))
                conn = sqlite3.connect(self._dfp_path, check_same_thread=False, timeout=10.0)
                conn.row_factory = sqlite3.Row
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.execute("PRAGMA foreign_keys=ON")
                except Exception:
                    pass
                self._dfp_conn = conn
                self._create_schema()
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_info("数据库已打开：%s" % self._dfp_path)
                return True
            except Exception:
                self._dfp_conn = None
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_exception("数据库打开失败：%s" % self._dfp_path)
                return False

    def dfp_close(self) -> None:
        with self._dfp_lock:
            if self._dfp_conn is None:
                return
            try:
                self._dfp_conn.commit()
            except Exception:
                pass
            try:
                self._dfp_conn.close()
            except Exception:
                pass
            self._dfp_conn = None
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_info("数据库已关闭（累计写入 %d 次）" % self._dfp_write_count)

    def dfp_is_open(self) -> bool:
        return self._dfp_conn is not None

    def dfp_path(self) -> str:
        return self._dfp_path

    def _create_schema(self) -> None:
        conn = self._dfp_conn
        if conn is None:
            return
        statements = (
            """
            CREATE TABLE IF NOT EXISTS dfp_kv (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS dfp_sessions (
                id            TEXT PRIMARY KEY,
                title         TEXT NOT NULL,
                created_at    REAL,
                updated_at    REAL,
                message_count INTEGER DEFAULT 0,
                tokens        INTEGER DEFAULT 0,
                pinned        INTEGER DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS dfp_messages (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id        TEXT NOT NULL,
                role              TEXT NOT NULL,
                content           TEXT,
                content_encrypted INTEGER DEFAULT 0,
                tokens            INTEGER DEFAULT 0,
                model             TEXT,
                ok                INTEGER DEFAULT 1,
                created_at        REAL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_dfp_messages_session ON dfp_messages(session_id, id)",
            """
            CREATE TABLE IF NOT EXISTS dfp_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                kind       TEXT NOT NULL,
                detail     TEXT,
                created_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS dfp_daily (
                day       TEXT PRIMARY KEY,
                chats     INTEGER DEFAULT 0,
                messages  INTEGER DEFAULT 0,
                tokens    INTEGER DEFAULT 0,
                pets      INTEGER DEFAULT 0,
                feeds     INTEGER DEFAULT 0,
                plays     INTEGER DEFAULT 0,
                walks     INTEGER DEFAULT 0,
                screens   INTEGER DEFAULT 0,
                backups   INTEGER DEFAULT 0,
                minutes   INTEGER DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS dfp_state_history (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       REAL,
                mood     REAL,
                satiety  REAL,
                energy   REAL,
                intimacy REAL
            )
            """,
            # ★ v1.1.16：余额变化记录（用户要的「扣钱清单」）
            #   ts_text 直接按「年月日 时:分:秒.毫秒」存好（排序用 ts，显示用 ts_text）
            """
            CREATE TABLE IF NOT EXISTS dfp_balance_log (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       REAL,
                ts_text  TEXT,
                delta    REAL,
                total    REAL,
                currency TEXT,
                source   TEXT
            )
            """,
        )
        for sql in statements:
            conn.execute(sql)
        conn.commit()
        try:
            current = self.dfp_kv_get("schema_version", None)
            if current is None:
                self.dfp_kv_set("schema_version", str(DFP_DB_SCHEMA_VERSION))
            elif dfp_safe_int(current, 0) < DFP_DB_SCHEMA_VERSION:
                self.dfp_kv_set("schema_version", str(DFP_DB_SCHEMA_VERSION))
        except Exception:
            pass

    # --- 底层执行 ---
    def dfp_execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._dfp_lock:
            conn = self._dfp_conn
            if conn is None and not self.dfp_open():
                return 0
            conn = self._dfp_conn
            try:
                cursor = conn.execute(sql, tuple(params or ()))
                conn.commit()
                self._dfp_write_count += 1
                return cursor.rowcount if cursor.rowcount is not None else 0
            except Exception:
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_exception("SQL 执行失败：%s" % sql)
                return 0

    def dfp_execute_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> int:
        payload = [tuple(row) for row in (rows or [])]
        if not payload:
            return 0
        with self._dfp_lock:
            if self._dfp_conn is None and not self.dfp_open():
                return 0
            try:
                self._dfp_conn.executemany(sql, payload)
                self._dfp_conn.commit()
                self._dfp_write_count += len(payload)
                return len(payload)
            except Exception:
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_exception("SQL 批量执行失败：%s" % sql)
                return 0

    def dfp_query_all(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        with self._dfp_lock:
            if self._dfp_conn is None and not self.dfp_open():
                return []
            try:
                return list(self._dfp_conn.execute(sql, tuple(params or ())).fetchall())
            except Exception:
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_exception("SQL 查询失败：%s" % sql)
                return []

    def dfp_query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        rows = self.dfp_query_all(sql, params)
        return rows[0] if rows else None

    def dfp_write_count(self) -> int:
        return self._dfp_write_count

    def dfp_size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                path = self._dfp_path + suffix
                if os.path.isfile(path):
                    total += os.path.getsize(path)
            except Exception:
                continue
        return total

    def dfp_vacuum(self) -> bool:
        with self._dfp_lock:
            if self._dfp_conn is None and not self.dfp_open():
                return False
            try:
                self._dfp_conn.execute("VACUUM")
                self._dfp_conn.commit()
                return True
            except Exception:
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_exception("VACUUM 失败")
                return False

    # --- 敏感字段加密 ---
    def dfp_configure_secrets(self, password: str, encrypt_messages: bool) -> None:
        self._dfp_secret_password = str(password or "")
        self._dfp_encrypt_messages = bool(encrypt_messages)

    def dfp_secrets_enabled(self) -> bool:
        return bool(self._dfp_encrypt_messages and self._dfp_secret_password)

    def _encode_content(self, text: str) -> Tuple[str, int]:
        if not self.dfp_secrets_enabled():
            return str(text or ""), 0
        blob = dfp_secret_encrypt(self._dfp_secret_password, text)
        if blob:
            return blob, 1
        return str(text or ""), 0

    def _decode_row_content(self, row: sqlite3.Row) -> str:
        try:
            content = row["content"] or ""
        except Exception:
            return ""
        try:
            encrypted = int(row["content_encrypted"] or 0)
        except Exception:
            encrypted = 0
        if not encrypted:
            return str(content)
        plain = dfp_secret_decrypt(self._dfp_secret_password, content)
        if plain is None:
            return "[无法解密的消息：请检查 .env 中的 DB_PASSWORD 是否与写入时一致]"
        return plain

    # --- 键值对 ---
    def dfp_kv_get(self, key: str, default: Any = None) -> Any:
        row = self.dfp_query_one("SELECT value FROM dfp_kv WHERE key = ?", (key,))
        if row is None:
            return default
        return row["value"]

    def dfp_kv_set(self, key: str, value: Any) -> bool:
        payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        sql = (
            "INSERT INTO dfp_kv(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
        )
        return bool(self.dfp_execute(sql, (key, payload, dfp_now_ts())))

    def dfp_kv_get_many(self, keys: Sequence[str]) -> Dict[str, Any]:
        names = list(keys or [])
        if not names:
            return {}
        marks = ",".join("?" for _ in names)
        rows = self.dfp_query_all("SELECT key, value FROM dfp_kv WHERE key IN (%s)" % marks, names)
        return {row["key"]: row["value"] for row in rows}

    def dfp_kv_delete(self, key: str) -> bool:
        return bool(self.dfp_execute("DELETE FROM dfp_kv WHERE key = ?", (key,)))

    # --- 会话 ---
    def dfp_create_session(self, title: str = "") -> str:
        session_id = dfp_new_id()
        now = dfp_now_ts()
        sql = (
            "INSERT INTO dfp_sessions(id, title, created_at, updated_at, message_count, tokens, pinned) "
            "VALUES(?, ?, ?, ?, 0, 0, 0)"
        )
        self.dfp_execute(sql, (session_id, str(title or "新会话"), now, now))
        return session_id

    def dfp_list_sessions(self, limit: int = 200) -> List[Dict[str, Any]]:
        sql = (
            "SELECT id, title, created_at, updated_at, message_count, tokens, pinned FROM dfp_sessions "
            "ORDER BY pinned DESC, updated_at DESC LIMIT ?"
        )
        rows = self.dfp_query_all(sql, (max(1, int(limit)),))
        return [dict(row) for row in rows]

    def dfp_get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self.dfp_query_one("SELECT * FROM dfp_sessions WHERE id = ?", (session_id,))
        return dict(row) if row else None

    def dfp_latest_session(self) -> Optional[Dict[str, Any]]:
        row = self.dfp_query_one("SELECT * FROM dfp_sessions ORDER BY pinned DESC, updated_at DESC LIMIT 1")
        return dict(row) if row else None

    def dfp_rename_session(self, session_id: str, title: str) -> bool:
        return bool(
            self.dfp_execute(
                "UPDATE dfp_sessions SET title = ?, updated_at = ? WHERE id = ?",
                (str(title or "未命名"), dfp_now_ts(), session_id),
            )
        )

    def dfp_pin_session(self, session_id: str, pinned: bool) -> bool:
        return bool(
            self.dfp_execute(
                "UPDATE dfp_sessions SET pinned = ?, updated_at = ? WHERE id = ?",
                (1 if pinned else 0, dfp_now_ts(), session_id),
            )
        )

    def dfp_delete_session(self, session_id: str) -> bool:
        self.dfp_execute("DELETE FROM dfp_messages WHERE session_id = ?", (session_id,))
        return bool(self.dfp_execute("DELETE FROM dfp_sessions WHERE id = ?", (session_id,)))

    def dfp_session_message_count(self, session_id: str) -> int:
        row = self.dfp_query_one("SELECT COUNT(*) AS c FROM dfp_messages WHERE session_id = ?", (session_id,))
        return int(row["c"]) if row else 0

    # --- 消息 ---
    def dfp_add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tokens: int = 0,
        model: str = "",
        ok: bool = True,
    ) -> int:
        blob, encrypted = self._encode_content(content)
        now = dfp_now_ts()
        sql = (
            "INSERT INTO dfp_messages(session_id, role, content, content_encrypted, tokens, model, ok, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)"
        )
        self.dfp_execute(sql, (session_id, role, blob, encrypted, int(tokens or 0), model, 1 if ok else 0, now))
        row = self.dfp_query_one("SELECT last_insert_rowid() AS r")
        message_id = int(row["r"]) if row else 0
        self.dfp_execute(
            "UPDATE dfp_sessions SET updated_at = ?, message_count = message_count + 1, "
            "tokens = tokens + ? WHERE id = ?",
            (now, int(tokens or 0), session_id),
        )
        return message_id

    def dfp_list_messages(self, session_id: str, limit: int = 500, ascending: bool = True) -> List[Dict[str, Any]]:
        """取「最近 limit 条」消息；ascending=True 返回时间正序（老的在前）。"""
        sql = "SELECT * FROM dfp_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?"
        rows = self.dfp_query_all(sql, (session_id, max(1, int(limit))))
        result: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["content"] = self._decode_row_content(row)
            result.append(item)
        if ascending:
            result.reverse()
        return result

    def dfp_recent_dialogue(self, session_id: str, rounds: int = 12) -> List[Dict[str, str]]:
        """取最近若干轮对话（只保留内容，供 API 上下文用）。"""
        limit = max(1, int(rounds)) * 2
        rows = self.dfp_list_messages(session_id, limit=limit, ascending=True)
        return [
            {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")}
            for item in rows
            if str(item.get("role") or "") in ("user", "assistant", "system")
        ]

    def dfp_clear_session_messages(self, session_id: str) -> int:
        count = self.dfp_session_message_count(session_id)
        self.dfp_execute("DELETE FROM dfp_messages WHERE session_id = ?", (session_id,))
        self.dfp_execute(
            "UPDATE dfp_sessions SET message_count = 0, tokens = 0, updated_at = ? WHERE id = ?",
            (dfp_now_ts(), session_id),
        )
        return count

    def dfp_clear_all_messages(self) -> int:
        row = self.dfp_query_one("SELECT COUNT(*) AS c FROM dfp_messages")
        total = int(row["c"]) if row else 0
        self.dfp_execute("DELETE FROM dfp_messages")
        self.dfp_execute("UPDATE dfp_sessions SET message_count = 0, tokens = 0")
        return total

    def dfp_seed_session(self, session_id: str, greeting: str) -> int:
        """给新会话写入一条桌宠开场白（不消耗接口）。"""
        return self.dfp_add_message(session_id, "assistant", greeting, 0, "offline", True)

    # --- 事件 ---
    def dfp_add_event(self, kind: str, detail: str = "") -> int:
        return self.dfp_execute(
            "INSERT INTO dfp_events(kind, detail, created_at) VALUES(?, ?, ?)",
            (str(kind), str(detail), dfp_now_ts()),
        )

    def dfp_list_events(self, limit: int = 200) -> List[Dict[str, Any]]:
        rows = self.dfp_query_all("SELECT * FROM dfp_events ORDER BY id DESC LIMIT ?", (max(1, int(limit)),))
        return [dict(row) for row in rows]

    def dfp_count_events(self) -> int:
        row = self.dfp_query_one("SELECT COUNT(*) AS c FROM dfp_events")
        return int(row["c"]) if row else 0

    def dfp_purge_events(self, keep_days: int) -> int:
        cutoff = dfp_now_ts() - max(0, int(keep_days)) * 86400.0
        row = self.dfp_query_one("SELECT COUNT(*) AS c FROM dfp_events WHERE created_at < ?", (cutoff,))
        count = int(row["c"]) if row else 0
        self.dfp_execute("DELETE FROM dfp_events WHERE created_at < ?", (cutoff,))
        return count

    def dfp_clear_events(self) -> int:
        count = self.dfp_count_events()
        self.dfp_execute("DELETE FROM dfp_events")
        return count

    # --- ★ v1.1.16：余额变化记录（扣钱清单）---
    def dfp_add_balance_log(self, delta: float, total: float, currency: str = "", source: str = "", ts: Optional[float] = None) -> int:
        """记一条余额变化（**只在真的变了的时候调**；负 delta = 扣钱）。返回新纪录 id（写不进去给 0）。

        ★ 这里不用 `dfp_execute()`：它返回的是 rowcount（插入永远是 1），拿不到自增 id。
        """
        moment = dfp_safe_float(ts, dfp_now_ts()) if ts else dfp_now_ts()
        with self._dfp_lock:
            conn = self._dfp_conn
            if conn is None and not self.dfp_open():
                return 0
            conn = self._dfp_conn
            try:
                cursor = conn.execute(
                    "INSERT INTO dfp_balance_log(ts, ts_text, delta, total, currency, source) VALUES(?, ?, ?, ?, ?, ?)",
                    (moment, dfp_timestamp_ms(moment), float(delta), float(total), str(currency or ""), str(source or "")),
                )
                conn.commit()
                self._dfp_write_count += 1
                return int(cursor.lastrowid or 0)
            except Exception:
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_exception("余额记录写入失败")
                return 0

    def dfp_list_balance_log(self, limit: int = 200, only_spend: bool = False) -> List[Dict[str, Any]]:
        """最近的余额变化（默认最新的在前；`only_spend=True` 只看扣钱）。"""
        sql = "SELECT * FROM dfp_balance_log"
        if only_spend:
            sql += " WHERE delta < 0"
        sql += " ORDER BY id DESC LIMIT ?"
        rows = self.dfp_query_all(sql, (max(1, int(limit)),))
        return [dict(row) for row in rows]

    def dfp_count_balance_log(self) -> int:
        row = self.dfp_query_one("SELECT COUNT(*) AS c FROM dfp_balance_log")
        return int(row["c"]) if row else 0

    def dfp_prune_balance_log(self, keep: int = DFP_BALANCE_LOG_KEEP) -> int:
        """只留最新 keep 条，返回删掉多少条。"""
        keep = max(1, int(keep))
        row = self.dfp_query_one("SELECT COUNT(*) AS c FROM dfp_balance_log")
        total = int(row["c"]) if row else 0
        if total <= keep:
            return 0
        self.dfp_execute(
            "DELETE FROM dfp_balance_log WHERE id NOT IN (SELECT id FROM dfp_balance_log ORDER BY id DESC LIMIT ?)",
            (keep,),
        )
        return total - keep

    def dfp_clear_balance_log(self) -> int:
        count = self.dfp_count_balance_log()
        self.dfp_execute("DELETE FROM dfp_balance_log")
        return count

    # --- 每日统计 ---
    def dfp_bump_daily(self, field_name: str, amount: int = 1) -> None:
        field_key = str(field_name)
        if field_key not in DFP_DAILY_FIELDS:
            return
        day = dfp_today_key()
        self.dfp_execute("INSERT OR IGNORE INTO dfp_daily(day) VALUES(?)", (day,))
        sql = "UPDATE dfp_daily SET %s = %s + ? WHERE day = ?" % (field_key, field_key)
        self.dfp_execute(sql, (int(amount), day))

    def dfp_list_daily(self, limit: int = 30) -> List[Dict[str, Any]]:
        rows = self.dfp_query_all("SELECT * FROM dfp_daily ORDER BY day DESC LIMIT ?", (max(1, int(limit)),))
        return [dict(row) for row in rows]

    def dfp_daily_today(self) -> Dict[str, Any]:
        row = self.dfp_query_one("SELECT * FROM dfp_daily WHERE day = ?", (dfp_today_key(),))
        if row is None:
            return {name: 0 for name in ("day",) + DFP_DAILY_FIELDS}
        return dict(row)

    # --- 状态采样 ---
    def dfp_add_state_sample(self, mood: float, satiety: float, energy: float, intimacy: float) -> int:
        return self.dfp_execute(
            "INSERT INTO dfp_state_history(ts, mood, satiety, energy, intimacy) VALUES(?, ?, ?, ?, ?)",
            (dfp_now_ts(), float(mood), float(satiety), float(energy), float(intimacy)),
        )

    def dfp_recent_state_samples(self, limit: int = 200) -> List[Dict[str, Any]]:
        rows = self.dfp_query_all("SELECT * FROM dfp_state_history ORDER BY id DESC LIMIT ?", (max(1, int(limit)),))
        return [dict(row) for row in rows]

    def dfp_purge_state_samples(self, keep_days: int) -> int:
        cutoff = dfp_now_ts() - max(0, int(keep_days)) * 86400.0
        row = self.dfp_query_one("SELECT COUNT(*) AS c FROM dfp_state_history WHERE ts < ?", (cutoff,))
        count = int(row["c"]) if row else 0
        self.dfp_execute("DELETE FROM dfp_state_history WHERE ts < ?", (cutoff,))
        return count

    def dfp_clear_state_samples(self) -> int:
        row = self.dfp_query_one("SELECT COUNT(*) AS c FROM dfp_state_history")
        count = int(row["c"]) if row else 0
        self.dfp_execute("DELETE FROM dfp_state_history")
        return count

    # --- 汇总统计 ---
    def dfp_statistics(self) -> Dict[str, Any]:
        sessions = self.dfp_query_one("SELECT COUNT(*) AS c FROM dfp_sessions")
        messages = self.dfp_query_one("SELECT COUNT(*) AS c FROM dfp_messages")
        tokens = self.dfp_query_one("SELECT COALESCE(SUM(tokens), 0) AS c FROM dfp_messages")
        events = self.dfp_count_events()
        samples = self.dfp_query_one("SELECT COUNT(*) AS c FROM dfp_state_history")
        daily = self.dfp_query_one("SELECT COALESCE(SUM(pets), 0) AS pets, COALESCE(SUM(feeds), 0) AS feeds, "
                                  "COALESCE(SUM(chats), 0) AS chats, COALESCE(SUM(minutes), 0) AS minutes FROM dfp_daily")
        return {
            "session_count": int(sessions["c"]) if sessions else 0,
            "message_count": int(messages["c"]) if messages else 0,
            "token_total": int(tokens["c"]) if tokens else 0,
            "event_count": int(events),
            "sample_count": int(samples["c"]) if samples else 0,
            "total_pets": int(daily["pets"]) if daily else 0,
            "total_feeds": int(daily["feeds"]) if daily else 0,
            "total_chats": int(daily["chats"]) if daily else 0,
            "total_minutes": int(daily["minutes"]) if daily else 0,
            "db_size": self.dfp_size_bytes(),
            "db_path": self._dfp_path,
            "write_count": self._dfp_write_count,
        }


# =============================================================================
#  十一、备份管理（复制数据库文件 + 按份数清理 + 还原 + 删除）
# =============================================================================


class DFPBackupManager:
    def __init__(self, database_path: str, backup_dir: str, logger: Optional[DFPLogger] = None):
        self._dfp_database_path = database_path
        self._dfp_dir = backup_dir
        self._dfp_logger = logger
        dfp_ensure_dir(backup_dir)

    def dfp_dir(self) -> str:
        return self._dfp_dir

    def dfp_list(self) -> List[Dict[str, Any]]:
        try:
            names = [
                name
                for name in os.listdir(self._dfp_dir)
                if name.lower().endswith(".db") and name.startswith("pet_data_")
            ]
        except Exception:
            return []
        items: List[Dict[str, Any]] = []
        for name in names:
            path = os.path.join(self._dfp_dir, name)
            try:
                stat = os.stat(path)
                items.append(
                    {
                        "path": path,
                        "name": name,
                        "size": int(stat.st_size),
                        "created_at": float(stat.st_mtime),
                        "text": "%s（%s，%s）" % (name, dfp_human_bytes(stat.st_size), dfp_fmt_ts(stat.st_mtime)),
                    }
                )
            except Exception:
                continue
        items.sort(key=lambda item: item["created_at"], reverse=True)
        return items

    def dfp_total_size(self) -> int:
        return sum(int(item["size"]) for item in self.dfp_list())

    def dfp_create(self, reason: str = "manual") -> Optional[str]:
        if not os.path.isfile(self._dfp_database_path):
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("数据库文件不存在，跳过备份：%s" % self._dfp_database_path)
            return None
        stamp = dfp_fmt_ts(fmt="%Y%m%d_%H%M%S")
        target = os.path.join(self._dfp_dir, "pet_data_%s_%s.db" % (stamp, str(reason or "manual")))
        try:
            dfp_ensure_dir(self._dfp_dir)
            # 用 sqlite 备份 API 而不是直接复制，避免 WAL 里的数据丢失
            src = sqlite3.connect(self._dfp_database_path)
            try:
                dst = sqlite3.connect(target)
                try:
                    src.backup(dst)
                    dst.commit()
                finally:
                    dst.close()
            finally:
                src.close()
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_info("已创建数据库备份：%s" % os.path.basename(target))
            return target
        except Exception:
            try:
                shutil.copy2(self._dfp_database_path, target)
                return target
            except Exception:
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_exception("创建备份失败")
                return None

    def dfp_prune(self, keep: int = DFP_BACKUP_KEEP_DEFAULT) -> int:
        keep = max(1, int(keep))
        items = self.dfp_list()
        removed = 0
        for item in items[keep:]:
            try:
                os.remove(item["path"])
                removed += 1
            except Exception:
                continue
        if removed and self._dfp_logger is not None:
            self._dfp_logger.dfp_info("备份清理完成：删除 %d 份，保留 %d 份" % (removed, keep))
        return removed

    def dfp_restore(self, backup_path: str) -> bool:
        if not os.path.isfile(backup_path):
            return False
        try:
            shutil.copy2(backup_path, self._dfp_database_path)
            for suffix in ("-wal", "-shm"):
                try:
                    extra = self._dfp_database_path + suffix
                    if os.path.exists(extra):
                        os.remove(extra)
                except Exception:
                    continue
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_info("已从备份还原数据库：%s" % os.path.basename(backup_path))
            return True
        except Exception:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_exception("从备份还原失败")
            return False

    def dfp_delete(self, backup_path: str) -> bool:
        try:
            os.remove(backup_path)
            return True
        except Exception:
            return False


# =============================================================================
#  十一、形象素材库（★ 新形象：素材驱动，不再靠代码画角色）
#  ★ v1.1.32：**桌宠会显示的形象图有自己的家了**（与「鲸宝音频」并列，都在程序目录下）：
#     「鲸宝形象\立绘」形象本体 /「表情」表情动画 /「动作」106 个动作片 /「启动图.png」；
#     相对路径解析顺序 = **素材表 → 程序目录 → 数据目录**（两者都认，搬不搬都能跑）。
#  其余素材仍住「大肥鱼素材表」（第三方素材包 素材1~6 都在里面）：
#    · 素材包带来的立绘 / 形态 SVG（素材4 / 素材5 / 素材6）
#  用户往目录里丢新素材，重跑 _scaffold\build_asset_manifest.py 生成中文名清单即可；
#  没有清单时程序会用内置兜底扫描（只按文件名关键字分类，两个家都扇），不会崩。
# =============================================================================

DFP_ASSET_DIR_NAME = "大肥鱼素材表"
DFP_ASSET_MANIFEST_NAME = "素材清单.json"
DFP_ASSET_SPRITE_EXT = (".png", ".webp", ".jpg", ".jpeg", ".bmp")
DFP_ASSET_MOVIE_EXT = (".gif", ".webp")
DFP_ASSET_AUDIO_EXT = (".mp3", ".wav", ".ogg", ".m4a")
# ★ 这些子目录里是素材包自带的表情包 / 鼠标光标图，不是桌宠的表情状态：
#   兜底扫描必须跳过，否则「重新扫描素材表」会把它们当成表情（桌宠会显示表情包或光标）。
DFP_ASSET_IGNORED_DIRS = ("memes", "pic", "icons", "icon", "cursor", "fonts", "font", ".git")
# ★ v1.1.36：名字里带这些词的目录是**原始稿 / 归档**，不是能上屏的素材 ——
#   兜底扫描必须跳过。实测踩到过：`素材6\原始图（带背景）\在床上被抓住左手1.png`
#   被当成表情（命中「抓」关键字）写进清单，表情从 0 条变成 1 条（'grab'）。
DFP_ASSET_IGNORED_DIR_WORDS = ("原始", "源稿", "退役", "带背景", "白底", "备份", "样板")
# ★ v1.1.34：下划线开头的目录一律跳过 —— 那是「退役 / 源稿」这类**不是素材**的归档区：
#     `鲸宝形象\_退役\`（旧立绘 / 旧表情 / 旧启动图）、`鲸宝形象\_源稿\`（设计稿母版：白底原图 /
#     关键帧 / 去背与生成脚本）、`大肥鱼素材表\素材2\_退役\`、`大肥鱼素材表\素材6\assets\_退役_skins\`。
#   不跳过的话兜底扫描会把这些旧图重新当成形象 / 表情（`素材2\_退役\pet_heart.png` 就是个表情）。
DFP_ASSET_SKIP_PREFIX = "_"
DFP_ASSET_MAX_CACHE = 64
# 16:9 的动作动画里角色只占约 50% 帧宽，放大到与立绘同尺寸才不会显小
DFP_ASSET_ANIM_ZOOM = 2.0
DFP_ASSET_ANIM_ASPECT = 1.35

# ★ v1.1.32：形象图也有了自己的家（与「鲸宝音频」并列）—— 桌宠真正会显示的那些图都放这里：
#     鲸宝形象\立绘\*.png            形象本体（清单 characters，如 pet.png / pet_sleepy_f0.png）
#     鲸宝形象\表情\pet_*.webp       表情动画（清单 expressions，眨眼/微笑/挥手/打哈欠/困倦/醒来/被拎起/爱心/摇晃）
#     鲸宝形象\动作\*.gif           动作片（清单 animations，106 个自主行为）
#     鲸宝形象\启动图.png           启动画面那张（原来在素材表根目录）
#   程序目录与数据目录下的同名目录都认（顺序：程序目录在前）；**素材表里那份老位置也照旧认**：
#   相对路径的解析顺序是「素材表 → 程序目录 → 数据目录」（见 `DFPAssetLibrary.dfp_abs`）。
#   子目录名只是**推荐布局**：程序是递归扫的，平铺在 `鲸宝形象\` 根下也一样认。
DFP_CHARACTER_DIR_NAME = "鲸宝形象"
DFP_CHARACTER_FACE_DIR = "立绘"
# ★ v1.1.35：新形象的目录名 —— `鲸宝形象\类别\` 里是「一个台词类别一张整图」
#   （78 张，每张一个 PNG + 一个同名 GIF；文件名 `序号_英文名.ext`）
DFP_CHARACTER_SAY_DIR = "类别"
DFP_CHARACTER_EXPR_DIR = "表情"
DFP_CHARACTER_ANIM_DIR = "动作"

# ★ v1.1.17：启动画面（DFPSplashScreen）左边那张小图，按顺序找：
#     ① 「启动图.png / .jpg / .jpeg / .webp / .bmp」—— **素材表根目录** 与 **`鲸宝形象\`**（v1.1.32 起）
#        —— 想固定用某一张就把它复制过去并改名成「启动图」，源图放哪儿都行；
#     ② 当前形象立绘（跟桌宠一致，认设置里的 `asset_character`）；
#     ③ 都没有才画手绘的那只（v1.0.0 的老行为：删掉启动图、且没有立绘时自动回退）。
DFP_SPLASH_IMAGE_NAMES = ("启动图.png", "启动图.jpg", "启动图.jpeg", "启动图.webp", "启动图.bmp")
DFP_SPLASH_IMAGE_BOX = (16, 46, 144, 154)  # x, y, 宽, 高（比手绘那张 130x154 稍宽，横图也装得下）

# ★ 素材包（v1.1.14）：素材表根目录下**自带清单的形象包**，如「素材4」：
#     <素材表>\素材4\assets\voices\manifest.json   ← 包自己的语音清单（记着每条音频的文本）
#     <素材表>\素材4\assets\voices\line-*.wav      ← 闲聊 / 摸头语音
#     <素材表>\素材4\assets\voices\notice-*.wav    ← 任务状态提示音
#     <素材表>\素材4\assets\skins\*.png            ← 可替换的立绘（形象）
#   包**不写进 `素材清单.json`**（那份清单是 build_asset_manifest.py 生成的），
#   而是运行时合并进来 —— 包整个删掉也不会影响原有效果。
DFP_ASSET_PACK_VOICE_MANIFEST = os.path.join("assets", "voices", "manifest.json")
DFP_ASSET_PACK_VOICE_DIR = os.path.join("assets", "voices")
DFP_ASSET_PACK_SKIN_DIR = os.path.join("assets", "skins")
DFP_ASSET_PACK_NOTICE_PREFIX = "notice-"
# 包清单里的 `kind` → 程序里的语音类别（pat＝被摸头，与现有 poke 合池）
DFP_ASSET_PACK_KIND_CATEGORY: Dict[str, str] = {
    "idle": "idle",
    "pat": "poke",
    "poke": "poke",
    "confirm": "confirm",
    "done": "done",
    "ask": "ask",
}
# 提示音的类别前缀：`notice-completed-1.wav` → 类别 `notice_completed`
DFP_ASSET_PACK_NOTICE_CATEGORY_PREFIX = "notice_"
# 包清单里声明的提示音状态（与文件名的中段一一对应）
DFP_ASSET_PACK_NOTICE_STATUSES: Tuple[str, ...] = (
    "completed",
    "failed",
    "interrupted",
    "needs_help",
    "low_balance",
    "approval",
)
# 提示音的中文名（设置窗口试听按钮与日志里用）
DFP_ASSET_PACK_NOTICE_LABELS: Dict[str, str] = {
    "completed": "任务完成",
    "failed": "任务失败",
    "interrupted": "被打断",
    "needs_help": "需要帮忙",
    "low_balance": "余额变少",
    "approval": "需要确认",
}

# ★ 状态形象（v1.1.36 起 = **新形象集里的图**；v1.1.15 那套「素材5 的整只小鲸鱼 SVG」已退役）。
#   用户 2026-10-03 的原话：「怎么还有旧的，不是下架了吗？而且新的已经覆盖了吧。为什么不用新的？」
#   —— 睡觉 / 忙 / 等 / 休息 / 庆祝 / 出错这几个状态，以前画的是 `素材5\assets\*.svg`
#      （旧鲸鱼），现在一律改成画 `鲸宝形象\类别\` 里对应的那张**新形象图**。
#   那 7 个旧 SVG 已搬进 `素材5\assets\_退役_forms\`（只搬不删，见 `_scaffold\retire_form_svgs.py`），
#   而且程序**再也不会去扫 SVG** —— 旧鲸鱼没有重见天日的路子。
#   状态键 → 新形象图 id（★ 单一来源：库的 `dfp_forms()` / `dfp_form_face()`、设置窗口、测试都走它）
DFP_FORM_FACE_MAP: Dict[str, str] = {
    "working": "work_hard_quiet",  # 忙碌（干活）→ 安静陪坐
    "waiting": "question_thinking",  # 等待（等回复 / 等确认）→ 歪头想问
    "resting": "sleepy_yawn",  # 休息（歇一会儿）→ 打小哈欠
    "sleeping": "sleep_curl",  # 睡觉 → 蜷成一团（★ 以前是旧鲸鱼 sleeping.svg）
    "celebrate": "level_up",  # 庆祝（成功 / 升级）→ 升级撒花
    "error": "error_panic",  # 出错（失败 / 出问题）→ 出错冒汗
}
# 状态键 → 中文名（顺序就是界面上显示的顺序）
DFP_FORM_LABELS: Dict[str, str] = {
    "working": "忙碌（干活）",
    "waiting": "等待（等回复 / 等确认）",
    "resting": "休息（歇一会儿）",
    "sleeping": "睡觉",
    "celebrate": "庆祝（成功 / 升级）",
    "error": "出错（失败 / 出问题）",
}
# 状态键 → 「什么时候适合用」+ 换成哪张新形象图（预览窗口、设置说明与文档共用同一份口径）
DFP_FORM_WHEN: Dict[str, str] = {
    "working": "后台任务在跑：AI 正在生成、导出、备份、查余额（换成「安静陪坐」）",
    "waiting": "发出消息后等回复；弹出确认框等你点「是 / 否」（换成「歪头想问」）",
    "resting": "它自己挑到「休息」类动作时；被打断 / 停下来歇一会儿（换成「打小哈欠」）",
    "sleeping": "睡着的时候（换成「蜷成一团」，醒了自动回基础形象）",
    "celebrate": "任务成功、升级、备份完成（换成「升级撒花」）",
    "error": "任务失败、接口出问题、Key 没配好（换成「出错冒汗」）",
}
# 一次性状态的默认时长（秒）；0 = 一直挂着，等 clear
DFP_FORM_WIDTH_FIT = 0.98  # 状态图的宽度上限（占画布宽的比例）：横图别跑出画布
DFP_FORM_FLASH_SECONDS: Dict[str, float] = {
    "working": 6.0,
    "waiting": 0.0,
    "resting": 4.0,
    "sleeping": 0.0,  # 由「是否睡着」驱动，不走 flash
    "celebrate": 4.5,
    "error": 6.0,
}
# 优先级（数值越大越优先；同一时刻只画一个状态形象）
DFP_FORM_PRIORITY: Dict[str, int] = {
    "error": 60,
    "celebrate": 50,
    "working": 40,
    "waiting": 30,
    "sleeping": 20,
    "resting": 10,
}
# 提示音状态 → 状态形象（v1.1.14 的六类提示音顺手把形象也切了；low_balance 不换，免得吵）
DFP_NOTICE_FORM: Dict[str, str] = {
    "completed": "celebrate",
    "failed": "error",
    "interrupted": "resting",
    "needs_help": "error",
    "approval": "waiting",
}
_DFP_FORM_PIXMAP_CACHE: Dict[str, Any] = {}


def dfp_svg_flatten(text: str) -> str:
    """（★ v1.1.36 已退役，不再被调用）把「嵌套 `<svg>`」展平成 `<g transform=…>`。

    保留原因：它是照着 PySide6 的 Svg Tiny 1.2 限制手工调试出来的，以后万一又要读 SVG 还能用。
    旧的「素材5 鲸鱼形态」已经不读 SVG 了（改成新形象集里的图），所以这里只是备用笔记。
    为什么必须做：Qt 的 SVG 引擎是 **Svg Tiny 1.2**，遇到嵌套 `<svg>` 会直接
      `Skipping a nested svg element` —— 整个图形等于没画（实测 `素材5\assets\icon.svg`
      渲染出来是**全透明空白**，而其余 6 张不带嵌套的都能画）。展平之后就都能画。
    换算：外层给了 x/y/width/height，内层给了自己的 viewBox ⇒
      transform = translate(x, y) scale(width / vbW, height / vbH) translate(-vbX, -vbY)
    只按文本结构处理（最外层 svg 的第一个内层 svg），不认识就原样返回，绝不抛。
    """
    value = str(text or "")
    if not value or "<svg" not in value:
        return value

    def _number(source: str, key: str, fallback: float) -> float:
        found = re.search(r'\b%s\s*=\s*"([^"]*)"' % re.escape(key), source)
        if not found:
            return fallback
        raw = found.group(1).strip()
        if raw.endswith("%"):
            raw = raw[:-1]
        return dfp_safe_float(raw, fallback)

    def _fmt(number: float) -> str:
        # ★ SVG 不认指数写法（1e+06），所以固定用小数位截断；-0 也顺手归一成 0
        value = float(number)
        if abs(value) < 1e-9:
            return "0"
        text = ("%.6f" % value).rstrip("0").rstrip(".")
        return text or "0"

    try:
        for _ in range(8):
            root_end = value.find(">", value.find("<svg"))
            if root_end < 0:
                return value
            inner_start = value.find("<svg", root_end)
            if inner_start < 0:
                return value  # 没有嵌套，直接返回
            inner_end = value.find(">", inner_start)
            if inner_end < 0:
                return value
            header = value[inner_start:inner_end]
            # ★ v1.1.36：原来的 `_string_attr()` 小助手随旧鲸鱼形态一起删了，这里就地取一次
            _vb = re.search(r'\bviewBox\s*=\s*"([^"]*)"', header)
            box = [part for part in re.split(r"[\s,]+", _vb.group(1) if _vb else "") if part]
            if len(box) != 4:
                return value
            box_x, box_y = dfp_safe_float(box[0], 0.0), dfp_safe_float(box[1], 0.0)
            box_w, box_h = dfp_safe_float(box[2], 0.0), dfp_safe_float(box[3], 0.0)
            if box_w <= 0.0 or box_h <= 0.0:
                return value
            x = _number(header, "x", 0.0)
            y = _number(header, "y", 0.0)
            width = _number(header, "width", box_w)
            height = _number(header, "height", box_h)
            if width <= 0.0 or height <= 0.0:
                width, height = box_w, box_h
            transform = "translate(%s,%s) scale(%s,%s) translate(%s,%s)" % (
                _fmt(x), _fmt(y), _fmt(width / box_w), _fmt(height / box_h), _fmt(-box_x), _fmt(-box_y),
            )
            # ★ 内层元素自己也有一个 </svg>，它**在**根元素那个之前 ⇒
            #   正文必须切到「倒数第二个 </svg>」为止，否则会把内层的结束标签
            #   一起塞进 <g> 里，生成 `<g>…</svg></g></svg>` 这种非法结构
            #   （Qt 会直接判 invalid，实测踩到过）。
            root_close = value.rfind("</svg>")
            if root_close <= inner_end:
                return value
            inner_close = value.rfind("</svg>", inner_end, root_close)
            if inner_close < 0:
                inner_close = root_close
            body = value[inner_end + 1:inner_close]
            value = "%s<g transform=\"%s\">%s</g>%s" % (value[:inner_start], transform, body, value[root_close:])
        return value
    except Exception:
        return str(text or "")


# 表情/状态 → 素材文件名关键字（兜底扫描时用；清单存在时优先用清单里的 id）
DFP_ASSET_EXPRESSION_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "base": ("pet", "main", "base", "normal", "stand", "立绘", "形象", "正面"),
    "blink": ("blink", "zhayan", "眨眼"),
    "smile": ("smile", "xiao", "微笑", "开心"),
    "wave": ("wave", "huishou", "挥手"),
    "yawn": ("yawn", "haqian", "哈欠"),
    "sleepy": ("sleepy", "sleep", "chenmian", "睡", "困"),
    "wakeup": ("wakeup", "wake", "xinglai", "醒"),
    "grab": ("grab", "diaoqi", "拎", "抓"),
    "heart": ("heart", "aixin", "爱心", "比心"),
    "shake": ("shake", "yaohuang", "摇晃", "颤抖"),
}

# 行为分类 → 文件名关键字（兜底扫描时用）
DFP_ASSET_CATEGORY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "交互": ("dianji", "beishubiao", "beixiayi", "jiqi", "zhuazhu", "huanrao"),
    "吃喝": ("chi", "he-", "heshui", "dakou", "chijiaozi", "chitangyuan", "hejiu"),
    "休息": ("shui", "haqian", "chenmian", "huxi", "xiuxian", "shenlanyao", "dun", "yao", "zha"),
    "工作": ("gongzuozhuangtai", "xie-daima", "xie-fuzi", "jilu", "zhengli", "guida", "yue", "sikao", "dianan"),
    "节日": ("zhongqiu", "shengdan", "yuanxiao", "duanwu", "chongyang", "qingming", "dongzhi", "yanhua", "kongmingdeng", "fengzheng", "duixueren", "he deng", "shangyue", "cai"),
    "碎碎念": ("suisuinian", "fadai", "shensi", "zhenxin", "youdai", "duiping", "cazhuo"),
    "玩耍": ("wan", "qi-", "muma", "qiuqiu", "wu", "mo", "qin", "di", "shui", "qiang", "yue", "chang", "tiao", "pao", "zhui", "jingzi", "xi", "you", "zhao", "lian"),
}

# 节日行为只在对应时段随机出现（让「自主行为」跟着真实日期走）
DFP_SEASONAL_WINDOWS: Tuple[Tuple[str, Tuple[int, int], Tuple[int, int]], ...] = (
    ("春节", (1, 20), (2, 20)),
    ("元宵", (2, 1), (2, 25)),
    ("清明", (4, 1), (4, 15)),
    ("端午", (5, 25), (6, 25)),
    ("中秋", (9, 5), (10, 10)),
    ("重阳", (10, 1), (10, 20)),
    ("冬至", (12, 15), (12, 31)),
    ("圣诞", (12, 10), (12, 31)),
)


def dfp_relative_or_abs(path: str, base: str) -> str:
    """尽量给出相对 base 的路径；跨盘符（Windows 上 C: 与 G:）时回退成绝对路径。

    踩过的坑：os.path.relpath 在不同盘符之间会抛 ValueError，而素材表完全可能
    放在另一个盘上（本机程序在 G:、临时目录在 C:）⇒ 直接让整个扫描炸掉。
    """
    try:
        return os.path.relpath(str(path), str(base)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def dfp_seasonal_ok(label: str, now: Optional[float] = None) -> bool:
    """带节令字样的行为只在对应时段可用；其余一律可用。"""
    text = str(label or "")
    moment = datetime.fromtimestamp(dfp_now_ts() if now is None else float(now))
    tag = moment.month * 100 + moment.day
    for keyword, start, end in DFP_SEASONAL_WINDOWS:
        if keyword not in text:
            continue
        low = start[0] * 100 + start[1]
        high = end[0] * 100 + end[1]
        if low <= high:
            return low <= tag <= high
        return tag >= low or tag <= high
    return True


class DFPAssetLibrary(QObject):
    """扫描并使用「大肥鱼素材表」里的形象、动作与声音。"""

    dfpScanned = Signal(int, str)  # 条目数, 摘要

    def __init__(self, settings: DFPSettingsStore, logger: Optional[DFPLogger] = None, parent=None):
        super().__init__(parent)
        self._dfp_settings = settings
        self._dfp_logger = logger
        self._dfp_manifest: Dict[str, Any] = {}
        self._dfp_ready = False
        self._dfp_pixmap_cache: Dict[str, QPixmap] = {}
        self._dfp_movie_cache: Dict[str, "QMovie"] = {}
        self._dfp_last_error = ""
        # ★ v1.1.14：素材包（素材4 这类自带清单的包）缓存 —— 换目录/重载时清空
        self._dfp_pack_dirs_cache: Optional[List[str]] = None
        self._dfp_pack_voice_cache: Optional[List[Dict[str, Any]]] = None
        self._dfp_pack_char_cache: Optional[List[Dict[str, Any]]] = None
        # ★ v1.1.23：用户自己录的语音（`鲸宝音频\voice_<类别>_<序号>.*`）缓存
        self._dfp_user_voice_cache: Optional[List[Dict[str, Any]]] = None
        # ★ v1.1.35：台词形象的「轮着挑候选」计数（按台词类别记账，如 act_cute 的 9 张）
        self._dfp_face_cycle: Dict[str, int] = {}

    # --- 目录 ---
    def dfp_root(self) -> str:
        custom = str(self._dfp_settings.dfp_get("asset_root", "") or "").strip()
        if custom:
            return custom if os.path.isabs(custom) else os.path.join(dfp_app_dir(), custom)
        return os.path.join(dfp_app_dir(), DFP_ASSET_DIR_NAME)

    def dfp_manifest_path(self) -> str:
        return os.path.join(self.dfp_root(), DFP_ASSET_MANIFEST_NAME)

    def dfp_exists(self) -> bool:
        return os.path.isdir(self.dfp_root())

    def dfp_last_error(self) -> str:
        return self._dfp_last_error

    def _dfp_note_error(self, message: str) -> None:
        """记录素材相关错误（同时写日志），避免被兜底 except 静默吞掉。"""
        self._dfp_last_error = str(message)
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_warning("[素材] %s" % message)

    def dfp_is_ready(self) -> bool:
        return bool(self._dfp_ready and self.dfp_count() > 0)

    def dfp_count(self) -> int:
        counts = self._dfp_manifest.get("counts") or {}
        if counts:
            return int(sum(dfp_safe_int(value, 0) for value in counts.values()))
        return int(
            len(self._dfp_manifest.get("animations") or [])
            + len(self._dfp_manifest.get("expressions") or [])
            + len(self._dfp_manifest.get("characters") or [])
            + len(self._dfp_manifest.get("faces") or [])
            + len(self._dfp_manifest.get("voices") or [])
        )

    # --- 载入 ---
    def dfp_load(self, force: bool = False) -> bool:
        if self._dfp_ready and not force:
            return True
        self._dfp_clear_pack_cache()
        root = self.dfp_root()
        if not os.path.isdir(root):
            self._dfp_last_error = "素材目录不存在：%s" % root
            self._dfp_ready = False
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning(self._dfp_last_error)
            return False
        payload = dfp_read_json(self.dfp_manifest_path(), None)
        if not isinstance(payload, dict) or force:
            if bool(self._dfp_settings.dfp_get("asset_auto_scan", True)):
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_info("素材清单缺失或强制刷新，执行内置扫描…")
                payload = self._dfp_scan_builtin()
        if not isinstance(payload, dict):
            self._dfp_last_error = "素材清单不可用"
            self._dfp_ready = False
            return False
        self._dfp_manifest = payload
        self._dfp_ready = self.dfp_count() > 0
        self._dfp_last_error = "" if self._dfp_ready else "素材清单里没有任何形象素材"
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("素材已载入：%s" % self.dfp_summary())
            if self.dfp_pack_dirs():
                self._dfp_logger.dfp_info("%s" % self.dfp_pack_summary())
        try:
            self.dfpScanned.emit(self.dfp_count(), self.dfp_summary())
        except Exception:
            pass
        return self._dfp_ready

    def dfp_rebuild(self) -> bool:
        """重新扫描素材目录并写回清单（内置扫描器，不需要 pypinyin）。"""
        self._dfp_pixmap_cache.clear()
        self._dfp_movie_cache.clear()
        payload = self._dfp_scan_builtin()
        if not payload:
            return False
        self._dfp_ready = False
        return self.dfp_load(force=True)

    def dfp_reload(self) -> bool:
        """按当前设置重新载入素材目录（换了目录时用）。

        ★ 关键差别：**清单优先**。目录里有清单就直接用，只有缺清单 / 清单损坏
          且设置允许自动扫描时才兜底扫描生成。
          绝不能用 `dfp_rebuild()` 代替它：内置兜底扫描是「按文件名关键字分类」的
          应急方案，会把 pypinyin 生成的正规清单整份重写 —— 实测把表情从 9 条写成
          16 条（静态 png 与动画 webp 各一条、memes/pic 的表情包与光标图也被算进来），
          桌宠的表情就退化成表情包和鼠标光标了。
        """
        self._dfp_ready = False
        self._dfp_manifest = {}
        self._dfp_pixmap_cache.clear()
        self._dfp_movie_cache.clear()
        return self.dfp_load()

    def _dfp_scan_builtin(self) -> Dict[str, Any]:
        """兜底扫描：按文件名关键字分类。

        ★ 合并不覆盖：旧清单里已经有的条目（连着中文名、帧数、尺寸、分类这些
        靠文件名推不出来的元数据）原样保留，只把「新出现的文件」补进来。
        这样「重新扫描素材表」不会把 pypinyin 生成的中文名降级成文件名，
        用户往「大肥鱼素材表」里丢新素材后点一下重新扫描就是增量更新。
        """
        root = self.dfp_root()
        old_manifest = dfp_read_json(self.dfp_manifest_path(), None)
        if not isinstance(old_manifest, dict):
            old_manifest = {}
        old_by_file: Dict[str, Dict[str, Any]] = {}
        old_by_name: Dict[str, Dict[str, Any]] = {}
        old_section: Dict[str, str] = {}
        for section in ("characters", "expressions", "animations", "faces", "voices"):
            for item in old_manifest.get(section) or []:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("file") or item.get("gif") or "").replace("\\", "/").lower()
                if key:
                    old_by_file[key] = item
                    old_section.setdefault(key, section)
                    old_by_name.setdefault(os.path.basename(key), item)
        old_labels: Dict[str, str] = {}
        for item in old_manifest.get("animations") or []:
            stem = str(item.get("id") or "")
            label = str(item.get("label") or "")
            if stem and label:
                old_labels[stem] = label
        animations: List[Dict[str, Any]] = []
        expressions: List[Dict[str, Any]] = []
        characters: List[Dict[str, Any]] = []
        voices: List[Dict[str, Any]] = []
        # ★ v1.1.35：新形象（`鲸宝形象\类别\`）—— 按「不带编号的 id」配对 PNG + GIF
        faces_by_id: Dict[str, Dict[str, Any]] = {}
        face_order: List[str] = []
        saw_any = False
        # ★ v1.1.32：要扇两个家 —— 素材表（第三方素材包都在里面）+「形象图自己的家」`鲸宝形象\`。
        #   第二个家的相对路径带 `鲸宝形象/` 前缀（解析时会在程序目录 / 数据目录下找）。
        scan_roots: List[Tuple[str, str]] = [(root, "")]
        for folder in dfp_character_dirs():
            scan_roots.append((folder, DFP_CHARACTER_DIR_NAME))

        def keep_old(relative: str, fallback: Dict[str, Any], path_fields: Tuple[str, ...] = ("file", "gif")) -> Dict[str, Any]:
            """旧清单里有同名文件就整个沿用（保住中文名/帧数/尺寸/分类）。

            ★ v1.1.32：**换了目录也算同一个文件**（相对路径对不上时按文件名再认一次），
              否则形象图从 `素材2\assets\` 搬进 `鲸宝形象\` 之后，重扫一次就把
              「眨眼 / 三球抛接」这些中文名和帧数/分类降级成文件名了。
              认领到的旧条目里的路径要换成**当前这个**（文件大小/位置真变了）。
            ★ v1.1.36：`path_fields` 说清楚「现在处理的是哪个文件」，只更新它对应的那个字段。
              以前 PNG 与 GIF **两个字段一起写成当前文件**，于是 `13_love_heart.gif` 配对完
              又被同名 `.png` 覆盖成 `gif: …png` —— 清单里的形象图全变成静态图，
              桌宠不呼吸也不动了（用户 2026-10-03 截图那会儿清单就是这个状态）。
            """
            key = str(relative).replace("\\", "/").lower()
            known = old_by_file.get(key) or old_by_name.get(os.path.basename(key))
            if not known:
                return fallback
            entry = dict(known)
            for field in path_fields:
                if field in entry:
                    entry[field] = relative
            return entry

        def _dfp_dedup_by_id(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            """同一个 id 只留一条。

            ★ 为什么必须去重：一个表情可能同时存在静态 `pet_heart.png` 与动画
              `pet_heart.webp`，旧扫描会把两条都塞进清单（16 条 / 10 个 id），
              于是 `dfp_summary()`（数条目）与 `dfp_expressions()`（按 id 去重）
              对不上，而且 `dfp_expression("heart")` 会取到静态图 ——
              桌宠会拿表情包/光标图当表情用。
            优先级：① 旧清单里认得的那条（人工/拼音生成的中文名）＞ ② 会动的格式。
            """
            picked: Dict[str, Dict[str, Any]] = {}
            scores: Dict[str, Tuple[int, int]] = {}
            order: List[str] = []
            for item in items:
                key = str(item.get("id") or "")
                if not key:
                    continue
                relative = str(item.get("file") or item.get("gif") or "").replace("\\", "/").lower()
                score = (
                    1 if relative in old_by_file else 0,
                    1 if os.path.splitext(relative)[1] in (".webp", ".gif") else 0,
                )
                if key not in picked:
                    order.append(key)
                    picked[key] = item
                    scores[key] = score
                elif score > scores[key]:
                    picked[key] = item
                    scores[key] = score
            return [picked[key] for key in order]

        for current, dirs, files in _dfp_walk_image_roots(scan_roots):
            # ★ memes / pic 是素材包里的表情包与光标图，不是桌宠的表情状态；
            #   以前把它们当表情扫进来 ⇒ 桌宠会显示一张表情包或鼠标光标。
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith((".", DFP_ASSET_SKIP_PREFIX))
                and d != "__pycache__"
                and d.lower() not in DFP_ASSET_IGNORED_DIRS
                and not any(word in d for word in DFP_ASSET_IGNORED_DIR_WORDS)
            ]
            # 当前目录属于哪个根：决定相对路径相对谁算、要不要加 `鲸宝形象/` 前缀
            scan_root, prefix = scan_roots[0]
            for _cand, _cand_prefix in scan_roots:
                if current == _cand or current.startswith(_cand + os.sep):
                    scan_root, prefix = _cand, _cand_prefix
                    break
            for name in sorted(files):
                path = os.path.join(current, name)
                stem, ext = os.path.splitext(name)
                ext = ext.lower()
                relative = dfp_relative_or_abs(path, scan_root)
                if prefix and not os.path.isabs(relative):
                    relative = "%s/%s" % (prefix, str(relative).replace("\\", "/"))
                # ★ v1.1.35：`鲸宝形象\类别\` —— 目录说了算：里面的一律是「台词形象」。
                #   ★ 这一支必须放在下面「旧清单里认得这个文件就放回老那一类」**之前**：
                #     否则同一张图的 PNG 与 GIF 会各自被放回老条目 ⇒ 同一个 id 出现两条。
                #   配对规则：`13_love_heart.png` + `13_love_heart.gif` ⇒ id `love_heart`。
                if os.path.basename(current) == DFP_CHARACTER_SAY_DIR and ext in DFP_ASSET_SPRITE_EXT + (".gif", ".webp"):
                    face_id = dfp_face_key(stem)
                    item = faces_by_id.get(face_id)
                    if item is None:
                        item = {
                            "id": face_id,
                            "label": dfp_face_label(face_id),
                            "face_of": list(dfp_face_categories(face_id)),
                            "category": dfp_face_group(face_id),
                            "source": DFP_CHARACTER_SAY_DIR,
                            "section": "faces",
                        }
                        faces_by_id[face_id] = item
                        face_order.append(face_id)
                    if ext in DFP_ASSET_MOVIE_EXT:
                        item["gif"] = relative
                        item.setdefault("file", relative)
                    else:
                        item["file"] = relative
                    # ★ v1.1.36：与旧条目合并时**以这次配对为准**（file = PNG、gif = GIF），
                    #   只把推不出来的元数据（帧数 / 尺寸 / 备注 / 中文名）从旧清单抄回来。
                    #   要是不管青红皁白沿用旧条目，上一次那个错误的 `gif: …png` 会一直留在清单里。
                    entry = dict(item)
                    known_face = old_by_file.get(str(relative).replace("\\", "/").lower()) or old_by_name.get(os.path.basename(relative))
                    if isinstance(known_face, dict):
                        for field in ("label", "frames", "size", "note", "category", "face_of", "source"):
                            value = known_face.get(field)
                            if value not in (None, "", [], {}):
                                entry[field] = value
                    entry["id"] = face_id
                    entry["label"] = str(entry.get("label") or dfp_face_label(face_id))
                    entry["face_of"] = list(entry.get("face_of") or dfp_face_categories(face_id))
                    entry["category"] = str(entry.get("category") or dfp_face_group(face_id))
                    entry["source"] = str(entry.get("source") or DFP_CHARACTER_SAY_DIR)
                    faces_by_id[face_id] = entry
                    saw_any = True
                    continue
                # 0) 旧清单里认得这个文件的：原样放回它原来那一类（中文名/帧数都不丢）
                known_relative = str(relative).replace("\\", "/").lower()
                known_section_name = old_section.get(known_relative, "")
                if known_section_name in ("characters", "expressions", "animations", "voices"):
                    bucket = {
                        "characters": characters,
                        "expressions": expressions,
                        "animations": animations,
                        "voices": voices,
                    }[known_section_name]
                    bucket.append(dict(old_by_file[known_relative]))
                    saw_any = True
                    continue
                if ext in DFP_ASSET_AUDIO_EXT:
                    # ★ v1.1.36：素材表里的音频**不再扫进清单**。
                    #   理由：① 素材包自带的那批（`素材4\assets\voices\…`）是**运行时**由
                    #   `dfp_pack_voices()` 合并的（v1.1.24 起这么定的）；② 本机语音归
                    #   `鲸宝音频\`（v1.1.30 搬完那 14 条）。再扫一遍只会把包语音写成
                    #   「本机语音 93 条」（实测就是这么把语音那一项弄脏的），所以直接跳过。
                    continue
                if ext not in DFP_ASSET_SPRITE_EXT and ext not in DFP_ASSET_MOVIE_EXT:
                    continue
                stem_lower = stem.lower()
                # ★ v1.1.34：**先看它在哪个子目录**（`鲸宝形象\{立绘,表情,动作}\` 是权威布局）。
                #   纯按文件名拼音关键字猜是应急方案，碰上 `xiaotiqin-yanzou`（含 "xiao"）、
                #   `haqian-liantian`（含 "haqian"）这类动作名就会认错 —— 实测 106 个动作里有 9 个
                #   被猜成表情、又跟真表情同 id 去重掉了（smile / yawn / sleepy / wave），
                #   兜底扫描只剩 97 个动作；立绘也少 1 张（`pet_sleepy_f0.png` 被当成 sleepy 表情）。
                #   ⇒ 目录说了算：动作目录里的一律动作片、表情目录里的一律表情、立绘目录里的一律立绘。
                folder_name = os.path.basename(current)
                in_anim_dir = folder_name == DFP_CHARACTER_ANIM_DIR
                in_expr_dir = folder_name == DFP_CHARACTER_EXPR_DIR
                in_face_dir = folder_name == DFP_CHARACTER_FACE_DIR
                # 1) 表情/状态类（在表情目录里，或文件名含关键字）
                #    注意：base 的关键字里有 "pet"，它会把 pet_heart / pet_wave / pet_sleepy
                #    这些都抢成「立绘」⇒ 匹配具体表情时跳过 base，让 pet_* 正常归到表情。
                matched_expression = ""
                for key, keywords in DFP_ASSET_EXPRESSION_KEYWORDS.items():
                    if key == "base":
                        continue
                    if any(keyword in stem_lower for keyword in keywords):
                        matched_expression = key
                        break
                # ★ v1.1.34：表情现在是 GIF（新设计的 9 个都是 `pet_*.gif`）⇒ 一起认，
                #   否则它们会掉到下面的「动作动画」分支去（.gif 在 MOVIE_EXT 里）
                if not in_anim_dir and not in_face_dir and (matched_expression or in_expr_dir) and ext in DFP_ASSET_SPRITE_EXT + (".webp", ".gif"):
                    expressions.append(keep_old(relative, {"id": matched_expression or stem, "label": stem, "file": relative}))
                    saw_any = True
                    continue
                if (
                    not in_anim_dir
                    and not in_expr_dir
                    and (in_face_dir or (not matched_expression and stem_lower.startswith(("pet", "main", "base", "立绘", "形象"))))
                    and ext in DFP_ASSET_SPRITE_EXT
                ):
                    characters.append(keep_old(relative, {"id": stem, "label": old_labels.get(stem, stem), "file": relative}))
                    saw_any = True
                    continue
                # 2) 动作动画
                #   ★ v1.1.34：`动作\` 目录里的**单帧 PNG** 也算动作片（新设计的 106 个动作是静帧；
                #   桌宠会把它们当「定格造型」演几秒）。只看「在动作目录里」这一个条件 ——
                #   立绘/表情目录里的 PNG 不能被当成动作片。
                if ext in DFP_ASSET_MOVIE_EXT or (in_anim_dir and ext in DFP_ASSET_SPRITE_EXT):
                    category = "其他"
                    for name_key, keywords in DFP_ASSET_CATEGORY_KEYWORDS.items():
                        if any(keyword in stem_lower for keyword in keywords):
                            category = name_key
                            break
                    animations.append(
                        keep_old(
                            relative,
                            {
                                "id": stem,
                                "label": old_labels.get(stem, stem),
                                "category": category,
                                "gif": relative,
                                "frames": 1 if ext == ".png" else 0,
                                "size": [0, 0],
                            },
                        )
                    )
                    saw_any = True
        if not saw_any:
            return {}
        # ★ 同名 id 只留一条（静态 png 与动画 webp 同名时必须留动画那条），
        #   否则清单条目数与按 id 去重后的真实数据对不上，表情还会退化成静态表情包。
        characters = _dfp_dedup_by_id(characters)
        expressions = _dfp_dedup_by_id(expressions)
        animations = _dfp_dedup_by_id(animations)
        # ★ v1.1.36：3 张基础形象（正面 / 背面 / 侧面）就住在 `类别\` 里 ⇒ 除了进 `faces`，
        #   还得出现在 `characters` —— 设置里那个「基础形象」下拉读的就是这一段。
        #   以前重扫一次 characters 就空了（它们全被归到 faces），用户会以为形象没了。
        bases: List[Dict[str, Any]] = []
        for base_id in DFP_BASE_FACE_IDS:
            entry = faces_by_id.get(str(base_id))
            if not entry:
                continue
            item = dict(entry)
            item.pop("section", None)
            bases.append(item)
        if bases:
            base_ids = {str(item.get("id") or "") for item in bases}
            characters = bases + [item for item in characters if str(item.get("id") or "") not in base_ids]
        # ★ v1.1.36：语音这一项**沿用旧清单**（本机语音归 `鲸宝音频\`，素材表里只有包语音，
        #   而包语音是运行时合并的）—— 不再把包语音写进清单。
        voices = [dict(item) for item in (old_manifest.get("voices") or []) if isinstance(item, dict)]
        faces = [faces_by_id[key] for key in face_order]
        payload: Dict[str, Any] = dict(old_manifest)
        payload.update(
            {
                "manifest_version": dfp_safe_int(old_manifest.get("manifest_version"), 1) or 1,
                "generated_at": dfp_now_ts(),
                "asset_root": dfp_relative_or_abs(root, dfp_app_dir()),
                "source_project": str(old_manifest.get("source_project") or "内置兜底扫描（未使用 pypinyin，中文名请用 _scaffold\\build_asset_manifest.py 生成）"),
                "characters": characters,
                "expressions": expressions,
                "animations": animations,
                "faces": faces,
                "voices": voices,
                "counts": {
                    "characters": len(characters),
                    "expressions": len(expressions),
                    "animations": len(animations),
                    "faces": len(faces),
                    "voices": len(voices),
                },
            }
        )
        payload.setdefault("memes", old_manifest.get("memes") or [])
        payload.setdefault("icons", old_manifest.get("icons") or [])
        payload.setdefault("prompt_headings", old_manifest.get("prompt_headings") or [])
        try:
            dfp_atomic_write_json(self.dfp_manifest_path(), payload)
        except Exception:
            pass
        return payload

    # --- 查询 ---
    def dfp_summary(self) -> str:
        counts = self._dfp_manifest.get("counts") or {}
        return "形象 %d · 形象图 %d · 表情 %d · 动作 %d · 语音 %d" % (
            dfp_safe_int(counts.get("characters"), len(self._dfp_manifest.get("characters") or [])),
            dfp_safe_int(counts.get("faces"), len(self._dfp_manifest.get("faces") or [])),
            dfp_safe_int(counts.get("expressions"), len(self._dfp_manifest.get("expressions") or [])),
            dfp_safe_int(counts.get("animations"), len(self._dfp_manifest.get("animations") or [])),
            dfp_safe_int(counts.get("voices"), len(self._dfp_manifest.get("voices") or [])),
        )

    def dfp_character(self) -> Optional[Dict[str, Any]]:
        """当前该画的那张「基础形象」：设置里指定了 `asset_character` 就用它，否则用第一张。

        ★ v1.1.35：候选就是新设计的 3 张基础形象（正面/背面/侧面）；
        设置里存的旧 id（如 `pet_pajama`）认不出来时自动回第一张。
        """
        items = self.dfp_base_faces()
        if not items:
            return None
        wanted = str(self._dfp_settings.dfp_get("asset_character", "") or "").strip()
        if wanted:
            for item in items:
                if str(item.get("id") or "") == wanted:
                    return dict(item)
        return dict(items[0])

    def dfp_characters(self) -> List[Dict[str, Any]]:
        """可选形象：清单的 `characters` + 素材包里的形象（顺序：清单在前）。

        ★ v1.1.35：形象图统一到 `鲸宝形象\类别\` 之后，清单的 `characters` 就是
        **3 张基础形象**（正面待机 / 背面 / 侧面）；旧那 12 张换装立绘已退役。
        """
        items = [dict(item) for item in (self._dfp_manifest.get("characters") or [])]
        items.extend(self.dfp_pack_characters())
        return items

    def dfp_character_by_id(self, character_id: str) -> Optional[Dict[str, Any]]:
        """按 id 取一张形象（找不到返回 None）。

        `dfp_character()` 读的是设置里选的那张；这个函数拿的是**指定**那一张
        （`base_idle` / `base_back` / `base_left` …），诊断与测试用。
        """
        wanted = str(character_id or "").strip()
        if not wanted:
            return None
        for item in self.dfp_characters():
            if str(item.get("id") or "") == wanted:
                return dict(item)
        return None

    # --- ★ v1.1.14：素材包（素材4 这类自带清单的形象包）---
    def _dfp_clear_pack_cache(self) -> None:
        self._dfp_pack_dirs_cache = None
        self._dfp_pack_voice_cache = None
        self._dfp_pack_char_cache = None
        # ★ v1.1.23：用户录音目录也可能刚丢进新文件，跟着一起重扫
        self._dfp_user_voice_cache = None
        # ★ v1.1.15：形态图是渲染好的位图，换了素材目录/重扫之后必须重画
        _DFP_FORM_PIXMAP_CACHE.clear()

    def dfp_pack_dirs(self) -> List[str]:
        """素材表根目录下「自带语音清单 / 立绘目录」的子目录名（如 `素材4`、`素材6`）。

        ★ v1.1.36：不再把「目录里有形态 SVG」当成素材包 —— 那套（素材5）已经退役，
          里面的旧鲸鱼 SVG 也搬进 `_退役_forms\` 了。
        """
        if self._dfp_pack_dirs_cache is not None:
            return list(self._dfp_pack_dirs_cache)
        found: List[str] = []
        root = self.dfp_root()
        try:
            if os.path.isdir(root):
                for name in sorted(os.listdir(root)):
                    folder = os.path.join(root, name)
                    if not os.path.isdir(folder):
                        continue
                    if name.startswith(DFP_ASSET_SKIP_PREFIX):
                        continue  # ★ 下划线开头＝退役/源稿区，不是包
                    has_manifest = os.path.isfile(os.path.join(folder, DFP_ASSET_PACK_VOICE_MANIFEST))
                    has_skins = os.path.isdir(os.path.join(folder, DFP_ASSET_PACK_SKIN_DIR))
                    if has_manifest or has_skins:
                        found.append(name)
        except Exception as exc:
            self._dfp_note_error("扫描素材包目录失败：%s: %s" % (type(exc).__name__, exc))
        self._dfp_pack_dirs_cache = found
        if found and self._dfp_logger is not None:
            self._dfp_logger.dfp_info("发现素材包：%s" % "、".join(found))
        return list(found)

    def dfp_pack_voices(self) -> List[Dict[str, Any]]:
        """素材包里的语音：包清单的 `lines[]`（含台词）+ `notice-*.wav` 提示音。"""
        if self._dfp_pack_voice_cache is not None:
            return [dict(item) for item in self._dfp_pack_voice_cache]
        items: List[Dict[str, Any]] = []
        for pack in self.dfp_pack_dirs():
            base = os.path.join(self.dfp_root(), pack)
            voice_dir = os.path.join(base, DFP_ASSET_PACK_VOICE_DIR)
            manifest_path = os.path.join(base, DFP_ASSET_PACK_VOICE_MANIFEST)
            payload = dfp_read_json(manifest_path, None)
            if isinstance(payload, dict):
                for row in payload.get("lines") or []:
                    if not isinstance(row, dict):
                        continue
                    rel = str(row.get("file") or "").replace("\\", "/").strip()
                    kind = str(row.get("kind") or "").strip().lower()
                    if not rel or not kind:
                        continue
                    full = "%s/assets/%s" % (pack, rel)
                    if not os.path.isfile(self.dfp_abs(full)):
                        self._dfp_note_error("素材包 %s 的语音文件不存在：%s" % (pack, rel))
                        continue
                    entry: Dict[str, Any] = {
                        "category": DFP_ASSET_PACK_KIND_CATEGORY.get(kind, kind),
                        "label": "素材包 %s · %s" % (pack, str(row.get("id", "")).strip()),
                        "file": full,
                        "text": str(row.get("text") or ""),
                        "kind": kind,
                        "source": "pack:%s" % pack,
                    }
                    # 包清单里可选的速度 / 增益 / 语气说明（line-16 就带了 speed/gain）
                    for extra in ("speed", "gain", "instruct"):
                        value = row.get(extra)
                        if value not in (None, ""):
                            entry[extra] = value
                    items.append(entry)
            elif os.path.isdir(voice_dir):
                # 清单不在也照样能用提示音（只是没有台词）
                self._dfp_note_error("素材包 %s 缺 %s（只收提示音）" % (pack, DFP_ASSET_PACK_VOICE_MANIFEST))
            # 提示音：notice-<status>[-N].wav → 类别 notice_<status>
            try:
                names = sorted(os.listdir(voice_dir)) if os.path.isdir(voice_dir) else []
            except Exception as exc:
                self._dfp_note_error("读素材包语音目录失败（%s）：%s: %s" % (pack, type(exc).__name__, exc))
                names = []
            for name in names:
                stem, ext = os.path.splitext(name)
                low = stem.lower()
                if ext.lower() not in DFP_ASSET_AUDIO_EXT or not low.startswith(DFP_ASSET_PACK_NOTICE_PREFIX):
                    continue
                status = low[len(DFP_ASSET_PACK_NOTICE_PREFIX):].split("-")[0].strip()
                if not status:
                    continue
                items.append(
                    {
                        "category": "%s%s" % (DFP_ASSET_PACK_NOTICE_CATEGORY_PREFIX, status),
                        "label": "提示音 · %s" % (DFP_ASSET_PACK_NOTICE_LABELS.get(status, status)),
                        "file": "%s/%s/%s" % (pack, DFP_ASSET_PACK_VOICE_DIR.replace("\\", "/"), name),
                        "text": "",
                        "kind": "notice",
                        "status": status,
                        "source": "pack:%s" % pack,
                    }
                )
        self._dfp_pack_voice_cache = items
        return [dict(item) for item in items]

    def dfp_pack_characters(self) -> List[Dict[str, Any]]:
        """素材包 `assets/skins` 里的立绘（可以当普通形象用，换形象就靠它们）。"""
        if self._dfp_pack_char_cache is not None:
            return [dict(item) for item in self._dfp_pack_char_cache]
        items: List[Dict[str, Any]] = []
        for pack in self.dfp_pack_dirs():
            skin_dir = os.path.join(self.dfp_root(), pack, DFP_ASSET_PACK_SKIN_DIR)
            try:
                names = sorted(os.listdir(skin_dir)) if os.path.isdir(skin_dir) else []
            except Exception as exc:
                self._dfp_note_error("读素材包立绘目录失败（%s）：%s: %s" % (pack, type(exc).__name__, exc))
                names = []
            for name in names:
                stem, ext = os.path.splitext(name)
                if ext.lower() not in DFP_ASSET_SPRITE_EXT:
                    continue
                items.append(
                    {
                        "id": "pack:%s/%s" % (pack, stem),
                        "label": "%s · %s" % (pack, stem),
                        "file": "%s/%s/%s" % (pack, DFP_ASSET_PACK_SKIN_DIR.replace("\\", "/"), name),
                        "source": "pack:%s" % pack,
                    }
                )
        self._dfp_pack_char_cache = items
        return [dict(item) for item in items]

    def dfp_pack_summary(self) -> str:
        """一行摘要：`素材包：素材4（3 形象 · 43 条语音）`（没有包就说没有）。"""
        packs = self.dfp_pack_dirs()
        if not packs:
            return "素材包：无"
        return "素材包：%s（%d 形象 · %d 条语音）" % (
            "、".join(packs),
            len(self.dfp_pack_characters()),
            len(self.dfp_pack_voices()),
        )

    # --- ★ v1.1.36：状态形象（睡觉 / 忙 / 等 / 休息 / 庆祝 / 出错）---
    #   ★ 以前这里是「读素材包里的形态 SVG」（素材5 那 7 张旧鲸鱼）；现在改成
    #     **从新形象集里取对应的那张图**（`DFP_FORM_FACE_MAP`），旧 SVG 不再被扫描与渲染。
    def dfp_form_sources(self) -> List[str]:
        """状态形象的图源（v1.1.36 起只有一处：新形象集 `鲸宝形象\类别\`）。"""
        return ["形象图"] if self.dfp_forms() else []

    def dfp_forms(self) -> List[Dict[str, Any]]:
        """所有状态形象：`[{state, label, when, face, file, source}]`（按 `DFP_FORM_LABELS` 顺序）。

        库里缺那张图的状态会被跳过（不会留下「只有半个状态」的情况）。
        """
        items: List[Dict[str, Any]] = []
        for state in DFP_FORM_LABELS:
            face_id = self.dfp_form_face(state)
            if not face_id:
                continue
            face = self.dfp_face(face_id) or {}
            items.append(
                {
                    "state": state,
                    "label": DFP_FORM_LABELS.get(state, state),
                    "when": DFP_FORM_WHEN.get(state, ""),
                    "face": face_id,
                    "face_label": dfp_face_label(face_id),
                    "file": str(face.get("file") or ""),
                    "source": "face:%s" % face_id,
                    "known": True,
                }
            )
        return items

    def dfp_form_face(self, state: str) -> str:
        """状态 → 该画哪张**新形象图** id（认不出 / 库里没这张图都返回空串）。"""
        wanted = str(state or "").strip().lower()
        if not wanted:
            return ""
        face_id = DFP_FORM_FACE_MAP.get(wanted, "")
        if not face_id:
            return ""
        return face_id if self.dfp_face(face_id) else ""

    def dfp_form(self, state: str) -> Optional[Dict[str, Any]]:
        """取一个状态形象（认不出返回 None）。"""
        wanted = str(state or "").strip().lower()
        if not wanted:
            return None
        for item in self.dfp_forms():
            if str(item.get("state")) == wanted:
                return dict(item)
        return None

    def dfp_form_path(self, state: str) -> str:
        item = self.dfp_form(state)
        if not item:
            return ""
        return self.dfp_abs(str(item.get("file") or ""))

    def dfp_form_pixmap(self, state: str, height: int = 512) -> QPixmap:
        """取一张状态形象的预览图（= 新形象集里那张 PNG；取不到返回空 QPixmap）。

        ★ v1.1.35 之前这里渲染的是素材5 的 SVG（旧鲸鱼）；现在预览与桌宠上看到的**是同一张图**。
        """
        item = self.dfp_form(state)
        if not item:
            return QPixmap()
        key = "%s@%d" % (str(item.get("state")), int(height))
        cached = _DFP_FORM_PIXMAP_CACHE.get(key)
        if cached is not None:
            return cached
        source = self.dfp_pixmap(str(item.get("file") or ""))
        if source.isNull():
            return QPixmap()
        scaled = source.scaledToHeight(
            max(24, int(height)),
            dfp_enum_value(0, lambda: Qt.TransformationMode.SmoothTransformation, lambda: Qt.SmoothTransformation),
        )
        if scaled.isNull():
            return QPixmap()
        _DFP_FORM_PIXMAP_CACHE[key] = scaled
        return scaled

    def dfp_form_summary(self) -> str:
        """一行摘要：`状态形象：6 种（忙碌、等待、休息…）· 新形象集`（没有就说没有）。"""
        items = self.dfp_forms()
        if not items:
            return "状态形象：无"
        names = "、".join(str(item.get("label") or item.get("state")) for item in items[:6])
        more = "" if len(items) <= 6 else " 等 %d 种" % len(items)
        return "状态形象：%d 种（%s%s）· 新形象集" % (len(items), names, more)

    def dfp_character_label(self, item: Optional[Dict[str, Any]]) -> str:
        """形象的显示名（界面与提示用）。"""
        if not isinstance(item, dict):
            return "—"
        label = str(item.get("label") or "").strip()
        return label or str(item.get("id") or os.path.basename(str(item.get("file") or "")))

    def dfp_expressions(self) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for item in self._dfp_manifest.get("expressions") or []:
            key = str(item.get("id") or "")
            if key and key not in result:
                result[key] = dict(item)
        return result

    def dfp_expression(self, key: str) -> Optional[Dict[str, Any]]:
        return self.dfp_expressions().get(str(key))

    # --- ★ v1.1.35：台词形象（`鲸宝形象\类别\` 那 78 张，一张图 = 一个完整的新形象）---
    def dfp_faces(self) -> Dict[str, Dict[str, Any]]:
        """所有「形象图」：`{id: item}`（清单的 `faces` 段；id 就是文件名去掉前导编号）。"""
        result: Dict[str, Dict[str, Any]] = {}
        for item in self._dfp_manifest.get("faces") or []:
            key = str(item.get("id") or "")
            if key and key not in result:
                result[key] = dict(item)
        return result

    def dfp_face(self, key: str) -> Optional[Dict[str, Any]]:
        """按 id 取一张形象图（认不出返回 None）。"""
        wanted = str(key or "").strip()
        if not wanted:
            return None
        return self.dfp_faces().get(wanted)

    def dfp_base_faces(self) -> List[Dict[str, Any]]:
        """3 张「基础形象」（多视角待机）；清单里只认得其中几张也照样能用。"""
        faces = self.dfp_faces()
        found = [faces[key] for key in DFP_BASE_FACE_IDS if key in faces]
        if found:
            return found
        # 兼容：清单里没有 `faces` 段时，退到 characters（老清单 / 兜底扫描的产物）
        return self.dfp_characters()

    def dfp_face_for_category(self, category: str) -> Optional[Dict[str, Any]]:
        """台词类别 → 一张形象图（**轮着挑候选**，同一类连着说不会老长一个样）。

        ★ 轮换计数按类别记在本对象上：同一次运行里轮流用，重启后重新从头数。
        """
        items = dfp_say_faces(category)
        if not items:
            return None
        name = str(category).strip()
        seen = self._dfp_face_cycle.get(name, 0)
        for offset in range(len(items)):
            face_id = items[(seen + offset) % len(items)]
            item = self.dfp_face(face_id)
            if item:
                self._dfp_face_cycle[name] = (seen + offset + 1) % len(items)
                return item
        return None

    def dfp_animations(self, category: str = "", seasonal: bool = True) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for item in self._dfp_manifest.get("animations") or []:
            if category and str(item.get("category") or "") != category:
                continue
            if seasonal and not dfp_seasonal_ok(str(item.get("label") or "")):
                continue
            items.append(dict(item))
        # ★ v1.1.35：老的 106 个「动作片」随形象一起退役了（图片全在 `_退役\`）——
        #   「演一个动作 / 随机动作」这两个功能**不能因此停摆** ⇒ 没动作片时改用
        #   新的 78 张形象图（形状一样：都有 id/ label/ gif/ frames/ size），
        #   效果就是「随机换一个样子演一下」。
        if not items and not self._dfp_manifest.get("animations"):
            items = [dict(item) for item in self.dfp_faces().values()]
            if category:
                items = [item for item in items if str(item.get("category") or "") == category]
        return items

    def dfp_categories(self) -> List[Tuple[str, int]]:
        counts: Dict[str, int] = {}
        for item in self.dfp_animations(seasonal=False):
            key = str(item.get("category") or "其他")
            counts[key] = counts.get(key, 0) + 1
        return sorted(counts.items(), key=lambda pair: -pair[1])

    def dfp_random_animation(
        self,
        categories: Optional[Sequence[str]] = None,
        avoid: Optional[Sequence[str]] = None,
        seasonal: bool = True,
    ) -> Optional[Dict[str, Any]]:
        pool = [item for item in self.dfp_animations(seasonal=seasonal) if not categories or str(item.get("category")) in categories]
        if not pool:
            pool = self.dfp_animations(seasonal=False)
        if not pool:
            return None
        avoided = set(avoid or ())
        fresh = [item for item in pool if str(item.get("id")) not in avoided]
        return dict(dfp_random_pick(fresh or pool, pool[0]))

    def dfp_voices(self, category: str = "") -> List[Dict[str, Any]]:
        """某一类语音（自己录的 + 清单里的 + 素材包里的，合池；不传类别就是全部）。

        ★ v1.1.23（统一音色）：**自己在「鲸宝音频」里录过的类别，素材包（素材4 / 素材6）同一类的
          声音就不再进池** —— 包里那些是别人配的音色，一起播就“一句一个声音”。
          ★ v1.1.30 补记：主人最早那 14 条参考音色（`voice_poke_1~4` 那批）**也搬进了「鲸宝音频」**，
          所以现在**语音只有一个家**：`鲸宝音频\`（提示音仍然照旧，它们是音效不是台词）。
          自己已录的类别在池里也排在前面（悬停清单更好读）。
        """
        mine = self.dfp_user_voices()
        my_categories = set(str(item.get("category") or "") for item in mine if str(item.get("category") or ""))
        # ★ v1.1.24：素材包自带的声音默认整个退役（`voice_use_pack` 默认 False）——
        #   用户明确要求「line-* / v_* / notice-* 先退休，我自己重录」。
        keep_pack = bool(self._dfp_settings.dfp_get("voice_use_pack", False))
        items: List[Dict[str, Any]] = []
        for item in mine:
            if category and str(item.get("category")) != str(category):
                continue
            items.append(dict(item))
        for item in list(self._dfp_manifest.get("voices") or []) + self.dfp_pack_voices():
            if category and str(item.get("category")) != str(category):
                continue
            from_pack = str(item.get("source") or "").startswith("pack:")
            if from_pack and (not keep_pack or str(item.get("category") or "") in my_categories):
                continue
            entry = dict(item)
            # ★ v1.1.23：清单里那几条补一个来源标记（以前是空的）——界面/探针一看就知道是你的
            if not str(entry.get("source") or "").strip():
                entry["source"] = DFP_ASSET_MANIFEST_NAME
            items.append(entry)
        return items

    def dfp_user_voices(self) -> List[Dict[str, Any]]:
        """★ v1.1.23：用户自己录在 `鲸宝音频\` 里的形象语音。

        · 文件名规则与素材表里那 14 条一样（那 14 条 v1.1.30 起也在这里）：`voice_poke_5.mp3` → 类别 `poke`；
        · **子目录随便分**（`鲸宝音频\poke\voice_poke_5.mp3` 也认）—— 递归找；
        · 文件名没有 `voice_` 前缀时，**把上一级目录名当类别**（`鲸宝音频\poke\5.mp3` 也算 poke）；
        · 数字读法那些（`1.mp3` / `合计.mp3` / `番.mp3`）一律忽略；
        · `text` 来自同目录的 `语音台词.json`（录音清单自动写的），没有就在界面上显示「你自己的录音」。
        """
        if self._dfp_user_voice_cache is not None:
            return [dict(item) for item in self._dfp_user_voice_cache]
        items: List[Dict[str, Any]] = []
        texts = dfp_user_voice_texts()
        for folder in dfp_user_voice_dirs():
            try:
                walker = os.walk(folder)
            except Exception as exc:
                self._dfp_note_error("读录音目录失败（%s）：%s: %s" % (folder, type(exc).__name__, exc))
                continue
            for current, dirs, files in walker:
                dirs[:] = [
                    name
                    for name in dirs
                    if not name.startswith((".", DFP_ASSET_SKIP_PREFIX)) and name != "__pycache__"
                ]
                sub = os.path.basename(current)
                for name in sorted(files):
                    path = os.path.join(current, name)
                    if not os.path.isfile(path):
                        continue
                    stem, ext = os.path.splitext(name)
                    if ext.lower() not in DFP_ASSET_AUDIO_EXT:
                        continue
                    category = ""
                    if stem.lower().startswith(DFP_USER_VOICE_PREFIX):
                        category = dfp_voice_category_from_name(stem)
                        if not category:
                            self._dfp_note_error("录音文件名看不懂（要 `voice_<类别>_<序号>.<后缀>`）：%s" % name)
                            continue
                    elif sub and sub != os.path.basename(folder) and dfp_safe_category_name(sub):
                        # 按类别分了子目录：`鲸宝音频\poke\…` → 类别就是文件夹名
                        category = sub
                    else:
                        continue
                    items.append(
                        {
                            "category": category,
                            "label": "我的录音 · %s" % stem,
                            "file": path,
                            "text": str(texts.get(name) or texts.get(stem) or ""),
                            "kind": category,
                            "source": DFP_USER_VOICE_SOURCE,
                        }
                    )
        self._dfp_user_voice_cache = items
        if items and self._dfp_logger is not None:
            self._dfp_logger.dfp_info(
                "发现你自己的录音 %d 条（%s）" % (len(items), "、".join(dfp_user_voice_dirs()))
            )
        return [dict(item) for item in items]

    def dfp_voice_categories(self) -> List[Tuple[str, int]]:
        """语音类别 → 条数（界面与日志用；含素材包带来的 idle / notice_* 类别）。"""
        counts: Dict[str, int] = {}
        for item in self.dfp_voices():
            key = str(item.get("category") or "").strip()
            if key:
                counts[key] = counts.get(key, 0) + 1
        return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))

    def dfp_voice_notice(self, status: str) -> Optional[Dict[str, Any]]:
        """按任务状态取一条提示音（completed / failed / interrupted / needs_help / low_balance / approval）。"""
        key = "%s%s" % (DFP_ASSET_PACK_NOTICE_CATEGORY_PREFIX, str(status or "").strip().lower())
        pool = self.dfp_voices(key)
        if not pool:
            return None
        return dict(dfp_random_pick(pool, pool[0]))

    def dfp_voice(self, category: str) -> Optional[Dict[str, Any]]:
        pool = self.dfp_voices(category)
        if not pool:
            return None
        return dict(dfp_random_pick(pool, pool[0]))

    def dfp_memes(self) -> List[str]:
        return [str(item) for item in (self._dfp_manifest.get("memes") or [])]

    def dfp_abs(self, relative: str) -> str:
        """相对路径 → 绝对路径。

        ★ v1.1.32：形象图有了自己的家（`鲸宝形象\`）⇒ 依次在
          **素材表 → 程序目录 → 数据目录** 下找，第一个真的存在的就算；
          三处都没有时返回素材表那份（报错信息与以前一致，不会变成空串）。
          绝对路径原样返回（素材包与临时测试用得上）。
        """
        text = str(relative or "")
        if not text:
            return ""
        if os.path.isabs(text):
            return text
        rel = text.replace("/", os.sep)
        first = os.path.join(self.dfp_root(), rel)
        if os.path.exists(first):
            return first
        candidates: List[str] = []
        parts = rel.split(os.sep)
        if parts and parts[0] == DFP_CHARACTER_DIR_NAME and len(parts) > 1:
            # ★ v1.1.32：形象图在自己的家里 ⇒ 一律按 `dfp_character_dirs()` 找
            #   （它才是「这个家在哪」的单一入口：程序目录 / 数据目录、以及测试里的替身目录）
            tail = os.path.join(*parts[1:])
            for folder in dfp_character_dirs():
                candidates.append(os.path.join(folder, tail))
        for base in (dfp_app_dir(), dfp_data_dir()):
            candidates.append(os.path.join(base, rel))
        for path in candidates:
            if os.path.exists(path):
                return path
        return first

    # --- 图片与动画 ---
    def dfp_pixmap(self, relative: str, size: Optional[QSize] = None) -> QPixmap:
        path = self.dfp_abs(relative)
        if not path or not os.path.isfile(path):
            return QPixmap()
        key = "%s|%s" % (path, size.toTuple() if isinstance(size, QSize) else "-")
        cached = self._dfp_pixmap_cache.get(key)
        if cached is not None:
            return cached
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return pixmap
        if isinstance(size, QSize) and size.isValid() and size.width() > 4:
            pixmap = pixmap.scaled(size, dfp_enum_value(0, lambda: Qt.AspectRatioMode.KeepAspectRatio, lambda: Qt.KeepAspectRatio), dfp_enum_value(0, lambda: Qt.TransformationMode.SmoothTransformation, lambda: Qt.SmoothTransformation))
        if len(self._dfp_pixmap_cache) > DFP_ASSET_MAX_CACHE:
            self._dfp_pixmap_cache.clear()
        self._dfp_pixmap_cache[key] = pixmap
        return pixmap

    def dfp_movie(self, relative: str) -> Optional["QMovie"]:
        path = self.dfp_abs(relative)
        if not path or not os.path.isfile(path):
            return None
        movie = self._dfp_movie_cache.get(path)
        if movie is not None:
            return movie
        try:
            movie = QMovie(path)
            movie.setCacheMode(QMovie.CacheAll)
            if not movie.isValid():
                self._dfp_note_error("动画无法播放：%s" % os.path.basename(path))
                return None
            if len(self._dfp_movie_cache) > 12:
                for key in list(self._dfp_movie_cache.keys())[:4]:
                    old = self._dfp_movie_cache.pop(key, None)
                    if old is not None:
                        try:
                            old.stop()
                        except Exception:
                            pass
            self._dfp_movie_cache[path] = movie
            return movie
        except Exception as exc:
            # 兜底失败必须留痕：曾经因为漏 import QMovie，NameError 被静默吞掉，
            # 表现为「动画永远不动」，排查很久（见 _scaffold/probe_movie_body.py）
            self._dfp_note_error("动画载入异常：%s: %s" % (type(exc).__name__, exc))
            return None

    def dfp_stats(self) -> Dict[str, Any]:
        counts = self._dfp_manifest.get("counts") or {}
        packs = self.dfp_pack_dirs()
        return {
            "root": self.dfp_root(),
            "manifest": self.dfp_manifest_path(),
            "manifest_exists": os.path.isfile(self.dfp_manifest_path()),
            "ready": self.dfp_is_ready(),
            "characters": dfp_safe_int(counts.get("characters"), 0),
            "faces": dfp_safe_int(counts.get("faces"), 0),
            "face_ids": sorted(self.dfp_faces()),
            "expressions": dfp_safe_int(counts.get("expressions"), 0),
            "animations": dfp_safe_int(counts.get("animations"), 0),
            "voices": dfp_safe_int(counts.get("voices"), 0),
            "memes": len(self._dfp_manifest.get("memes") or []),
            "icons": len(self._dfp_manifest.get("icons") or []),
            # ★ v1.1.32：形象图自己的家（`鲸宝形象\`；不在时是空表，界面/探针一看就知道）
            "character_dirs": dfp_character_dirs(),
            "categories": self.dfp_categories(),
            "source": str(self._dfp_manifest.get("source_project") or ""),
            "error": self._dfp_last_error,
            # ★ v1.1.14：素材包（素材4 这类）——不改上面那几个「清单口径」的数字，另开几个键
            "packs": packs,
            "pack_characters": len(self.dfp_pack_characters()),
            "pack_voices": len(self.dfp_pack_voices()),
            "characters_all": len(self.dfp_characters()),
            "voices_all": len(self.dfp_voices()),
            "voice_categories": self.dfp_voice_categories(),
            "pack_summary": self.dfp_pack_summary(),
            # ★ v1.1.36：状态形象（新形象集里的图）—— 同样不动清单口径
            "forms": len(self.dfp_forms()),
            "form_states": [str(item.get("state")) for item in self.dfp_forms()],
            "form_summary": self.dfp_form_summary(),
        }


# =============================================================================
#  十二、形象声音（mp3 走 QtMultimedia 的 FFmpeg 后端；没有就退到系统提示音）
# =============================================================================

_DFP_MULTIMEDIA: Dict[str, Any] = {}


def dfp_multimedia_available() -> bool:
    if "ok" not in _DFP_MULTIMEDIA:
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer  # noqa: F401

            _DFP_MULTIMEDIA["ok"] = True
        except Exception:
            _DFP_MULTIMEDIA["ok"] = False
    return bool(_DFP_MULTIMEDIA["ok"])


class DFPVoicePlayer(QObject):
    """播放素材目录里的形象声音（voice_poke_* / voice_confirm_* / voice_done_* / voice_ask_*）。"""

    def __init__(self, settings: DFPSettingsStore, library: Optional[DFPAssetLibrary] = None, logger: Optional[DFPLogger] = None):
        super().__init__()
        self._dfp_settings = settings
        self._dfp_library = library
        self._dfp_logger = logger
        self._dfp_player: Any = None
        self._dfp_output: Any = None
        self._dfp_play_count = 0
        self._dfp_last = ""
        self._dfp_last_text = ""

    def dfp_set_library(self, library: DFPAssetLibrary) -> None:
        self._dfp_library = library

    def dfp_play_count(self) -> int:
        return self._dfp_play_count

    def dfp_last_played(self) -> str:
        return self._dfp_last

    def dfp_last_text(self) -> str:
        """刚播的那条语音的台词（人工听写，没有就返回空串）。"""
        return self._dfp_last_text

    def dfp_library(self) -> Optional[DFPAssetLibrary]:
        return self._dfp_library

    def dfp_is_available(self) -> bool:
        if not bool(self._dfp_settings.dfp_get("voice_enabled", True)):
            return False
        if self._dfp_library is None or not self._dfp_library.dfp_is_ready():
            return False
        return bool(self._dfp_library.dfp_voices())

    def _dfp_ensure_player(self) -> Optional[Any]:
        if self._dfp_player is not None:
            return self._dfp_player
        if not dfp_multimedia_available():
            return None
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._dfp_player = QMediaPlayer()
            self._dfp_output = QAudioOutput()
            self._dfp_player.setAudioOutput(self._dfp_output)
            return self._dfp_player
        except Exception:
            self._dfp_player = None
            return None

    def dfp_set_volume(self, volume: float) -> None:
        try:
            if self._dfp_output is not None:
                self._dfp_output.setVolume(dfp_clamp(float(volume), 0.0, 1.0))
        except Exception:
            pass

    def dfp_play(self, category: str = "poke") -> Optional[str]:
        """按类别随机播一条语音；返回播放的文件名（没播成返回 None）。"""
        if not self.dfp_is_available():
            return None
        name = str(category or "poke")
        if name.startswith(DFP_ASSET_PACK_NOTICE_CATEGORY_PREFIX):
            # ★ v1.1.14：素材包里的任务状态提示音（notice_completed / notice_failed / …）
            if not bool(self._dfp_settings.dfp_get("voice_on_notice", True)):
                return None
        else:
            # ★ v1.1.33：闸门表挪到模块级 `DFP_VOICE_GATE_KEYS`（新增的 7 类情绪语音也在里面，
            #   以后加类别只改那一处；表里没有的类别照旧直接播）。
            guard = DFP_VOICE_GATE_KEYS.get(name, "")
            if guard and not bool(self._dfp_settings.dfp_get(guard, True)):
                return None
        item = self._dfp_library.dfp_voice(name)
        if not item:
            return None
        path = self._dfp_library.dfp_abs(str(item.get("file") or ""))
        if not path or not os.path.isfile(path):
            return None
        player = self._dfp_ensure_player()
        if player is None:
            return None
        try:
            from PySide6.QtCore import QUrl

            base_volume = dfp_safe_float(self._dfp_settings.dfp_get("voice_volume", 0.8), 0.8)
            gain = item.get("gain")
            if gain not in (None, ""):
                base_volume = base_volume * dfp_safe_float(gain, 1.0)
            self.dfp_set_volume(base_volume)
            player.stop()
            player.setSource(QUrl.fromLocalFile(path))
            # 包清单里可以给单条语音标 speed（line-16 就是 0.85：轻轻说）——每条都要复位
            speed = item.get("speed")
            try:
                player.setPlaybackRate(dfp_clamp(dfp_safe_float(speed, 1.0), 0.5, 2.0) if speed not in (None, "") else 1.0)
            except Exception:
                pass
            player.play()
            self._dfp_play_count += 1
            self._dfp_last = os.path.basename(path)
            self._dfp_last_text = str(item.get("text") or "")
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_debug("播放语音：%s（%s）" % (self._dfp_last, name))
            return self._dfp_last
        except Exception:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_exception("语音播放失败")
            return None

    def dfp_stop(self) -> None:
        try:
            if self._dfp_player is not None:
                self._dfp_player.stop()
        except Exception:
            pass


# =============================================================================
#  十四、渲染状态与画笔工具
# =============================================================================


@dataclass
class DFPRenderState:
    """一次绘制所需的全部信息（渲染器是纯函数式的：给状态就出图）。

    所有偏移量单位都是「画布像素」（画布 220×260），渲染器内部再缩放到目标矩形。
    """

    expression: Any = DFPExpression.NORMAL
    action: Any = DFPAction.IDLE
    action_progress: float = 0.0
    time: float = 0.0
    breathe: float = 0.0
    blink: float = 0.0
    tail_phase: float = 0.0
    walk_bob: float = 0.0
    squash: float = 0.0
    mouth_open: float = 0.0
    spout: float = 0.0
    heart: float = 0.0
    star: float = 0.0
    zzz: float = 0.0
    tilt: float = 0.0
    spin: float = 0.0
    stretch: float = 0.0
    asleep: bool = False
    speaking: bool = False
    flip: bool = False
    show_shadow: bool = True
    nametag: str = ""
    nametag_color: str = "#4D6BFE"
    palette: str = "ocean"


def dfp_no_pen() -> QPen:
    """无边框画笔。

    ★ 用 dfp_enum_value 逐个求解：绝不能让某个已废弃的枚举写法把结果污染成 None，
      否则 QPainter.setPen(None) 会在每次重绘时抛异常（Qt 只会打 stderr）。
    """
    style = dfp_enum_value(
        None,
        lambda: Qt.PenStyle.NoPen,  # type: ignore[attr-defined]
        lambda: Qt.NoPen,  # type: ignore[attr-defined]
    )
    pen = QPen()
    if style is not None:
        pen.setStyle(style)
    else:
        pen.setColor(QColor(0, 0, 0, 0))
    pen.setWidth(0)
    return pen


def dfp_line_pen(color: Any, width: float = 1.5) -> QPen:
    pen = QPen(dfp_color(color))
    pen.setWidthF(max(0.1, float(width)))
    pen.setCapStyle(dfp_enum_value(0, lambda: Qt.PenCapStyle.RoundCap, lambda: Qt.RoundCap))
    pen.setJoinStyle(dfp_enum_value(0, lambda: Qt.PenJoinStyle.RoundJoin, lambda: Qt.RoundJoin))
    pen.setStyle(dfp_enum_value(1, lambda: Qt.PenStyle.SolidLine, lambda: Qt.SolidLine))
    return pen


# =============================================================================
#  十五、形象渲染器：Q 版鲸鱼拟人少女（素材缺失时的兜底画风）
#     画布坐标系 220×260，脚底约在 y=244，所有部件位置都写死在这里，
#     改形象只需要调这些常量，不要动渲染流程。
# =============================================================================

DFP_HEAD_CX = 110.0
DFP_HEAD_CY = 106.0
DFP_HEAD_RX = 58.0
DFP_HEAD_RY = 56.0
DFP_EYE_Y = 118.0
DFP_EYE_OFFSET = 24.0
DFP_FEET_Y = 244.0


class DFPPetRenderer:
    """把 DFPRenderState 画成角色。

    分层顺序（从后到前）：
      地面阴影 → 鲸尾 → 后发 → 腿 → 身体/裙子 → 手臂 → 头（鳍耳→脸→前发）
      → 喷水 → 特效 → 名牌
    """

    def __init__(self, palette: str = "ocean"):
        self._dfp_palette_key = "ocean"
        self._dfp_palette: Dict[str, str] = dict(DFP_PALETTES["ocean"])
        self.dfp_set_palette(palette)

    # --- 配色 ---
    def dfp_set_palette(self, palette: str) -> None:
        key = str(palette or "ocean")
        if key not in DFP_PALETTES:
            key = "ocean"
        self._dfp_palette_key = key
        self._dfp_palette = dict(DFP_PALETTES[key])

    def dfp_palette_name(self) -> str:
        return self._dfp_palette_key

    def dfp_palette_choices(self) -> List[Tuple[str, str]]:
        return [(key, str(DFP_PALETTES[key].get("label", key))) for key in DFP_PALETTES]

    def _c(self, key: str) -> QColor:
        return dfp_color(self._dfp_palette.get(key, "#4D6BFE"))

    def _shade(self, key: str, factor: float) -> QColor:
        return dfp_color_shade(self._c(key), factor)

    # --- 主入口 ---
    def dfp_render(self, painter: QPainter, rect: QRectF, state: DFPRenderState) -> None:
        if painter is None or not isinstance(rect, QRectF) and not isinstance(rect, QRect):
            return
        rect = QRectF(rect)
        if rect.width() <= 1 or rect.height() <= 1:
            return
        scale = min(rect.width() / DFP_CANVAS_W, rect.height() / DFP_CANVAS_H)
        painter.save()
        painter.setRenderHint(dfp_enum_value(0, lambda: QPainter.RenderHint.Antialiasing, lambda: QPainter.Antialiasing), True)
        painter.setRenderHint(dfp_enum_value(0, lambda: QPainter.RenderHint.SmoothPixmapTransform, lambda: QPainter.SmoothPixmapTransform), True)
        painter.translate(rect.center())
        painter.scale(-scale if state.flip else scale, scale)
        painter.translate(-DFP_CANVAS_W / 2.0, -DFP_CANVAS_H / 2.0)

        # 整体上下浮动（走路/蹦跳/漂游）
        bob = float(state.walk_bob or 0.0)
        if bob:
            painter.translate(0.0, -bob)

        if state.show_shadow:
            self._dfp_draw_shadow(painter, state)

        # 身体倾斜（蹦跳/转圈/被拎起来时会晃）
        if state.tilt:
            painter.translate(DFP_HEAD_CX, DFP_FEET_Y)
            painter.rotate(float(state.tilt))
            painter.translate(-DFP_HEAD_CX, -DFP_FEET_Y)

        # 旋转动作：用水平缩放模拟「绕竖直轴转圈」，而不是在画面里打滚
        if state.spin:
            factor = math.cos(dfp_clamp(float(state.spin), 0.0, 1.0) * math.pi * 2.0)
            if abs(factor) < 0.16:
                factor = 0.16 if factor >= 0.0 else -0.16
            painter.translate(DFP_HEAD_CX, DFP_FEET_Y)
            painter.scale(factor, 1.0)
            painter.translate(-DFP_HEAD_CX, -DFP_FEET_Y)

        # 呼吸/压扁：以脚底为支点做轻微缩放，不要整张图位移
        squash = dfp_clamp(float(state.squash or 0.0), -0.35, 0.35)
        breathe = dfp_clamp(float(state.breathe or 0.0), -1.0, 1.0)
        scale_x = 1.0 + squash * 0.5 + breathe * 0.012
        scale_y = 1.0 - squash * 0.5 + breathe * 0.020
        stretch = dfp_clamp(float(state.stretch or 0.0), 0.0, 1.0)
        scale_x *= 1.0 - stretch * 0.10
        scale_y *= 1.0 + stretch * 0.12
        painter.translate(DFP_HEAD_CX, DFP_FEET_Y)
        painter.scale(scale_x, scale_y)
        painter.translate(-DFP_HEAD_CX, -DFP_FEET_Y)

        self._dfp_draw_hair_back(painter, state)
        # 鲸尾画在后发之上、身体之下：否则尾巴会被头发和裙子完全遮住，
        # 整只角色就只剩下“企鹅”轮廓了
        self._dfp_draw_tail(painter, state)
        self._dfp_draw_legs(painter, state)
        self._dfp_draw_body(painter, state)
        self._dfp_draw_arms(painter, state)
        self._dfp_draw_head_group(painter, state)
        self._dfp_draw_spout(painter, state)
        self._dfp_draw_effects(painter, state)
        painter.restore()

        # 名牌不受缩放影响，单独画在目标矩形顶部
        if state.nametag:
            self._dfp_draw_nametag(painter, rect, state)

    # --- 地面阴影 ---
    def _dfp_draw_shadow(self, painter: QPainter, state: DFPRenderState) -> None:
        painter.save()
        painter.setPen(dfp_no_pen())
        cx = DFP_HEAD_CX
        cy = DFP_FEET_Y + 4.0
        bob = float(state.walk_bob or 0.0)
        strength = dfp_clamp(1.0 - bob / 26.0, 0.35, 1.0)
        painter.setBrush(QBrush(dfp_color_alpha("#20304F", int(52 * strength))))
        rx = 54.0 * (0.92 + 0.08 * strength)
        painter.drawEllipse(QPointF(cx, cy), rx, 11.0 * strength)
        painter.setBrush(QBrush(dfp_color_alpha("#20304F", int(30 * strength))))
        painter.drawEllipse(QPointF(cx, cy), rx * 1.35, 15.0 * strength)
        painter.restore()

    # --- 鲸尾（身后底部的大尾鳍） ---
    def _dfp_draw_tail(self, painter: QPainter, state: DFPRenderState) -> None:
        """鲸尾：从腰部两侧探出的尾鳍（画在身体之后，形成鲸的特征轮廓）。"""
        painter.save()
        painter.setPen(dfp_no_pen())
        path = QPainterPath()
        # 鲸尾：经典「两片尾叶 + 中间 V 形缺口」的形状，否则会被看成翅膀
        path.moveTo(48.0, 180.0)
        path.cubicTo(70.0, 184.0, 94.0, 192.0, 108.0, 200.0)
        path.lineTo(112.0, 200.0)
        path.cubicTo(126.0, 192.0, 150.0, 184.0, 172.0, 180.0)
        path.cubicTo(154.0, 198.0, 132.0, 208.0, 118.0, 212.0)
        path.cubicTo(113.0, 213.0, 107.0, 213.0, 102.0, 212.0)
        path.cubicTo(88.0, 208.0, 66.0, 198.0, 48.0, 180.0)
        path.closeSubpath()
        gradient = QLinearGradient(0.0, 176.0, 0.0, 214.0)
        gradient.setColorAt(0.0, self._shade("body", 1.16))
        gradient.setColorAt(0.55, self._c("body"))
        gradient.setColorAt(1.0, self._shade("body_dark", 0.82))
        painter.translate(DFP_HEAD_CX, 196.0)
        painter.rotate(math.degrees(float(state.tail_phase or 0.0)) * 0.5)
        painter.translate(-DFP_HEAD_CX, -196.0)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(path)
        # 尾鳍上的浅色纹路
        painter.setPen(dfp_line_pen(dfp_color_alpha(self._c("fin"), 175), 2.4))
        for side in (-1.0, 1.0):
            painter.drawLine(QPointF(DFP_HEAD_CX + side * 30.0, 191.0), QPointF(DFP_HEAD_CX + side * 58.0, 184.0))
        painter.restore()

    # --- 后发（大块头发 + 两缕侧发） ---
    def _dfp_draw_hair_back(self, painter: QPainter, state: DFPRenderState) -> None:
        painter.save()
        painter.setPen(dfp_no_pen())
        path = QPainterPath()
        path.moveTo(DFP_HEAD_CX, 42.0)
        path.cubicTo(56.0, 42.0, 36.0, 86.0, 40.0, 122.0)
        path.cubicTo(42.0, 162.0, 48.0, 198.0, 58.0, 216.0)
        path.cubicTo(74.0, 204.0, 80.0, 166.0, 78.0, 130.0)
        path.cubicTo(92.0, 148.0, 128.0, 148.0, 142.0, 130.0)
        path.cubicTo(140.0, 166.0, 146.0, 204.0, 162.0, 216.0)
        path.cubicTo(172.0, 198.0, 178.0, 162.0, 180.0, 122.0)
        path.cubicTo(184.0, 86.0, 164.0, 42.0, DFP_HEAD_CX, 42.0)
        path.closeSubpath()
        gradient = QLinearGradient(0.0, 40.0, 0.0, 220.0)
        gradient.setColorAt(0.0, self._shade("hair", 1.18))
        gradient.setColorAt(0.5, self._c("hair"))
        gradient.setColorAt(1.0, self._shade("hair", 0.82))
        painter.setBrush(QBrush(gradient))
        painter.drawPath(path)
        painter.restore()

    # --- 腿 ---
    def _dfp_draw_legs(self, painter: QPainter, state: DFPRenderState) -> None:
        painter.save()
        painter.setPen(dfp_no_pen())
        kick = 0.0
        if state.action == DFPAction.WALK:
            kick = math.sin(float(state.action_progress or 0.0) * math.pi * 2.0) * 5.0
        elif state.action == DFPAction.JUMP:
            kick = 3.0
        for side, offset in ((0, -16.0), (1, 16.0)):
            sway = kick if side == 0 else -kick
            cx = DFP_HEAD_CX + offset + sway * 0.5
            painter.setBrush(QBrush(self._c("skin")))
            painter.drawRoundedRect(QRectF(cx - 8.5, 200.0, 17.0, 32.0), 8.0, 8.0)
            # 小鞋子，跟裙子呼应
            painter.setBrush(QBrush(self._c("body_dark")))
            painter.drawRoundedRect(QRectF(cx - 10.5, 226.0 + sway * 0.2, 21.0, 13.0), 6.0, 5.0)
            painter.setBrush(QBrush(dfp_color_alpha(self._c("fin"), 210)))
            painter.drawEllipse(QPointF(cx, 232.0 + sway * 0.2), 6.4, 2.8)
        painter.restore()

    # --- 身体 / 裙子 / 白肚皮 ---
    def _dfp_draw_body(self, painter: QPainter, state: DFPRenderState) -> None:
        painter.save()
        painter.setPen(dfp_no_pen())

        # 1) 躯干（圆滚滚，撑起“肥”的轮廓）
        body = QPainterPath()
        body.moveTo(DFP_HEAD_CX, 150.0)
        body.cubicTo(76.0, 154.0, 68.0, 178.0, 70.0, 196.0)
        body.cubicTo(72.0, 210.0, 148.0, 210.0, 150.0, 196.0)
        body.cubicTo(152.0, 178.0, 144.0, 154.0, DFP_HEAD_CX, 150.0)
        body.closeSubpath()
        gradient = QRadialGradient(QPointF(94.0, 170.0), 82.0)
        gradient.setColorAt(0.0, dfp_color_shade(self._c("outfit"), 1.04))
        gradient.setColorAt(0.65, self._c("outfit"))
        gradient.setColorAt(1.0, dfp_color_mix(self._c("outfit"), self._c("body"), 0.55))
        painter.setBrush(QBrush(gradient))
        painter.drawPath(body)

        # 2) 白肚皮（鲸的标志，压裙子上露出上半截）
        belly = QPainterPath()
        belly.moveTo(DFP_HEAD_CX, 164.0)
        belly.cubicTo(86.0, 172.0, 84.0, 192.0, 92.0, 200.0)
        belly.cubicTo(102.0, 208.0, 118.0, 208.0, 128.0, 200.0)
        belly.cubicTo(136.0, 192.0, 134.0, 172.0, DFP_HEAD_CX, 164.0)
        belly.closeSubpath()
        painter.setBrush(QBrush(self._c("belly")))
        painter.drawPath(belly)

        # 3) 领口装饰
        painter.setBrush(QBrush(self._c("outfit_trim")))
        collar = QPainterPath()
        collar.moveTo(84.0, 156.0)
        collar.cubicTo(96.0, 170.0, 124.0, 170.0, 136.0, 156.0)
        collar.cubicTo(126.0, 152.0, 94.0, 152.0, 84.0, 156.0)
        collar.closeSubpath()
        painter.drawPath(collar)

        # 4) 腰带（accent 色，跟配色呼应）
        painter.setBrush(QBrush(dfp_color_alpha(self._c("accent"), 225)))
        painter.drawRoundedRect(QRectF(78.0, 180.0, 64.0, 8.0), 4.0, 4.0)
        painter.setBrush(QBrush(self._c("accent")))
        painter.drawEllipse(QPointF(DFP_HEAD_CX, 184.0), 6.5, 6.5)

        # 5) 裙摆（梯形，裙边用 trim 色，一眼能看出是“穿衣服”）
        skirt = QPainterPath()
        skirt.moveTo(78.0, 186.0)
        skirt.cubicTo(66.0, 196.0, 60.0, 204.0, 58.0, 212.0)
        skirt.cubicTo(88.0, 219.0, 132.0, 219.0, 162.0, 212.0)
        skirt.cubicTo(160.0, 204.0, 154.0, 196.0, 142.0, 186.0)
        skirt.cubicTo(130.0, 193.0, 90.0, 193.0, 78.0, 186.0)
        skirt.closeSubpath()
        skirt_gradient = QLinearGradient(0.0, 186.0, 0.0, 219.0)
        skirt_gradient.setColorAt(0.0, self._c("body"))
        skirt_gradient.setColorAt(1.0, self._shade("body_dark", 0.92))
        painter.setBrush(QBrush(skirt_gradient))
        painter.drawPath(skirt)
        painter.setPen(dfp_line_pen(dfp_color_alpha(self._c("outfit_trim"), 235), 3.0))
        hem = QPainterPath()
        hem.moveTo(59.0, 211.0)
        hem.cubicTo(88.0, 218.0, 132.0, 218.0, 161.0, 211.0)
        painter.drawPath(hem)
        painter.setPen(dfp_no_pen())
        painter.restore()

    # --- 手臂（小鳍手） ---
    def _dfp_draw_arms(self, painter: QPainter, state: DFPRenderState) -> None:
        painter.save()
        painter.setPen(dfp_no_pen())
        waving = state.action == DFPAction.WAVE
        dance = state.action == DFPAction.DANCE
        progress = float(state.action_progress or 0.0)

        # 左臂（小鳍手，别画大，大了会被看成袖子）
        painter.setBrush(QBrush(self._c("skin")))
        left = QPainterPath()
        left.moveTo(84.0, 170.0)
        left.cubicTo(74.0, 176.0, 68.0, 186.0, 66.0, 198.0)
        left.cubicTo(74.0, 199.0, 82.0, 190.0, 88.0, 180.0)
        left.closeSubpath()
        painter.drawPath(left)

        # 右臂
        right_angle = 0.0
        if waving:
            right_angle = -105.0 + math.sin(progress * math.pi * 4.0) * 16.0
        elif dance:
            right_angle = -40.0 + math.sin(progress * math.pi * 6.0) * 22.0
        painter.save()
        painter.translate(140.0, 170.0)
        painter.rotate(right_angle)
        painter.translate(-140.0, -170.0)
        right = QPainterPath()
        right.moveTo(136.0, 170.0)
        right.cubicTo(146.0, 176.0, 152.0, 186.0, 154.0, 198.0)
        right.cubicTo(146.0, 199.0, 138.0, 190.0, 132.0, 180.0)
        right.closeSubpath()
        painter.drawPath(right)
        painter.restore()
        painter.restore()

    # --- 头部组（鳍耳 → 头 → 脸 → 前发 → 眉毛） ---
    def _dfp_draw_head_group(self, painter: QPainter, state: DFPRenderState) -> None:
        self._dfp_draw_fin_ears(painter, state)
        self._dfp_draw_head(painter, state)
        self._dfp_draw_face(painter, state)
        self._dfp_draw_hair_front(painter, state)
        # 眉毛画在刘海之上：否则生气/难过的表情会被头发盖住，用户根本看不出来
        self._dfp_draw_brows(painter, state)

    # --- 眉毛（独立一层，画在头发之上） ---
    def _dfp_draw_brows(self, painter: QPainter, state: DFPRenderState) -> None:
        expr = state.expression
        painter.save()
        painter.setPen(dfp_no_pen())
        brow_y = DFP_EYE_Y - 20.0
        lift = -3.5 if expr in (DFPExpression.SURPRISED, DFPExpression.HAPPY) else 0.0
        tilt = 0.0
        if expr == DFPExpression.ANGRY:
            tilt = 12.0
        elif expr == DFPExpression.SAD:
            tilt = -13.0
        if state.asleep:
            lift = 2.0
        painter.setPen(dfp_line_pen(dfp_color_alpha(self._c("hair_light"), 235), 3.2))
        for side in (-1.0, 1.0):
            cx = DFP_HEAD_CX + side * DFP_EYE_OFFSET
            painter.save()
            painter.translate(cx, brow_y + lift)
            painter.rotate(-side * tilt)
            painter.drawLine(QPointF(-10.0, 2.0), QPointF(10.0, 0.0))
            painter.restore()
        painter.restore()

    # --- 鲸鱼鳍耳 ---
    def _dfp_draw_fin_ears(self, painter: QPainter, state: DFPRenderState) -> None:
        painter.save()
        painter.setPen(dfp_no_pen())
        flap = math.sin(float(state.time or 0.0) * 1.3) * 5.0
        for side in (-1.0, 1.0):
            painter.save()
            painter.translate(DFP_HEAD_CX + side * 54.0, 92.0)
            painter.rotate(side * (14.0 + flap * 0.4))
            painter.translate(-DFP_HEAD_CX - side * 54.0, -92.0)
            path = QPainterPath()
            if side < 0:
                path.moveTo(58.0, 84.0)
                path.cubicTo(38.0, 86.0, 20.0, 100.0, 12.0, 120.0)
                path.cubicTo(26.0, 126.0, 48.0, 116.0, 62.0, 100.0)
            else:
                path.moveTo(162.0, 84.0)
                path.cubicTo(182.0, 86.0, 200.0, 100.0, 208.0, 120.0)
                path.cubicTo(194.0, 126.0, 172.0, 116.0, 158.0, 100.0)
            path.closeSubpath()
            gradient = QLinearGradient(DFP_HEAD_CX + side * 60.0, 84.0, DFP_HEAD_CX + side * 10.0, 124.0)
            gradient.setColorAt(0.0, self._shade("fin", 1.05))
            gradient.setColorAt(1.0, self._c("fin"))
            painter.setBrush(QBrush(gradient))
            painter.drawPath(path)
            painter.restore()
        painter.restore()

    # --- 头 ---
    def _dfp_draw_head(self, painter: QPainter, state: DFPRenderState) -> None:
        painter.save()
        painter.setPen(dfp_no_pen())
        gradient = QRadialGradient(QPointF(DFP_HEAD_CX - 18.0, DFP_HEAD_CY - 20.0), 104.0)
        gradient.setColorAt(0.0, dfp_color_shade(self._c("skin"), 1.06))
        gradient.setColorAt(0.72, self._c("skin"))
        gradient.setColorAt(1.0, self._c("skin_shadow"))
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QPointF(DFP_HEAD_CX, DFP_HEAD_CY), DFP_HEAD_RX, DFP_HEAD_RY)
        painter.restore()

    # --- 脸（眼睛 / 眉毛 / 腮红） ---
    def _dfp_draw_face(self, painter: QPainter, state: DFPRenderState) -> None:
        expr = state.expression
        painter.save()
        painter.setPen(dfp_no_pen())
        eye_color = self._c("eye")
        blink = dfp_clamp(float(state.blink or 0.0), 0.0, 1.0)
        if state.asleep:
            blink = 1.0
        closed = blink > 0.72

        # 腮红（先画在眼睛下方，眼睛后画盖住重叠部分）
        blush_alpha = 118
        if expr in (DFPExpression.HAPPY, DFPExpression.LOVE):
            blush_alpha = 168
        elif expr == DFPExpression.DIZZY:
            blush_alpha = 190
        painter.setBrush(QBrush(dfp_color_alpha(self._c("blush"), blush_alpha)))
        for side in (-1.0, 1.0):
            painter.drawEllipse(QPointF(DFP_HEAD_CX + side * 40.0, DFP_EYE_Y + 20.0), 13.0, 8.0)

        for side in (-1.0, 1.0):
            cx = DFP_HEAD_CX + side * DFP_EYE_OFFSET
            cy = DFP_EYE_Y
            if expr == DFPExpression.LOVE:
                painter.setBrush(QBrush(dfp_color_alpha("#FF4F7B", 235)))
                painter.drawPath(self._dfp_heart_path(cx, cy + 1.0, 15.0))
                continue
            if expr == DFPExpression.DIZZY:
                painter.setPen(dfp_line_pen(eye_color, 2.4))
                painter.setBrush(QBrush(QColor(255, 255, 255, 60)))
                painter.drawEllipse(QPointF(cx, cy), 9.0, 9.0)
                painter.drawLine(QPointF(cx - 6.0, cy - 6.0), QPointF(cx + 6.0, cy + 6.0))
                painter.drawLine(QPointF(cx + 6.0, cy - 6.0), QPointF(cx - 6.0, cy + 6.0))
                painter.setPen(dfp_no_pen())
                continue
            if closed:
                painter.setPen(dfp_line_pen(eye_color, 3.0))
                path = QPainterPath()
                if expr in (DFPExpression.HAPPY, DFPExpression.LOVE) and not state.asleep:
                    path.moveTo(cx - 10.0, cy + 2.0)
                    path.quadTo(cx, cy - 8.0, cx + 10.0, cy + 2.0)
                else:
                    path.moveTo(cx - 10.0, cy)
                    path.quadTo(cx, cy + 6.0, cx + 10.0, cy)
                painter.drawPath(path)
                painter.setPen(dfp_no_pen())
                continue

            ry = dfp_lerp(14.5, 2.4, blink)
            painter.setPen(dfp_no_pen())
            if expr == DFPExpression.ANGRY:
                # 半眯眼
                painter.setBrush(QBrush(eye_color))
                painter.drawEllipse(QPointF(cx, cy + 2.0), 10.5, ry * 0.66)
                painter.setBrush(QBrush(self._c("skin")))
                painter.drawEllipse(QPointF(cx, cy - ry * 0.62), 12.0, ry * 0.75)
            else:
                painter.setBrush(QBrush(eye_color))
                painter.drawEllipse(QPointF(cx, cy), 11.0, ry)
                # 瞳孔高光：一大一小才是活的眼睛
                painter.setBrush(QBrush(QColor(255, 255, 255, 235)))
                painter.drawEllipse(QPointF(cx + 3.8, cy - ry * 0.34), 4.0, max(1.2, ry * 0.30))
                painter.setBrush(QBrush(QColor(255, 255, 255, 165)))
                painter.drawEllipse(QPointF(cx - 4.0, cy + ry * 0.38), 2.2, max(0.8, ry * 0.18))
            if expr == DFPExpression.SURPRISED:
                painter.setBrush(QBrush(QColor(255, 255, 255, 90)))
                painter.drawEllipse(QPointF(cx, cy), 13.0, ry * 1.12)
            # 上睫毛：给眼睛加一条弧线，少女感的关键一笔
            painter.setPen(dfp_line_pen(dfp_color_alpha(eye_color, 220), 2.6))
            lash = QPainterPath()
            lash.moveTo(cx - 11.5, cy - ry * 0.45)
            lash.quadTo(cx, cy - ry * 1.28, cx + 11.5, cy - ry * 0.48)
            painter.drawPath(lash)
            painter.setPen(dfp_no_pen())
        painter.setPen(dfp_no_pen())
        self._dfp_draw_mouth(painter, state, expr)
        painter.restore()

    # --- 嘴巴 ---
    def _dfp_draw_mouth(self, painter: QPainter, state: DFPRenderState, expr: Any) -> None:
        cx = DFP_HEAD_CX
        cy = DFP_EYE_Y + 24.0
        open_ratio = dfp_clamp(max(float(state.mouth_open or 0.0), 0.55 if state.speaking else 0.0), 0.0, 1.0)
        inner = dfp_color_mix(self._c("blush"), self._c("eye"), 0.35)
        painter.setPen(dfp_no_pen())

        if state.action == DFPAction.EAT:
            painter.setBrush(QBrush(inner))
            painter.drawEllipse(QPointF(cx, cy + 3.0), 11.0, 9.0)
            painter.setBrush(QBrush(self._c("belly")))
            painter.drawEllipse(QPointF(cx, cy + 1.0), 8.0, 3.0)
            return

        if expr == DFPExpression.HAPPY and open_ratio <= 0.01:
            painter.setPen(dfp_line_pen(inner, 3.0))
            path = QPainterPath()
            path.moveTo(cx - 12.0, cy - 2.0)
            path.quadTo(cx, cy + 12.0, cx + 12.0, cy - 2.0)
            painter.drawPath(path)
            painter.setPen(dfp_no_pen())
            return

        if expr == DFPExpression.SURPRISED and open_ratio <= 0.01:
            painter.setBrush(QBrush(inner))
            painter.drawEllipse(QPointF(cx, cy + 2.0), 5.0, 6.5)
            return

        if expr == DFPExpression.SAD:
            painter.setPen(dfp_line_pen(inner, 3.0))
            path = QPainterPath()
            path.moveTo(cx - 9.0, cy + 5.0)
            path.quadTo(cx, cy - 5.0, cx + 9.0, cy + 5.0)
            painter.drawPath(path)
            painter.setPen(dfp_no_pen())
            return

        if expr == DFPExpression.ANGRY:
            painter.setPen(dfp_line_pen(inner, 3.2))
            painter.drawLine(QPointF(cx - 9.0, cy + 3.0), QPointF(cx + 9.0, cy + 3.0))
            painter.setPen(dfp_no_pen())
            return

        if expr == DFPExpression.LOVE:
            painter.setPen(dfp_line_pen(inner, 3.0))
            path = QPainterPath()
            path.moveTo(cx - 10.0, cy)
            path.quadTo(cx - 4.0, cy + 8.0, cx, cy + 1.0)
            path.quadTo(cx + 4.0, cy + 8.0, cx + 10.0, cy)
            painter.drawPath(path)
            painter.setPen(dfp_no_pen())
            return

        if open_ratio > 0.01:
            painter.setBrush(QBrush(inner))
            painter.drawEllipse(QPointF(cx, cy + 3.0 * open_ratio), 6.0 + 4.0 * open_ratio, 4.0 + 6.0 * open_ratio)
            if open_ratio > 0.4:
                painter.setBrush(QBrush(dfp_color_alpha("#FF7A96", 200)))
                painter.drawEllipse(QPointF(cx, cy + 5.5 * open_ratio), 3.6 * open_ratio, 2.6 * open_ratio)
            return

        # 默认：小小的微笑
        painter.setPen(dfp_line_pen(inner, 2.8))
        path = QPainterPath()
        path.moveTo(cx - 8.0, cy)
        path.quadTo(cx - 4.0, cy + 5.0, cx, cy + 1.0)
        path.quadTo(cx + 4.0, cy + 5.0, cx + 8.0, cy)
        painter.drawPath(path)
        painter.setPen(dfp_no_pen())

    # --- 前发（刘海 + 呆毛 + 鲸尾发饰 + 高光） ---
    def _dfp_draw_hair_front(self, painter: QPainter, state: DFPRenderState) -> None:
        painter.save()
        painter.setPen(dfp_no_pen())
        # 刘海：外缘平滑、下缘做成“分缕尖角”（动漫式），否则整块头发读起来像顶帽子
        fringe = QPainterPath()
        fringe.moveTo(46.0, 98.0)
        fringe.cubicTo(42.0, 54.0, 74.0, 28.0, DFP_HEAD_CX, 28.0)
        fringe.cubicTo(146.0, 28.0, 178.0, 54.0, 174.0, 98.0)
        fringe.cubicTo(170.0, 88.0, 165.0, 80.0, 158.0, 72.0)
        fringe.cubicTo(154.0, 86.0, 150.0, 100.0, 147.0, 112.0)
        fringe.cubicTo(143.0, 96.0, 137.0, 84.0, 131.0, 74.0)
        fringe.cubicTo(129.0, 90.0, 126.0, 104.0, 123.0, 114.0)
        fringe.cubicTo(119.0, 98.0, 115.0, 86.0, DFP_HEAD_CX, 78.0)
        fringe.cubicTo(105.0, 88.0, 101.0, 104.0, 97.0, 114.0)
        fringe.cubicTo(94.0, 98.0, 89.0, 84.0, 83.0, 74.0)
        fringe.cubicTo(79.0, 92.0, 74.0, 106.0, 70.0, 112.0)
        fringe.cubicTo(64.0, 96.0, 58.0, 84.0, 52.0, 76.0)
        fringe.cubicTo(50.0, 84.0, 48.0, 92.0, 46.0, 98.0)
        fringe.closeSubpath()
        gradient = QLinearGradient(0.0, 26.0, 0.0, 116.0)
        gradient.setColorAt(0.0, self._shade("hair", 1.10))
        gradient.setColorAt(1.0, self._c("hair"))
        painter.setBrush(QBrush(gradient))
        painter.drawPath(fringe)

        # 刘海高光
        painter.setPen(dfp_line_pen(dfp_color_alpha(self._c("hair_light"), 190), 6.0))
        shine = QPainterPath()
        shine.moveTo(70.0, 62.0)
        shine.cubicTo(86.0, 48.0, 134.0, 48.0, 150.0, 62.0)
        painter.drawPath(shine)
        painter.setPen(dfp_no_pen())

        # 两侧翘发：打破“帽檐”感的关键小细节
        for side in (-1.0, 1.0):
            tuft = QPainterPath()
            tuft.moveTo(DFP_HEAD_CX + side * 50.0, 52.0)
            tuft.cubicTo(DFP_HEAD_CX + side * 62.0, 42.0, DFP_HEAD_CX + side * 78.0, 38.0, DFP_HEAD_CX + side * 84.0, 44.0)
            tuft.cubicTo(DFP_HEAD_CX + side * 74.0, 50.0, DFP_HEAD_CX + side * 66.0, 62.0, DFP_HEAD_CX + side * 62.0, 74.0)
            tuft.closeSubpath()
            painter.setBrush(QBrush(self._shade("hair", 1.06)))
            painter.drawPath(tuft)

        # 呆毛
        painter.setPen(dfp_line_pen(self._c("hair"), 6.0))
        ahoge = QPainterPath()
        bob = math.sin(float(state.time or 0.0) * 2.1) * 3.0
        ahoge.moveTo(104.0, 40.0)
        ahoge.cubicTo(96.0 + bob, 22.0, 106.0 + bob, 8.0, 122.0, 10.0)
        ahoge.cubicTo(110.0, 18.0, 108.0, 30.0, 116.0, 40.0)
        painter.drawPath(ahoge)
        painter.setPen(dfp_no_pen())

        # 鲸尾发饰（点题：DeepSeek 的鲸）
        painter.save()
        painter.translate(72.0, 74.0)
        painter.rotate(-28.0)
        painter.setBrush(QBrush(self._c("accent")))
        clip = QPainterPath()
        clip.moveTo(0.0, 0.0)
        clip.cubicTo(-3.0, -9.0, -13.0, -14.0, -19.0, -11.0)
        clip.cubicTo(-13.0, -3.0, -6.0, 3.0, 0.0, 0.0)
        clip.cubicTo(3.0, -9.0, 13.0, -14.0, 19.0, -11.0)
        clip.cubicTo(13.0, -3.0, 6.0, 3.0, 0.0, 0.0)
        clip.closeSubpath()
        painter.drawPath(clip)
        painter.setBrush(QBrush(self._shade("accent", 0.8)))
        painter.drawEllipse(QPointF(0.0, -2.0), 3.4, 3.4)
        painter.restore()

        # 前面两缕侧发：它们是“少女感”的主要来源，画在身体之上。
        # 长度只到肩膀 —— 再长就会把腰后的鲸尾遮住。
        painter.setPen(dfp_no_pen())
        for side in (-1.0, 1.0):
            lock = QPainterPath()
            outer_x = 110.0 + side * 64.0
            inner_x = 110.0 + side * 46.0
            lock.moveTo(110.0 + side * 52.0, 78.0)
            lock.cubicTo(outer_x - side * 4.0, 108.0, outer_x - side * 2.0, 136.0, 110.0 + side * 50.0, 162.0)
            lock.cubicTo(inner_x + side * 8.0, 158.0, inner_x + side * 2.0, 126.0, inner_x + side * 2.0, 96.0)
            lock.closeSubpath()
            lock_gradient = QLinearGradient(110.0 + side * 50.0, 80.0, 110.0 + side * 62.0, 162.0)
            lock_gradient.setColorAt(0.0, self._shade("hair", 1.05))
            lock_gradient.setColorAt(1.0, self._shade("hair", 0.86))
            painter.setBrush(QBrush(lock_gradient))
            painter.drawPath(lock)
            painter.setPen(dfp_line_pen(dfp_color_alpha(self._c("hair_light"), 140), 2.0))
            painter.drawLine(QPointF(110.0 + side * 57.0, 96.0), QPointF(110.0 + side * 57.0, 148.0))
            painter.setPen(dfp_no_pen())
        painter.restore()

    # --- 喷水（鲸鱼头顶的招牌动作） ---
    def _dfp_draw_spout(self, painter: QPainter, state: DFPRenderState) -> None:
        strength = dfp_clamp(float(state.spout or 0.0), 0.0, 1.0)
        if strength <= 0.01:
            return
        painter.save()
        painter.setPen(dfp_no_pen())
        base_x = DFP_HEAD_CX - 4.0
        base_y = 42.0
        droplet = self._c("fin")
        count = 7
        for index in range(count):
            phase = (float(state.time or 0.0) * 0.9 + index / float(count)) % 1.0
            height = 42.0 * phase * strength
            radius = (5.0 - index * 0.35) * (1.0 - phase * 0.35) * (0.5 + strength * 0.6)
            if radius <= 0.4:
                continue
            drift = math.sin(phase * math.pi * 2.0 + index) * 7.0 * (1.0 - phase)
            alpha = int(200 * (1.0 - phase * 0.75))
            painter.setBrush(QBrush(dfp_color_alpha(droplet, alpha)))
            painter.drawEllipse(QPointF(base_x + drift, base_y - height), radius, radius * 1.1)
        painter.restore()

    # --- 特效（爱心 / 星星 / Zzz / 汗滴） ---
    def _dfp_draw_effects(self, painter: QPainter, state: DFPRenderState) -> None:
        painter.save()
        painter.setPen(dfp_no_pen())
        now = float(state.time or 0.0)

        if state.heart > 0.01:
            for index in range(3):
                phase = (now * 0.55 + index * 0.33) % 1.0
                size = 12.0 + index * 3.0
                x = DFP_HEAD_CX + (18.0 + index * 16.0) * (1 if index % 2 else -1)
                y = 78.0 - phase * 54.0
                alpha = int(220 * (1.0 - phase))
                painter.setBrush(QBrush(dfp_color_alpha("#FF5C8A", alpha)))
                painter.drawPath(self._dfp_heart_path(x, y, size * (1.0 - phase * 0.25)))

        if state.star > 0.01:
            for index in range(5):
                phase = (now * 0.9 + index * 0.2) % 1.0
                angle = now * 1.6 + index * 1.26
                radius = 62.0 + phase * 16.0
                x = DFP_HEAD_CX + math.cos(angle) * radius
                y = 96.0 + math.sin(angle) * radius * 0.7
                alpha = int(230 * (1.0 - phase))
                painter.setBrush(QBrush(dfp_color_alpha("#FFD75E", alpha)))
                painter.drawPath(self._dfp_star_path(x, y, 7.0 + (index % 3) * 2.0))

        if state.zzz > 0.01 or state.asleep:
            for index in range(3):
                phase = (now * 0.42 + index * 0.34) % 1.0
                alpha = int(215 * (1.0 - phase))
                size = 13 + index * 4
                font = dfp_desktop_font(max(8, size), True)
                painter.setFont(font)
                painter.setPen(QPen(dfp_color_alpha(self._c("accent"), alpha)))
                painter.drawText(
                    QRectF(DFP_HEAD_CX + 44.0 + phase * 20.0, 62.0 - phase * 46.0, 30.0, 24.0),
                    dfp_text_flags(0x0084, 0x0001),  # AlignCenter | AlignVCenter
                    "z",
                )
            painter.setPen(dfp_no_pen())

        if state.expression == DFPExpression.DIZZY:
            painter.setBrush(QBrush(dfp_color_alpha("#79C7FF", 210)))
            painter.drawEllipse(QPointF(DFP_HEAD_CX + 46.0, 74.0), 5.5, 8.0)
        painter.restore()

    # --- 名牌（不随角色缩放，直接画在窗口顶部） ---
    def _dfp_draw_nametag(self, painter: QPainter, rect: QRectF, state: DFPRenderState) -> None:
        text = dfp_truncate_text(state.nametag, 16)
        if not text:
            return
        painter.save()
        font = dfp_desktop_font(9, True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        text_width = metrics.horizontalAdvance(text) + 20
        height = max(20, metrics.height() + 8)
        x = rect.center().x() - text_width / 2.0
        y = rect.top() + 2.0
        box = QRectF(x, y, text_width, height)
        painter.setRenderHint(dfp_enum_value(0, lambda: QPainter.RenderHint.Antialiasing, lambda: QPainter.Antialiasing), True)
        painter.setPen(dfp_line_pen(dfp_color_alpha(state.nametag_color, 190), 1.4))
        painter.setBrush(QBrush(dfp_color_alpha("#FFFFFF", 225)))
        painter.drawRoundedRect(box, height / 2.0, height / 2.0)
        painter.setPen(QPen(dfp_color(state.nametag_color)))
        painter.drawText(box, dfp_text_flags(0x0084, 0x0001), text)
        painter.restore()

    # --- 形状工具 ---
    def _dfp_heart_path(self, cx: float, cy: float, size: float) -> QPainterPath:
        path = QPainterPath()
        s = max(2.0, float(size)) / 2.0
        path.moveTo(cx, cy + s * 1.15)
        path.cubicTo(cx - s * 1.75, cy - s * 0.35, cx - s * 0.85, cy - s * 1.35, cx, cy - s * 0.45)
        path.cubicTo(cx + s * 0.85, cy - s * 1.35, cx + s * 1.75, cy - s * 0.35, cx, cy + s * 1.15)
        path.closeSubpath()
        return path

    def _dfp_star_path(self, cx: float, cy: float, size: float) -> QPainterPath:
        path = QPainterPath()
        outer = max(2.0, float(size)) / 2.0
        inner = outer * 0.42
        for index in range(10):
            angle = -math.pi / 2.0 + index * math.pi / 5.0
            radius = outer if index % 2 == 0 else inner
            point = QPointF(cx + math.cos(angle) * radius, cy + math.sin(angle) * radius)
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        path.closeSubpath()
        return path

    # --- 离屏渲染 ---
    def dfp_render_pixmap(
        self,
        state: DFPRenderState,
        width: int = 160,
        height: int = 190,
        background: Optional[Any] = None,
    ) -> QPixmap:
        """离屏渲染一张图片（用于预览、托盘图标、默认程序图标、导出形象）。"""
        width = max(8, int(width))
        height = max(8, int(height))
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(background) if background else QColor(0, 0, 0, 0))
        painter = QPainter(image)
        try:
            self.dfp_render(painter, QRectF(0.0, 0.0, float(width), float(height)), state)
        finally:
            painter.end()
        return QPixmap.fromImage(image.copy())


# =============================================================================
#  十六、程序图标（icon.ico 存在就用，不存在就用程序画的鲸鱼图标兜底）
# =============================================================================

_DFP_FALLBACK_ICON: Optional[QIcon] = None


def dfp_build_fallback_icon(size: int = 256) -> QIcon:
    """icon.ico 不存在时用程序绘制的鲸鱼头图标兜底。"""
    icon = QIcon()
    for edge in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(edge, edge)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(dfp_enum_value(0, lambda: QPainter.RenderHint.Antialiasing, lambda: QPainter.Antialiasing), True)
            unit = float(edge)
            box = QRectF(unit * 0.04, unit * 0.04, unit * 0.92, unit * 0.92)
            gradient = QLinearGradient(box.topLeft(), box.bottomRight())
            gradient.setColorAt(0.0, dfp_color("#5A78FF"))
            gradient.setColorAt(1.0, dfp_color("#3450D8"))
            painter.setPen(dfp_no_pen())
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(box, unit * 0.22, unit * 0.22)

            # 白色小鲸鱼：身体 + 尾鳍 + 眼睛 + 头顶水花
            body = QPainterPath()
            body.moveTo(unit * 0.18, unit * 0.60)
            body.cubicTo(unit * 0.18, unit * 0.40, unit * 0.36, unit * 0.30, unit * 0.52, unit * 0.34)
            body.cubicTo(unit * 0.68, unit * 0.38, unit * 0.76, unit * 0.50, unit * 0.76, unit * 0.62)
            body.cubicTo(unit * 0.76, unit * 0.76, unit * 0.60, unit * 0.84, unit * 0.44, unit * 0.80)
            body.cubicTo(unit * 0.28, unit * 0.76, unit * 0.18, unit * 0.70, unit * 0.18, unit * 0.60)
            body.closeSubpath()
            painter.setBrush(QBrush(dfp_color("#FFFFFF")))
            painter.drawPath(body)

            tail = QPainterPath()
            tail.moveTo(unit * 0.72, unit * 0.56)
            tail.cubicTo(unit * 0.80, unit * 0.42, unit * 0.90, unit * 0.36, unit * 0.94, unit * 0.40)
            tail.cubicTo(unit * 0.90, unit * 0.50, unit * 0.88, unit * 0.60, unit * 0.90, unit * 0.70)
            tail.cubicTo(unit * 0.82, unit * 0.70, unit * 0.74, unit * 0.64, unit * 0.72, unit * 0.56)
            tail.closeSubpath()
            painter.drawPath(tail)

            painter.setBrush(QBrush(dfp_color("#25304A")))
            painter.drawEllipse(QPointF(unit * 0.36, unit * 0.52), unit * 0.055, unit * 0.065)
            painter.setBrush(QBrush(dfp_color_alpha("#FFFFFF", 235)))
            painter.drawEllipse(QPointF(unit * 0.375, unit * 0.50), unit * 0.022, unit * 0.024)

            painter.setPen(dfp_line_pen(dfp_color_alpha("#25304A", 190), max(1.0, unit * 0.028)))
            smile = QPainterPath()
            smile.moveTo(unit * 0.30, unit * 0.66)
            smile.quadTo(unit * 0.36, unit * 0.72, unit * 0.42, unit * 0.66)
            painter.drawPath(smile)
            painter.setPen(dfp_no_pen())

            painter.setBrush(QBrush(dfp_color_alpha("#8FE3FF", 235)))
            painter.drawEllipse(QPointF(unit * 0.30, unit * 0.28), unit * 0.045, unit * 0.055)
            painter.drawEllipse(QPointF(unit * 0.40, unit * 0.20), unit * 0.032, unit * 0.040)
        finally:
            painter.end()
        icon.addPixmap(pixmap)
    if size != 256:
        pixmap = QPixmap(int(size), int(size))
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(dfp_enum_value(0, lambda: QPainter.RenderHint.Antialiasing, lambda: QPainter.Antialiasing), True)
            painter.drawPixmap(0, 0, int(size), int(size), icon.pixmap(256, 256))
        finally:
            painter.end()
        icon.addPixmap(pixmap)
    return icon


def dfp_app_icon() -> QIcon:
    """程序图标：icon.ico 存在则用，否则用程序内绘制图标。注意缓存，读不到也不报错。"""
    global _DFP_FALLBACK_ICON
    path = dfp_icon_path()
    if path:
        try:
            icon = QIcon(path)
            if not icon.isNull() and icon.availableSizes():
                return icon
        except Exception:
            pass
    if _DFP_FALLBACK_ICON is None:
        try:
            _DFP_FALLBACK_ICON = dfp_build_fallback_icon(256)
        except Exception:
            _DFP_FALLBACK_ICON = QIcon()
    return _DFP_FALLBACK_ICON


def dfp_text_flags(*flags: Any) -> int:
    """把 Qt 的 TextFlag / AlignmentFlag 组合成 int（兼容不同 PySide6 版本）。"""
    result = 0
    for flag in flags:
        if flag is None:
            continue
        try:
            result |= int(flag)
        except Exception:
            try:
                result |= int(flag.value)
            except Exception:
                continue
    return result


def dfp_event_pos(event: Any) -> QPoint:
    """取鼠标事件中的局部坐标（Qt5 用 pos()，Qt6 用 position().toPoint()）。"""
    try:
        pos = event.position()
        return pos.toPoint() if hasattr(pos, "toPoint") else QPoint(pos)
    except Exception:
        pass
    try:
        return QPoint(event.pos())
    except Exception:
        return QPoint(0, 0)


def dfp_event_global_pos(event: Any) -> QPoint:
    """取鼠标事件中的全局坐标（兼容不同 Qt 版本）。"""
    try:
        pos = event.globalPosition()
        return pos.toPoint() if hasattr(pos, "toPoint") else QPoint(pos)
    except Exception:
        pass
    try:
        return QPoint(event.globalPos())
    except Exception:
        return QCursor.pos()


# =============================================================================
#  十七、气泡（说话时出现在头顶，自动淡出；点击穿透，不抢焦点）
# =============================================================================


# 气泡排版常量（v1.1.8 新增）
# ★ 量文字（_dfp_measure_text / dfp_text_need_height）与画文字（paintEvent / dfp_text_rect）
#   必须用同一组数字，否则「窗口算出来的高度」会比「文字真正需要的高度」矮，字就被切掉。
#   老代码就是栽在这里：尺寸只留了 24px 内边距，而绘制时又扣掉尾巴 12 + 边框 1.5 + 上下 8+8
#   ⇒ 真正能画字的地方比需要的高度少 5px，底下一行/一笔常被削掉（用户报「字不完整」）。
DFP_BUBBLE_TAIL = 12.0     # 指向桌宠的小尾巴高度
DFP_BUBBLE_BORDER = 1.5    # 四周留白（边框线宽的一半）
DFP_BUBBLE_PAD_X = 12.0    # 文字与气泡边的左右内边距
DFP_BUBBLE_PAD_Y = 9.0     # 文字的上下内边距
DFP_BUBBLE_EXTRA_H = 2.0   # 高度容错（字体度量与实际绘制偶尔差 1~2 像素）
DFP_BUBBLE_MIN_H = 48      # 气泡最小高度（太扁了尾巴会看着怪）


class DFPBubbleWidget(QWidget):
    """对话气泡：圆角矩形 + 指向桌宠的小尾巴，淡出后自动隐藏。"""

    def __init__(self, parent=None):
        super().__init__(None)
        self._dfp_text = ""
        self._dfp_show_count = 0
        self._dfp_font_size = 10
        self._dfp_max_width = 320
        self._dfp_html = False  # ★ v1.1.12：True 表示 _dfp_text 是一小段 HTML（余额变化提示要染色）
        self._dfp_colors = {
            "border": DFP_UI_COLORS["accent"],
            "fill": "#FFFFFF",
            "text": DFP_UI_COLORS["text"],
        }
        self._dfp_anchor: Optional[Callable[[], QRect]] = None
        self._dfp_alpha = 1.0
        self._dfp_holding = False
        self._dfp_hide_timer = QTimer(self)
        self._dfp_hide_timer.setSingleShot(True)
        self._dfp_hide_timer.timeout.connect(self._dfp_start_fade_out)
        self._dfp_fade_timer = QTimer(self)
        self._dfp_fade_timer.setInterval(28)
        self._dfp_fade_timer.timeout.connect(self._dfp_step_fade)
        self._dfp_size = QSize(200, 60)

        window_flags = (
            dfp_enum_value(0, lambda: Qt.WindowType.Tool, lambda: Qt.Tool)
            | dfp_enum_value(0, lambda: Qt.WindowType.FramelessWindowHint, lambda: Qt.FramelessWindowHint)
            | dfp_enum_value(0, lambda: Qt.WindowType.WindowStaysOnTopHint, lambda: Qt.WindowStaysOnTopHint)
            | dfp_enum_value(0, lambda: Qt.WindowType.WindowDoesNotAcceptFocus, lambda: Qt.WindowDoesNotAcceptFocus)
        )
        self.setWindowFlags(window_flags)
        self.setAttribute(dfp_enum_value(0, lambda: Qt.WidgetAttribute.WA_TranslucentBackground, lambda: Qt.WA_TranslucentBackground), True)
        self.setAttribute(dfp_enum_value(0, lambda: Qt.WidgetAttribute.WA_ShowWithoutActivating, lambda: Qt.WA_ShowWithoutActivating), True)
        self.setAttribute(dfp_enum_value(0, lambda: Qt.WidgetAttribute.WA_TransparentForMouseEvents, lambda: Qt.WA_TransparentForMouseEvents), True)
        self.setFocusPolicy(dfp_enum_value(0, lambda: Qt.FocusPolicy.NoFocus, lambda: Qt.NoFocus))
        self.setFont(dfp_desktop_font(self._dfp_font_size))
        self.hide()

    # --- 配置 ---
    def dfp_set_font_size(self, size: int) -> None:
        self._dfp_font_size = int(dfp_clamp(size, 7, 26))
        self.setFont(dfp_desktop_font(self._dfp_font_size))
        self._dfp_relayout_if_showing()

    def dfp_set_max_width(self, width: int) -> None:
        self._dfp_max_width = int(dfp_clamp(width, 140, 900))
        self._dfp_relayout_if_showing()

    def _dfp_relayout_if_showing(self) -> None:
        """气泡正显示着的时候改了字号 / 最大宽度：立即按新尺寸重排（否则字会被切或留白）. """
        if not self._dfp_text or not self.isVisible():
            return
        self._dfp_size = self._dfp_measure_text()
        self._dfp_apply_geometry()
        self._dfp_reposition()
        self.update()

    def dfp_set_colors(self, border: str = "", fill: str = "", text: str = "") -> None:
        if border:
            self._dfp_colors["border"] = border
        if fill:
            self._dfp_colors["fill"] = fill
        if text:
            self._dfp_colors["text"] = text

    # --- 状态 ---
    def dfp_show_count(self) -> int:
        return self._dfp_show_count

    def dfp_is_showing(self) -> bool:
        return bool(self.isVisible() and self._dfp_alpha > 0.02)

    def dfp_text(self) -> str:
        return self._dfp_text

    def dfp_is_html(self) -> bool:
        """★ v1.1.12：当前气泡内容是 HTML 吗（余额变化提示要染色）。"""
        return bool(self._dfp_html)

    def dfp_attach(self, anchor: Any) -> None:
        """绑定锚点：可以是 QRect，也可以是一个返回 QRect 的函数。"""
        if callable(anchor):
            self._dfp_anchor = anchor
        elif isinstance(anchor, QRect):
            fixed = QRect(anchor)
            self._dfp_anchor = lambda: QRect(fixed)
        else:
            self._dfp_anchor = None

    # --- 显示/隐藏 ---
    def dfp_show_text(self, text: str, duration_ms: Optional[int] = None, anchor: Any = None) -> None:
        text = ("" if text is None else str(text)).strip()
        if not text:
            return
        if anchor is not None:
            self.dfp_attach(anchor)
        self._dfp_text = text
        self._dfp_html = False
        self._dfp_show_count += 1
        self._dfp_alpha = 1.0
        self._dfp_holding = True
        self._dfp_size = self._dfp_measure_text()
        self._dfp_apply_geometry()
        self.setWindowOpacity(1.0)
        self.show()
        self._dfp_reposition()
        self.raise_()
        self.update()
        self._dfp_fade_timer.stop()
        self._dfp_hide_timer.start(max(800, int(duration_ms or 6000)))

    def dfp_show_html(self, html_text: str, duration_ms: Optional[int] = None, anchor: Any = None) -> None:
        """★ v1.1.12：显示一小段 HTML（能染色 / 加粗 / 分两行）。

        量高度与画内容都用 `QTextDocument`，并且**沿用同一组 `DFP_BUBBLE_*` 内边距常量**
        ⇒ 「能画字的地方 ≥ 文字需要的高度」这条不变量对 HTML 一样成立（不会切字）。
        """
        html_text = ("" if html_text is None else str(html_text)).strip()
        if not html_text:
            return
        if anchor is not None:
            self.dfp_attach(anchor)
        self._dfp_text = html_text
        self._dfp_html = True
        self._dfp_show_count += 1
        self._dfp_alpha = 1.0
        self._dfp_holding = True
        self._dfp_size = self._dfp_measure_text()
        self._dfp_apply_geometry()
        self.setWindowOpacity(1.0)
        self.show()
        self._dfp_reposition()
        self.raise_()
        self.update()
        self._dfp_fade_timer.stop()
        self._dfp_hide_timer.start(max(800, int(duration_ms or 6000)))

    def dfp_hide_now(self) -> None:
        self._dfp_holding = False
        self._dfp_hide_timer.stop()
        self._dfp_fade_timer.stop()
        self._dfp_alpha = 0.0
        self.hide()

    def _dfp_start_fade_out(self) -> None:
        self._dfp_holding = False
        self._dfp_fade_timer.start()

    def _dfp_step_fade(self) -> None:
        self._dfp_alpha -= 0.11
        if self._dfp_alpha <= 0.02:
            self._dfp_alpha = 0.0
            self._dfp_fade_timer.stop()
            self.hide()
            return
        self.setWindowOpacity(self._dfp_alpha)
        self.update()

    # --- 尺寸与位置 ---
    def dfp_text_area_width(self) -> int:
        """气泡里文字能用的宽度（去掉左右内边距与边框）。"""
        return max(80, int(self._dfp_max_width - 2 * DFP_BUBBLE_PAD_X - 2 * DFP_BUBBLE_BORDER))

    def dfp_text_need_height(self, text: str = "") -> int:
        """这段文字按当前字号 / 最大宽度排版后**真正需要**多高（字不能被切，所以这是硬下限）。"""
        content = self._dfp_text if not text else str(text)
        if self._dfp_html and not text:
            return self.dfp_html_need_height(content)
        metrics = QFontMetrics(dfp_desktop_font(self._dfp_font_size))
        rect = metrics.boundingRect(
            QRect(0, 0, self.dfp_text_area_width(), 20000),
            dfp_text_flags(0x1000, 0x0080),  # TextWordWrap | AlignVCenter
            content,
        )
        return int(max(1, rect.height()))

    def dfp_html_document(self, html_text: str = "") -> Any:
        """★ v1.1.12：按当前字号和可用宽度建一个 QTextDocument（量高度与画内容共用同一份）。"""
        doc = QTextDocument()
        doc.setDefaultFont(dfp_desktop_font(self._dfp_font_size))
        try:
            doc.setDocumentMargin(0.0)
        except Exception:
            pass
        doc.setHtml(html_text if html_text else self._dfp_text)
        doc.setTextWidth(float(self.dfp_text_area_width()))
        return doc

    def dfp_html_need_height(self, html_text: str = "") -> int:
        """HTML 内容真正需要的高度（向上取整，宁可多给一个像素也不切字）。"""
        try:
            height = float(self.dfp_html_document(html_text).size().height())
        except Exception:
            return int(self.dfp_text_need_height(""))
        return int(max(1, math.ceil(height - 1e-6)))

    def dfp_html_need_width(self, html_text: str = "") -> int:
        """HTML 内容真正需要的宽度（用于把气泡缩到刚好）。"""
        try:
            width = float(self.dfp_html_document(html_text).idealWidth())
        except Exception:
            return self.dfp_text_area_width()
        return int(min(self.dfp_text_area_width(), max(1.0, math.ceil(width))))

    def dfp_text_rect(self) -> QRectF:
        """气泡内**实际画字**的区域（paintEvent 用的就是它，测试也拿它比对高度）。"""
        body = QRectF(
            DFP_BUBBLE_BORDER,
            DFP_BUBBLE_BORDER,
            max(1.0, self.width() - 2 * DFP_BUBBLE_BORDER),
            max(1.0, self.height() - DFP_BUBBLE_TAIL - DFP_BUBBLE_BORDER),
        )
        return body.adjusted(DFP_BUBBLE_PAD_X, DFP_BUBBLE_PAD_Y, -DFP_BUBBLE_PAD_X, -DFP_BUBBLE_PAD_Y)

    def dfp_text_space_height(self) -> int:
        """气泡内实际能画字的高度（= dfp_text_rect().height()，取整后给你比的）。"""
        return int(self.dfp_text_rect().height())

    def _dfp_measure_text(self) -> QSize:
        """算气泡该多大。

        ★ 高度 = 文字真正需要的高度 + 上下内边距 + 边框 + **尾巴** + 容错，
          量法与画法共用上面那组常量，保证「能画字的地方 ≥ 文字需要的高度」。
        ★ v1.1.12：内容是 HTML 时改用 QTextDocument 量（内边距/尾巴常量完全一致）。
        """
        if self._dfp_html:
            text_w = self.dfp_html_need_width()
            text_h = self.dfp_html_need_height()
            width = int(min(self._dfp_max_width, text_w + 2 * DFP_BUBBLE_PAD_X + 2 * DFP_BUBBLE_BORDER + 2))
            height = int(text_h + 2 * DFP_BUBBLE_PAD_Y + 2 * DFP_BUBBLE_BORDER + DFP_BUBBLE_TAIL + DFP_BUBBLE_EXTRA_H)
            return QSize(max(96, width), max(DFP_BUBBLE_MIN_H, height))
        metrics = QFontMetrics(dfp_desktop_font(self._dfp_font_size))
        area_w = self.dfp_text_area_width()
        laid = metrics.boundingRect(
            QRect(0, 0, area_w, 20000),
            dfp_text_flags(0x1000, 0x0080),
            self._dfp_text,
        )
        text_w = int(min(area_w, max(1, laid.width())))
        text_h = int(max(1, laid.height()))
        width = int(min(self._dfp_max_width, text_w + 2 * DFP_BUBBLE_PAD_X + 2 * DFP_BUBBLE_BORDER + 2))
        height = int(text_h + 2 * DFP_BUBBLE_PAD_Y + 2 * DFP_BUBBLE_BORDER + DFP_BUBBLE_TAIL + DFP_BUBBLE_EXTRA_H)
        return QSize(max(96, width), max(DFP_BUBBLE_MIN_H, height))

    def _dfp_apply_geometry(self) -> None:
        self.resize(self._dfp_size)

    def _dfp_reposition(self) -> None:
        anchor_rect = self._dfp_anchor_rect()
        if not anchor_rect.isValid():
            return
        tail = 12
        x = anchor_rect.center().x() - self.width() // 2
        y = anchor_rect.top() - self.height() - tail // 2
        screen = None
        try:
            app = QApplication.instance()
            if app is not None:
                screen = app.screenAt(QPoint(anchor_rect.center().x(), anchor_rect.center().y()))
        except Exception:
            screen = None
        if screen is None:
            rects = dfp_screen_available_rects()
            available = rects[0] if rects else QRect(0, 0, 1920, 1040)
        else:
            available = QRect(screen.availableGeometry())
        if y < available.top():
            y = anchor_rect.bottom() + tail // 2
        x = int(dfp_clamp(x, available.left() + 4, max(available.left() + 4, available.right() - self.width() - 4)))
        self.move(x, y)

    def _dfp_anchor_rect(self) -> QRect:
        if self._dfp_anchor is not None:
            try:
                rect = self._dfp_anchor()
                if isinstance(rect, QRect) and rect.isValid():
                    return QRect(rect)
            except Exception:
                pass
        return QRect()

    # --- 绘制 ---
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        try:
            painter.setRenderHint(dfp_enum_value(0, lambda: QPainter.RenderHint.Antialiasing, lambda: QPainter.Antialiasing), True)
            painter.setOpacity(max(0.0, min(1.0, self._dfp_alpha)))
            tail = DFP_BUBBLE_TAIL
            body = QRectF(
                DFP_BUBBLE_BORDER,
                DFP_BUBBLE_BORDER,
                max(1.0, self.width() - 2 * DFP_BUBBLE_BORDER),
                max(1.0, self.height() - tail - DFP_BUBBLE_BORDER),
            )
            border = dfp_color(self._dfp_colors["border"])
            painter.setPen(dfp_line_pen(dfp_color_alpha(border, 220), 1.6))
            painter.setBrush(QBrush(dfp_color_alpha(self._dfp_colors["fill"], 242)))
            painter.drawRoundedRect(body, 12.0, 12.0)

            # 指向下方的小尾巴
            anchor_rect = self._dfp_anchor_rect()
            tip_x = self.width() / 2.0
            if anchor_rect.isValid():
                tip_x = dfp_clamp(anchor_rect.center().x() - self.x(), 22.0, self.width() - 22.0)
            tail_path = QPainterPath()
            tail_path.moveTo(tip_x - 8.0, body.bottom() - 1.0)
            tail_path.lineTo(tip_x, body.bottom() + tail - 2.0)
            tail_path.lineTo(tip_x + 8.0, body.bottom() - 1.0)
            tail_path.closeSubpath()
            painter.drawPath(tail_path)

            painter.setPen(QPen(dfp_color(self._dfp_colors["text"])))
            painter.setFont(dfp_desktop_font(self._dfp_font_size))
            if self._dfp_html:
                # ★ v1.1.12：HTML（余额变化提示要染色）用 QTextDocument 画；
                #   不设 clip，宁可溢出来也不把字剪掉（与下面 TextDontClip 一个思路）。
                doc = self.dfp_html_document()
                text_rect = self.dfp_text_rect()
                painter.save()
                painter.translate(text_rect.topLeft())
                doc.drawContents(painter)
                painter.restore()
            else:
                # ★ TextDontClip(0x0200)：万一还是不够高，宁可溢出来也不把字剪掉（老代码没有它）
                painter.drawText(
                    self.dfp_text_rect().toRect(),
                    dfp_text_flags(0x1000, 0x0200, 0x0084),  # TextWordWrap | TextDontClip | AlignCenter
                    self._dfp_text,
                )
        finally:
            painter.end()


# =============================================================================
#  十八、桌宠窗口（无边框 / 透明 / 置顶 / 可拖动；素材模式与矢量模式共用）
# =============================================================================

DFP_ACTION_DURATIONS: Dict[Any, float] = {
    DFPAction.IDLE: 0.0,
    DFPAction.WALK: 0.0,  # 0 表示「不自动结束」，由控制器决定什么时候停下
    DFPAction.JUMP: 1.1,
    DFPAction.WAVE: 1.6,
    DFPAction.DANCE: 3.4,
    DFPAction.SIT: 4.0,
    DFPAction.SLEEP: 0.0,
    DFPAction.DRAG: 0.0,
    DFPAction.EAT: 2.4,
    DFPAction.SPIN: 1.8,
}

# 动作 / 表情 → **新形象图 id**（清单里没有这个 id 就自动跳过）
#   ★ v1.1.35：以前这里填的是「9 个表情」的 id（smile / sleepy / heart …）；
#   新设计的形象图按**台词类别**命名，所以这里改填最贴切的那张场景图 ——
#   拖动时「举手喊停」、吃饭时「接小鱼干」、睡觉时「蜷成一团」…… 比原来那 9 张通用脸贴题得多。
DFP_ACTION_ASSET: Dict[Any, str] = {
    DFPAction.IDLE: "",
    DFPAction.WALK: "",
    DFPAction.JUMP: "happy_jump",
    DFPAction.WAVE: "greet_wave",
    DFPAction.DANCE: "excited_burst",
    DFPAction.SIT: "bored_table",
    DFPAction.SLEEP: "sleep_curl",
    DFPAction.DRAG: "pet_stop",
    DFPAction.EAT: "feed_fish",
    DFPAction.SPIN: "cute_play_tail",
}
DFP_EXPRESSION_ASSET: Dict[Any, str] = {
    DFPExpression.NORMAL: "",
    DFPExpression.HAPPY: "happy_jump",
    DFPExpression.SAD: "sad_tear",
    DFPExpression.ANGRY: "cute_mad_back",
    DFPExpression.SLEEPY: "sleepy_yawn",
    DFPExpression.SURPRISED: "error_panic",
    DFPExpression.LOVE: "love_heart",
    DFPExpression.DIZZY: "dead_faint",
    DFPExpression.TALK: "idle_bubble",
}

# 素材画面里「角色本体」所占的比例（决定怎么缩放才能和立绘一样大）
DFP_ASSET_CHAR_BOX: Dict[str, Tuple[float, float, float]] = {
    # key: (头顶位置, 脚底位置, 高度占比)  —— 归一化到画面自身的宽高
    "square": (0.05, 0.95, 0.90),
    "wide": (0.20, 0.85, 0.65),
}
DFP_ASSET_CHAR_FILL = 0.78  # 角色高度占窗口高度的比例
DFP_ASSET_FEET_RATIO = 0.94  # 脚底在窗口里的相对高度


class DFPPetWidget(QWidget):
    """桌宠本体窗口。"""

    dfpClicked = Signal()
    dfpDoubleClicked = Signal()
    dfpWheelZoom = Signal(int)
    dfpContextMenu = Signal(QPoint)
    dfpMoved = Signal(QPoint)
    dfpPressed = Signal()
    dfpDragStarted = Signal()
    dfpDragFinished = Signal()
    dfpActionFinished = Signal(str)
    dfpScaleChanged = Signal(float)

    DFP_MIN_SCALE = 0.35
    DFP_MAX_SCALE = 3.0

    def __init__(self, logger: Optional[DFPLogger] = None):
        super().__init__(None)
        self._dfp_logger = logger
        self._dfp_renderer = DFPPetRenderer("ocean")
        self._dfp_scale = 1.0
        self._dfp_opacity = 1.0
        self._dfp_locked = False
        self._dfp_drag_enabled = True
        self._dfp_keep_on_screen = True
        self._dfp_wheel_zoom_enabled = True
        self._dfp_flip = False
        self._dfp_on_top = True
        self._dfp_asleep = False
        self._dfp_fps = 30
        self._dfp_spout_enabled = True
        # --- 素材渲染（新形象：大肥鱼素材表）---
        self._dfp_library: Optional[DFPAssetLibrary] = None
        self._dfp_render_mode = "assets"
        self._dfp_asset_key = ""
        self._dfp_asset_item: Optional[Dict[str, Any]] = None
        self._dfp_asset_movie: Optional[Any] = None
        self._dfp_asset_until = 0.0
        self._dfp_asset_label = ""
        self._dfp_asset_zoom = 0.0
        self._dfp_asset_last_error = ""
        self._dfp_asset_plays = 0
        # --- ★ v1.1.35：台词驱动的**形象图**（`鲸宝形象\类别\` 那 78 张）---
        self._dfp_say_face_id = ""  # 台词让它换的那张形象（清单 id，如 `love_heart`）
        self._dfp_say_face_until = 0.0
        # --- ★ v1.1.36：状态形象（以前是「非人形鲸鱼形态覆盖层」，现在是新形象图）---
        self._dfp_form_enabled = True
        self._dfp_form_sleep = True
        self._dfp_form_state = ""  # 控制器给的一次性形态（'' = 没有）
        self._dfp_form_until = 0.0  # 到期时间（0 = 一直挂着，等 clear）
        self._dfp_form_drawn = ""  # 这一帧实际画了哪个形态（状态面板/测试用）
        self._dfp_state = DFPRenderState()
        self._dfp_expression_until = 0.0
        self._dfp_speaking_until = 0.0
        self._dfp_action_started = 0.0
        self._dfp_action_duration = 0.0
        self._dfp_blink_start = 0.0
        self._dfp_blink_next = 0.0
        self._dfp_last_frame = time.monotonic()
        self._dfp_press_pos: Optional[QPoint] = None
        self._dfp_press_global = QPoint()
        self._dfp_drag_origin = QPoint()
        self._dfp_dragging = False
        self._dfp_drag_moved = False
        self._dfp_click_timer = QTimer(self)
        self._dfp_click_timer.setSingleShot(True)
        self._dfp_click_timer.setInterval(220)
        self._dfp_click_timer.timeout.connect(self._dfp_emit_pending_click)

        flags = (
            dfp_enum_value(0, lambda: Qt.WindowType.Tool, lambda: Qt.Tool)
            | dfp_enum_value(0, lambda: Qt.WindowType.FramelessWindowHint, lambda: Qt.FramelessWindowHint)
            | dfp_enum_value(0, lambda: Qt.WindowType.WindowStaysOnTopHint, lambda: Qt.WindowStaysOnTopHint)
        )
        self.setWindowFlags(flags)
        self.setAttribute(dfp_enum_value(0, lambda: Qt.WidgetAttribute.WA_TranslucentBackground, lambda: Qt.WA_TranslucentBackground), True)
        self.setAttribute(dfp_enum_value(0, lambda: Qt.WidgetAttribute.WA_ShowWithoutActivating, lambda: Qt.WA_ShowWithoutActivating), True)
        self.setAttribute(dfp_enum_value(0, lambda: Qt.WidgetAttribute.WA_AlwaysShowToolTips, lambda: Qt.WA_AlwaysShowToolTips), True)
        self.setMouseTracking(True)
        self.setWindowTitle(DFP_APP_TITLE)
        self.setContextMenuPolicy(dfp_enum_value(0, lambda: Qt.ContextMenuPolicy.DefaultContextMenu, lambda: Qt.DefaultContextMenu))
        self.setCursor(dfp_cursor_open_hand())

        size = self.dfp_canvas_size(self._dfp_scale)
        self.resize(size)
        self._dfp_timer = QTimer(self)
        self._dfp_timer.timeout.connect(self._dfp_advance_frame)
        self._dfp_tune_frame_rate()
        self._dfp_state.palette = self._dfp_renderer.dfp_palette_name()
        now = time.monotonic()
        self._dfp_blink_next = now + random.uniform(2.0, 5.0)
        self._dfp_last_frame = now

    # --- 尺寸与缩放 ---
    def dfp_canvas_size(self, scale: float) -> QSize:
        return QSize(max(24, int(DFP_CANVAS_W * scale)), max(28, int(DFP_CANVAS_H * scale)))

    def dfp_scale(self) -> float:
        return self._dfp_scale

    def dfp_apply_scale(self, scale: float, keep_anchor: bool = True) -> float:
        new_scale = dfp_clamp(scale, self.DFP_MIN_SCALE, self.DFP_MAX_SCALE)
        if abs(new_scale - self._dfp_scale) < 1e-6 and self.width() > 0:
            return self._dfp_scale
        old_rect = QRect(self.geometry())
        self._dfp_scale = new_scale
        self.resize(self.dfp_canvas_size(new_scale))
        if keep_anchor and old_rect.isValid():
            # 以底边中点为锚点放大缩小，视觉上「原地长大」
            # 注意 bottom() 是含端点的，这里用 y+height 才能保证底边不飘
            new_x = old_rect.center().x() - self.width() // 2
            new_y = old_rect.y() + old_rect.height() - self.height()
            self.move(new_x, new_y)
        self.dfpScaleChanged.emit(self._dfp_scale)
        self.update()
        return self._dfp_scale

    def dfp_set_opacity(self, value: float) -> None:
        self._dfp_opacity = dfp_clamp(float(value), 0.15, 1.0)
        self.setWindowOpacity(self._dfp_opacity)

    def dfp_opacity(self) -> float:
        return self._dfp_opacity

    def dfp_set_renderer_palette(self, palette: str) -> None:
        self._dfp_renderer.dfp_set_palette(palette)
        self._dfp_state.palette = self._dfp_renderer.dfp_palette_name()
        self.update()

    def dfp_palette_name(self) -> str:
        return self._dfp_renderer.dfp_palette_name()

    def dfp_renderer(self) -> DFPPetRenderer:
        return self._dfp_renderer

    def dfp_set_flip(self, enabled: bool) -> None:
        self._dfp_flip = bool(enabled)
        self._dfp_state.flip = self._dfp_flip
        self.update()

    def dfp_is_flipped(self) -> bool:
        return self._dfp_flip

    def dfp_set_locked(self, locked: bool) -> None:
        self._dfp_locked = bool(locked)
        if self._dfp_locked:
            self._dfp_dragging = False
            self.setCursor(dfp_cursor_open_hand(True))
        else:
            self.setCursor(dfp_cursor_open_hand(False))

    def dfp_is_locked(self) -> bool:
        return self._dfp_locked

    def dfp_set_drag_enabled(self, enabled: bool) -> None:
        self._dfp_drag_enabled = bool(enabled)

    def dfp_set_keep_on_screen(self, enabled: bool) -> None:
        """拖动时是否把窗口夹在屏幕内（对应 keep_on_screen 设置）。"""
        self._dfp_keep_on_screen = bool(enabled)

    def dfp_is_keep_on_screen(self) -> bool:
        return self._dfp_keep_on_screen

    def dfp_is_dragging(self) -> bool:
        return bool(self._dfp_dragging)

    def dfp_is_pressed(self) -> bool:
        return self._dfp_press_pos is not None

    def dfp_set_wheel_zoom_enabled(self, enabled: bool) -> None:
        self._dfp_wheel_zoom_enabled = bool(enabled)

    def dfp_set_shadow(self, enabled: bool) -> None:
        self._dfp_state.show_shadow = bool(enabled)
        self.update()

    def dfp_set_spout(self, enabled: bool) -> None:
        self._dfp_spout_enabled = bool(enabled)
        self.update()

    def dfp_set_nametag(self, text: str) -> None:
        self._dfp_state.nametag = str(text or "")
        self.update()

    def dfp_set_nametag_color(self, color: Any) -> None:
        self._dfp_state.nametag_color = str(color or "#4D6BFE")
        self.update()

    def dfp_set_always_on_top(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._dfp_on_top and self.isVisible():
            return
        self._dfp_on_top = enabled
        was_visible = self.isVisible()
        flags = self.windowFlags()
        hint = dfp_enum_value(0, lambda: Qt.WindowType.WindowStaysOnTopHint, lambda: Qt.WindowStaysOnTopHint)
        if enabled:
            flags |= hint
        else:
            flags &= ~hint
        self.setWindowFlags(flags)
        if was_visible:
            self.show()

    def dfp_set_frame_rate(self, fps: int) -> None:
        self._dfp_fps = int(dfp_clamp(fps, 5, 60))
        self._dfp_tune_frame_rate()

    # --- 素材渲染 ---
    def dfp_set_asset_library(self, library: Optional[DFPAssetLibrary]) -> None:
        self._dfp_library = library
        self._dfp_asset_key = ""
        self._dfp_asset_item = None
        self.dfp_stop_asset_movie()
        self.update()

    def dfp_asset_library(self) -> Optional[DFPAssetLibrary]:
        return self._dfp_library

    def dfp_set_render_mode(self, mode: str) -> None:
        mode = str(mode or "assets")
        if mode not in ("assets", "auto", "vector"):
            mode = "assets"
        self._dfp_render_mode = mode
        self._dfp_asset_key = ""
        self.update()

    def dfp_render_mode(self) -> str:
        return self._dfp_render_mode

    def dfp_set_anim_zoom(self, zoom: float) -> None:
        self._dfp_asset_zoom = max(0.0, float(zoom or 0.0))
        self.update()

    def dfp_asset_ready(self) -> bool:
        return bool(self._dfp_library is not None and self._dfp_library.dfp_is_ready())

    def dfp_asset_key(self) -> str:
        return self._dfp_asset_key

    def dfp_asset_label(self) -> str:
        return self._dfp_asset_label

    def dfp_asset_play_count(self) -> int:
        return self._dfp_asset_plays

    def dfp_asset_last_error(self) -> str:
        return self._dfp_asset_last_error

    # --- ★ v1.1.35：台词驱动的「形象图」（整张新形象，一张图 = 一个完整造型）---
    def dfp_show_say_face(self, face_id: str, hold_seconds: float = DFP_SAY_FACE_HOLD) -> bool:
        """直接换成清单里的某张**形象图**（`love_heart` / `night_whisper` / `cute_hug_beg` …），
        保持 `hold_seconds` 秒后自然回落到设置里选的那张基础形象。

        旧设计里这里是「摆一副表情 + 换一张立绘」两件事，新设计把两者合成一张整图：
        78 张图各自就是一个完整造型（含表情、动作、随身道具），所以只需要这一个槽位。
        清单里没这个 id 就返回 False（调用方可以什么都不做）。
        """
        wanted = str(face_id or "").strip()
        library = self._dfp_library
        if not wanted or library is None or library.dfp_face(wanted) is None:
            return False
        hold = dfp_safe_float(hold_seconds, DFP_SAY_FACE_HOLD)
        self._dfp_say_face_id = wanted
        self._dfp_say_face_until = time.monotonic() + hold if hold > 0 else 0.0
        self.update()
        return True

    def dfp_say_face_key(self) -> str:
        """现在挂着的那张「台词形象」id（过期 / 没有返回空串；诊断与测试用）。"""
        if not self._dfp_say_face_id:
            return ""
        if self._dfp_say_face_until and time.monotonic() >= self._dfp_say_face_until:
            self._dfp_say_face_id = ""
            self._dfp_say_face_until = 0.0
            return ""
        return self._dfp_say_face_id

    def dfp_clear_say_face(self) -> bool:
        """收掉「台词形象」（开关关掉 / 手动复位时用）。"""
        if not self._dfp_say_face_id:
            return False
        self._dfp_say_face_id = ""
        self._dfp_say_face_until = 0.0
        self.update()
        return True

    def dfp_character_item(self) -> Optional[Dict[str, Any]]:
        """现在该画哪张基础形象（= 设置里选的那张；`asset_character` 认不出时回第一张）。"""
        library = self._dfp_library
        if library is None:
            return None
        return library.dfp_character()

    def dfp_play_asset(self, item: Optional[Dict[str, Any]], label: str = "", duration: float = 0.0) -> bool:
        """播放素材表里的一个动作片（item 来自 DFPAssetLibrary.dfp_animations()）。"""
        if not self.dfp_asset_ready() or not isinstance(item, dict):
            return False
        file_key = str(item.get("gif") or item.get("file") or "")
        if not file_key:
            return False
        self._dfp_asset_item = dict(item)
        self._dfp_asset_label = str(label or item.get("label") or "")
        self._dfp_asset_key = "anim:%s" % str(item.get("id") or file_key)
        self._dfp_asset_until = time.monotonic() + max(1.0, float(duration or 0.0)) if duration else 0.0
        self._dfp_asset_plays += 1
        self.dfp_stop_asset_movie(keep_item=True)
        # ★ v1.1.34：静态 PNG（新设计的 106 个动作是单帧）不建 QMovie ——
        #   交给 `_dfp_draw_asset` 当**定格造型**画，口径与 `_dfp_sync_asset` 里的 `is_movie` 一致
        #   （Qt 其实也认 PNG 当 1 帧动画，但那样 `movie.frameChanged` 永远不响、还白占缓存）
        if file_key.lower().endswith((".gif", ".webp")):
            movie = self._dfp_library.dfp_movie(file_key)
            if movie is not None:
                try:
                    movie.frameChanged.connect(self.update)
                    movie.start()
                    self._dfp_asset_movie = movie
                except Exception:
                    self._dfp_asset_movie = None
        self._dfp_tune_frame_rate()
        self.update()
        return True

    def dfp_stop_asset_movie(self, keep_item: bool = False) -> None:
        movie = self._dfp_asset_movie
        self._dfp_asset_movie = None
        if movie is not None:
            try:
                movie.stop()
            except Exception:
                pass
        if not keep_item:
            self._dfp_asset_item = None
            self._dfp_asset_key = ""
            self._dfp_asset_until = 0.0
            self._dfp_asset_label = ""

    def dfp_cancel_asset_play(self) -> None:
        self.dfp_stop_asset_movie()
        self.update()

    # --- ★ v1.1.36：状态形象（睡觉 / 忙 / 等 / 休息 / 庆祝 / 出错）---
    def dfp_set_form_enabled(self, enabled: bool) -> None:
        """总开关：关掉就永远用设置里选的基础形象（不切状态形象）。"""
        self._dfp_form_enabled = bool(enabled)
        self._dfp_form_drawn = ""
        self.update()

    def dfp_form_enabled(self) -> bool:
        return bool(self._dfp_form_enabled)

    def dfp_set_form_sleep(self, enabled: bool) -> None:
        """「睡觉时切成睡着那张形象」这一条单独可关（其余状态是一次性的，关总开关即可）。"""
        self._dfp_form_sleep = bool(enabled)
        self._dfp_form_drawn = ""
        self.update()

    def dfp_form_sleep(self) -> bool:
        return bool(self._dfp_form_sleep)

    def dfp_set_form(self, state: str, seconds: float = 0.0) -> bool:
        """临时切到某个状态形象（`seconds` = 0 表示一直挂着，等 `dfp_clear_form()`）。

        返回是否真的切过去了（关掉开关 / 状态名不对 / 库里缺那张图都返回 False）。
        """
        wanted = str(state or "").strip().lower()
        if not wanted:
            return self.dfp_clear_form()
        library = self._dfp_library
        if not self._dfp_form_enabled or library is None or library.dfp_form(wanted) is None:
            return False
        self._dfp_form_state = wanted
        self._dfp_form_until = time.monotonic() + max(0.0, float(seconds or 0.0)) if seconds else 0.0
        self._dfp_form_drawn = ""  # 下一帧重新算（诊断口径：永远反映“这一帧画了什么”）
        self.update()
        return True

    def dfp_clear_form(self, state: str = "") -> bool:
        """清掉一次性形态（给了 `state` 就只在当前正是它的时候清）。"""
        wanted = str(state or "").strip().lower()
        if wanted and self._dfp_form_state != wanted:
            return False
        if not self._dfp_form_state:
            return False
        self._dfp_form_state = ""
        self._dfp_form_until = 0.0
        self._dfp_form_drawn = ""
        self.update()
        return True

    def dfp_form_state(self) -> str:
        """现在该画哪个状态形象（'' = 画设置里选的基础形象）。★ 优先级：一次性状态 > 睡着。"""
        if not self._dfp_form_enabled:
            return ""
        library = self._dfp_library
        if library is None or not library.dfp_is_ready():
            return ""
        if self._dfp_form_state:
            if self._dfp_form_until <= 0.0 or time.monotonic() < self._dfp_form_until:
                return self._dfp_form_state
            self._dfp_form_state = ""
            self._dfp_form_until = 0.0
        if self._dfp_asleep and self._dfp_form_sleep and library.dfp_form("sleeping") is not None:
            return "sleeping"
        return ""

    def dfp_form_active(self) -> str:
        """这一帧实际画出去的状态形象（诊断用：`face:<id>`；没有就空串）。"""
        return self._dfp_form_drawn

    def dfp_form_face_key(self) -> str:
        """当前状态该画哪张**新形象图**（'' = 没状态 / 关掉了 / 库里缺这张图）。

        ★ v1.1.36 的单一入口：`_dfp_asset_plan()` 与测试都走它；
          以前这里是去读素材5 的形态 SVG（旧鲸鱼），现在换成新形象集里的图。
        """
        state = self.dfp_form_state()
        if not state:
            return ""
        library = self._dfp_library
        if library is None:
            return ""
        try:
            return library.dfp_form_face(state)
        except Exception:
            return ""

    def dfp_form_label(self) -> str:
        state = self.dfp_form_state()
        return DFP_FORM_LABELS.get(state, state) if state else ""

    # --- 素材渲染内部 ---
    def dfp_asset_is_landscape(self, item: Dict[str, Any], source: QPixmap) -> bool:
        width = source.width() or dfp_safe_int((item.get("size") or [0, 0])[0], 0)
        height = source.height() or dfp_safe_int((item.get("size") or [0, 0])[1], 0)
        if not width or not height:
            return False
        return width > height * DFP_ASSET_ANIM_ASPECT

    def _dfp_asset_plan(self) -> Tuple[Optional[Dict[str, Any]], str]:
        """决定现在该画哪张素材：显式动作片 > **状态形象** > 台词形象图 > 动作/状态形象 > 基础形象。"""
        library = self._dfp_library
        if library is None or not library.dfp_is_ready():
            return (None, "")
        # ★ v1.1.36：显式动作片先按老规矩处理**过期**，但不急着返回 —— 状态形象比它更优先
        #   （v1.1.15 的「形态覆盖层」就是画在一切之上的：睡觉时哪怕刚演过动作，
        #    屏幕上也是睡着那张；沿用这个优先级，免得状态图被一段自主行为顶掉）
        anim_item: Optional[Dict[str, Any]] = None
        if self._dfp_asset_item is not None and self._dfp_asset_key.startswith("anim:"):
            if self._dfp_asset_until <= 0.0 or time.monotonic() < self._dfp_asset_until:
                anim_item = self._dfp_asset_item
            else:
                self._dfp_asset_item = None
                self._dfp_asset_key = ""
                self.dfp_stop_asset_movie()
        self._dfp_form_drawn = ""
        # ★ v1.1.36：状态形象（睡觉 / 忙 / 等 / 休息 / 庆祝 / 出错）—— 新形象集里的图；
        #   状态优先于台词形象（睡着时就是「蜷成一团」那张，不再画素材5 的旧鲸鱼）
        form_face = self.dfp_form_face_key()
        if form_face:
            item = library.dfp_face(form_face)
            if item:
                self._dfp_form_drawn = "face:%s" % form_face
                # 状态面板/日志里显示的「当前素材」：中文名（如「蜷成一团」）
                self._dfp_asset_label = str(item.get("label") or dfp_face_label(form_face))
                return (item, "face:%s" % form_face)
        if anim_item is not None:
            return (anim_item, self._dfp_asset_key)
        # ★ v1.1.35：台词让它换的那张形象图（到期自动回落）
        say_face = self.dfp_say_face_key()
        if say_face:
            item = library.dfp_face(say_face)
            if item:
                return (item, "face:%s" % say_face)
        candidates = [
            DFP_ACTION_ASSET.get(self._dfp_state.action, ""),
            DFP_EXPRESSION_ASSET.get(self._dfp_state.expression, ""),
        ]
        for key in candidates:
            if not key:
                continue
            # ★ v1.1.35：两张表的值换成了新形象 id（拖动 / 吃饭 / 睡觉 / 高兴…都有对应图）；
            #   旧的表情 id 还留一条退路（以后又把表情放回来也不至于什么都不画）
            item = library.dfp_face(key) or library.dfp_expression(key)
            if item:
                return (item, "face:%s" % key)
        item = self.dfp_character_item()
        if item:
            return (item, "base")
        return (None, "")

    def _dfp_sync_asset(self) -> None:
        item, key = self._dfp_asset_plan()
        if key == self._dfp_asset_key and item is not None:
            return
        self._dfp_asset_key = key
        self._dfp_asset_item = item
        if item is None:
            self.dfp_stop_asset_movie()
            return
        self._dfp_asset_last_error = ""  # 这次有素材可画，清掉上一次「素材不可用」的旧提示
        file_key = str(item.get("gif") or item.get("file") or "")
        is_movie = file_key.lower().endswith((".gif", ".webp")) and key.startswith(("expr:", "anim:", "face:", "base"))
        if self._dfp_asset_movie is not None and is_movie:
            try:
                if self._dfp_asset_movie.fileName().replace("/", os.sep).endswith(file_key.replace("/", os.sep)):
                    return
            except Exception:
                pass
        self.dfp_stop_asset_movie(keep_item=True)
        if is_movie:
            movie = self._dfp_library.dfp_movie(file_key)
            if movie is not None:
                try:
                    movie.frameChanged.connect(self.update)
                    movie.start()
                    self._dfp_asset_movie = movie
                except Exception:
                    self._dfp_asset_movie = None

    def _dfp_draw_asset(self, painter: QPainter, rect: QRectF) -> bool:
        if self._dfp_library is None or not self._dfp_asset_item:
            self._dfp_asset_last_error = "没有可用素材"
            return False
        item = self._dfp_asset_item
        source = None
        if self._dfp_asset_movie is not None:
            try:
                source = self._dfp_asset_movie.currentPixmap()
            except Exception:
                source = None
        if source is None or source.isNull():
            source = self._dfp_library.dfp_pixmap(str(item.get("gif") or item.get("file") or ""))
        if source is None or source.isNull():
            self._dfp_asset_last_error = "素材读取失败：%s" % item.get("file")
            return False
        self._dfp_blit_character(painter, rect, source, item)
        return True

    def _dfp_blit_character(self, painter: QPainter, rect: QRectF, source: QPixmap, item: Dict[str, Any], fit_width: float = 0.0) -> None:
        """把一张「角色图」按素材盒（DFP_ASSET_CHAR_BOX）摆到窗口里。

        ★ v1.1.15 从 `_dfp_draw_asset` 里抽出来的：人形素材（立绘/表情/动作片）与          非人形形态（鲸鱼 SVG）**必须用同一套装箱规矩**，否则切换形态时会跳一下大小/位置。
        `fit_width` > 0 时额外限制宽度占比（只给形态用：鲸鱼图有 2:1 的横图，
        只按高度缩放会超出画布被剪掉）。
        """
        image_w = max(1, source.width())
        image_h = max(1, source.height())
        landscape = self.dfp_asset_is_landscape(item, source)
        top_ratio, bottom_ratio, height_ratio = DFP_ASSET_CHAR_BOX["wide" if landscape else "square"]
        want_char_h = rect.height() * DFP_ASSET_CHAR_FILL
        scale = want_char_h / max(1.0, height_ratio * image_h)
        if self._dfp_asset_zoom > 0.0:
            scale *= self._dfp_asset_zoom
        if fit_width > 0.0:
            scale = min(scale, rect.width() * float(fit_width) / image_w)
        draw_w = image_w * scale
        draw_h = image_h * scale
        bob = float(self._dfp_state.walk_bob or 0.0)
        feet_y = rect.height() * DFP_ASSET_FEET_RATIO
        draw_x = rect.center().x() - draw_w / 2.0
        draw_y = feet_y - bottom_ratio * draw_h - bob

        painter.save()
        painter.setRenderHint(dfp_enum_value(0, lambda: QPainter.RenderHint.SmoothPixmapTransform, lambda: QPainter.SmoothPixmapTransform), True)
        if self._dfp_state.show_shadow:
            painter.setPen(dfp_no_pen())
            strength = dfp_clamp(1.0 - abs(bob) / 26.0, 0.35, 1.0)
            painter.setBrush(QBrush(dfp_color_alpha("#20304F", int(56 * strength))))
            painter.drawEllipse(
                QPointF(rect.center().x(), rect.height() * 0.965),
                rect.width() * 0.30 * strength,
                max(3.0, rect.height() * 0.022 * strength),
            )
        # 变换：以脚底为支点，保持与矢量模式一致的规矩
        anchor = QPointF(rect.center().x(), feet_y)
        painter.translate(anchor)
        if self._dfp_state.flip:
            painter.scale(-1.0, 1.0)
        if self._dfp_state.tilt:
            painter.rotate(float(self._dfp_state.tilt))
        if self._dfp_state.spin:
            factor = math.cos(dfp_clamp(float(self._dfp_state.spin), 0.0, 1.0) * math.pi * 2.0)
            painter.scale(0.16 if factor >= 0 else -0.16 if abs(factor) < 0.16 else factor, 1.0)
        squash = dfp_clamp(float(self._dfp_state.squash or 0.0), -0.35, 0.35)
        breathe = dfp_clamp(float(self._dfp_state.breathe or 0.0), -1.0, 1.0)
        stretch = dfp_clamp(float(self._dfp_state.stretch or 0.0), 0.0, 1.0)
        painter.scale(
            (1.0 + squash * 0.5 + breathe * 0.012) * (1.0 - stretch * 0.10),
            (1.0 - squash * 0.5 + breathe * 0.020) * (1.0 + stretch * 0.12),
        )
        painter.translate(-anchor)
        painter.drawPixmap(QRectF(draw_x, draw_y, draw_w, draw_h), source, QRectF(0.0, 0.0, float(image_w), float(image_h)))
        painter.restore()

    def _dfp_draw_form_disabled_note(self) -> None:
        """（占位说明）★ v1.1.36：非人形形态的**单独绘制路径已删除**。

        以前这里有个 `_dfp_draw_form()`：把素材5 的整只小鲸鱼 SVG 直接盖在人形素材上面
        （睡觉时最明显 —— 用户 2026-10-03 看到的就是它）。现在状态形象就是新形象集里的
        **同一批图**，由 `_dfp_asset_plan()` 挑图后走普通素材绘制，不需要（也不允许）另外一条路径。
        """

    # --- 状态查询 ---
    def dfp_bubble_anchor_rect(self) -> QRect:
        """气泡锚点：桌宠头顶靠上的区域。"""
        rect = QRect(self.geometry())
        head = QRect(rect.center().x() - max(20, rect.width() // 4), rect.top(), max(40, rect.width() // 2), max(16, rect.height() // 6))
        return head

    def dfp_current_action(self) -> Any:
        return self._dfp_state.action

    def dfp_play_action(self, action: Any, duration: Optional[float] = None) -> None:
        """播放动作：duration 为 0 表示不自动结束（走路/睡觉/被拎起）。"""
        if isinstance(action, str):
            action = DFP_ACTION_BY_NAME.get(action, DFPAction.IDLE)
        if not isinstance(action, DFPAction):
            action = DFPAction.IDLE
        now = time.monotonic()
        self._dfp_state.action = action
        self._dfp_state.action_progress = 0.0
        self._dfp_action_started = now
        self._dfp_action_duration = float(
            DFP_ACTION_DURATIONS.get(action, 1.5) if duration is None else max(0.0, float(duration))
        )
        if action == DFPAction.DRAG:
            self._dfp_state.expression = DFPExpression.SURPRISED
        elif action == DFPAction.EAT:
            self._dfp_state.expression = DFPExpression.HAPPY
        elif action == DFPAction.SLEEP:
            self._dfp_state.asleep = True
            self._dfp_state.expression = DFPExpression.SLEEPY
        elif action == DFPAction.DANCE:
            self._dfp_state.expression = DFPExpression.HAPPY
        elif action == DFPAction.JUMP:
            self._dfp_state.expression = DFPExpression.HAPPY
        self._dfp_tune_frame_rate()
        self.update()

    def dfp_action_progress(self) -> float:
        if self._dfp_action_duration <= 0.0:
            return 0.0
        elapsed = time.monotonic() - self._dfp_action_started
        return dfp_clamp(elapsed / self._dfp_action_duration, 0.0, 1.0)

    def dfp_set_expression(self, expression: Any, hold_seconds: float = 3.0) -> None:
        if isinstance(expression, str):
            expression = DFP_EXPRESSION_BY_NAME.get(expression, DFPExpression.NORMAL)
        if not isinstance(expression, DFPExpression):
            expression = DFPExpression.NORMAL
        self._dfp_state.expression = expression
        self._dfp_expression_until = time.monotonic() + max(0.0, float(hold_seconds)) if hold_seconds else 0.0
        self.update()

    def dfp_expression(self) -> Any:
        return self._dfp_state.expression

    def dfp_begin_talk(self, seconds: float = 4.0) -> None:
        self._dfp_speaking_until = time.monotonic() + max(0.5, float(seconds))
        self._dfp_state.speaking = True
        if self._dfp_state.expression == DFPExpression.NORMAL:
            self._dfp_state.expression = DFPExpression.TALK
            self._dfp_expression_until = self._dfp_speaking_until
        self._dfp_tune_frame_rate()

    def dfp_set_asleep(self, asleep: bool) -> None:
        asleep = bool(asleep)
        if asleep == self._dfp_asleep:
            return
        self._dfp_asleep = asleep
        self._dfp_state.asleep = asleep
        if asleep:
            self._dfp_state.expression = DFPExpression.SLEEPY
            self._dfp_expression_until = 0.0
            self.dfp_play_action(DFPAction.SLEEP)
            self._dfp_state.zzz = 1.0
        else:
            self._dfp_state.zzz = 0.0
            self._dfp_state.expression = DFPExpression.HAPPY
            self._dfp_expression_until = time.monotonic() + 3.0
            self.dfp_play_action(DFPAction.JUMP, 1.1)
        self._dfp_tune_frame_rate()
        self.update()

    def dfp_is_asleep(self) -> bool:
        return self._dfp_asleep

    def dfp_random_idle_action(self) -> Any:
        pool = [
            DFPAction.JUMP,
            DFPAction.WAVE,
            DFPAction.DANCE,
            DFPAction.SIT,
            DFPAction.SPIN,
            DFPAction.WALK,
            DFPAction.IDLE,
        ]
        action = dfp_random_pick(pool, DFPAction.IDLE)
        self.dfp_play_action(action)
        return action

    # --- 动画 ---
    def _dfp_tune_frame_rate(self) -> None:
        fps = self._dfp_fps
        if self._dfp_asleep:
            fps = min(fps, 12)
        elif self._dfp_state.action in (DFPAction.IDLE, DFPAction.SIT):
            fps = min(fps, 24)
        interval = max(16, int(1000.0 / max(5, fps)))
        if self._dfp_timer.interval() != interval:
            self._dfp_timer.setInterval(interval)
        if self.isVisible() and not self._dfp_timer.isActive():
            self._dfp_timer.start()

    def _dfp_advance_frame(self) -> None:
        now = time.monotonic()
        dt = dfp_clamp(now - self._dfp_last_frame, 0.0, 0.25)
        self._dfp_last_frame = now
        state = self._dfp_state
        state.time = float(state.time or 0.0) + dt
        state.breathe = math.sin(state.time * 1.55)
        state.tail_phase = math.sin(state.time * 1.05) * 0.30
        self._dfp_update_blink(now, state)
        self._dfp_apply_action_effects(now, dt, state)

        if self._dfp_speaking_until and now >= self._dfp_speaking_until:
            self._dfp_speaking_until = 0.0
            state.speaking = False
            if state.expression == DFPExpression.TALK:
                state.expression = DFPExpression.NORMAL
        if self._dfp_expression_until and now >= self._dfp_expression_until:
            self._dfp_expression_until = 0.0
            if not self._dfp_asleep:
                state.expression = DFPExpression.NORMAL
        if state.heart > 0.0:
            state.heart = max(0.0, state.heart - dt * 0.35)
        if state.star > 0.0:
            state.star = max(0.0, state.star - dt * 0.30)
        if self._dfp_spout_enabled and not self._dfp_asleep:
            state.spout = dfp_clamp(state.spout + dt * 0.55, 0.0, 1.0)
            if state.spout >= 1.0 and random.random() < 0.004:
                state.spout = 0.0
        else:
            state.spout = max(0.0, state.spout - dt * 0.8)
        self.update()

    def _dfp_update_blink(self, now: float, state: DFPRenderState) -> None:
        if self._dfp_asleep:
            state.blink = 1.0
            return
        if self._dfp_blink_start <= 0.0:
            if now >= self._dfp_blink_next:
                self._dfp_blink_start = now
                self._dfp_blink_next = now + random.uniform(2.2, 6.4)
            else:
                state.blink = 0.0
                return
        duration = 0.17
        progress = (now - self._dfp_blink_start) / duration
        if progress >= 1.0:
            self._dfp_blink_start = 0.0
            state.blink = 0.0
            return
        state.blink = math.sin(progress * math.pi)

    def _dfp_apply_action_effects(self, now: float, dt: float, state: DFPRenderState) -> None:
        action = state.action
        duration = self._dfp_action_duration
        elapsed = now - self._dfp_action_started
        progress = dfp_clamp(elapsed / duration, 0.0, 1.0) if duration > 0 else 0.0
        state.action_progress = progress
        state.walk_bob = 0.0
        state.squash = 0.0
        state.tilt = 0.0
        state.spin = 0.0
        state.stretch = 0.0
        state.mouth_open = 0.0

        if self._dfp_asleep or action == DFPAction.SLEEP:
            state.walk_bob = math.sin(state.time * 0.9) * 1.6
        elif action == DFPAction.IDLE:
            state.walk_bob = math.sin(state.time * 0.85) * 1.8
        elif action == DFPAction.WALK:
            state.walk_bob = abs(math.sin(state.time * 5.2)) * 5.0
            state.tilt = math.sin(state.time * 5.2) * 2.4
        elif action == DFPAction.JUMP:
            arc = math.sin(progress * math.pi)
            state.walk_bob = arc * 26.0
            state.squash = -0.16 * arc
        elif action == DFPAction.WAVE:
            state.walk_bob = math.sin(state.time * 2.2) * 2.2
        elif action == DFPAction.DANCE:
            state.tilt = math.sin(progress * math.pi * 6.0) * 12.0
            state.walk_bob = abs(math.sin(progress * math.pi * 6.0)) * 6.0
        elif action == DFPAction.SIT:
            state.walk_bob = -7.0
            state.squash = 0.18
        elif action == DFPAction.SPIN:
            state.spin = progress
        elif action == DFPAction.DRAG:
            state.stretch = 0.85
            state.tilt = math.sin(state.time * 6.0) * 7.0
        elif action == DFPAction.EAT:
            state.mouth_open = abs(math.sin(progress * math.pi * 5.0))
            state.squash = math.sin(progress * math.pi) * 0.08

        if state.speaking and action != DFPAction.EAT:
            state.mouth_open = 0.35 + abs(math.sin(state.time * 9.0)) * 0.65

        if duration > 0 and elapsed >= duration:
            finished = action
            state.action = DFPAction.IDLE
            self._dfp_action_duration = 0.0
            self._dfp_action_started = now
            self._dfp_tune_frame_rate()
            try:
                self.dfpActionFinished.emit(DFP_ACTION_LABELS.get(finished, "动作"))
            except Exception:
                pass

    # --- 绘制 ---
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        try:
            rect = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
            drawn = False
            # ★ v1.1.36：状态形象（睡觉 / 忙 / 等 / 休息 / 庆祝 / 出错）已经是**新形象图**，
            #   由 `_dfp_asset_plan()` 的那一层挑好图后走下面的素材绘制。
            #   （旧素材5 SVG 的单独绘制路径已删除 ⇒ 旧鲸鱼不可能再出现在桌宠身上）
            if self._dfp_render_mode != "vector":
                self._dfp_sync_asset()
                drawn = self._dfp_draw_asset(painter, rect)
            if not drawn:
                if self._dfp_render_mode == "assets" and not self.dfp_asset_ready():
                    self._dfp_asset_last_error = "素材表不可用，已自动改用代码绘制（请检查大肥鱼素材表目录）"
                self._dfp_renderer.dfp_render(painter, rect, self._dfp_state)
        except Exception:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_exception("桌宠绘制失败")
        finally:
            painter.end()

    # --- 鼠标 ---
    def mousePressEvent(self, event) -> None:  # noqa: N802
        button = dfp_enum_value(None, lambda: event.button())
        left = dfp_enum_value(1, lambda: Qt.MouseButton.LeftButton, lambda: Qt.LeftButton)
        if button == left:
            self._dfp_press_pos = dfp_event_pos(event)
            self._dfp_press_global = dfp_event_global_pos(event)
            self._dfp_drag_moved = False
            self._dfp_dragging = False
            try:
                self.dfpPressed.emit()
            except Exception:
                pass
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dfp_press_pos is None or self._dfp_locked or not self._dfp_drag_enabled:
            super().mouseMoveEvent(event)
            return
        global_pos = dfp_event_global_pos(event)
        if not self._dfp_dragging:
            # ★ 必须用「全局鼠标位移」判拖拽，不能用窗口内局部坐标：
            #   桌宠自己会走动 / 播素材动作时窗口在动，局部坐标跟着变，
            #   手没动也会被误判成拖拽 ⇒ 摸头怎么点都不触发。
            if (global_pos - self._dfp_press_global).manhattanLength() < 6:
                return
            self._dfp_dragging = True
            self._dfp_drag_moved = True
            # 记下「展开拖拽时窗口在哪」：之后一律用「原点 + 相对按下点的位移」绝对定位，
            # 既能首帧就跟手，又不会因为窗口中途自己走过而一按住就瞬移。
            self._dfp_drag_origin = QPoint(self.x(), self.y())
            self.dfp_play_action(DFPAction.DRAG)
            self.setCursor(dfp_cursor_closed_hand())
            try:
                self.dfpDragStarted.emit()
            except Exception:
                pass
        offset = global_pos - self._dfp_press_global
        target = QPoint(self._dfp_drag_origin.x() + offset.x(), self._dfp_drag_origin.y() + offset.y())
        if self._dfp_keep_on_screen:
            target = dfp_clamp_point_into_screen(target, self.size())
        self.move(target)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        pressed = self._dfp_press_pos is not None
        self._dfp_press_pos = None
        if not pressed:
            super().mouseReleaseEvent(event)
            return
        if not self._dfp_locked:
            self.setCursor(dfp_cursor_open_hand())
        self.dfp_set_expression(DFPExpression.NORMAL, 3.0)
        if self._dfp_dragging:
            self._dfp_dragging = False
            if self._dfp_state.action == DFPAction.DRAG:
                self.dfp_play_action(DFPAction.IDLE)
            self.dfpMoved.emit(self.pos())
            self.dfpDragFinished.emit()
        else:
            # 单击与双击区分：延迟 220ms，若期间来了双击就取消
            self._dfp_click_timer.start()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self._dfp_click_timer.stop()
        self.dfpDoubleClicked.emit()
        event.accept()

    def _dfp_emit_pending_click(self) -> None:
        self.dfpClicked.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self._dfp_wheel_zoom_enabled:
            super().wheelEvent(event)
            return
        delta = 0
        try:
            delta = int(event.angleDelta().y())
        except Exception:
            delta = 0
        if delta == 0:
            return
        self.dfpWheelZoom.emit(1 if delta > 0 else -1)
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        pos = event.globalPos() if hasattr(event, "globalPos") else QCursor.pos()
        self.dfpContextMenu.emit(pos)
        event.accept()

    def enterEvent(self, event) -> None:  # noqa: N802
        if not self._dfp_locked:
            self.setCursor(dfp_cursor_open_hand())
        super().enterEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        self._dfp_last_frame = time.monotonic()
        self._dfp_tune_frame_rate()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._dfp_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._dfp_timer.stop()
        self._dfp_click_timer.stop()
        super().closeEvent(event)


def dfp_cursor_open_hand(arrow: bool = False) -> QCursor:
    if arrow:
        shape = dfp_enum_value(0, lambda: Qt.CursorShape.ArrowCursor, lambda: Qt.ArrowCursor)
    else:
        shape = dfp_enum_value(0, lambda: Qt.CursorShape.OpenHandCursor, lambda: Qt.OpenHandCursor)
    return QCursor(shape)


def dfp_cursor_closed_hand() -> QCursor:
    return QCursor(dfp_enum_value(0, lambda: Qt.CursorShape.ClosedHandCursor, lambda: Qt.ClosedHandCursor))


DFP_ACTION_BY_NAME: Dict[str, Any] = {action.name: action for action in DFPAction}
DFP_EXPRESSION_BY_NAME: Dict[str, Any] = {expression.name: expression for expression in DFPExpression}

DFP_ACTION_DURATIONS_MS: Dict[Any, float] = {
    DFPAction.JUMP: 1.1,
    DFPAction.WAVE: 1.6,
    DFPAction.DANCE: 3.4,
    DFPAction.SIT: 4.0,
    DFPAction.EAT: 2.4,
    DFPAction.SPIN: 1.8,
}


# =============================================================================
#  十九、行为大脑（四条属性、等级、台词、自主行为；关掉程序期间同样计算衰减）
# =============================================================================

DFP_BRAIN_TICK_MS = 5000
DFP_BRAIN_SAVE_INTERVAL = 60.0
DFP_STATE_KV_KEY = "pet_state"
DFP_MAX_LEVEL = 99  # 内置修改器可拉到的最高等级
# 自主行为的类别权重（越大越常出现；吃喝/玩耍/交互 这些「有事做」的优先）
DFP_BEHAVIOR_WEIGHTS: Dict[str, int] = {
    "吃喝": 3,
    "玩耍": 4,
    "休息": 2,
    "交互": 3,
    "工作": 2,
    "碎碎念": 2,
    "节日": 1,
    "其他": 3,
}

DFP_STATE_DEFAULTS: Dict[str, Any] = {
    "mood": 78.0,
    "satiety": 82.0,
    "energy": 88.0,
    "intimacy": 12.0,
    "exp": 0,
    "level": 1,
    "born_at": 0.0,
    "last_tick": 0.0,
    "last_interaction": 0.0,
    "last_save": 0.0,
    "asleep": False,
    "sleep_started": 0.0,
    "total_pets": 0,
    "total_feeds": 0,
    "total_plays": 0,
    "total_chats": 0,
    "total_tokens": 0,
    "minutes_awake": 0.0,
}


class DFPPetBrain(QObject):
    """桌宠的「大脑」：只管数值与决策，不碰界面。

    状态全部可序列化，存进数据库 dfp_kv 的 pet_state 键；
    下次启动时按真实经过时间补算衰减，避免「关了程序回来发现它还是满血」。
    """

    dfpSaid = Signal(str, str)             # text, category
    dfpStateChanged = Signal(dict)
    dfpLevelUp = Signal(int)
    dfpExpressionWanted = Signal(object, float)
    dfpActionWanted = Signal(object, float)
    dfpBehaviorWanted = Signal(object, str, float)  # item, label, duration（素材表里的动作片）
    dfpEventLogged = Signal(str, str)
    dfpSleepChanged = Signal(bool)

    def __init__(self, settings: DFPSettingsStore, database: DFPDatabase, logger: Optional[DFPLogger] = None):
        super().__init__()
        # ★ v1.1.33：上一次**真正用到的台词类别**（`chatter` 会被解析成 hungry / lonely / act_cute / 时段…）
        #   —— 控制器拿它去挑同名的语音（`dfp_last_line_category()`）。
        self._dfp_last_line_category = ""
        self._dfp_settings = settings
        self._dfp_database = database
        self._dfp_logger = logger
        self._dfp_state: Dict[str, Any] = dict(DFP_STATE_DEFAULTS)
        self._dfp_state["born_at"] = dfp_now_ts()
        self._dfp_running = False
        self._dfp_speaking_until = 0.0
        self._dfp_next_talk_at = 0.0
        self._dfp_next_action_at = 0.0
        self._dfp_last_sample_at = 0.0
        self._dfp_last_sleep_day = ""
        self._dfp_last_dream_at = 0.0
        # ★ v1.1.26：灵魂档案（惰性载入；存进 kv 的 pet_soul）
        self._dfp_soul: Optional[Dict[str, Any]] = None
        # ★ v1.1.28：三魂七魄档案（惰性载入；存进 kv 的 pet_spirits）
        self._dfp_spirits: Optional[Dict[str, Any]] = None
        # ★ v1.1.29：修真层档案（道行 / 定数 / 气血 / 脏腑 / 因果 / 业 / 缘 / 神仙鬼妖）
        self._dfp_dao: Optional[Dict[str, Any]] = None
        self._dfp_behavior_provider: Optional[Callable[[Sequence[str]], Optional[Dict[str, Any]]]] = None
        self._dfp_recent_behaviors: List[str] = []
        self._dfp_behavior_count = 0
        self._dfp_timer = QTimer(self)
        self._dfp_timer.setInterval(DFP_BRAIN_TICK_MS)
        self._dfp_timer.timeout.connect(self.dfp_tick)

    # --- 只读 ---
    def dfp_state(self) -> Dict[str, Any]:
        return self._dfp_state

    def dfp_state_value(self, key: str, default: Any = 0.0) -> Any:
        if key not in self._dfp_state:
            return default
        value = self._dfp_state[key]
        return default if value is None else value

    def dfp_is_running(self) -> bool:
        return self._dfp_running

    # --- 生命周期 ---
    def dfp_start(self) -> None:
        if self._dfp_running:
            return
        self.dfp_load_state()
        self._dfp_running = True
        now = dfp_now_ts()
        self._dfp_state["last_tick"] = now
        self._dfp_last_sample_at = now
        self._dfp_schedule_next_talk(now)
        self._dfp_schedule_next_action(now)
        self._dfp_timer.start()
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("行为大脑已启动：%s" % self.dfp_status_text())

    def dfp_stop(self) -> None:
        if not self._dfp_running:
            return
        self._dfp_timer.stop()
        self.dfp_save_state(force=True)
        self._dfp_running = False
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("行为大脑已停止")

    # --- 存取 ---
    def dfp_load_state(self) -> Dict[str, Any]:
        raw = None
        try:
            raw = self._dfp_database.dfp_kv_get(DFP_STATE_KV_KEY, None)
        except Exception:
            raw = None
        loaded: Dict[str, Any] = {}
        if isinstance(raw, str):
            try:
                loaded = json.loads(raw)
            except Exception:
                loaded = {}
        elif isinstance(raw, dict):
            loaded = dict(raw)
        state = dict(DFP_STATE_DEFAULTS)
        for key, value in (loaded or {}).items():
            if key in state and isinstance(value, (int, float, bool, str)):
                state[key] = value
        if not state.get("born_at"):
            state["born_at"] = dfp_now_ts()
        # 关机期间的衰减照样算
        last = dfp_safe_float(state.get("last_tick"), 0.0)
        now = dfp_now_ts()
        if last > 0.0 and now > last:
            gap_hours = (now - last) / 3600.0
            if gap_hours > 0.02:
                self._dfp_state = state
                self._dfp_apply_decay(gap_hours)
                state = self._dfp_state
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_info("离线 %s，已补算状态衰减" % dfp_human_duration(now - last))
        state["last_tick"] = now
        state["asleep"] = bool(state.get("asleep"))
        self._dfp_state = state
        try:
            self.dfpStateChanged.emit(self.dfp_state_snapshot())
        except Exception:
            pass
        return state

    def dfp_save_state(self, force: bool = False) -> bool:
        now = dfp_now_ts()
        last_save = dfp_safe_float(self._dfp_state.get("last_save"), 0.0)
        if not force and now - last_save < DFP_BRAIN_SAVE_INTERVAL:
            return False
        self._dfp_state["last_save"] = now
        try:
            ok = bool(self._dfp_database.dfp_kv_set(DFP_STATE_KV_KEY, json.dumps(self._dfp_state, ensure_ascii=False)))
        except Exception:
            ok = False
        return ok

    # --- 心跳 ---
    def dfp_tick(self) -> None:
        now = dfp_now_ts()
        last = dfp_safe_float(self._dfp_state.get("last_tick"), now)
        hours = max(0.0, (now - last) / 3600.0)
        self._dfp_state["last_tick"] = now
        if hours > 0.0:
            self._dfp_apply_decay(hours)
        if not self._dfp_state.get("asleep"):
            self._dfp_state["minutes_awake"] = dfp_safe_float(self._dfp_state.get("minutes_awake"), 0.0) + hours * 60.0
        self._dfp_check_autonomous_talk(now)
        self._dfp_check_autonomous_action(now)
        self._dfp_check_auto_sleep(now)
        # ★ v1.1.26：灵魂 —— 记里程碑 / 攒心事 / 到点主动开口（睡着的时侯不吵她）
        self.dfp_soul_tick(now)
        # ★ v1.1.28：三魂七魄 —— 缓慢养魂 + 看阈值（跨了就会排队说一句）
        self.dfp_spirit_tick(now)
        # ★ v1.1.29：修真层心跳（气血 / 脏腑 / 因果 / 鬼妖气 / 跨档说话）
        self.dfp_dao_tick(now)
        self._dfp_maybe_sample(now)
        self.dfp_save_state(force=False)
        try:
            self.dfpStateChanged.emit(self.dfp_state_snapshot())
        except Exception:
            pass

    def _dfp_apply_decay(self, hours: float) -> None:
        hours = max(0.0, float(hours))
        if hours <= 0.0:
            return
        settings = self._dfp_settings
        asleep = bool(self._dfp_state.get("asleep"))
        if asleep:
            recover = dfp_safe_float(settings.dfp_get("sleep_recover_per_hour", 12.0), 12.0)
            self._dfp_bump("energy", recover * hours, 0.0, 100.0)
            self._dfp_bump("satiety", -dfp_safe_float(settings.dfp_get("satiety_decay_per_hour", 4.0), 4.0) * 0.4 * hours, 0.0, 100.0)
            self._dfp_bump("mood", 1.5 * hours, 0.0, 100.0)
        else:
            self._dfp_bump("satiety", -dfp_safe_float(settings.dfp_get("satiety_decay_per_hour", 4.0), 4.0) * hours, 0.0, 100.0)
            self._dfp_bump("energy", -dfp_safe_float(settings.dfp_get("energy_decay_per_hour", 3.0), 3.0) * hours, 0.0, 100.0)
            self._dfp_bump("mood", -dfp_safe_float(settings.dfp_get("mood_decay_per_hour", 2.0), 2.0) * hours, 0.0, 100.0)
        intimacy = dfp_safe_float(self._dfp_state.get("intimacy"), 0.0)
        decay = dfp_safe_float(settings.dfp_get("intimacy_decay_per_hour", 0.5), 0.5)
        if intimacy > 60.0:
            decay = 0.0
        self._dfp_bump("intimacy", -decay * hours, 0.0, 100.0)

    # --- 锁定（内置修改器；★ 与窗口的“位置锁定”区分，这里管的是数值）---
    def dfp_value_locked(self, key: str) -> bool:
        return bool(self._dfp_settings.dfp_get("lock_%s" % str(key), False))

    def dfp_set_value_lock(self, key: str, value: bool) -> bool:
        if key not in DFP_STATE_DEFAULTS and key not in DFP_STAT_KEYS:
            return False
        self._dfp_settings.dfp_set("lock_%s" % str(key), bool(value))
        self.dfpStateChanged.emit(self.dfp_state_snapshot())
        return True

    def dfp_locked_keys(self) -> List[str]:
        keys = ["level", "exp"] + list(DFP_STAT_KEYS)
        return [key for key in keys if self.dfp_value_locked(key)]

    def dfp_lock_all(self, enabled: bool = True, keys: Optional[Sequence[str]] = None) -> int:
        targets = list(keys) if keys else ["level", "exp"] + list(DFP_STAT_KEYS)
        for key in targets:
            self._dfp_settings.dfp_set("lock_%s" % key, bool(enabled))
        self.dfpStateChanged.emit(self.dfp_state_snapshot())
        if enabled:
            self.dfp_say("locked")
        return len(targets)

    def dfp_max_all(self, tell: bool = True) -> Dict[str, Any]:
        """内置修改器：四条属性拉满、亲密度拉满。"""
        for key in DFP_STAT_KEYS:
            self._dfp_state[key] = 100.0
        self._dfp_state["total_tokens"] = dfp_safe_int(self._dfp_state.get("total_tokens"), 0)
        self.dfp_save_state(force=True)
        self.dfpStateChanged.emit(self.dfp_state_snapshot())
        if tell:
            self.dfp_say("maxed")
        return self.dfp_state_snapshot()

    def dfp_set_level(self, level: int) -> int:
        """直接改等级（1~%d），经验清零。"""
        new_level = int(dfp_clamp(dfp_safe_int(level, 1), 1, DFP_MAX_LEVEL))
        self._dfp_state["level"] = new_level
        self._dfp_state["exp"] = 0
        self.dfp_save_state(force=True)
        self.dfpStateChanged.emit(self.dfp_state_snapshot())
        return new_level

    def dfp_set_exp(self, exp: int) -> int:
        need = self.dfp_exp_needed()
        value = int(dfp_clamp(dfp_safe_int(exp, 0), 0, max(0, need)))
        self._dfp_state["exp"] = value
        self.dfp_save_state(force=True)
        self.dfpStateChanged.emit(self.dfp_state_snapshot())
        return value

    def dfp_max_level(self) -> int:
        level = self.dfp_set_level(DFP_MAX_LEVEL)
        self._dfp_state["exp"] = self.dfp_exp_needed(level)
        self.dfp_save_state(force=True)
        self.dfpStateChanged.emit(self.dfp_state_snapshot())
        self.dfp_say("level_max")
        return level

    def dfp_trainer_summary(self) -> str:
        locked = self.dfp_locked_keys()
        return "等级 Lv%d：心情 %.0f｜饱食 %.0f｜精力 %.0f｜亲密度 %.0f%s" % (
            dfp_safe_int(self._dfp_state.get("level"), 1),
            dfp_safe_float(self._dfp_state.get("mood"), 0.0),
            dfp_safe_float(self._dfp_state.get("satiety"), 0.0),
            dfp_safe_float(self._dfp_state.get("energy"), 0.0),
            dfp_safe_float(self._dfp_state.get("intimacy"), 0.0),
            "（已锁定 %s）" % "、".join(locked) if locked else "",
        )

    def _dfp_maybe_sample(self, now: float) -> None:
        interval = max(1.0, dfp_safe_float(self._dfp_settings.dfp_get("state_sample_interval_min", 30), 30.0)) * 60.0
        if now - self._dfp_last_sample_at < interval:
            return
        self._dfp_last_sample_at = now
        try:
            self._dfp_database.dfp_add_state_sample(
                dfp_safe_float(self._dfp_state.get("mood"), 0.0),
                dfp_safe_float(self._dfp_state.get("satiety"), 0.0),
                dfp_safe_float(self._dfp_state.get("energy"), 0.0),
                dfp_safe_float(self._dfp_state.get("intimacy"), 0.0),
            )
        except Exception:
            pass

    def _dfp_schedule_next_talk(self, now: Optional[float] = None) -> None:
        now = dfp_now_ts() if now is None else now
        interval = max(10.0, dfp_safe_float(self._dfp_settings.dfp_get("idle_talk_interval_sec", 120), 120.0))
        self._dfp_next_talk_at = now + interval * random.uniform(0.75, 1.35)

    def _dfp_schedule_next_action(self, now: Optional[float] = None) -> None:
        now = dfp_now_ts() if now is None else now
        interval = max(5.0, dfp_safe_float(self._dfp_settings.dfp_get("idle_action_interval_sec", 30), 30.0))
        if self.dfp_behavior_ready():
            interval = max(8.0, dfp_safe_float(self._dfp_settings.dfp_get("behavior_asset_interval_sec", 40), 40.0))
        self._dfp_next_action_at = now + interval * random.uniform(0.7, 1.4)

    # --- 素材行为（自主行为的「扩展包」：素材表里每个动作片就是一个行为）---
    def dfp_set_behavior_provider(self, provider: Optional[Callable[[Sequence[str]], Optional[Dict[str, Any]]]]) -> None:
        self._dfp_behavior_provider = provider

    def dfp_behavior_ready(self) -> bool:
        return bool(self._dfp_behavior_provider is not None and self._dfp_settings.dfp_get("behavior_asset_enabled", True))

    def dfp_behavior_count(self) -> int:
        return self._dfp_behavior_count

    def dfp_recent_behaviors(self) -> List[str]:
        return list(self._dfp_recent_behaviors)

    def _dfp_apply_behavior_effect(self, category: str) -> None:
        """不同类别的动作给不同的微小状态反馈（吃喝回饱食、休息回精力……）。"""
        mapping = {
            "吃喝": ("satiety", 9.0, "mood", 2.0),
            "休息": ("energy", 8.0, "satiety", -1.0),
            "玩耍": ("mood", 6.0, "energy", -2.0),
            "工作": ("exp", 0.0, "mood", 1.0),
            "交互": ("intimacy", 1.5, "mood", 3.0),
            "碎碎念": ("mood", 1.0, "intimacy", 0.4),
            "节日": ("mood", 5.0, "intimacy", 1.0),
        }
        rules = mapping.get(str(category))
        if not rules:
            self._dfp_bump("mood", 2.0)
            return
        self._dfp_bump(rules[0], rules[1])
        self._dfp_bump(rules[2], rules[3])
        if str(category) == "工作":
            self.dfp_add_exp(3)

    def _dfp_check_autonomous_action(self, now: float) -> None:
        if not self._dfp_settings.dfp_get("idle_action_enabled", True):
            return
        if now < self._dfp_next_action_at:
            return
        self._dfp_schedule_next_action(now)
        if self._dfp_state.get("asleep"):
            return
        if self.dfp_behavior_ready():
            item = None
            try:
                item = self._dfp_behavior_provider(tuple(self._dfp_recent_behaviors))
            except Exception:
                item = None
            if item:
                label = str(item.get("label") or item.get("id") or "")
                category = str(item.get("category") or "")
                self._dfp_behavior_count += 1
                self._dfp_recent_behaviors.append(str(item.get("id") or ""))
                if len(self._dfp_recent_behaviors) > 10:
                    del self._dfp_recent_behaviors[: len(self._dfp_recent_behaviors) - 10]
                self._dfp_apply_behavior_effect(category)
                try:
                    self.dfpBehaviorWanted.emit(item, label, 10.0)
                except Exception:
                    pass
                if random.random() < 0.45:
                    self.dfp_say("idle")
                return
        pool = [DFPAction.WAVE, DFPAction.JUMP, DFPAction.SPIN, DFPAction.DANCE, DFPAction.SIT, DFPAction.EAT]
        action = dfp_random_pick(pool, DFPAction.WAVE)
        duration = DFP_ACTION_DURATIONS_MS.get(action, 1.6)
        try:
            self.dfpActionWanted.emit(action, duration)
        except Exception:
            pass

    def _dfp_check_autonomous_talk(self, now: float) -> None:
        if not self._dfp_settings.dfp_get("idle_talk_enabled", True):
            return
        if now < self._dfp_next_talk_at:
            return
        self._dfp_schedule_next_talk(now)
        if self._dfp_state.get("asleep"):
            return
        # ★ v1.1.14：自言自语用 chatter 类别 —— 台词照旧退回 idle，
        #   但控制器看到这个类别就知道「可以顺便播一条 idle 语音了」
        self.dfp_say(DFP_CHATTER_CATEGORY)

    def _dfp_check_auto_sleep(self, now: float) -> None:
        if not self._dfp_settings.dfp_get("auto_sleep_enabled", False):
            return
        hour = datetime.fromtimestamp(now).hour
        sleep_hour = dfp_safe_int(self._dfp_settings.dfp_get("auto_sleep_hour", 23), 23)
        wake_hour = dfp_safe_int(self._dfp_settings.dfp_get("auto_wake_hour", 7), 7)
        day_key = dfp_today_key(now)
        if self._dfp_state.get("asleep"):
            if hour == wake_hour and self._dfp_last_sleep_day != day_key:
                self._dfp_last_sleep_day = day_key
                self.dfp_wake(automatic=True)
            return
        if hour == sleep_hour:
            if self._dfp_last_sleep_day != day_key:
                self._dfp_last_sleep_day = day_key
                self.dfp_sleep(automatic=True)

    def _dfp_bump(self, key: str, delta: float, low: float = 0.0, high: float = 100.0) -> None:
        if key not in self._dfp_state:
            return
        if self.dfp_value_locked(key):
            return  # 内置修改器锁定后数值不再变化
        current = dfp_safe_float(self._dfp_state.get(key), 0.0)
        self._dfp_state[key] = round(dfp_clamp(current + float(delta), low, high), 3)

    # --- 互动 ---
    def dfp_react_dragged(self) -> None:
        """被拖拽放下后的反应（不计入互动次数，只是给点表现）。"""
        self._dfp_state["mood"] = round(dfp_clamp(dfp_safe_float(self._dfp_state.get("mood"), 0.0) + 2.0, 0.0, 100.0), 3)
        try:
            self.dfpExpressionWanted.emit(DFPExpression.SURPRISED, 2.0)
        except Exception:
            pass
        self.dfp_say("idle")

    def dfp_pet_head(self) -> str:
        was_asleep = bool(self._dfp_state.get("asleep"))
        if was_asleep:
            self.dfp_wake(automatic=False)
        self._dfp_bump("mood", 6.0)
        self._dfp_bump("intimacy", 2.0)
        self._dfp_state["total_pets"] = int(dfp_safe_int(self._dfp_state.get("total_pets"), 0)) + 1
        self.dfp_add_exp(3)
        self._dfp_touch()
        # ★ v1.1.28：摸头养「尸狗 / 幽精 / 雀阴」
        self.dfp_spirit_grow_by("pet")
        # ★ v1.1.29：修真层（道行 / 心胆 / 妖气 / 缘，并埋下「亲昵」这个因）
        self.dfp_dao_grow_by("pet")
        try:
            self._dfp_database.dfp_add_event("pet", "摸摸头")
            self._dfp_database.dfp_bump_daily("pets", 1)
        except Exception:
            pass
        self.dfpEventLogged.emit("pet", "摸摸头")
        try:
            self.dfpExpressionWanted.emit(DFPExpression.HAPPY, 2.6)
        except Exception:
            pass
        # ★ 关系够亲的时候，摸头会有四分之一概率变成撒娇（act_cute 类）
        # ★ 刚才被晾了一阵、现在回头来摸它 ⇒ 先说「被戳穿就收 / 见好就收 / 条件式退让」那几句
        gap = dfp_now_ts() - dfp_safe_float(self._dfp_state.get("last_interaction"), 0.0)
        if gap >= 1800.0 and random.random() < 0.35:
            soft = self._dfp_soft_category("soft_hint_caught", "soft_hint_retreat", "soft_hint_condition")
            if soft:
                return self.dfp_say(soft)
        if dfp_safe_float(self._dfp_state.get("intimacy"), 0.0) >= 40.0 and random.random() < 0.25:
            return self.dfp_say("act_cute")
        return self.dfp_say("pet")

    def dfp_feed(self, drink: bool = False) -> str:
        if self._dfp_state.get("asleep"):
            self.dfp_wake(automatic=False)
        if drink:
            self._dfp_bump("satiety", 6.0)
            self._dfp_bump("mood", 3.0)
            self.dfp_add_exp(2)
            category = "drink"
            action = DFPAction.SIT
        else:
            self._dfp_bump("satiety", 28.0)
            self._dfp_bump("mood", 7.0)
            self._dfp_bump("intimacy", 1.0)
            self.dfp_add_exp(6)
            category = "feed"
            action = DFPAction.EAT
        self._dfp_state["total_feeds"] = int(dfp_safe_int(self._dfp_state.get("total_feeds"), 0)) + 1
        self._dfp_touch()
        # ★ v1.1.28：投喂养「伏矢 / 吞贼」
        self.dfp_spirit_grow_by("feed")
        # ★ v1.1.29：修真层（喝的是「水」、吃的是「食」，分开算）
        self.dfp_dao_grow_by("drink" if drink else "feed")
        try:
            self._dfp_database.dfp_add_event("drink" if drink else "feed", "喝水" if drink else "投喂小鱼干")
            self._dfp_database.dfp_bump_daily("feeds", 1)
        except Exception:
            pass
        self.dfpEventLogged.emit("feed", "投喂")
        try:
            self.dfpActionWanted.emit(action, DFP_ACTION_DURATIONS_MS.get(action, 2.4))
            self.dfpExpressionWanted.emit(DFPExpression.HAPPY, 3.0)
        except Exception:
            pass
        return self.dfp_say(category)

    def dfp_play(self) -> str:
        if self._dfp_state.get("asleep"):
            self.dfp_wake(automatic=False)
        self._dfp_bump("mood", 10.0)
        self._dfp_bump("energy", -8.0)
        self._dfp_bump("intimacy", 3.0)
        self._dfp_state["total_plays"] = int(dfp_safe_int(self._dfp_state.get("total_plays"), 0)) + 1
        self.dfp_add_exp(12)
        self._dfp_touch()
        # ★ v1.1.28：陪玩养「爽灵 / 雀阴」
        self.dfp_spirit_grow_by("play")
        # ★ v1.1.29：修真层（肝心动起来、肾耗一点、妖气涨一点）
        self.dfp_dao_grow_by("play")
        try:
            self._dfp_database.dfp_add_event("play", "陪玩")
            self._dfp_database.dfp_bump_daily("plays", 1)
        except Exception:
            pass
        self.dfpEventLogged.emit("play", "陪玩")
        try:
            self.dfpActionWanted.emit(DFPAction.DANCE, 3.4)
            self.dfpExpressionWanted.emit(DFPExpression.HAPPY, 4.0)
        except Exception:
            pass
        return self.dfp_say("happy")

    def dfp_sleep(self, automatic: bool = False) -> None:
        if self._dfp_state.get("asleep"):
            return
        self._dfp_state["asleep"] = True
        self._dfp_state["sleep_started"] = dfp_now_ts()
        try:
            self._dfp_database.dfp_add_event("sleep", "自动入睡" if automatic else "主人让它睡觉")
        except Exception:
            pass
        self.dfpEventLogged.emit("sleep", "入睡")
        self.dfpSleepChanged.emit(True)
        self.dfp_say("sleepy")
        try:
            self.dfpActionWanted.emit(DFPAction.SLEEP, 0.0)
        except Exception:
            pass
        # ★ v1.1.28：睡觉养「臭肺 / 胎光」
        self.dfp_spirit_grow_by("sleep")
        # ★ v1.1.29：修真层（气血涨最多、肺肾三焦得养、鬼气退）
        self.dfp_dao_grow_by("sleep")
        self.dfp_save_state(force=True)

    def dfp_wake(self, automatic: bool = False) -> None:
        if not self._dfp_state.get("asleep"):
            return
        self._dfp_state["asleep"] = False
        self._dfp_state["sleep_started"] = 0.0
        try:
            self._dfp_database.dfp_add_event("wake", "自动醒来" if automatic else "主人叫醒")
        except Exception:
            pass
        self.dfpEventLogged.emit("wake", "醒来")
        self.dfpSleepChanged.emit(False)
        # ★ v1.1.28：醒来养「尸狗」（睡醒了更警觉）
        self.dfp_spirit_grow_by("wake")
        # ★ v1.1.29：修真层（醒来补一口气、元神长一点）
        self.dfp_dao_grow_by("wake")
        self.dfp_say("wake")
        try:
            self.dfpActionWanted.emit(DFPAction.JUMP, DFP_ACTION_DURATIONS_MS.get(DFPAction.JUMP, 1.1))
        except Exception:
            pass
        # ★ 梦境：醒来时若距上次做梦够久，就复述一段梦（可在设置里关掉）
        if self.dfp_dream_due() and self._dfp_settings.dfp_get("dream_on_wake", True):
            try:
                text, seed = self.dfp_compose_dream()
                self._dfp_last_dream_at = dfp_now_ts()
                self.dfp_say("idle", text)
                self.dfp_dream_effect(seed)
                self._dfp_database.dfp_add_event("dream", seed)
            except Exception as exc:
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_warning(
                        "醒来复述梦境失败：%s: %s" % (type(exc).__name__, exc)
                    )
        self.dfp_save_state(force=True)

    def dfp_toggle_sleep(self) -> bool:
        if self._dfp_state.get("asleep"):
            self.dfp_wake()
        else:
            self.dfp_sleep()
        return bool(self._dfp_state.get("asleep"))

    def dfp_register_chat(self, tokens: int = 0) -> None:
        self._dfp_state["total_chats"] = int(dfp_safe_int(self._dfp_state.get("total_chats"), 0)) + 1
        # ★ v1.1.29：聊过也算修（道行 / 心性 / 缘，并埋下「唤名」这个因）
        self.dfp_dao_grow_by("chat")
        self._dfp_state["total_tokens"] = int(dfp_safe_int(self._dfp_state.get("total_tokens"), 0)) + int(tokens or 0)
        self._dfp_bump("intimacy", 2.0)
        self._dfp_bump("mood", 3.0)
        self.dfp_add_exp(5)
        self._dfp_touch()
        try:
            self._dfp_database.dfp_bump_daily("chats", 1)
            self._dfp_database.dfp_bump_daily("messages", 2)
            self._dfp_database.dfp_bump_daily("tokens", int(tokens or 0))
        except Exception:
            pass

    def _dfp_touch(self) -> None:
        self._dfp_state["last_interaction"] = dfp_now_ts()
        self.dfp_save_state(force=True)

    # --- 等级 ---
    def dfp_exp_needed(self, level: Optional[int] = None) -> int:
        level = int(level if level is not None else dfp_safe_int(self._dfp_state.get("level"), 1))
        return int(50 + 30 * max(0, level - 1))

    def dfp_level_title(self, level: Optional[int] = None) -> str:
        level = int(level if level is not None else dfp_safe_int(self._dfp_state.get("level"), 1))
        titles = dfp_level_titles()
        title = titles[0][1] if titles else ""
        for need, name in titles:
            if level >= need:
                title = name
        return title

    def dfp_add_exp(self, amount: int) -> int:
        if self.dfp_value_locked("exp"):
            return 0
        self._dfp_state["exp"] = int(dfp_safe_int(self._dfp_state.get("exp"), 0)) + int(amount)
        gained = 0
        while self._dfp_state["exp"] >= self.dfp_exp_needed() and not self.dfp_value_locked("level"):
            need = self.dfp_exp_needed()
            if int(dfp_safe_int(self._dfp_state.get("level"), 1)) >= DFP_MAX_LEVEL:
                self._dfp_state["exp"] = min(int(self._dfp_state["exp"]), need)
                break
            self._dfp_state["exp"] = int(self._dfp_state["exp"]) - need
            self._dfp_state["level"] = int(dfp_safe_int(self._dfp_state.get("level"), 1)) + 1
            gained += 1
        if gained:
            level = int(self._dfp_state["level"])
            try:
                self._dfp_database.dfp_add_event("level_up", "升级到 Lv%d" % level)
            except Exception:
                pass
            self.dfpEventLogged.emit("level_up", "Lv%d" % level)
            self.dfpLevelUp.emit(level)
            self.dfp_say("level_max" if level >= DFP_MAX_LEVEL else "level_up")
        return gained

    # --- 说话 ---
    def _dfp_soft_category(self, *preferred: str) -> str:
        """在「软话」子类别里挑一个当面能用的。

        `preferred` 是一个**候选池**：池里可用的那几个随机挑（不是严格按顺序取第一个 ——
        否则排在前面的类别会把后面的全挡住，后面的台词永远说不出来）；
        池里一个都没有就退回「任意软话类」。

        ★ 返回值可能是空串（台词库文件不在时），调用方必须退回原本的行为，绝不能不说话。
        """
        available = dfp_soft_categories()
        if not available:
            return ""
        pool = [name for name in preferred if name and name in available]
        if not pool:
            pool = list(available)
        return pool[random.randrange(len(pool))]

    def _dfp_priority_category(self) -> str:
        satiety = dfp_safe_float(self._dfp_state.get("satiety"), 100.0)
        energy = dfp_safe_float(self._dfp_state.get("energy"), 100.0)
        mood = dfp_safe_float(self._dfp_state.get("mood"), 100.0)
        intimacy = dfp_safe_float(self._dfp_state.get("intimacy"), 0.0)
        last_touch = dfp_safe_float(self._dfp_state.get("last_interaction"), 0.0)
        if last_touch > 0.0 and dfp_now_ts() - last_touch > 6 * 3600:
            return "lonely"
        if satiety <= 25.0:
            return "hungry"
        if energy <= 22.0:
            return "tired"
        if mood <= 25.0:
            return "sad"
        # ★ 软话（以退为进 / 无辜试探 / 委屈示弱 / 软性索取）：被晾着超过半小时、或心情中低时才说
        gap = (dfp_now_ts() - last_touch) if last_touch > 0.0 else 0.0
        if gap >= 1800.0 and random.random() < 0.6:
            soft = self._dfp_soft_category(
                "soft_hint_pretend",  # 装没事：我没听见 / 我什么都没看到
                "soft_hint_hint",  # 模糊暗示：有句话我先存着
                "soft_hint_thirdparty",  # 借第三方：听说你最近很忙
                "soft_hint",
                "soft_hint_ask",
                "soft_hint_probe",
            )
            if soft:
                return soft
        if mood <= 45.0 and random.random() < 0.5:
            soft = self._dfp_soft_category("soft_hint_sad", "soft_hint_selfblame", "soft_hint_retreat")
            if soft:
                return soft
        if mood >= 88.0 and intimacy >= 60.0:
            roll = random.random()
            if roll < 0.30:
                # ★ 关系很亲的时候会撒娇（台词库里的 act_cute 类，JSON 里目前 500 句）
                return "act_cute"
            return "love" if roll < 0.65 else "happy"
        if intimacy >= 55.0 and mood >= 70.0 and random.random() < 0.25:
            return "act_cute"
        if energy >= 85.0 and mood >= 70.0 and random.random() < 0.25:
            return "bored"
        return dfp_time_category()

    def dfp_pick_line(self, category: str = "idle") -> str:
        text = str(category or "idle")
        if text in ("idle", "", DFP_CHATTER_CATEGORY):
            override = self._dfp_priority_category()
            if override:
                text = override
        elif text == "greet":
            # 太久没来先说想念，其次按时段问候
            last_touch = dfp_safe_float(self._dfp_state.get("last_interaction"), 0.0)
            if last_touch > 0.0 and dfp_now_ts() - last_touch > 8 * 3600:
                text = "lonely"
        # ★ v1.1.33：记住**这次真正用到哪一类**（上面可能把 chatter 解析成了 hungry 之类）——
        #   语音按它选类别，见 `dfp_last_line_category()`。
        # ★ 走可外部化的台词库（`台词库.json` 存在时以它为准，见 dfp_lines_load）
        lines = dfp_lines(text)
        if not lines:
            lines = dfp_lines("idle")
            text = "idle"  # 这一类一句都没有 ⇒ 实际说的是 idle，语音也跟着退回 idle
        if not lines:
            lines = DFP_IDLE_LINES.get("idle", ())
        self._dfp_last_line_category = text
        return str(dfp_random_pick(lines, "……"))

    def dfp_last_line_category(self) -> str:
        """上一次挑句**真正用到**的台词类别（`chatter` 会被解析成 hungry / lonely / act_cute / 时段…）。

        ★ v1.1.33：语音用它选类别 —— 自言自语说到「饿了」那一类，就播 `voice_hungry_*`（录了才有），
          没录就退回 `idle`（旧行为）。这是「台词类别 → 语音类别」的第二个入口，
          另一个是 `DFP_SAY_VOICE_MAP`（摸头 / 投喂 这种显式类别的）。
        """
        return str(self._dfp_last_line_category or "")

    def dfp_say(self, category: str = "idle", text: str = "") -> str:
        if not self._dfp_settings.dfp_get("offline_lines_enabled", True) and not text:
            return ""
        # ★ v1.1.33：先按传进来的类别记一笔（下面 `dfp_pick_line` 会把它细化成真正用的那一类；
        #   直接给了 text 时不走 pick_line，那就按传进来的类别算）。
        self._dfp_last_line_category = str(category or "idle")
        spoken = str(text or "").strip() or self.dfp_pick_line(category)
        if not spoken:
            return ""
        # ★ v1.1.29：撒娇长妖气（就这一个出口，免得几处重复算）
        if str(category) == "act_cute":
            self.dfp_dao_grow_by("act_cute")
        self._dfp_speaking_until = dfp_now_ts() + max(1.6, len(spoken) * 0.18)
        self.dfpSaid.emit(spoken, str(category))
        return spoken

    def dfp_is_speaking(self) -> bool:
        return dfp_now_ts() < self._dfp_speaking_until

    def dfp_status_text(self) -> str:
        return "Lv%d %s｜心情 %.0f｜饱食 %.0f｜精力 %.0f｜好感 %.0f%s" % (
            dfp_safe_int(self._dfp_state.get("level"), 1),
            self.dfp_level_title(),
            dfp_safe_float(self._dfp_state.get("mood"), 0.0),
            dfp_safe_float(self._dfp_state.get("satiety"), 0.0),
            dfp_safe_float(self._dfp_state.get("energy"), 0.0),
            dfp_safe_float(self._dfp_state.get("intimacy"), 0.0),
            "（睡觉中）" if self._dfp_state.get("asleep") else "",
        )

    # --- 梦境 ---
    def _dfp_daily_today(self) -> Dict[str, Any]:
        """今日统计（读不到就给空字典：梦境不该因为数据库抖动而做不成）。"""
        try:
            row = self._dfp_database.dfp_daily_today()
            return dict(row) if isinstance(row, dict) else {}
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("读今日统计失败（梦境受影响）：%s: %s" % (type(exc).__name__, exc))
            return {}

    def _dfp_dream_recent_events(self, seconds: float, limit: int = 12) -> List[str]:
        """最近 N 秒内发生过的事件（kind 列表，最近的在前）。读不到就返回空表。"""
        try:
            cutoff = dfp_now_ts() - max(0.0, dfp_safe_float(seconds, 0.0))
            rows = self._dfp_database.dfp_query_all(
                "SELECT kind FROM dfp_events WHERE created_at >= ? ORDER BY id DESC LIMIT ?",
                (cutoff, max(1, int(limit))),
            )
            return [str(row["kind"]) for row in rows if row and row["kind"]]
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("读今天的事件失败（梦境受影响）：%s: %s" % (type(exc).__name__, exc))
            return []

    def _dfp_dream_today_events(self, limit: int = 12) -> List[str]:
        """今天（零点到现在）发生过的事件（kind 列表，最近的在前）。"""
        return self._dfp_dream_recent_events(dfp_now_ts() - dfp_start_of_day_ts(), limit)

    def _dfp_dream_count(self) -> int:
        """一共梦过几次（事件表里 `kind='dream'` 的条数）。"""
        try:
            row = self._dfp_database.dfp_query_one("SELECT COUNT(*) AS c FROM dfp_events WHERE kind = ?", ("dream",))
            return int(row["c"]) if row else 0
        except Exception:
            return 0

    def _dfp_dream_recent_label(self) -> str:
        """最近做的一件事（中文说法，**不带「今天」二字** —— JSON 里自己会写「今天{recent}」）。

        今天没什么事就说「什么也没做」。
        """
        for kind in self._dfp_dream_today_events():
            label = DFP_DREAM_RECENT_LABELS.get(str(kind))
            if label:
                return label
        return "什么也没做"

    def dfp_days_together(self) -> int:
        """认识主人多少天（按 `born_at` 算，至少 1 天）。"""
        try:
            born = dfp_safe_float(self._dfp_state.get("born_at"), 0.0)
            if born <= 0.0:
                return 1
            return max(1, int((dfp_now_ts() - born) / 86400.0) + 1)
        except Exception:
            return 1

    def dfp_dream_seed(self) -> str:
        """按优先级定情绪种子（写法参考 `_dfp_priority_category`）。

        基础四路（v1.1.13 起，一直不变）：久没被搭理 → lonely；今天被摸得多 → love；
        饿了 → hungry；精力足心情好 → curious。
        ★ v1.1.25 新增：心情很低 → sad；精力很低 → sleepy；今天升过级 → proud；
        被晾了两三个小时（想念） → miss；亲密度高而且今天聊得多 → shy；
        今天玩过而且心情很好 → mischief；认识 ≥7 天 → memory；
        另有小概率的荒诞梦（`DFP_DREAM_ABSURD_CHANCE`）；其余在 happy / bored / sea 里随机。
        ★ 最后一道守卫：选出来的情绪**必须在当前梦境库里**（不在就在库里随机挑一个）——
        这样「JSON 里自己加的情绪」（只要条件命中）也能被用上，而库里没有的种子不会拼出错梦。
        """
        try:
            state = self._dfp_state
            mood = dfp_safe_float(state.get("mood"), 100.0)
            satiety = dfp_safe_float(state.get("satiety"), 100.0)
            energy = dfp_safe_float(state.get("energy"), 100.0)
            intimacy = dfp_safe_float(state.get("intimacy"), 0.0)
            last_touch = dfp_safe_float(state.get("last_interaction"), 0.0)
            gap_hours = ((dfp_now_ts() - last_touch) / 3600.0) if last_touch > 0.0 else 0.0
            today = self._dfp_daily_today()
            pets = dfp_safe_int(today.get("pets"), 0)
            plays = dfp_safe_int(today.get("plays"), 0)
            chats = dfp_safe_int(today.get("chats"), 0)
            seed = "happy"
            if gap_hours > 6.0:
                seed = "lonely"
            elif pets > 5:
                seed = "love"
            elif satiety < 30.0:
                seed = "hungry"
            elif energy > 85.0 and mood > 70.0:
                seed = "curious"
            elif mood < 25.0:
                seed = "sad"
            elif energy < 35.0:
                seed = "sleepy"
            elif "level_up" in self._dfp_dream_recent_events(DFP_DREAM_PROUD_WINDOW_SEC, 24):
                seed = "proud"
            elif gap_hours > 2.0:
                seed = "miss"
            elif intimacy >= 60.0 and chats >= 3:
                seed = "shy"
            elif mood >= 75.0 and plays > 0:
                seed = "mischief"
            elif self.dfp_days_together() >= 7:
                seed = "memory"
            elif random.random() < DFP_DREAM_ABSURD_CHANCE:
                seed = "absurd"
            else:
                seed = str(dfp_random_pick(("happy", "bored", "sea"), "happy"))
            return self.dfp_dream_available_seed(seed)
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("梦境种子判定失败：%s: %s" % (type(exc).__name__, exc))
            return "happy"

    def dfp_dream_available_seed(self, seed: str) -> str:
        """把种子校正成「梦境库里真的存在」的那一个（库为空就只好用 happy）。"""
        name = str(seed or "").strip()
        try:
            library = dfp_dreams()
        except Exception:
            library = dict(DFP_DREAMS_FALLBACK)
        if not library:
            return name or "happy"
        if name and name in library:
            return name
        preferred = ["happy", "bored", "curious", "love", "lonely"]
        options = [item for item in preferred if item in library] or sorted(library)
        return str(dfp_random_pick(options, sorted(library)[0]))

    def dfp_dream_fragments(self, seed: str) -> List[str]:
        """从真实数据里抽 2~4 个「记忆碎片」，用来填梦里的 `{fragment}`。

        ★ 每一条都要能当**名词**用（梦的句子是「{fragment}从云上伸下来」这种写法）——
        所以「最近做的一件事」（「刚备份过数据库」这种谓语短语）**不进这个池**，它只给 `{recent}` 占位符用。
        候选＝被摸头累计次数 / 今天的小鱼干条数 / 当前等级称号 / 该情绪最近说过的台词（截 12 字）/
        今天玩的那几次 / 今天聊的那几句 / 认识 N 天这件事 / 主人今天叹的三口气。
        任何一步出错都退回 ["一片海", "一个气泡"]。
        """
        try:
            state = self._dfp_state
            today = self._dfp_daily_today()
            pool: List[str] = []

            def push(text: str) -> None:
                tip = str(text or "").strip()
                if tip and tip not in pool:
                    pool.append(tip)

            total_pets = dfp_safe_int(state.get("total_pets"), 0)
            if total_pets > 0:
                push("%d 只手" % total_pets)
            feeds = dfp_safe_int(today.get("feeds"), 0)
            if feeds > 0:
                push("%d 条小鱼干" % feeds)
            push("Lv%d 的%s" % (dfp_safe_int(state.get("level"), 1), self.dfp_level_title()))
            line = str(dfp_random_pick(dfp_lines(seed), "") or "").strip()
            if line:
                push(dfp_truncate_text(line, 12))
            # ★ v1.1.25：再补几样「她真的经历过的事」，梦才像真做的
            plays = dfp_safe_int(today.get("plays"), 0)
            if plays > 0:
                push("今天玩的那 %d 次" % plays)
            chats = dfp_safe_int(today.get("chats"), 0)
            if chats > 0:
                push("今天聊的那 %d 句" % chats)
            days = self.dfp_days_together()
            if days > 1:
                push("认识 %d 天这件事" % days)
            push("主人今天叹的三口气")
            if len(pool) < 2:
                return list(DFP_DREAM_FRAGMENT_FALLBACK)
            count = min(len(pool), max(2, random.randint(2, 4)))
            return [str(item) for item in random.sample(pool, count)]
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("梦境碎片抽取失败：%s: %s" % (type(exc).__name__, exc))
            return list(DFP_DREAM_FRAGMENT_FALLBACK)

    def dfp_dream_placeholders(self, seed: str) -> Dict[str, List[str]]:
        """做梦要用的真实数据（`{键}` → 候选值列表）。

        ★ v1.1.25：除了 `{fragment}`（记忆碎片，会给好几个轮着用），
          还有 `{title}` / `{level}` / `{days}` / `{time}` / `{pets}` / `{feeds}` /
          `{plays}` / `{chats}` / `{dreams}` / `{recent}` —— 全部现取现算，取不到就不放进去
          （组梦时会跳过它、最后把没换掉的占位符清干净，所以气泡里永远不会出现大括号）。
        """
        values: Dict[str, List[str]] = {}
        try:
            state = self._dfp_state
            today = self._dfp_daily_today()
            values["fragment"] = [str(item) for item in self.dfp_dream_fragments(seed) if str(item).strip()]
            values["title"] = [self.dfp_level_title()]
            values["level"] = [str(dfp_safe_int(state.get("level"), 1))]
            values["days"] = [str(self.dfp_days_together())]
            values["time"] = [DFP_DREAM_TIME_LABELS.get(dfp_time_category(), "现在")]
            values["pets"] = [str(dfp_safe_int(today.get("pets"), 0))]
            values["feeds"] = [str(dfp_safe_int(today.get("feeds"), 0))]
            values["plays"] = [str(dfp_safe_int(today.get("plays"), 0))]
            values["chats"] = [str(dfp_safe_int(today.get("chats"), 0))]
            values["dreams"] = [str(self._dfp_dream_count())]
            values["recent"] = [self._dfp_dream_recent_label()]
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("梦境占位符取值失败：%s: %s" % (type(exc).__name__, exc))
        return {key: [str(item) for item in items if str(item)] for key, items in values.items()}

    def dfp_compose_dream(self) -> Tuple[str, str]:
        """拼一个梦：返回 `(梦的文本, 情绪种子)`。

        三段（加可选「余韵」）用换行分隔，占位符换成真数据：
        `{fragment}` 是记忆碎片 —— **梦身里用过的那些，醒来 / 余韵里会回响**（同一个梦前后呼应才像真做梦）。
        ★ 任何一步失败都返回兜底文案 + `bored`，绝不抛异常（做梦是“锦上添花”，不该弄坏桌宠）。
        """
        try:
            seed = str(self.dfp_dream_seed() or "happy")
            library = dfp_dreams()
            pool = library.get(seed) or library.get("happy") or {}
            fallback = DFP_DREAMS_FALLBACK.get("happy", {})
            order = list(DFP_DREAM_STAGES) + [name for name in DFP_DREAM_OPTIONAL_STAGES if pool.get(name)]
            picked: Dict[str, str] = {}
            for stage in order:
                options = pool.get(stage) or fallback.get(stage, ())
                text = str(dfp_random_pick(options, "") or "").strip()
                if not text and stage in DFP_DREAM_STAGES:
                    return ("……我好像做了一个梦，但不记得了。", "bored")
                if text:
                    picked[stage] = text
            if not picked:
                return ("……我好像做了一个梦，但不记得了。", "bored")

            values = self.dfp_dream_placeholders(seed)
            fragments = [str(item) for item in (values.get("fragment") or []) if str(item).strip()]
            used: List[str] = []
            stages: List[str] = []
            for stage in order:
                text = picked.get(stage)
                if not text:
                    continue
                for key, items in values.items():
                    token = "{%s}" % key
                    if key != "fragment" and token in text:
                        text = text.replace(token, str(items[0]))
                echo = 0
                while DFP_DREAM_PLACEHOLDER in text:
                    piece = ""
                    if stage == DFP_DREAM_STAGES[1] and fragments:
                        piece = fragments[len(used) % len(fragments)]
                        used.append(piece)
                    elif used:
                        piece = used[echo % len(used)]
                        echo += 1
                    elif fragments:
                        piece = fragments[0]
                        used.append(piece)
                    if not piece:
                        piece = str(dfp_random_pick(DFP_DREAM_FRAGMENT_FALLBACK, "一片海") or "一片海")
                        used.append(piece)
                    text = text.replace(DFP_DREAM_PLACEHOLDER, piece, 1)
                # ★ 未知占位符（可能是中文，比如 {不认识的键}）一律清掉 —— 气泡里永不出现大括号
                text = re.sub(r"\{[^{}\r\n]{1,24}\}", "", text)
                text = re.sub(r"\s{2,}", " ", text).strip()
                if text:
                    stages.append(text)
            if not stages:
                return ("……我好像做了一个梦，但不记得了。", "bored")
            return ("\n".join(stages), seed)
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("梦境拼装失败：%s: %s" % (type(exc).__name__, exc))
            return ("……我好像做了一个梦，但不记得了。", "bored")

    def dfp_dream_effect(self, seed: str) -> None:
        """做梦对状态的影响（走 `_dfp_bump`：数值夹取与修改器锁定都自动生效）。

        ★ v1.1.25：新情绪各有各的余味 —— 困的梦反而补精力、得意的梦涨心情、想念的梦涨亲密度……
        """
        name = str(seed or "happy")
        try:
            if name == "lonely":
                self._dfp_bump("mood", -4.0)
                self._dfp_bump("intimacy", 1.5)
            elif name == "sad":
                self._dfp_bump("mood", -3.0)
                self._dfp_bump("intimacy", 1.0)
            elif name == "miss":
                self._dfp_bump("mood", -1.0)
                self._dfp_bump("intimacy", 2.0)
            elif name in ("love", "happy"):
                self._dfp_bump("mood", 5.0)
                self._dfp_bump("energy", -2.0)
            elif name == "proud":
                self._dfp_bump("mood", 6.0)
                self._dfp_bump("intimacy", 1.0)
            elif name == "shy":
                self._dfp_bump("mood", 3.0)
                self._dfp_bump("intimacy", 2.0)
            elif name == "mischief":
                self._dfp_bump("mood", 4.0)
                self._dfp_bump("energy", -3.0)
            elif name == "sleepy":
                self._dfp_bump("energy", 8.0)
                self._dfp_bump("mood", 1.0)
            elif name == "sea":
                self._dfp_bump("mood", 3.0)
                self._dfp_bump("energy", -1.0)
            elif name == "memory":
                self._dfp_bump("mood", 2.0)
                self._dfp_bump("intimacy", 1.0)
            elif name == "hungry":
                self._dfp_bump("satiety", -3.0)
                self._dfp_bump("mood", 1.0)
            else:
                self._dfp_bump("mood", 1.0)
            self.dfpStateChanged.emit(self.dfp_state_snapshot())
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("梦境状态效果失败：%s: %s" % (type(exc).__name__, exc))

    def dfp_dream_now(self) -> str:
        """强制做一次梦（忽略间隔）。返回梦的文本；不能做时返回空串。"""
        if not self._dfp_settings.dfp_get("dream_enabled", True):
            return ""
        try:
            text, seed = self.dfp_compose_dream()
            self._dfp_last_dream_at = dfp_now_ts()
            # ★ v1.1.29：做梦也算修（道行 + 元神，梦里养神）
            self.dfp_dao_grow_by("dream")
            self.dfp_say("idle", text)
            self.dfp_dream_effect(seed)
            self._dfp_database.dfp_add_event("dream", seed)
            return text
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("做梦失败：%s: %s" % (type(exc).__name__, exc))
            return ""

    def dfp_dream_due(self) -> bool:
        """距上次做梦够久了吗（关了梦境功能就直接 False）。"""
        if not self._dfp_settings.dfp_get("dream_enabled", True):
            return False
        hours = dfp_safe_float(self._dfp_settings.dfp_get("dream_interval_hours", 6.0), 6.0)
        last = dfp_safe_float(self._dfp_last_dream_at, 0.0)
        return (dfp_now_ts() - last) >= max(0.0, hours) * 3600.0

    # =========================================================================
    #  ★ v1.1.26：灵魂 —— 会记得 / 有心事 / 会主动开口 / 会成长
    #  -----------------------------------------------------------------------
    #  「灵魂」在这里是四件可测的事：
    #    ① 记得住：里程碑（第一次见面、被摸最多的一天、最高等级、最难过的一天…）
    #    ② 藏得住：心事（攒着不马上说，等到时机才说出来，说完清掉）
    #    ③ 会主动：每隔一段时间（soul_interval_min）自己找你说一句
    #    ④ 会成长：关系阶段 陌生→熟悉→亲近→依赖→离不开（**只升不降**）
    #  档案存进 kv 的 `pet_soul` 键；任何一步失败都只是「少说一句」，绝不弄坏桌宠。
    # =========================================================================
    def dfp_soul_state(self) -> Dict[str, Any]:
        """灵魂档案（懒载入 + 缓存；字段逐个兜底，旧档案也能用）。"""
        if self._dfp_soul is not None:
            return self._dfp_soul
        raw: Any = None
        try:
            raw = self._dfp_database.dfp_kv_get(DFP_SOUL_KV_KEY, None)
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("读灵魂档案失败：%s: %s" % (type(exc).__name__, exc))
            raw = None
        data: Dict[str, Any] = {}
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
        elif isinstance(raw, dict):
            data = dict(raw)
        if not isinstance(data, dict):
            data = {}
        born = dfp_safe_float(self._dfp_state.get("born_at"), dfp_now_ts()) or dfp_now_ts()
        notes = [row for row in (data.get("heart_notes") or []) if isinstance(row, dict)]
        soul: Dict[str, Any] = {
            "version": dfp_safe_int(data.get("version"), 1) or 1,
            "created_at": dfp_safe_float(data.get("created_at"), born) or born,
            "traits": [str(item) for item in (data.get("traits") or []) if str(item).strip()][:DFP_SOUL_TRAIT_COUNT],
            "heart_notes": notes[-DFP_SOUL_NOTE_LIMIT:],
            "milestones": dict(data.get("milestones") or {}) if isinstance(data.get("milestones"), dict) else {},
            "mentioned": [str(item) for item in (data.get("mentioned") or [])],
            # ★ v1.1.27：记下「每件里程碑是什么时候说过的」——过一阵可以「又想起」
            "mentioned_at": (
                {str(key): dfp_safe_float(value, 0.0) for key, value in (data.get("mentioned_at") or {}).items()}
                if isinstance(data.get("mentioned_at"), dict) else {}
            ),
            "echo_at": dfp_safe_float(data.get("echo_at"), 0.0),
            "echo_kind": str(data.get("echo_kind") or ""),
            "stage": str(data.get("stage") or DFP_SOUL_STAGES[0]),
            "stage_since": dfp_safe_float(data.get("stage_since"), born) or born,
            "pending_stage": str(data.get("pending_stage") or ""),
            "spoken": dfp_safe_int(data.get("spoken"), 0),
            "last_soul_at": dfp_safe_float(data.get("last_soul_at"), 0.0),
            "last_note_at": dfp_safe_float(data.get("last_note_at"), 0.0),
        }
        if len(soul["traits"]) < DFP_SOUL_TRAIT_COUNT:
            soul["traits"] = self._dfp_soul_pick_traits(soul["traits"])
        self._dfp_soul = soul
        # ★ 第一次建档就把小癖好定下来：不然重启一次就换一批（「终身不变」得先存住）
        if not data.get("traits"):
            self.dfp_soul_save()
        return soul

    def _dfp_soul_pick_traits(self, have: Sequence[str]) -> List[str]:
        """抽小癖好：已有的保留，不够再从库里补（库里也没有就用兜底那 3 条）。"""
        pool = [str(item) for item in (dfp_soul().get("traits") or []) if str(item).strip()]
        picked = [str(item) for item in have if str(item).strip()]
        for item in pool:
            if len(picked) >= DFP_SOUL_TRAIT_COUNT:
                break
            if item not in picked:
                picked.append(item)
        while len(picked) < DFP_SOUL_TRAIT_COUNT and pool:
            picked.append(str(dfp_random_pick(pool, pool[0])))
        return picked[:DFP_SOUL_TRAIT_COUNT]

    def dfp_soul_save(self) -> bool:
        """写回灵魂档案（每次都立刻写，灵魂不该因为崩了就丢了）。"""
        soul = self._dfp_soul
        if not isinstance(soul, dict):
            return False
        try:
            payload = dict(soul)
            payload["saved_at"] = dfp_now_ts()
            return bool(self._dfp_database.dfp_kv_set(DFP_SOUL_KV_KEY, json.dumps(payload, ensure_ascii=False)))
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("写灵魂档案失败：%s: %s" % (type(exc).__name__, exc))
            return False

    def dfp_soul_traits(self) -> List[str]:
        return list(self.dfp_soul_state().get("traits") or [])

    def dfp_soul_score(self) -> float:
        """关系分 = 亲密度 + 认识天数×1.2 + 累计互动（摸头+投喂+聊天）×0.05。"""
        state = self._dfp_state
        intimacy = dfp_safe_float(state.get("intimacy"), 0.0)
        days = float(max(1, self.dfp_days_together()))
        interactions = (
            dfp_safe_int(state.get("total_pets"), 0)
            + dfp_safe_int(state.get("total_feeds"), 0)
            + dfp_safe_int(state.get("total_chats"), 0)
        )
        return float(intimacy + days * 1.2 + interactions * 0.05)

    def dfp_soul_stage(self) -> str:
        """当前关系阶段（只升不降）。"""
        return str(self.dfp_soul_state().get("stage") or DFP_SOUL_STAGES[0])

    def dfp_soul_stage_label(self) -> str:
        return dfp_soul_stage_label(self.dfp_soul_stage())

    def _dfp_soul_refresh_stage(self) -> str:
        """按关系分重算阶段；升了就把新阶段挂成 `pending_stage`（等下一次开口时说出来）。"""
        soul = self.dfp_soul_state()
        want = dfp_soul_stage_for_score(self.dfp_soul_score())
        current = str(soul.get("stage") or DFP_SOUL_STAGES[0])
        if dfp_soul_stage_rank(want) > dfp_soul_stage_rank(current):
            soul["stage"] = want
            soul["stage_since"] = dfp_now_ts()
            soul["pending_stage"] = want
            self.dfp_soul_save()
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_info("关系阶段升到「%s」（关系分 %.1f）" % (dfp_soul_stage_label(want), self.dfp_soul_score()))
            return want
        return current

    def dfp_soul_notes(self) -> List[Dict[str, Any]]:
        """攒着的心事（先进先出）。"""
        return [dict(row) for row in (self.dfp_soul_state().get("heart_notes") or [])]

    def dfp_soul_notes_count(self) -> int:
        return len(self.dfp_soul_state().get("heart_notes") or [])

    def dfp_soul_placeholders(self) -> Dict[str, List[str]]:
        """灵魂文案要用的真实数据（梦境那 11 个 + 小癖好 + 关系阶段）。"""
        values: Dict[str, List[str]] = {}
        try:
            values.update(self.dfp_dream_placeholders("happy"))
            traits = self.dfp_soul_traits()
            if traits:
                values["trait"] = [str(dfp_random_pick(traits, traits[0]))]
            values["stage"] = [self.dfp_soul_stage_label()]
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("灵魂占位符取值失败：%s: %s" % (type(exc).__name__, exc))
        return {key: [str(item) for item in items if str(item)] for key, items in values.items()}

    def dfp_soul_fill(self, text: str) -> str:
        """把灵魂文案里的占位符换成真数据；不认识的占位符一律清掉（气泡里不漏大括号）。"""
        result = str(text or "")
        values = self.dfp_soul_placeholders()
        for key, items in values.items():
            token = "{%s}" % key
            if token in result and items:
                result = result.replace(token, str(items[0]))
        result = re.sub(r"\{[^{}\r\n]{1,24}\}", "", result)
        return re.sub(r"\s{2,}", " ", result).strip()

    def dfp_soul_note(self, kind: str) -> bool:
        """攒一条心事（同一类没说的心事只会有一条；20 分钟内不重复触发）。"""
        name = str(kind or "").strip()
        pool = (dfp_soul().get("heart_notes") or {}).get(name) or ()
        if not pool:
            return False
        soul = self.dfp_soul_state()
        now = dfp_now_ts()
        if now - dfp_safe_float(soul.get("last_note_at"), 0.0) < DFP_SOUL_NOTE_COOLDOWN_SEC:
            return False
        notes = soul.get("heart_notes") or []
        if any(str(row.get("kind") or "") == name for row in notes):
            return False
        line = str(dfp_random_pick(pool, pool[0]))
        if not line:
            return False
        notes.append({"at": now, "kind": name, "line": line})
        soul["heart_notes"] = notes[-DFP_SOUL_NOTE_LIMIT:]
        soul["last_note_at"] = now
        self.dfp_soul_save()
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_debug("她攒了一条心事（%s）" % name)
        return True

    def dfp_soul_observe(self) -> None:
        """记里程碑（只记「硬事实」，说过没说过由 `mentioned` 管）。"""
        try:
            soul = self.dfp_soul_state()
            marks = dict(soul.get("milestones") or {})
            state = self._dfp_state
            today = self._dfp_daily_today()
            born = dfp_safe_float(state.get("born_at"), dfp_now_ts()) or dfp_now_ts()
            marks.setdefault("first_meet", born)
            if dfp_safe_int(state.get("total_feeds"), 0) > 0 and "first_feed" not in marks:
                marks["first_feed"] = dfp_now_ts()
            if dfp_safe_int(state.get("total_pets"), 0) > 0 and "first_pet" not in marks:
                marks["first_pet"] = dfp_now_ts()
            pets_today = dfp_safe_int(today.get("pets"), 0)
            top = marks.get("top_pets") if isinstance(marks.get("top_pets"), dict) else {}
            if pets_today > dfp_safe_int((top or {}).get("value"), 0):
                marks["top_pets"] = {"value": pets_today, "day": dfp_today_key()}
            level = dfp_safe_int(state.get("level"), 1)
            if level > dfp_safe_int(marks.get("top_level"), 0):
                marks["top_level"] = level
            mood = dfp_safe_float(state.get("mood"), 100.0)
            sad = marks.get("saddest") if isinstance(marks.get("saddest"), dict) else {}
            if mood < dfp_safe_float((sad or {}).get("mood"), 100.0):
                marks["saddest"] = {"mood": mood, "at": dfp_now_ts()}
            last_touch = dfp_safe_float(state.get("last_interaction"), 0.0)
            gap = ((dfp_now_ts() - last_touch) / 3600.0) if last_touch > 0.0 else 0.0
            if gap > dfp_safe_float(marks.get("longest_gap"), 0.0):
                marks["longest_gap"] = round(gap, 2)
            # ★ v1.1.28 修：dreams / chats 以前根本没记过 ⇒ 判定函数对它们一律 return True，
            #   刚出生、一次梦都没做、一句都没聊的桌宠也会说「我一共梦过 0 次了」。
            dreams = dfp_safe_int(self._dfp_dream_count(), 0)
            if dreams > dfp_safe_int((marks.get("dreams") or {}).get("value"), 0):
                marks["dreams"] = {"value": dreams, "at": dfp_now_ts()}
            chats = dfp_safe_int(state.get("total_chats"), 0)
            if chats > dfp_safe_int((marks.get("chats") or {}).get("value"), 0):
                marks["chats"] = {"value": chats, "at": dfp_now_ts()}
            changed = marks != (soul.get("milestones") or {})
            soul["milestones"] = marks
            if changed:
                self.dfp_soul_save()
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("记里程碑失败：%s: %s" % (type(exc).__name__, exc))

    def dfp_soul_moment(self, force: bool = False) -> str:
        """说一句「灵魂的话」：优先把攒着的心事说出来，其次关系升级感言、里程碑回忆、日常心声。

        返回说出去的那句话（没说返回空串）。`force=True` 表示「现在就说」（忽略开关与间隔）。
        """
        if not force and not bool(self._dfp_settings.dfp_get("soul_enabled", True)):
            return ""
        soul = self.dfp_soul_state()
        now = dfp_now_ts()
        if not force:
            minutes = dfp_safe_float(self._dfp_settings.dfp_get("soul_interval_min", 45), 45.0)
            gap = now - dfp_safe_float(soul.get("last_soul_at"), 0.0)
            if gap < max(1.0, minutes) * 60.0:
                return ""
        library = dfp_soul()
        text = ""
        kind = ""
        try:
            # ① 余响：上一次把心事说出口之后，隔一会儿补一句（「……说出来了，好像轻了一点」）
            echo_at = dfp_safe_float(soul.get("echo_at"), 0.0)
            if echo_at > 0.0 and (now - echo_at) >= DFP_SOUL_ECHO_DELAY_SEC:
                pool_echo = library.get("heart_echo") or ()
                echo_kind = str(soul.get("echo_kind") or "")
                if pool_echo:
                    text = self.dfp_soul_fill(str(dfp_random_pick(pool_echo, pool_echo[0])))
                    kind = "echo:%s" % echo_kind
                soul["echo_at"] = 0.0
                soul["echo_kind"] = ""
            # ② 有心事：说最早那一条，说完就清掉（这才是「藏了一段时间才说出口」）
            notes = soul.get("heart_notes") or []
            if not text and notes:
                row = notes[0]
                template = str(row.get("line") or "")
                note_kind = str(row.get("kind") or "")
                if template:
                    text = self.dfp_soul_fill(template)
                    kind = "note:%s" % note_kind
                    soul["heart_notes"] = notes[1:]
                    # ★ 说完了挂上「余响」：过几分钟她会再补一句
                    soul["echo_at"] = now
                    soul["echo_kind"] = note_kind
            if not text:
                pending = str(soul.get("pending_stage") or "")
                pool = (library.get("stages") or {}).get(pending) or ()
                if pending and pool:
                    text = self.dfp_soul_fill(str(dfp_random_pick(pool, pool[0])))
                    soul["pending_stage"] = ""
                    kind = "stage:%s" % pending
            if not text:
                text, kind = self._dfp_soul_pick_milestone(soul, library)
            if not text:
                # ★ v1.1.28：魂魄有感（跨过旺/弱门槛、或者已经「十全」）
                _spirit_text = self.dfp_spirit_pick()
                if _spirit_text:
                    text, kind = _spirit_text, "spirit"
            if not text:
                # ★ v1.1.29：修真层有感（跨档 / 因果结果 / 缘谱新签 / 登仙）
                _dao_text = self.dfp_dao_pick()
                if _dao_text:
                    text, kind = _dao_text, "dao"
            if not text:
                # ③ 日常心声：优先当前关系阶段那一档（关系变了，说话方式也变）
                pool_idle = self.dfp_soul_idle_pool()
                if pool_idle:
                    text = self.dfp_soul_fill(str(dfp_random_pick(pool_idle, pool_idle[0])))
                    kind = "stage_idle:%s" % self.dfp_soul_stage() if (library.get("stage_idle") or {}).get(self.dfp_soul_stage()) else "idle"
            if not text:
                return ""
            soul["last_soul_at"] = now
            soul["spoken"] = dfp_safe_int(soul.get("spoken"), 0) + 1
            self.dfp_soul_save()
            self.dfp_say("idle", text)
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_info("她说了一句心里话（%s）：%s" % (kind or "soul", dfp_truncate_text(text, 40)))
            return text
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("灵魂开口失败：%s: %s" % (type(exc).__name__, exc))
            return ""

    def dfp_soul_idle_pool(self) -> List[str]:
        """日常心声的候选池：优先当前关系阶段那一档，没写就退回通用心声（旧版灵魂库照样能用）。"""
        library = dfp_soul()
        pool = (library.get("stage_idle") or {}).get(self.dfp_soul_stage()) or ()
        if not pool:
            pool = library.get("soul_idle") or ()
        return [str(item) for item in pool if str(item).strip()]

    def _dfp_soul_milestone_ok(self, marks: Dict[str, Any], key: str) -> bool:
        """这件里程碑「确实发生过」吗（没发生过的不能拿出来说）。"""
        if key == "first_meet":
            return bool(marks.get("first_meet"))
        if key == "first_feed":
            return bool(marks.get("first_feed"))
        if key == "first_pet":
            return bool(marks.get("first_pet"))
        if key == "top_pets":
            return dfp_safe_int((marks.get("top_pets") or {}).get("value"), 0) > 0
        if key == "top_level":
            return dfp_safe_int(marks.get("top_level"), 0) > 1
        if key == "longest_gap":
            return dfp_safe_float(marks.get("longest_gap"), 0.0) >= 3.0
        if key == "dreams":
            # ★ v1.1.28 修：没做过梦就不该说「我一共梦过 0 次了」
            # ★ 容忍两种写法：档案里是 {"value": n, "at": t}，手写的也可能是直接一个数
            raw = marks.get("dreams")
            return dfp_safe_int(raw.get("value") if isinstance(raw, dict) else raw, 0) > 0
        if key == "chats":
            # ★ v1.1.28 修：一句都没聊过也不该拿出来回忆（同样容忍两种写法）
            raw = marks.get("chats")
            return dfp_safe_int(raw.get("value") if isinstance(raw, dict) else raw, 0) > 0
        if key == "saddest":
            return dfp_safe_float((marks.get("saddest") or {}).get("mood"), 100.0) <= 40.0
        return True

    def _dfp_soul_note_said(self, soul: Dict[str, Any], key: str) -> None:
        """记下「这件里程碑刚说过」（列表 + 带时间的表都更新，最新的在前）。"""
        now = dfp_now_ts()
        said = [str(item) for item in (soul.get("mentioned") or []) if str(item) != key]
        soul["mentioned"] = ([key] + said)[:DFP_SOUL_AGAIN_LIMIT]
        said_at = dict(soul.get("mentioned_at") or {})
        said_at[key] = now
        keep = sorted(said_at.items(), key=lambda row: row[1], reverse=True)[:DFP_SOUL_AGAIN_LIMIT]
        soul["mentioned_at"] = dict(keep)

    def _dfp_soul_pick_milestone(self, soul: Dict[str, Any], library: Dict[str, Any]) -> Tuple[str, str]:
        """挑一件「她记得的事」说出来。

        ① 先挑**没说过**、且确实发生过的（说过的记进 `mentioned` / `mentioned_at`）
        ② 都说过一遍之后，挑一件**很久没提**（≥`DFP_SOUL_AGAIN_AFTER_SEC`）的，用「又想起」的口气再说一次。
        """
        marks = soul.get("milestones") or {}
        said_at = dict(soul.get("mentioned_at") or {})
        for key in ("first_meet", "first_feed", "first_pet", "top_pets", "top_level", "dreams", "chats", "longest_gap", "saddest"):
            if key in said_at or not self._dfp_soul_milestone_ok(marks, key):
                continue
            line = str(dfp_random_pick((library.get("milestones") or {}).get(key) or (), ""))
            if not line:
                continue
            self._dfp_soul_note_said(soul, key)
            return (self.dfp_soul_fill(line), "milestone:%s" % key)
        # ② 又想起：说过的那些，隔够久可以再提一次（口气不同，不会显得反复）
        again_pools = library.get("milestones_again") or {}
        now = dfp_now_ts()
        for key in sorted(said_at, key=lambda name: said_at.get(name, 0.0)):
            if key not in again_pools or not self._dfp_soul_milestone_ok(marks, key):
                continue
            if (now - dfp_safe_float(said_at.get(key), 0.0)) < DFP_SOUL_AGAIN_AFTER_SEC:
                continue
            line = str(dfp_random_pick(again_pools.get(key) or (), ""))
            if not line:
                continue
            self._dfp_soul_note_said(soul, key)
            return (self.dfp_soul_fill(line), "again:%s" % key)
        return ("", "")

    def dfp_soul_tick(self, now: float) -> None:
        """心跳里的灵魂逻辑：记里程碑 → 重算阶段 → 攒心事 → 到点开口。"""
        if not bool(self._dfp_settings.dfp_get("soul_enabled", True)):
            return
        try:
            self.dfp_soul_observe()
            self._dfp_soul_refresh_stage()
            gap = now - dfp_safe_float(self.dfp_soul_state().get("last_note_at"), 0.0)
            if gap >= DFP_SOUL_NOTE_INTERVAL_SEC:
                self._dfp_soul_pick_note()
            self.dfp_soul_moment()
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("灵魂心跳失败：%s: %s" % (type(exc).__name__, exc))

    def _dfp_soul_pick_note(self) -> str:
        """按当前处境决定「攒哪一种心事」（一次最多攒一条）。"""
        try:
            state = self._dfp_state
            now = dfp_now_ts()
            mood = dfp_safe_float(state.get("mood"), 100.0)
            last_touch = dfp_safe_float(state.get("last_interaction"), 0.0)
            gap_hours = ((now - last_touch) / 3600.0) if last_touch > 0.0 else 0.0
            today = self._dfp_daily_today()
            hour = dfp_local_hour()
            feeds_today = dfp_safe_int(today.get("feeds"), 0)
            chats_today = dfp_safe_int(today.get("chats"), 0)
            if "level_up" in self._dfp_dream_recent_events(DFP_DREAM_PROUD_WINDOW_SEC, 12):
                kind = "proud"
            elif gap_hours > 6.0:
                kind = "lonely"
            elif hour >= 22 or hour < 5:
                # ★ v1.1.27：深夜容易攒下「有点怕」这类心事
                kind = "afraid"
            elif mood < 25.0:
                kind = "sad"
            elif gap_hours > 2.0:
                kind = "miss"
            elif feeds_today > 0 and mood >= 70.0:
                # ★ v1.1.27：今天被喂过又心情好 ⇒ 想谢谢你
                kind = "thankful"
            elif chats_today > 0 and mood >= 60.0:
                # ★ v1.1.27：今天聊过又心情不错 ⇒ 攒着一个想问的问题
                kind = "curious"
            elif mood > 85.0 and dfp_safe_int(today.get("plays"), 0) > 0:
                kind = "happy"
            elif gap_hours > 1.0 and mood < 60.0:
                kind = "bored"
            else:
                return ""
            return kind if self.dfp_soul_note(kind) else ""
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("攒心事失败：%s: %s" % (type(exc).__name__, exc))
            return ""

    def dfp_soul_reset(self) -> None:
        """把灵魂重新养一遍（设置窗口「重置灵魂」用）：清掉心事与里程碑，保留小癖好与关系。"""
        soul = self.dfp_soul_state()
        soul["heart_notes"] = []
        soul["mentioned"] = []
        soul["mentioned_at"] = {}
        soul["echo_at"] = 0.0
        soul["echo_kind"] = ""
        soul["milestones"] = {"first_meet": dfp_safe_float(soul.get("created_at"), dfp_now_ts())}
        self.dfp_soul_save()

    def dfp_soul_summary_text(self) -> str:
        """一行给界面用的现状：`亲近 · 记得 7 件 · 心事 2 条 · 小癖好 3 条 · 说过 9 次`。"""
        soul = self.dfp_soul_state()
        return "%s · 记得 %d 件 · 心事 %d 条 · 小癖好 %d 条 · 说过 %d 次" % (
            self.dfp_soul_stage_label(),
            len(soul.get("milestones") or {}),
            len(soul.get("heart_notes") or []),
            len(soul.get("traits") or []),
            dfp_safe_int(soul.get("spoken"), 0),
        )

    # =========================================================================
    #  ★ v1.1.28：三魂七魄 —— 她的「精气神」
    #  -----------------------------------------------------------------------
    #    命盘：出生按配额随机（3 旺 / 4 平 / 3 弱），**终身不变** ⇒ 底子各不同
    #    灵气：以命盘值为起点，**只涨不跌**（陪伴 / 互动 / 睡觉 / 打理都养魂），上限 100
    #    表达：跨过旺（≥75）/ 弱（≤35）阈值时排队说一句；十个都 ≥90 说一句「十全」；
    #          没有排队时「看看魂魄」会拿最旺 / 最弱的那个来说
    #    ★ 刻意**不改任何既有数值** —— 这是「精气神」的表达层，不动平衡好的手感。
    # =========================================================================
    def dfp_spirits_state(self) -> Dict[str, Any]:
        """魂魄档案（懒载入 + 缓存；缺字段 / 旧档案都能用）。"""
        if self._dfp_spirits is not None:
            return self._dfp_spirits
        raw: Any = None
        try:
            raw = self._dfp_database.dfp_kv_get(DFP_SPIRIT_KV_KEY, None)
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("读魂魄档案失败：%s: %s" % (type(exc).__name__, exc))
            raw = None
        data: Dict[str, Any] = {}
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
        elif isinstance(raw, dict):
            data = dict(raw)
        if not isinstance(data, dict):
            data = {}
        raw_fate = data.get("fate") if isinstance(data.get("fate"), dict) else {}
        fate: Dict[str, float] = {}
        for key in DFP_SPIRITS_ALL:
            fate[key] = round(dfp_clamp(dfp_safe_float(raw_fate.get(key), 0.0), 0.0, DFP_SPIRIT_MAX), 1)
        # ★ 命盘「不全」也算没定下来（旧档案 / 手改坏了）⇒ 重抽一整张，免得出现九条 0 灵气
        fresh = any(value <= 0.0 for value in fate.values())
        if fresh:
            fate = self._dfp_spirit_roll_fate()
        raw_value = data.get("value") if isinstance(data.get("value"), dict) else {}
        value: Dict[str, float] = {}
        for key in DFP_SPIRITS_ALL:
            got = dfp_safe_float(raw_value.get(key), fate.get(key, 50.0))
            value[key] = round(dfp_clamp(got, 0.0, DFP_SPIRIT_MAX), 1)
        raw_seen = data.get("seen") if isinstance(data.get("seen"), dict) else {}
        seen: Dict[str, str] = {}
        for key in DFP_SPIRITS_ALL:
            band = str(raw_seen.get(key) or "")
            seen[key] = band if band in ("high", "mid", "low") else dfp_spirit_band(value[key])
        pending: List[Dict[str, Any]] = []
        for row in (data.get("pending") or []):
            if isinstance(row, dict) and str(row.get("key") or "").strip():
                pending.append({
                    "key": str(row.get("key")).strip(),
                    "band": "low" if str(row.get("band")) == "low" else "high",
                    "at": dfp_safe_float(row.get("at"), 0.0),
                })
        spirit: Dict[str, Any] = {
            "version": dfp_safe_int(data.get("version"), 1) or 1,
            "created_at": dfp_safe_float(data.get("created_at"), dfp_now_ts()) or dfp_now_ts(),
            "fate": fate,
            "value": value,
            "seen": seen,
            "pending": pending[-DFP_SPIRIT_PENDING_LIMIT:],
            "all_said": bool(data.get("all_said")),
            "spoken": dfp_safe_int(data.get("spoken"), 0),
            "grown": round(dfp_safe_float(data.get("grown"), 0.0), 1),
            "last_tick_at": dfp_safe_float(data.get("last_tick_at"), 0.0),
        }
        self._dfp_spirits = spirit
        # ★ 出生那一刻就把命盘定下来（不然重启一次就换一张命盘）
        if fresh:
            self.dfp_spirits_save()
        return spirit

    def _dfp_spirit_roll_fate(self) -> Dict[str, float]:
        """抽命盘：按配额（3 旺 / 4 平 / 3 弱）把十个魂魄分档，各自在自己那一段里随机。"""
        keys = [str(key) for key in DFP_SPIRITS_ALL]
        random.shuffle(keys)
        ranges = {band: (low, high) for band, low, high in DFP_SPIRIT_FATE_BANDS}
        counts = {band: int(count) for band, count in DFP_SPIRIT_FATE_COUNTS}
        fate: Dict[str, float] = {}
        cursor = 0
        for band in ("high", "mid", "low"):
            low, high = ranges.get(band, (36.0, 74.0))
            for _ in range(counts.get(band, 0)):
                if cursor >= len(keys):
                    break
                fate[keys[cursor]] = round(random.uniform(low, high), 1)
                cursor += 1
        mid_low, mid_high = ranges.get("mid", (36.0, 74.0))
        for key in keys:
            fate.setdefault(key, round(random.uniform(mid_low, mid_high), 1))
        return fate

    def dfp_spirits_save(self) -> bool:
        """写回魂魄档案（每次变化立刻写）。"""
        spirit = self._dfp_spirits
        if not isinstance(spirit, dict):
            return False
        try:
            payload = dict(spirit)
            payload["saved_at"] = dfp_now_ts()
            return bool(self._dfp_database.dfp_kv_set(DFP_SPIRIT_KV_KEY, json.dumps(payload, ensure_ascii=False)))
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("写魂魄档案失败：%s: %s" % (type(exc).__name__, exc))
            return False

    def dfp_spirit_value(self, key: str) -> float:
        name = str(key or "").strip()
        return dfp_safe_float((self.dfp_spirits_state().get("value") or {}).get(name), 0.0)

    def dfp_spirit_fate_value(self, key: str) -> float:
        name = str(key or "").strip()
        return dfp_safe_float((self.dfp_spirits_state().get("fate") or {}).get(name), 0.0)

    def dfp_spirit_band_now(self, key: str) -> str:
        return dfp_spirit_band(self.dfp_spirit_value(key))

    def dfp_spirits_rows(self) -> List[Dict[str, Any]]:
        """给界面用的十行（组 / 键 / 中文名 / 管什么 / 档位 / 数值 / 出生值）。"""
        rows: List[Dict[str, Any]] = []
        for key in DFP_SPIRITS_ALL:
            value = self.dfp_spirit_value(key)
            band = dfp_spirit_band(value)
            rows.append({
                "key": key,
                "group": dfp_spirit_group(key),
                "name": dfp_spirit_name(key),
                "sense": dfp_spirit_sense(key),
                "band": band,
                "band_label": dfp_spirit_band_label(band),
                "value": value,
                "fate": self.dfp_spirit_fate_value(key),
            })
        return rows

    def dfp_spirits_summary_text(self) -> str:
        """两行给界面用的现状：`三魂：胎光 旺88 · …` / `七魄：尸狗 旺86 · …`。"""
        soul_text = " · ".join(
            "%s %s%d" % (row["name"], row["band_label"], int(round(row["value"])))
            for row in self.dfp_spirits_rows() if row["group"] == "魂")
        po_text = " · ".join(
            "%s %s%d" % (row["name"], row["band_label"], int(round(row["value"])))
            for row in self.dfp_spirits_rows() if row["group"] == "魄")
        return "三魂：%s\n七魄：%s" % (soul_text, po_text)

    def dfp_spirit_fill(self, text: str, key: str = "") -> str:
        """魂魄文案的占位符：先换 `{spirit}` / `{group}` / `{value}`，剩下的交给灵魂那套清。"""
        result = str(text or "")
        name = str(key or "").strip()
        if name == "all":
            spirit_name, group_name = "三魂七魄", "魂魄"
            numbers = [self.dfp_spirit_value(item) for item in DFP_SPIRITS_ALL]
            number = min(numbers) if numbers else 0.0
        else:
            spirit_name = dfp_spirit_name(name)
            group_name = dfp_spirit_group(name)
            number = self.dfp_spirit_value(name)
        result = result.replace("{spirit}", str(spirit_name))
        result = result.replace("{group}", str(group_name))
        result = result.replace("{value}", "%d" % int(round(number)))
        return self.dfp_soul_fill(result)

    def dfp_spirit_grow(self, key: str, amount: float) -> float:
        """给某一条加灵气（**只涨不跌**、不超过 100）。返回真正加了多少。"""
        name = str(key or "").strip()
        if name not in DFP_SPIRITS_ALL:
            return 0.0
        spirit = self.dfp_spirits_state()
        values = dict(spirit.get("value") or {})
        before = dfp_safe_float(values.get(name), 0.0)
        after = min(DFP_SPIRIT_MAX, before + max(0.0, dfp_safe_float(amount, 0.0)))
        if after <= before + 1e-9:
            return 0.0
        values[name] = round(after, 1)
        spirit["value"] = values
        spirit["grown"] = round(dfp_safe_float(spirit.get("grown"), 0.0) + (after - before), 1)
        self.dfp_spirits_save()
        return round(after - before, 2)

    def dfp_spirit_grow_by(self, source: str, times: float = 1.0) -> Dict[str, float]:
        """按「养魂表」给对应魂/魄加值。返回这次真的涨了哪些。"""
        grown: Dict[str, float] = {}
        try:
            for key, amount in (DFP_SPIRIT_GROW.get(str(source or "").strip()) or ()):
                delta = self.dfp_spirit_grow(key, float(amount) * float(times or 1.0))
                if delta > 0.0:
                    grown[key] = delta
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("养魂失败：%s: %s" % (type(exc).__name__, exc))
        return grown

    def dfp_spirits_all_ready(self) -> bool:
        """三魂七魄是不是都到「十全」的门槛（≥90）。"""
        try:
            return all(self.dfp_spirit_value(key) >= DFP_SPIRIT_ALL_READY for key in DFP_SPIRITS_ALL)
        except Exception:
            return False

    def dfp_spirit_observe(self) -> None:
        """看看有没有魂/魄跨过旺/弱门槛（跨了就排队说一句），顺便看「十全」。"""
        try:
            spirit = self.dfp_spirits_state()
            seen = dict(spirit.get("seen") or {})
            pending = list(spirit.get("pending") or [])
            changed = False
            for key in DFP_SPIRITS_ALL:
                band = dfp_spirit_band(self.dfp_spirit_value(key))
                if band == seen.get(key):
                    continue
                seen[key] = band
                changed = True
                if band in ("high", "low") and dfp_spirit_pool(key, band):
                    if not any(str(row.get("key")) == key for row in pending):
                        pending.append({"key": key, "band": band, "at": dfp_now_ts()})
            spirit["seen"] = seen
            if (self.dfp_spirits_all_ready() and not spirit.get("all_said")
                    and dfp_spirit_all_complete_lines()
                    and not any(str(row.get("key")) == "all" for row in pending)):
                pending.append({"key": "all", "band": "high", "at": dfp_now_ts()})
                changed = True
            if changed:
                spirit["pending"] = pending[-DFP_SPIRIT_PENDING_LIMIT:]
                self.dfp_spirits_save()
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("看魂魄阈值失败：%s: %s" % (type(exc).__name__, exc))

    def dfp_spirit_pick(self) -> str:
        """取一条「魂魄想说的话」的成品（取走就出队；没有就返回空串）。**不开口**。"""
        try:
            spirit = self.dfp_spirits_state()
            pending = list(spirit.get("pending") or [])
            if not pending:
                return ""
            row = pending.pop(0)
            spirit["pending"] = pending
            key = str(row.get("key") or "").strip()
            band = "low" if str(row.get("band")) == "low" else "high"
            if key == "all":
                pool = dfp_spirit_all_complete_lines()
                spirit["all_said"] = True
                tag = "十全"
            else:
                pool = dfp_spirit_pool(key, band)
                tag = "%s%s" % (dfp_spirit_name(key), dfp_spirit_band_label(band))
            text = ""
            if pool:
                text = self.dfp_spirit_fill(str(dfp_random_pick(pool, pool[0])), key)
            spirit["spoken"] = dfp_safe_int(spirit.get("spoken"), 0) + 1
            self.dfp_spirits_save()
            if text and self._dfp_logger is not None:
                self._dfp_logger.dfp_info("魂魄有感（%s）：%s" % (tag, dfp_truncate_text(text, 40)))
            return text
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("魂魄开口失败：%s: %s" % (type(exc).__name__, exc))
            return ""

    def dfp_spirits_now(self) -> str:
        """界面入口「看看她的三魂七魄」：没有排队时，就着最旺 / 最弱的那个说一句。"""
        try:
            spirit = self.dfp_spirits_state()
            if not (spirit.get("pending") or []):
                rows = self.dfp_spirits_rows()
                if rows and dfp_spirit_all_complete_lines() and self.dfp_spirits_all_ready() and not spirit.get("all_said"):
                    spirit["pending"] = [{"key": "all", "band": "high", "at": dfp_now_ts()}]
                elif rows:
                    strongest = max(rows, key=lambda row: row["value"])
                    weakest = min(rows, key=lambda row: row["value"])
                    pick = strongest if strongest["value"] >= 50.0 else weakest
                    spirit["pending"] = [{"key": pick["key"], "band": "high" if pick["band"] == "mid" else pick["band"],
                                          "at": dfp_now_ts()}]
            text = self.dfp_spirit_pick()
            if text:
                self.dfp_say("idle", text)
            return text
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("魂魄开口失败：%s: %s" % (type(exc).__name__, exc))
            return ""

    def dfp_spirit_reborn(self) -> Dict[str, float]:
        """重新投胎：重抽命盘（灵气回到命盘值），清掉排队与「十全已说」。"""
        spirit = self.dfp_spirits_state()
        fate = self._dfp_spirit_roll_fate()
        spirit["fate"] = fate
        spirit["value"] = dict(fate)
        spirit["seen"] = {key: dfp_spirit_band(fate.get(key, 50.0)) for key in DFP_SPIRITS_ALL}
        spirit["pending"] = []
        spirit["all_said"] = False
        spirit["created_at"] = dfp_now_ts()
        spirit["grown"] = 0.0
        self.dfp_spirits_save()
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("重新投胎：%s" % self.dfp_spirits_summary_text().replace("\n", " / "))
        return fate

    def dfp_spirit_tick(self, now: float) -> None:
        """心跳里的魂魄逻辑：缓慢代谢（除秽 / 非毒）+ 状态好时养魂 + 看阈值。"""
        if not bool(self._dfp_settings.dfp_get("soul_enabled", True)):
            return
        try:
            spirit = self.dfp_spirits_state()
            last = dfp_safe_float(spirit.get("last_tick_at"), 0.0)
            if now - last >= DFP_SPIRIT_TICK_SEC:
                spirit["last_tick_at"] = now
                self.dfp_spirit_grow_by("tidy")
                if (dfp_safe_float(self._dfp_state.get("mood"), 0.0) >= 70.0
                        and dfp_safe_float(self._dfp_state.get("satiety"), 0.0) >= 60.0):
                    self.dfp_spirit_grow_by("health")
            self.dfp_spirit_observe()
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("魂魄心跳失败：%s: %s" % (type(exc).__name__, exc))

    # --- ★ v1.1.29：道（修真层：道行 / 心性 / 元神 / 五脏六腑 / 气血 / 命 / 运 / 数 / 因果 / 业 / 缘 / 神 / 仙 / 鬼 / 妖）---
    def dfp_dao_state(self) -> Dict[str, Any]:
        """道档案（懒载入 + 逐字段兜底）。

        ★ 两件「必须落盘」的事：① **定数**（本命数 / 寿数 / 缘数）第一次建档就定下来，
          否则重启一次命格就换一个；② 命盘那一套（三魂七魄）照旧各管各的，这里只管修真层。
        """
        if self._dfp_dao is not None:
            return self._dfp_dao
        raw: Any = None
        try:
            raw = self._dfp_database.dfp_kv_get(DFP_DAO_KV_KEY, None)
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("读道档案失败：%s: %s" % (type(exc).__name__, exc))
            raw = None
        data: Dict[str, Any] = {}
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
        elif isinstance(raw, dict):
            data = dict(raw)
        if not isinstance(data, dict):
            data = {}
        raw_birth = data.get("birth") if isinstance(data.get("birth"), dict) else {}
        birth = {
            "shu": dfp_safe_int(raw_birth.get("shu"), 0),
            "shou": dfp_safe_int(raw_birth.get("shou"), 0),
            "yuan": dfp_safe_int(raw_birth.get("yuan"), 0),
        }
        fresh = not (1 <= birth["shu"] <= 9)
        if fresh:
            birth = self._dfp_dao_roll_birth()
        raw_value = data.get("value") if isinstance(data.get("value"), dict) else {}
        defaults = {
            "dao": 0.0, "xing": 40.0, "yuanshen": 0.0, "qi": 60.0, "blood": 60.0,
            "ye_good": 0.0, "ye_bad": 0.0, "yuan": 5.0,
            "shen": 0.0, "xian": 0.0, "gui": 0.0, "yao": 0.0,
        }
        values: Dict[str, float] = {}
        for key, fallback in defaults.items():
            high = DFP_DAO_REALM_MAX if key == "dao" else DFP_DAO_MAX
            values[key] = round(dfp_clamp(dfp_safe_float(raw_value.get(key), fallback), 0.0, high), 1)
        raw_zangfu = data.get("zangfu") if isinstance(data.get("zangfu"), dict) else {}
        zangfu: Dict[str, float] = {}
        for name in dfp_dao_zangfu_names():
            zangfu[name] = round(dfp_clamp(dfp_safe_float(raw_zangfu.get(name), 60.0), 0.0, DFP_DAO_MAX), 1)
        pending: List[Dict[str, Any]] = []
        for row in (data.get("pending") or []):
            if isinstance(row, dict) and str(row.get("pool") or "").strip():
                pending.append({
                    "pool": str(row.get("pool")).strip(),
                    "arg": str(row.get("arg") or "").strip(),
                    "at": dfp_safe_float(row.get("at"), 0.0),
                })
        karma: List[Dict[str, Any]] = []
        for row in (data.get("karma") or []):
            if isinstance(row, dict) and str(row.get("cause") or "").strip():
                karma.append({
                    "cause": str(row.get("cause")).strip(),
                    "at": dfp_safe_float(row.get("at"), 0.0),
                    "ripen_at": dfp_safe_float(row.get("ripen_at"), 0.0),
                })
        karma_done: List[Dict[str, Any]] = []
        for row in (data.get("karma_done") or []):
            if isinstance(row, dict) and str(row.get("cause") or "").strip():
                karma_done.append({
                    "cause": str(row.get("cause")).strip(),
                    "at": dfp_safe_float(row.get("at"), 0.0),
                    "done_at": dfp_safe_float(row.get("done_at"), 0.0),
                    "fruit": str(row.get("fruit") or "").strip(),
                })
        raw_seen = data.get("seen") if isinstance(data.get("seen"), dict) else {}
        seen: Dict[str, Any] = {}
        for name in DFP_DAO_TIER_BLOCKS:
            seen[name] = str(raw_seen.get(name) or "")
        seen["dao"] = str(raw_seen.get("dao") or "")
        seen["yun"] = str(raw_seen.get("yun") or "")
        raw_seen_zang = raw_seen.get("zangfu") if isinstance(raw_seen.get("zangfu"), dict) else {}
        seen["zangfu"] = {name: str(raw_seen_zang.get(name) or "") for name in zangfu}
        raw_yun = data.get("yun") if isinstance(data.get("yun"), dict) else {}
        dao: Dict[str, Any] = {
            "version": dfp_safe_int(data.get("version"), 1) or 1,
            "created_at": dfp_safe_float(data.get("created_at"), dfp_now_ts()) or dfp_now_ts(),
            "birth": birth,
            "value": values,
            "zangfu": zangfu,
            "yun": {
                "day": str(raw_yun.get("day") or ""),
                "value": dfp_safe_int(raw_yun.get("value"), 2),
                "name": str(raw_yun.get("name") or ""),
            },
            "seen": seen,
            "pending": pending[-DFP_DAO_PENDING_LIMIT:],
            "karma": karma[-DFP_DAO_KARMA_LIMIT:],
            "karma_done": karma_done[-DFP_DAO_KARMA_DONE_LIMIT:],
            "found_yuan": [str(item) for item in (data.get("found_yuan") or []) if str(item).strip()],
            "xian_said": bool(data.get("xian_said")),
            "spoken": dfp_safe_int(data.get("spoken"), 0),
            "last_tick_at": dfp_safe_float(data.get("last_tick_at"), 0.0),
        }
        self._dfp_dao = dao
        # ★ 出生那一刻就把定数定下来（不然重启一次命格就换一个）
        if fresh:
            self.dfp_dao_save()
        self._dfp_dao_refresh_yun(notify=False)
        return dao

    def _dfp_dao_roll_birth(self) -> Dict[str, int]:
        """定数：本命数（1~9，按出生时刻定，**终身不变**）+ 寿数（天）+ 缘数（能遇几种缘）。"""
        born = dfp_safe_float(self._dfp_state.get("born_at"), 0.0) or dfp_now_ts()
        return {
            "shu": int(int(born) % 9) + 1,
            "shou": int(DFP_DAO_BIRTH_SHOU_DAYS),
            "yuan": len(dfp_dao_yuan_kinds()) or len(DFP_DAO_YUAN_KINDS),
        }

    def dfp_dao_save(self) -> bool:
        """写回道档案（每次变化立刻写）。"""
        dao = self._dfp_dao
        if not isinstance(dao, dict):
            return False
        try:
            payload = dict(dao)
            payload["saved_at"] = dfp_now_ts()
            return bool(self._dfp_database.dfp_kv_set(DFP_DAO_KV_KEY, json.dumps(payload, ensure_ascii=False)))
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("写道档案失败：%s: %s" % (type(exc).__name__, exc))
            return False

    def dfp_dao_birth(self) -> Dict[str, Any]:
        return dict(self.dfp_dao_state().get("birth") or {})

    def dfp_dao_shu(self) -> int:
        """本命数（1~9）。"""
        return dfp_safe_int(self.dfp_dao_birth().get("shu"), 1)

    def dfp_dao_ming_ge(self) -> str:
        """命格名（由本命数定，例：木命 · 长）。"""
        return dfp_dao_ming_ge_name(self.dfp_dao_shu())

    def dfp_dao_shu_name(self) -> str:
        """定数名（例：三之数 · 生）。"""
        return dfp_dao_shu_name(self.dfp_dao_shu())

    def dfp_dao_ming_line(self) -> str:
        """一行「命 / 数」：本命数 · 命格 · 寿数 · 缘数。"""
        birth = self.dfp_dao_birth()
        return "本命 %d ｜ 命格 %s ｜ 定数 %s ｜ 寿数 %d 天 ｜ 缘数 %d" % (
            self.dfp_dao_shu(), self.dfp_dao_ming_ge(), self.dfp_dao_shu_name(),
            dfp_safe_int(birth.get("shou"), 0), dfp_safe_int(birth.get("yuan"), 0))

    def dfp_dao_value(self, key: str) -> float:
        """取一条数值（dao / xing / yuanshen / qi / blood / ye_good / ye_bad / yuan / shen / xian / gui / yao）。"""
        name = str(key or "").strip()
        return dfp_safe_float((self.dfp_dao_state().get("value") or {}).get(name), 0.0)

    def dfp_dao_zangfu_value(self, name: str) -> float:
        key = str(name or "").strip()
        return dfp_safe_float((self.dfp_dao_state().get("zangfu") or {}).get(key), 60.0)

    def dfp_dao_zangfu_weakest(self) -> str:
        """最虚的那个脏腑（一样虚就按内置顺序取第一个）。"""
        rows = [(name, self.dfp_dao_zangfu_value(name)) for name in dfp_dao_zangfu_names()]
        if not rows:
            return ""
        return min(rows, key=lambda row: row[1])[0]

    def dfp_dao_realm_index(self) -> int:
        return dfp_dao_realm_index(self.dfp_dao_value("dao"))

    def dfp_dao_realm(self) -> str:
        """当前境界名（例：化鲸）。"""
        return dfp_dao_realm_for(self.dfp_dao_value("dao"))

    def dfp_dao_realm_next(self) -> Tuple[str, float]:
        """下一段境界名与还差多少道行（已是最后一段就返回空串）。"""
        value = self.dfp_dao_value("dao")
        rows = dfp_dao_realms()
        for item in rows:
            if value < dfp_safe_float(item.get("need"), 0.0):
                return str(item.get("name") or ""), round(dfp_safe_float(item.get("need"), 0.0) - value, 1)
        return "", 0.0

    def dfp_dao_qixue(self) -> Dict[str, Any]:
        """气血：`{qi, blood, mean, name}`（name = 五档里的哪一档）。"""
        qi = self.dfp_dao_value("qi")
        blood = self.dfp_dao_value("blood")
        mean = round((qi + blood) / 2.0, 1)
        return {"qi": qi, "blood": blood, "mean": mean, "name": dfp_dao_tier_name("qixue", mean)}

    def dfp_dao_qixue_name(self) -> str:
        return str(self.dfp_dao_qixue().get("name") or "")

    def dfp_dao_ye(self) -> Dict[str, Any]:
        """业：`{good, bad, net, name}`（net = 净业值 = 善 - 恶，可以为负）。"""
        good = self.dfp_dao_value("ye_good")
        bad = self.dfp_dao_value("ye_bad")
        net = round(good - bad, 1)
        return {"good": good, "bad": bad, "net": net, "name": dfp_dao_tier_name("ye", net)}

    def dfp_dao_ye_name(self) -> str:
        return str(self.dfp_dao_ye().get("name") or "")

    def dfp_dao_yun(self) -> Dict[str, Any]:
        """今日运势（按日期算：**当天不变、隔天就换**）。"""
        self._dfp_dao_refresh_yun(notify=False)
        return dict(self.dfp_dao_state().get("yun") or {})

    def dfp_dao_yun_name(self) -> str:
        return str(self.dfp_dao_yun().get("name") or "")

    def _dfp_dao_yun_roll(self) -> Dict[str, Any]:
        """算今天的运势：日期 + 本命数 一起做种子（纯本地、与网络无关）。"""
        day = dfp_today_key()
        try:
            seed = "%s|%d" % (day, self.dfp_dao_shu())
            number = int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:8], 16) % 5
        except Exception:
            number = 2
        return {"day": day, "value": number, "name": dfp_dao_tier_name("yun", number)}

    def _dfp_dao_refresh_yun(self, notify: bool = True) -> bool:
        """换天就换运势（顺手排一句「今日运势」）。返回有没有换。"""
        dao = self._dfp_dao if isinstance(self._dfp_dao, dict) else None
        if dao is None:
            return False
        today = dfp_today_key()
        current = dao.get("yun") if isinstance(dao.get("yun"), dict) else {}
        if str(current.get("day") or "") == today and str(current.get("name") or ""):
            return False
        rolled = self._dfp_dao_yun_roll()
        dao["yun"] = rolled
        if notify:
            self._dfp_dao_queue("yun", str(rolled.get("name") or ""))
        self.dfp_dao_save()
        return True

    def dfp_dao_add(self, key: str, amount: float, low: float = 0.0, high: float = DFP_DAO_MAX) -> float:
        """给某条数值加一点（自动夹在 low~high）。返回变化后的值。"""
        name = str(key or "").strip()
        dao = self.dfp_dao_state()
        values = dict(dao.get("value") or {})
        if name not in values:
            return 0.0
        # ★ 道行只加不减（与灵气同一条规矩）⇒ 境界也就只升不降
        delta = dfp_safe_float(amount, 0.0)
        if name == "dao":
            delta = max(0.0, delta)
        ceiling = DFP_DAO_REALM_MAX if name == "dao" else float(high)
        after = round(dfp_clamp(dfp_safe_float(values.get(name), 0.0) + delta, float(low), ceiling), 1)
        if abs(after - dfp_safe_float(values.get(name), 0.0)) < 1e-9:
            return dfp_safe_float(values.get(name), 0.0)
        values[name] = after
        dao["value"] = values
        self.dfp_dao_save()
        return after

    def dfp_dao_zangfu_add(self, name: str, amount: float) -> float:
        """给某个脏腑加一点（虚了会说「虚」的话，旺了会说「实」的话）。"""
        key = str(name or "").strip()
        zangfu = dict(self.dfp_dao_state().get("zangfu") or {})
        if key not in zangfu:
            return 0.0
        after = round(dfp_clamp(dfp_safe_float(zangfu.get(key), 60.0) + dfp_safe_float(amount, 0.0),
                                0.0, DFP_DAO_MAX), 1)
        if abs(after - dfp_safe_float(zangfu.get(key), 60.0)) < 1e-9:
            return dfp_safe_float(zangfu.get(key), 60.0)
        zangfu[key] = after
        self.dfp_dao_state()["zangfu"] = zangfu
        self.dfp_dao_save()
        return after

    def dfp_dao_ye_add(self, good: float = 0.0, bad: float = 0.0) -> Dict[str, float]:
        """记一笔业（善 / 恶各自累计，净业值 = 善 - 恶）。"""
        if good:
            self.dfp_dao_add("ye_good", float(good), 0.0, DFP_DAO_YE_LIMIT)
        if bad:
            self.dfp_dao_add("ye_bad", float(bad), 0.0, DFP_DAO_YE_LIMIT)
        return self.dfp_dao_ye()

    def dfp_dao_karma_record(self, cause: str) -> bool:
        """埋一个「因」（过 `DFP_DAO_KARMA_RIPEN_SEC` 才结果）。同一瞬间同一因只记一条。"""
        name = str(cause or "").strip()
        if not name or name not in dfp_dao_causes():
            return False
        dao = self.dfp_dao_state()
        now = dfp_now_ts()
        karma = list(dao.get("karma") or [])
        if any(str(row.get("cause")) == name and now - dfp_safe_float(row.get("at"), 0.0) < 60.0 for row in karma):
            return False
        karma.append({"cause": name, "at": now, "ripen_at": now + DFP_DAO_KARMA_RIPEN_SEC})
        dao["karma"] = karma[-DFP_DAO_KARMA_LIMIT:]
        self.dfp_dao_save()
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_debug("埋下一个因（%s）：%s" % (name, dfp_dao_cause_detail(name)))
        return True

    def dfp_dao_grow_by(self, source: str, times: float = 1.0) -> Dict[str, float]:
        """按「修真层」的各种表养一次（道行 / 气血 / 脏腑 / 妖气；互动那几处都调它）。

        ★ 只动这一层自己的数字，**绝不回写**既有数值（饱食 / 精力 / 经验 / 亲密度）。
        """
        name = str(source or "").strip()
        if not name:
            return {}
        # ★ 关掉「让她修」之后整层都不动（互动里那几处也走这里，所以在这里统一拦）
        if not bool(self._dfp_settings.dfp_get("dao_enabled", True)):
            return {}
        grown: Dict[str, float] = {}
        try:
            scale = float(times or 1.0)
            gain = DFP_DAO_GAIN.get(name)
            if gain:
                self.dfp_dao_add("dao", float(gain) * scale)
                grown["dao"] = round(float(gain) * scale, 2)
            qixue = DFP_DAO_QIXUE_GAIN.get(name)
            if qixue:
                self.dfp_dao_add("qi", float(qixue[0]) * scale)
                self.dfp_dao_add("blood", float(qixue[1]) * scale)
                grown["qi"] = round(float(qixue[0]) * scale, 2)
            for organ, amount in (DFP_DAO_ZANGFU_GAIN.get(name) or {}).items():
                self.dfp_dao_zangfu_add(organ, float(amount) * scale)
                grown["zangfu:%s" % organ] = round(float(amount) * scale, 2)
            yao = DFP_DAO_YAO_GAIN.get(name)
            if yao:
                self.dfp_dao_add("yao", float(yao) * scale)
                grown["yao"] = round(float(yao) * scale, 2)
            gui = DFP_DAO_GUI_GAIN.get(name)
            if gui:
                self.dfp_dao_add("gui", float(gui) * scale)
                grown["gui"] = round(float(gui) * scale, 2)
            if name in ("pet", "feed", "play", "coax", "sleep", "chat"):
                self.dfp_dao_add("yuan", 0.3 * scale)
            if name in ("sleep", "wake"):
                self.dfp_dao_add("yuanshen", 0.5 * scale)
            if name == "feed":
                self.dfp_dao_karma_record("施食")
            elif name == "pet":
                self.dfp_dao_karma_record("亲昵")
            elif name == "play":
                self.dfp_dao_karma_record("陪玩")
            elif name == "sleep":
                self.dfp_dao_karma_record("同眠")
            elif name == "chat":
                self.dfp_dao_karma_record("唤名")
            if name == "act_cute":
                self.dfp_dao_karma_record("贪眠")
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("修真层结算失败（%s）：%s: %s" % (name, type(exc).__name__, exc))
        return grown

    def _dfp_dao_queue(self, pool: str, arg: str) -> bool:
        """排一句「道」的话（同一池同一名字不重复排、最多 3 条）。"""
        key = str(pool or "").strip()
        name = str(arg or "").strip()
        if not key or not name:
            return False
        if not dfp_dao_pool_lines(key, name):
            return False
        dao = self.dfp_dao_state()
        pending = list(dao.get("pending") or [])
        if any(str(row.get("pool")) == key and str(row.get("arg")) == name for row in pending):
            return False
        pending.append({"pool": key, "arg": name, "at": dfp_now_ts()})
        dao["pending"] = pending[-DFP_DAO_PENDING_LIMIT:]
        self.dfp_dao_save()
        return True

    def dfp_dao_observe(self) -> None:
        """看看修真层有没有跨档（跨了就排队说一句），顺便结因果 / 记缘谱 / 看登仙。"""
        try:
            dao = self.dfp_dao_state()
            seen = dict(dao.get("seen") or {})
            self._dfp_dao_refresh_yun(notify=True)
            # ① 境界（只升不降，升了就排「突破」那句；出生那一刻不报）
            realm = self.dfp_dao_realm()
            if realm and str(seen.get("dao") or "") != realm:
                first_realm = str((dfp_dao_realms() or [{}])[0].get("name") or "")
                if seen.get("dao") or realm != first_realm:
                    self._dfp_dao_queue("realm", realm)
                seen["dao"] = realm
            # ② 九块档位（心性 / 元神 / 气血 / 业 / 神 / 仙 / 鬼 / 妖）
            for block in ("xing", "yuanshen", "qixue", "ye", "shen", "xian", "gui", "yao"):
                value = self.dfp_dao_ye()["net"] if block == "ye" else (
                    self.dfp_dao_qixue()["mean"] if block == "qixue" else self.dfp_dao_value(block))
                label = dfp_dao_tier_name(block, value)
                if label and str(seen.get(block) or "") != label:
                    if seen.get(block):
                        self._dfp_dao_queue(block, label)
                    seen[block] = label
            # ③ 脏腑：虚（≤35）说虚话、旺（≥75）说实话
            seen_zang = dict(seen.get("zangfu") or {})
            for organ in dfp_dao_zangfu_names():
                value = self.dfp_dao_zangfu_value(organ)
                band = "weak" if value <= DFP_SPIRIT_LOW else ("strong" if value >= DFP_SPIRIT_HIGH else "mid")
                if str(seen_zang.get(organ) or "") != band and band != "mid":
                    self._dfp_dao_queue("organ_weak" if band == "weak" else "organ_strong", organ)
                seen_zang[organ] = band
            seen["zangfu"] = seen_zang
            # ④ 缘谱：条件到了就记上一条新的缘
            kinds = list(dao.get("found_yuan") or [])
            hour = dfp_local_hour()
            night = hour >= DFP_DAO_YIN_HOURS[0] or hour < DFP_DAO_YIN_HOURS[1]
            for kind, ready in (
                ("主人", True),
                ("梦", dfp_safe_int(self._dfp_dream_count(), 0) > 0),
                ("月", night and random.random() < 0.25),
                ("星", (hour >= 23 or hour < 4) and self.dfp_dao_value("xing") >= 60.0),
                ("灯", (hour >= 21 or hour < 2) and not self._dfp_state.get("asleep")),
                ("影", self.dfp_dao_value("gui") >= 40.0),
            ):
                if ready and kind not in kinds and dfp_dao_pool_lines("yuan_kind", kind):
                    kinds.append(kind)
                    self._dfp_dao_queue("yuan_kind", kind)
            dao["found_yuan"] = kinds
            # ⑤ 登仙：道行到「过龙门」+ 神性够 + 仙阶满 ⇒ 一辈子说一次
            if (not dao.get("xian_said") and self.dfp_dao_value("xian") >= 90.0
                    and self.dfp_dao_realm_index() >= dfp_dao_xian_gate_index()
                    and dfp_dao_pool_lines("xian", "位列仙班")):
                dao["xian_said"] = True
                self._dfp_dao_queue("xian", "位列仙班")
            # ⑥ 因果：到点了就「结果」——说一句果 + 改一笔业
            now = dfp_now_ts()
            karma = list(dao.get("karma") or [])
            done = list(dao.get("karma_done") or [])
            rest: List[Dict[str, Any]] = []
            ripened = 0
            for row in karma:
                if now - dfp_safe_float(row.get("ripen_at"), 0.0) < 0.0:
                    rest.append(row)
                    continue
                cause = str(row.get("cause") or "")
                good = dfp_dao_cause_good(cause)
                self.dfp_dao_ye_add(good if good > 0 else 0.0, -good if good < 0 else 0.0)
                done.append({"cause": cause, "at": dfp_safe_float(row.get("at"), 0.0),
                             "done_at": now, "fruit": dfp_dao_cause_detail(cause)})
                self._dfp_dao_queue("karma", cause)
                ripened += 1
            if ripened:
                dao["karma"] = rest
                dao["karma_done"] = done[-DFP_DAO_KARMA_DONE_LIMIT:]
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_info("因果结果了 %d 笔（业：善 %.1f / 恶 %.1f）"
                                              % (ripened, self.dfp_dao_value("ye_good"), self.dfp_dao_value("ye_bad")))
            dao["seen"] = seen
            self.dfp_dao_save()
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("看修真层跨档失败：%s: %s" % (type(exc).__name__, exc))

    def dfp_dao_fill(self, text: str, pool: str = "", arg: str = "") -> str:
        """修真层文案的占位符：先换自己这套，剩下的交给三魂七魄 / 灵魂那两套清。"""
        result = str(text or "")
        key = str(arg or "").strip()
        which = str(pool or "").strip()
        if which == "organ_weak" or which == "organ_strong":
            number = self.dfp_dao_zangfu_value(key)
        elif which == "karma":
            number = self.dfp_dao_ye()["net"]
        elif which == "yuan" or which == "yuan_kind":
            number = self.dfp_dao_value("yuan")
        else:
            number = self.dfp_dao_value("dao") if which in ("realm", "realm_idle", "dao") else 0.0
        if which in ("realm", "realm_idle", "ming", "shu", "note"):
            number = self.dfp_dao_value("dao")
        return self._dfp_dao_placeholders(result, number)

    def _dfp_dao_placeholders(self, text: str, number: float = 0.0) -> str:
        """修真层自己那批占位符（数值一律取当前档案，保证「说的」与「显示的」一致）。"""
        result = str(text or "")
        pairs = {
            "{realm}": self.dfp_dao_realm(),
            "{dao}": "%d" % int(round(self.dfp_dao_value("dao"))),
            "{xing}": dfp_dao_tier_name("xing", self.dfp_dao_value("xing")),
            "{zang}": self.dfp_dao_zangfu_weakest(),
            "{qixue}": self.dfp_dao_qixue_name(),
            "{yun}": self.dfp_dao_yun_name(),
            "{shu}": self.dfp_dao_shu_name(),
            "{ye}": self.dfp_dao_ye_name(),
            "{yuan}": dfp_dao_tier_name("yuan", self.dfp_dao_value("yuan")),
            "{shen}": dfp_dao_tier_name("shen", self.dfp_dao_value("shen")),
            "{xian}": dfp_dao_tier_name("xian", self.dfp_dao_value("xian")),
            "{gui}": dfp_dao_tier_name("gui", self.dfp_dao_value("gui")),
            "{yao}": dfp_dao_tier_name("yao", self.dfp_dao_value("yao")),
            "{value}": "%d" % int(round(dfp_safe_float(number, 0.0))),
        }
        for token, value in pairs.items():
            result = result.replace(token, str(value))
        return self.dfp_spirit_fill(result)

    def dfp_dao_pick(self) -> str:
        """取一条「修真层想说的话」的成品（取走就出队）。**不开口**。"""
        try:
            dao = self.dfp_dao_state()
            pending = list(dao.get("pending") or [])
            if not pending:
                return ""
            row = pending.pop(0)
            dao["pending"] = pending
            pool = str(row.get("pool") or "").strip()
            arg = str(row.get("arg") or "").strip()
            lines = dfp_dao_pool_lines(pool, arg)
            text = ""
            if lines:
                text = self.dfp_dao_fill(str(dfp_random_pick(lines, lines[0])), pool, arg)
            dao["spoken"] = dfp_safe_int(dao.get("spoken"), 0) + 1
            self.dfp_dao_save()
            if text and self._dfp_logger is not None:
                self._dfp_logger.dfp_info("修真层有感（%s:%s）：%s" % (pool, arg, dfp_truncate_text(text, 40)))
            return text
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("修真层开口失败：%s: %s" % (type(exc).__name__, exc))
            return ""

    def dfp_dao_now(self) -> str:
        """界面入口「看看她的道」：没排队时就说一句最当紧的（最虚的脏腑 / 今日运势 / 定数）。"""
        try:
            dao = self.dfp_dao_state()
            if not (dao.get("pending") or []):
                weakest = self.dfp_dao_zangfu_weakest()
                self._dfp_dao_refresh_yun(notify=False)
                if not self._dfp_dao_queue("yun", self.dfp_dao_yun_name()):
                    if not self._dfp_dao_queue("qixue", self.dfp_dao_qixue_name()):
                        if not self._dfp_dao_queue("organ_weak", weakest):
                            self._dfp_dao_queue("note", "定数")
            text = self.dfp_dao_pick()
            if text:
                self.dfp_say("idle", text)
            return text
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("修真层开口失败：%s: %s" % (type(exc).__name__, exc))
            return ""

    def dfp_dao_reborn(self) -> Dict[str, Any]:
        """重定命数（重新投胎）：定数重抽 + 修真层数值回到起点。**灵魂 / 三魂七魄 / 关系都留着**。"""
        dao = self.dfp_dao_state()
        dao["birth"] = self._dfp_dao_roll_birth()
        dao["value"] = {
            "dao": 0.0, "xing": 40.0, "yuanshen": 0.0, "qi": 60.0, "blood": 60.0,
            "ye_good": 0.0, "ye_bad": 0.0, "yuan": 5.0, "shen": 0.0, "xian": 0.0, "gui": 0.0, "yao": 0.0,
        }
        dao["zangfu"] = {name: 60.0 for name in dfp_dao_zangfu_names()}
        dao["seen"] = {name: "" for name in DFP_DAO_TIER_BLOCKS}
        dao["seen"]["dao"] = ""
        dao["seen"]["yun"] = ""
        dao["seen"]["zangfu"] = {name: "mid" for name in dfp_dao_zangfu_names()}
        dao["pending"] = []
        dao["karma"] = []
        dao["karma_done"] = []
        dao["found_yuan"] = []
        dao["xian_said"] = False
        dao["created_at"] = dfp_now_ts()
        dao["yun"] = {"day": "", "value": 2, "name": ""}
        self.dfp_dao_save()
        self._dfp_dao_refresh_yun(notify=False)
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("重定命数：%s" % self.dfp_dao_ming_line())
        return self.dfp_dao_birth()

    def dfp_dao_tick(self, now: float) -> None:
        """心跳里的修真层：气血消耗 / 脏腑 / 鬼妖气 / 道行与元神慢长 / 神性仙阶 / 因果 / 看跨档。"""
        if not bool(self._dfp_settings.dfp_get("dao_enabled", True)):
            return
        try:
            dao = self.dfp_dao_state()
            last = dfp_safe_float(dao.get("last_tick_at"), 0.0)
            if now - last >= DFP_DAO_TICK_SEC:
                dao["last_tick_at"] = now
                hour = dfp_local_hour()
                night = hour >= DFP_DAO_YIN_HOURS[0] or hour < DFP_DAO_YIN_HOURS[1]
                # ① 气血：白天回一点、夜里耗一点
                self.dfp_dao_grow_by("night" if night else "day")
                # ② 脏腑：按昼夜慢走 + 被晾久了伤心肝
                for organ, amount in (DFP_DAO_ZANGFU_GAIN.get("night" if night else "day") or {}).items():
                    self.dfp_dao_zangfu_add(organ, float(amount))
                gap = now - dfp_safe_float(self._dfp_state.get("last_interaction"), 0.0)
                lonely = gap >= DFP_DAO_LONELY_GAP_SEC
                if lonely:
                    for organ, amount in (DFP_DAO_ZANGFU_GAIN.get("lonely") or {}).items():
                        self.dfp_dao_zangfu_add(organ, float(amount))
                    self.dfp_dao_karma_record("疏慢")
                # ③ 道行 / 元神 / 心性 / 缘：陪着就慢慢长
                self.dfp_dao_grow_by("tidy")
                self.dfp_dao_add("yuanshen", 0.2)
                self.dfp_dao_add("xing", -0.3 if lonely else 0.15)
                if not lonely:
                    self.dfp_dao_add("yuan", 0.15)
                # ④ 鬼气 / 妖气：夜长昼退；气血虚更容易招阴；静心收妖
                self.dfp_dao_add("gui", 0.6 if night else -0.6)
                if self.dfp_dao_qixue()["mean"] <= DFP_SPIRIT_LOW:
                    self.dfp_dao_add("gui", 0.8)
                if bool(self._dfp_state.get("asleep")):
                    self.dfp_dao_add("gui", -1.5)
                if self.dfp_dao_value("xing") >= 60.0:
                    self.dfp_dao_grow_by("calm")
                # ⑤ 神性（善业养神）/ 仙阶（要过龙门 + 神性够）
                self.dfp_dao_add("shen", max(-0.2, self.dfp_dao_ye()["net"] / 400.0))
                if (self.dfp_dao_realm_index() >= dfp_dao_xian_gate_index()
                        and self.dfp_dao_value("shen") >= DFP_DAO_XIAN_GATE_SHEN):
                    self.dfp_dao_add("xian", 0.3)
            self.dfp_dao_observe()
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("修真层心跳失败：%s: %s" % (type(exc).__name__, exc))

    def dfp_dao_tier_rows(self) -> List[Dict[str, Any]]:
        """九块档位的现状（界面用）：`[{block, label, name, value, min, next_name, next_min}]`。"""
        dao = self.dfp_dao_state()
        rows: List[Dict[str, Any]] = []
        for block in ("xing", "yuanshen", "qixue", "ye", "shen", "xian", "gui", "yao", "yun"):
            if block == "ye":
                value = self.dfp_dao_ye()["net"]
            elif block == "qixue":
                value = self.dfp_dao_qixue()["mean"]
            elif block == "yun":
                value = dfp_safe_float((dao.get("yun") or {}).get("value"), 0.0)
            else:
                value = self.dfp_dao_value(block)
            tiers = sorted((dfp_dao().get(block) or []), key=lambda item: dfp_safe_float(item.get("min"), 0.0))
            current = dfp_dao_tier(block, value)
            nxt = {}
            for item in tiers:
                if dfp_safe_float(item.get("min"), 0.0) > dfp_safe_float(current.get("min"), 0.0):
                    nxt = item
                    break
            rows.append({
                "block": block,
                "label": DFP_DAO_BLOCK_LABELS.get(block, block),
                "name": str(current.get("name") or ""),
                "value": round(value, 1),
                "min": dfp_safe_float(current.get("min"), 0.0),
                "next_name": str(nxt.get("name") or ""),
                "next_min": dfp_safe_float(nxt.get("min"), 0.0),
            })
        return rows

    def dfp_dao_zangfu_rows(self) -> List[Dict[str, Any]]:
        """五脏六腑十一个的现状（界面用）。"""
        rows: List[Dict[str, Any]] = []
        for name in dfp_dao_zangfu_names():
            value = self.dfp_dao_zangfu_value(name)
            band = "weak" if value <= DFP_SPIRIT_LOW else ("strong" if value >= DFP_SPIRIT_HIGH else "mid")
            rows.append({
                "name": name,
                "zhu": dfp_dao_zangfu_zhu(name),
                "value": round(value, 1),
                "band": band,
                "band_label": {"weak": "虚", "strong": "实", "mid": "平"}.get(band, "平"),
            })
        return rows

    def dfp_dao_karma_rows(self) -> List[Dict[str, Any]]:
        """因果账：未结（还差多久）+ 已结（最近几条）。"""
        dao = self.dfp_dao_state()
        now = dfp_now_ts()
        pending: List[Dict[str, Any]] = []
        for row in (dao.get("karma") or []):
            cause = str(row.get("cause") or "")
            pending.append({
                "cause": cause,
                "detail": dfp_dao_cause_detail(cause),
                "good": dfp_dao_cause_good(cause),
                "at": dfp_safe_float(row.get("at"), 0.0),
                "left": max(0.0, dfp_safe_float(row.get("ripen_at"), 0.0) - now),
                "done": False,
            })
        done: List[Dict[str, Any]] = []
        for row in (dao.get("karma_done") or []):
            cause = str(row.get("cause") or "")
            done.append({
                "cause": cause,
                "detail": str(row.get("fruit") or dfp_dao_cause_detail(cause)),
                "good": dfp_dao_cause_good(cause),
                "at": dfp_safe_float(row.get("done_at"), 0.0),
                "left": 0.0,
                "done": True,
            })
        return pending + done[-DFP_DAO_KARMA_DONE_LIMIT:]

    def dfp_dao_summary_text(self) -> str:
        """给界面用的现状（三到四行）。"""
        birth = self.dfp_dao_birth()
        nxt_name, nxt_left = self.dfp_dao_realm_next()
        line2 = "道行 %.0f ｜ 心性 %s ｜ 元神 %s ｜ 今日运势 %s" % (
            self.dfp_dao_value("dao"),
            dfp_dao_tier_name("xing", self.dfp_dao_value("xing")),
            dfp_dao_tier_name("yuanshen", self.dfp_dao_value("yuanshen")),
            self.dfp_dao_yun_name(),
        )
        line3 = "气血 %s（气 %.0f / 血 %.0f）｜ 最虚的脏腑：%s %.0f" % (
            self.dfp_dao_qixue_name(), self.dfp_dao_value("qi"), self.dfp_dao_value("blood"),
            self.dfp_dao_zangfu_weakest(), self.dfp_dao_zangfu_value(self.dfp_dao_zangfu_weakest()),
        )
        line4 = "业 %s（净 %.1f）｜ 缘 %s（谱 %d 类）｜ 神 %s ｜ 仙 %s ｜ 鬼 %s ｜ 妖 %s" % (
            self.dfp_dao_ye_name(), self.dfp_dao_ye()["net"],
            dfp_dao_tier_name("yuan", self.dfp_dao_value("yuan")), len(self.dfp_dao_state().get("found_yuan") or []),
            dfp_dao_tier_name("shen", self.dfp_dao_value("shen")),
            dfp_dao_tier_name("xian", self.dfp_dao_value("xian")),
            dfp_dao_tier_name("gui", self.dfp_dao_value("gui")),
            dfp_dao_tier_name("yao", self.dfp_dao_value("yao")),
        )
        return "境界 %s%s ｜ %s\n%s\n%s\n%s" % (
            self.dfp_dao_realm(),
            ("（下一段 %s 还差 %.0f）" % (nxt_name, nxt_left)) if nxt_name else "（已是最后一段）",
            self.dfp_dao_ming_line(), line2, line3, line4,
        )

    def dfp_ai_context_line(self) -> str:
        return (
            "【桌宠当前状态】等级 Lv%s（%s）；心情 %.0f/100；饱食 %.0f/100；精力 %.0f/100；与主人的亲密度 %.0f/100；"
            "当前%s；累计被摸头 %d 次、被投喂 %d 次、聊天 %d 次。"
            % (
                dfp_safe_int(self._dfp_state.get("level"), 1),
                self.dfp_level_title(),
                dfp_safe_float(self._dfp_state.get("mood"), 0.0),
                dfp_safe_float(self._dfp_state.get("satiety"), 0.0),
                dfp_safe_float(self._dfp_state.get("energy"), 0.0),
                dfp_safe_float(self._dfp_state.get("intimacy"), 0.0),
                "在睡觉" if self._dfp_state.get("asleep") else "醒着",
                dfp_safe_int(self._dfp_state.get("total_pets"), 0),
                dfp_safe_int(self._dfp_state.get("total_feeds"), 0),
                dfp_safe_int(self._dfp_state.get("total_chats"), 0),
            )
        )

    def dfp_state_snapshot(self) -> Dict[str, Any]:
        snapshot = dict(self._dfp_state)
        snapshot["level_title"] = self.dfp_level_title()
        snapshot["exp_needed"] = self.dfp_exp_needed()
        snapshot["exp_percent"] = dfp_clamp(
            100.0 * dfp_safe_int(self._dfp_state.get("exp"), 0) / max(1, snapshot["exp_needed"]), 0.0, 100.0
        )
        snapshot["age_text"] = dfp_human_duration(dfp_now_ts() - dfp_safe_float(self._dfp_state.get("born_at"), dfp_now_ts()))
        snapshot["status_text"] = self.dfp_status_text()
        for key in DFP_STAT_KEYS:
            snapshot[key] = dfp_safe_float(self._dfp_state.get(key), 0.0)
        return snapshot

    def dfp_set_state_value(self, key: str, value: Any) -> bool:
        """外部（设置/调试）直接改状态值；仅接受已知键。"""
        if key not in DFP_STATE_DEFAULTS:
            return False
        if isinstance(DFP_STATE_DEFAULTS[key], bool):
            self._dfp_state[key] = bool(value)
        elif isinstance(DFP_STATE_DEFAULTS[key], str):
            self._dfp_state[key] = str(value)
        else:
            self._dfp_state[key] = dfp_clamp(dfp_safe_float(value, 0.0), 0.0, 100.0 if key in DFP_STAT_KEYS else 1e12)
        self.dfp_save_state(force=True)
        self.dfpStateChanged.emit(self.dfp_state_snapshot())
        return True

    def dfp_reset_state(self) -> None:
        self._dfp_state = dict(DFP_STATE_DEFAULTS)
        self._dfp_state["born_at"] = dfp_now_ts()
        self._dfp_state["last_tick"] = dfp_now_ts()
        self._dfp_state["last_save"] = dfp_now_ts()
        try:
            self._dfp_database.dfp_add_event("reset", "状态已重置")
        except Exception:
            pass
        self.dfpEventLogged.emit("reset", "状态已重置")
        self.dfp_save_state(force=True)
        self.dfpStateChanged.emit(self.dfp_state_snapshot())


# =============================================================================
#  二十、DeepSeek 客户端（Key 只在 .env；支持多 Key 轮换、重试、流式、余额查询）
# =============================================================================


class DFPConfigError(Exception):
    """配置缺失（例如没有 API Key、没装 requests）。"""


class DFPNetworkError(Exception):
    """网络层错误（超时、连接失败）。"""


class DFPCancelledError(Exception):
    """用户主动取消。"""


class DFPHttpError(Exception):
    def __init__(self, status: int, message: str, retryable: bool = False):
        super().__init__("HTTP %s: %s" % (status, message))
        self.status = int(status)
        self.message = str(message)
        self.retryable = bool(retryable)


class DFPDeepSeekClient:
    """OpenAI 兼容的 DeepSeek 接口客户端（同步，供线程池调用）。"""

    def __init__(self, logger: Optional[DFPLogger] = None):
        self._dfp_logger = logger
        self._dfp_api_base = "https://api.deepseek.com"
        self._dfp_keys: List[str] = []
        self._dfp_key_index = 0
        self._dfp_timeout = 60.0
        self._dfp_default_model = "deepseek-chat"
        self._dfp_local = threading.local()
        self._dfp_lock = threading.RLock()
        self._dfp_stats: Dict[str, Any] = {
            "requests": 0,
            "success": 0,
            "failed": 0,
            "retries": 0,
            "tokens": 0,
            "last_error": "",
            "last_latency": 0.0,
            "last_model": "",
        }
        self.dfp_reload_env()

    # --- .env ---
    def dfp_reload_env(self) -> None:
        dfp_load_env(force=True)
        base = dfp_env_str("DEEPSEEK_API_BASE", "") or dfp_env_str("DEEPSEEK_BASE_URL", "")
        self._dfp_api_base = (base or "https://api.deepseek.com").rstrip("/")
        self._dfp_keys = dfp_collect_api_keys()
        self._dfp_timeout = dfp_env_float("DEEPSEEK_TIMEOUT", 60.0, 5.0, 600.0)
        self._dfp_default_model = dfp_env_str("DEEPSEEK_MODEL", "deepseek-chat")
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info(
                "接口配置已载入：%s，Key %d 个，默认模型 %s" % (self._dfp_api_base, len(self._dfp_keys), self._dfp_default_model)
            )

    def dfp_api_base(self) -> str:
        return self._dfp_api_base

    def dfp_default_model(self) -> str:
        return self._dfp_default_model

    def dfp_timeout(self) -> float:
        return self._dfp_timeout

    def dfp_key_count(self) -> int:
        return len(self._dfp_keys)

    def dfp_has_key(self) -> bool:
        return bool(self._dfp_keys)

    def dfp_masked_key(self) -> str:
        if not self._dfp_keys:
            return "（.env 中未配置 DEEPSEEK_API_KEY）"
        return dfp_mask_secret(self._dfp_keys[0])

    def dfp_statistics(self) -> Dict[str, Any]:
        with self._dfp_lock:
            stats = dict(self._dfp_stats)
        stats["key_count"] = len(self._dfp_keys)
        stats["api_base"] = self._dfp_api_base
        return stats

    def dfp_next_key(self) -> str:
        with self._dfp_lock:
            if not self._dfp_keys:
                raise DFPConfigError("未在 .env 中配置 DEEPSEEK_API_KEY")
            key = self._dfp_keys[self._dfp_key_index % len(self._dfp_keys)]
            self._dfp_key_index = (self._dfp_key_index + 1) % len(self._dfp_keys)
            return key

    def dfp_peek_key(self, offset: int = 0) -> str:
        with self._dfp_lock:
            if not self._dfp_keys:
                raise DFPConfigError("未在 .env 中配置 DEEPSEEK_API_KEY")
            return self._dfp_keys[offset % len(self._dfp_keys)]

    def _dfp_session(self) -> Any:
        """requests.Session 不是线程安全的，每个线程各留一份。"""
        if requests is None:
            raise DFPConfigError("未安装 requests 库，无法访问网络接口")
        session = getattr(self._dfp_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": DFP_USER_AGENT})
            self._dfp_local.session = session
        return session

    def dfp_close(self) -> None:
        session = getattr(self._dfp_local, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
            self._dfp_local.session = None

    # --- 请求构造 ---
    def dfp_build_payload(
        self,
        messages: Sequence[Dict[str, str]],
        model: str = "",
        temperature: float = 1.0,
        max_tokens: int = 512,
        stream: bool = False,
        top_p: float = 1.0,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model or self._dfp_default_model,
            "messages": [{"role": str(item.get("role", "user")), "content": str(item.get("content", ""))} for item in messages],
            "temperature": float(dfp_clamp(temperature, 0.0, 2.0)),
            "max_tokens": int(dfp_clamp(max_tokens, 1, 8192)),
            "stream": bool(stream),
        }
        if top_p is not None and abs(float(top_p) - 1.0) > 1e-6:
            payload["top_p"] = float(dfp_clamp(top_p, 0.0, 1.0))
        if frequency_penalty:
            payload["frequency_penalty"] = float(dfp_clamp(frequency_penalty, -2.0, 2.0))
        if presence_penalty:
            payload["presence_penalty"] = float(dfp_clamp(presence_penalty, -2.0, 2.0))
        return payload

    # --- 对话 ---
    def dfp_chat(
        self,
        messages: Sequence[Dict[str, str]],
        model: str = "",
        temperature: float = 1.0,
        max_tokens: int = 512,
        stream: bool = False,
        top_p: float = 1.0,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
        cancel_event: Optional[threading.Event] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int], None]] = None,
        max_retry: int = 2,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """发起一次对话请求，返回 {text, usage, model, finish_reason, key_index}。"""
        if not self._dfp_keys:
            raise DFPConfigError("未在 .env 中配置 DEEPSEEK_API_KEY")
        payload = self.dfp_build_payload(
            messages, model, temperature, max_tokens, stream, top_p, frequency_penalty, presence_penalty
        )
        timeout = float(timeout if timeout is not None else self._dfp_timeout)
        attempts = max(1, int(dfp_clamp(max_retry, 0, 6)) + 1)
        last_error: Optional[Exception] = None
        for attempt in range(attempts):
            if cancel_event is not None and cancel_event.is_set():
                raise DFPCancelledError("请求已取消")
            api_key = self.dfp_next_key()
            key_index = (self._dfp_key_index - 1) % max(1, len(self._dfp_keys))
            started = time.monotonic()
            with self._dfp_lock:
                self._dfp_stats["requests"] += 1
            try:
                result = self._dfp_request_once(payload, api_key, stream, cancel_event, on_chunk, on_progress, timeout)
                with self._dfp_lock:
                    self._dfp_stats["success"] += 1
                    self._dfp_stats["last_latency"] = time.monotonic() - started
                    self._dfp_stats["last_model"] = result.get("model", payload["model"])
                    self._dfp_stats["tokens"] += int((result.get("usage") or {}).get("total_tokens", 0) or 0)
                result["key_index"] = key_index
                return result
            except DFPCancelledError:
                raise
            except DFPConfigError:
                raise
            except Exception as exc:
                last_error = exc
                retryable = True
                if isinstance(exc, DFPHttpError):
                    retryable = exc.retryable
                    if exc.status in (401, 402, 403, 404, 405, 422):
                        retryable = False
                if attempt >= attempts - 1 or not retryable:
                    break
                wait = min(8.0, 1.0 * (2 ** attempt))
                with self._dfp_lock:
                    self._dfp_stats["retries"] += 1
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_warning("请求失败将重试（第 %d 次）：%s" % (attempt + 1, exc))
                # 取消时不要傻等，每 0.2 秒看一眼取消标志
                waited = 0.0
                while waited < wait:
                    if cancel_event is not None and cancel_event.is_set():
                        raise DFPCancelledError("请求已取消")
                    time.sleep(0.2)
                    waited += 0.2
        message = str(last_error) if last_error else "未知错误"
        with self._dfp_lock:
            self._dfp_stats["failed"] += 1
            self._dfp_stats["last_error"] = message
        if isinstance(last_error, (DFPConfigError, DFPCancelledError, DFPHttpError)):
            raise last_error
        if isinstance(last_error, DFPNetworkError):
            raise last_error
        raise DFPNetworkError(message)

    def _dfp_request_once(
        self,
        payload: Dict[str, Any],
        api_key: str,
        stream: bool,
        cancel_event: Optional[threading.Event],
        on_chunk: Optional[Callable[[str], None]],
        on_progress: Optional[Callable[[int], None]],
        timeout: float,
    ) -> Dict[str, Any]:
        session = self._dfp_session()
        url = self._dfp_api_base + DFP_CHAT_ENDPOINT
        headers = {"Authorization": "Bearer %s" % api_key, "Content-Type": "application/json", "Accept": "application/json"}
        try:
            response = session.post(url, json=payload, headers=headers, timeout=timeout, stream=bool(stream))
        except Exception as exc:
            raise DFPNetworkError("连接失败：%s" % exc)
        try:
            if response.status_code >= 400:
                detail = ""
                try:
                    data = response.json()
                    detail = str((data.get("error") or {}).get("message") or data)[:300]
                except Exception:
                    detail = (response.text or "")[:300]
                retryable = response.status_code == 429 or response.status_code >= 500
                raise DFPHttpError(response.status_code, detail or "请求被拒绝", retryable)
            if stream:
                return self._dfp_consume_stream(response, cancel_event, on_chunk, on_progress, payload.get("model", ""))
            try:
                data = response.json()
            except Exception:
                raise DFPNetworkError("返回内容不是合法 JSON")
            return self._dfp_parse_completion(data, payload.get("model", ""))
        finally:
            try:
                response.close()
            except Exception:
                pass

    def _dfp_parse_completion(self, data: Dict[str, Any], model: str) -> Dict[str, Any]:
        choices = data.get("choices") or []
        text = ""
        finish_reason = ""
        if choices:
            first = choices[0] or {}
            message = first.get("message") or {}
            text = str(message.get("content") or "")
            finish_reason = str(first.get("finish_reason") or "")
        return {
            "text": text,
            "usage": data.get("usage") or {},
            "model": str(data.get("model") or model),
            "finish_reason": finish_reason,
        }

    def _dfp_consume_stream(
        self,
        response: Any,
        cancel_event: Optional[threading.Event],
        on_chunk: Optional[Callable[[str], None]],
        on_progress: Optional[Callable[[int], None]],
        model: str,
    ) -> Dict[str, Any]:
        pieces: List[str] = []
        usage: Dict[str, Any] = {}
        finish_reason = ""
        received = 0
        try:
            for raw_line in response.iter_lines(decode_unicode=False):
                if cancel_event is not None and cancel_event.is_set():
                    raise DFPCancelledError("请求已取消")
                if not raw_line:
                    continue
                try:
                    line = raw_line.decode("utf-8", "replace").strip()
                except Exception:
                    continue
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                except Exception:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        pieces.append(str(piece))
                        received += len(str(piece))
                        if on_chunk is not None:
                            try:
                                on_chunk(str(piece))
                            except Exception:
                                pass
                        if on_progress is not None:
                            try:
                                on_progress(received)
                            except Exception:
                                pass
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])
        except DFPCancelledError:
            raise
        except Exception as exc:
            raise DFPNetworkError("流式读取中断：%s" % exc)
        return {
            "text": "".join(pieces),
            "usage": usage,
            "model": model,
            "finish_reason": finish_reason,
        }

    # --- 余额 ---
    def dfp_balance(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """查询账户余额（不消耗 token），供设置里的「测试连接」使用。"""
        if not self._dfp_keys:
            raise DFPConfigError("未在 .env 中配置 DEEPSEEK_API_KEY")
        session = self._dfp_session()
        url = self._dfp_api_base + DFP_BALANCE_ENDPOINT
        headers = {"Authorization": "Bearer %s" % self.dfp_peek_key(0), "Accept": "application/json"}
        try:
            response = session.get(url, headers=headers, timeout=float(timeout or min(30.0, self._dfp_timeout)))
        except Exception as exc:
            raise DFPNetworkError("连接失败：%s" % exc)
        try:
            if response.status_code >= 400:
                raise DFPHttpError(response.status_code, (response.text or "")[:300], response.status_code >= 500)
            return response.json() or {}
        finally:
            try:
                response.close()
            except Exception:
                pass


class DFPDeepSeekSignals(QObject):
    """QRunnable 不能带信号，所以信号单独放在一个 QObject 里（在主线程创建）。"""

    dfpStarted = Signal(str)
    dfpChunk = Signal(str, str)
    dfpProgress = Signal(str, int)
    dfpDone = Signal(str, str, dict)
    dfpError = Signal(str, str)
    dfpFinished = Signal(str)


class DFPDeepSeekWorker(QRunnable):
    """一次对话请求的工作单元。"""

    def __init__(self, signals: DFPDeepSeekSignals, client: DFPDeepSeekClient, request_id: str, messages: Sequence[Dict[str, str]], options: Dict[str, Any]):
        super().__init__()
        self._dfp_signals = signals
        self._dfp_client = client
        self._dfp_request_id = request_id
        self._dfp_messages = list(messages)
        self._dfp_options = dict(options or {})
        self._dfp_cancel = threading.Event()
        try:
            self.setAutoDelete(True)
        except Exception:
            pass

    def dfp_request_id(self) -> str:
        return self._dfp_request_id

    def dfp_cancel(self) -> None:
        self._dfp_cancel.set()

    def dfp_is_cancelled(self) -> bool:
        return self._dfp_cancel.is_set()

    def run(self) -> None:
        rid = self._dfp_request_id
        try:
            self._dfp_signals.dfpStarted.emit(rid)
            result = self._dfp_client.dfp_chat(
                self._dfp_messages,
                model=str(self._dfp_options.get("model", "")),
                temperature=dfp_safe_float(self._dfp_options.get("temperature"), 1.0),
                max_tokens=dfp_safe_int(self._dfp_options.get("max_tokens"), 512),
                stream=bool(self._dfp_options.get("stream", True)),
                top_p=dfp_safe_float(self._dfp_options.get("top_p"), 1.0),
                frequency_penalty=dfp_safe_float(self._dfp_options.get("frequency_penalty"), 0.0),
                presence_penalty=dfp_safe_float(self._dfp_options.get("presence_penalty"), 0.0),
                cancel_event=self._dfp_cancel,
                on_chunk=lambda piece: self._dfp_signals.dfpChunk.emit(rid, str(piece)),
                on_progress=lambda count: self._dfp_signals.dfpProgress.emit(rid, int(count)),
                max_retry=dfp_safe_int(self._dfp_options.get("max_retry"), 2),
                timeout=dfp_safe_float(self._dfp_options.get("timeout"), 60.0) or None,
            )
            self._dfp_signals.dfpDone.emit(rid, str(result.get("text") or ""), dict(result or {}))
        except DFPCancelledError:
            self._dfp_signals.dfpError.emit(rid, "已取消")
        except DFPConfigError as exc:
            self._dfp_signals.dfpError.emit(rid, str(exc))
        except DFPHttpError as exc:
            self._dfp_signals.dfpError.emit(rid, self._dfp_friendly_http_error(exc))
        except DFPNetworkError as exc:
            self._dfp_signals.dfpError.emit(rid, "网络错误：%s" % exc)
        except Exception as exc:
            self._dfp_signals.dfpError.emit(rid, "未知错误：%s" % exc)
        finally:
            self._dfp_signals.dfpFinished.emit(rid)

    @staticmethod
    def _dfp_friendly_http_error(exc: DFPHttpError) -> str:
        mapping = {
            401: "API Key 无效或已过期（401）",
            402: "账户余额不足（402）",
            403: "没有访问权限（403）",
            404: "接口地址不存在（404），请检查 .env 中的 DEEPSEEK_API_BASE",
            422: "请求参数不合法（422）",
            429: "请求过于频繁，请稍后再试（429）",
        }
        if exc.status in mapping:
            return mapping[exc.status]
        if exc.status >= 500:
            return "服务端错误（%d），已重试仍失败" % exc.status
        return "请求失败（%d）：%s" % (exc.status, exc.message)


class DFPChatManager(QObject):
    """线程池 + 状态聚合：所有 UI 操作留在主线程，worker 只负责发请求。"""

    dfpRequestStarted = Signal(str)
    dfpReplyChunk = Signal(str, str)
    dfpReplyProgress = Signal(str, int)
    dfpReplyDone = Signal(str, str, dict)
    dfpReplyError = Signal(str, str)
    dfpRequestFinished = Signal(str)

    def __init__(self, client: DFPDeepSeekClient, settings: DFPSettingsStore, logger: Optional[DFPLogger] = None):
        super().__init__()
        self._dfp_client = client
        self._dfp_settings = settings
        self._dfp_logger = logger
        self._dfp_pool = QThreadPool(self)
        self._dfp_signals = DFPDeepSeekSignals()
        self._dfp_active: Dict[str, DFPDeepSeekWorker] = {}
        self._dfp_history_provider: Optional[Callable[[str, int], List[Dict[str, str]]]] = None
        self._dfp_shutdown = False
        self._dfp_signals.dfpStarted.connect(self._dfp_on_started)
        self._dfp_signals.dfpChunk.connect(self._dfp_on_chunk)
        self._dfp_signals.dfpProgress.connect(self._dfp_on_progress)
        self._dfp_signals.dfpDone.connect(self._dfp_on_done)
        self._dfp_signals.dfpError.connect(self._dfp_on_error)
        self._dfp_signals.dfpFinished.connect(self._dfp_on_finished)
        self.dfp_apply_settings()

    def dfp_apply_settings(self) -> None:
        concurrency = int(dfp_clamp(dfp_safe_int(self._dfp_settings.dfp_get("ai_request_concurrency", 2), 2), 1, 8))
        try:
            self._dfp_pool.setMaxThreadCount(concurrency)
        except Exception:
            pass

    def dfp_pool(self) -> QThreadPool:
        return self._dfp_pool

    def dfp_active_count(self) -> int:
        return len(self._dfp_active)

    def dfp_is_busy(self) -> bool:
        return bool(self._dfp_active)

    def dfp_active_ids(self) -> List[str]:
        return list(self._dfp_active.keys())

    def dfp_check_ready(self) -> Tuple[bool, str]:
        if self._dfp_shutdown:
            return False, "聊天组件已关闭"
        if not self._dfp_settings.dfp_get("ai_enabled", True):
            return False, "AI 功能已在设置里关闭（仍可用离线台词）"
        if requests is None:
            return False, "未安装 requests 库，无法联网"
        if not self._dfp_client.dfp_has_key():
            return False, "未在 .env 中配置 DEEPSEEK_API_KEY（仍可用离线台词）"
        return True, "AI 就绪：%s · %s · %d 个 Key 轮换" % (
            self._dfp_settings.dfp_get("ai_model", "deepseek-chat"),
            self._dfp_client.dfp_api_base(),
            self._dfp_client.dfp_key_count(),
        )

    def dfp_send(self, messages: Sequence[Dict[str, str]], request_id: str = "") -> str:
        """提交一次请求；返回 request_id（失败时抛异常）。"""
        ready, reason = self.dfp_check_ready()
        if not ready:
            raise DFPConfigError(reason)
        rid = request_id or dfp_new_id()
        options = {
            "model": self._dfp_settings.dfp_get("ai_model", "deepseek-chat"),
            "temperature": self._dfp_settings.dfp_get("ai_temperature", 1.0),
            "max_tokens": self._dfp_settings.dfp_get("ai_max_tokens", 512),
            "stream": self._dfp_settings.dfp_get("ai_stream", True),
            "top_p": self._dfp_settings.dfp_get("ai_top_p", 1.0),
            "frequency_penalty": self._dfp_settings.dfp_get("ai_frequency_penalty", 0.0),
            "presence_penalty": self._dfp_settings.dfp_get("ai_presence_penalty", 0.0),
            "max_retry": self._dfp_settings.dfp_get("ai_max_retry", 2),
            "timeout": self._dfp_settings.dfp_get("ai_timeout", 60),
        }
        worker = DFPDeepSeekWorker(self._dfp_signals, self._dfp_client, rid, messages, options)
        self._dfp_active[rid] = worker
        self._dfp_pool.start(worker)
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("已提交对话请求 %s（%d 条上下文）" % (rid[:8], len(messages)))
        return rid

    def dfp_build_messages(self, session_id: str, user_text: str, extra_system: str = "") -> List[Dict[str, str]]:
        system_prompt = str(self._dfp_settings.dfp_get("ai_system_prompt", DFP_DEFAULT_PERSONA) or DFP_DEFAULT_PERSONA)
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if extra_system:
            messages.append({"role": "system", "content": str(extra_system)})
        rounds = dfp_safe_int(self._dfp_settings.dfp_get("ai_context_rounds", 12), 12)
        history = self._dfp_history_provider(session_id, rounds) if self._dfp_history_provider else []
        for item in history or []:
            role = str(item.get("role") or "user")
            if role not in ("user", "assistant", "system"):
                role = "user"
            content = str(item.get("content") or "")
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": str(user_text)})
        return messages

    def dfp_set_history_provider(self, provider: Callable[[str, int], List[Dict[str, str]]]) -> None:
        self._dfp_history_provider = provider

    def _dfp_history_provider_default(self, session_id: str, rounds: int) -> List[Dict[str, str]]:
        return []

    def dfp_cancel(self, request_id: str) -> bool:
        worker = self._dfp_active.get(request_id)
        if worker is None:
            return False
        worker.dfp_cancel()
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("已取消请求 %s" % request_id[:8])
        return True

    def dfp_cancel_all(self) -> int:
        count = 0
        for worker in list(self._dfp_active.values()):
            worker.dfp_cancel()
            count += 1
        return count

    def dfp_wait_all(self, timeout_ms: int = 5000) -> bool:
        self.dfp_cancel_all()
        try:
            return bool(self._dfp_pool.waitForDone(int(timeout_ms)))
        except Exception:
            return False

    # --- 信号转发（全部在主线程） ---
    def _dfp_on_started(self, request_id: str) -> None:
        self.dfpRequestStarted.emit(request_id)

    def _dfp_on_chunk(self, request_id: str, piece: str) -> None:
        self.dfpReplyChunk.emit(request_id, piece)

    def _dfp_on_progress(self, request_id: str, received: int) -> None:
        self.dfpReplyProgress.emit(request_id, received)

    def _dfp_on_done(self, request_id: str, text: str, meta: Dict[str, Any]) -> None:
        self.dfpReplyDone.emit(request_id, text, meta)

    def _dfp_on_error(self, request_id: str, message: str) -> None:
        self.dfpReplyError.emit(request_id, message)

    def _dfp_on_finished(self, request_id: str) -> None:
        self._dfp_active.pop(request_id, None)
        self.dfpRequestFinished.emit(request_id)

    def dfp_shutdown(self) -> None:
        self._dfp_shutdown = True
        self.dfp_cancel_all()
        try:
            self._dfp_pool.waitForDone(3000)
        except Exception:
            pass


# =============================================================================
#  二十一、后台任务（导出、备份、清理都用它；带进度回报）
# =============================================================================


class DFPTaskSignals(QObject):
    dfpTaskStarted = Signal(str)
    dfpTaskProgress = Signal(str, int, int, str)
    dfpTaskDone = Signal(str, object)
    dfpTaskFailed = Signal(str, str)


class DFPTaskWorker(QRunnable):
    """把任意可调用对象放到线程池里跑；函数签名 function(report)。"""

    def __init__(self, task_name: str, function: Callable[[Callable[[int, int, str], None]], Any], signals: DFPTaskSignals):
        super().__init__()
        self._dfp_task_name = str(task_name)
        self._dfp_function = function
        self._dfp_signals = signals
        try:
            self.setAutoDelete(True)
        except Exception:
            pass

    def dfp_task_name(self) -> str:
        return self._dfp_task_name

    def dfp_report(self, value: int = 0, maximum: int = 100, text: str = "") -> None:
        try:
            self._dfp_signals.dfpTaskProgress.emit(self._dfp_task_name, int(value), int(maximum), str(text))
        except Exception:
            pass

    def run(self) -> None:
        try:
            self._dfp_signals.dfpTaskStarted.emit(self._dfp_task_name)
            result = self._dfp_function(self.dfp_report)
            self._dfp_signals.dfpTaskDone.emit(self._dfp_task_name, result)
        except Exception as exc:
            self._dfp_signals.dfpTaskFailed.emit(self._dfp_task_name, "%s: %s" % (type(exc).__name__, exc))


# =============================================================================
#  二十二、聊天窗口（多会话 / 流式输出 / 停止生成 / 导出 / 进度条）
# =============================================================================


class DFPChatWindow(QDialog):
    """和肥鱼娘聊天的主窗口。没有 Key 也不会坏：自动退回离线台词。"""

    dfpStatusChanged = Signal(str, str)  # text, level
    dfpVoiceWanted = Signal(str)  # ask / done（交由控制器播形象声音）

    def __init__(
        self,
        settings: DFPSettingsStore,
        database: DFPDatabase,
        brain: DFPPetBrain,
        chat_manager: DFPChatManager,
        client: DFPDeepSeekClient,
        logger: Optional[DFPLogger] = None,
    ):
        super().__init__(None)
        self._dfp_settings = settings
        self._dfp_database = database
        self._dfp_brain = brain
        self._dfp_chat = chat_manager
        self._dfp_client = client
        self._dfp_logger = logger
        self._dfp_session_id = ""
        self._dfp_stream_text = ""
        self._dfp_stream_request = ""
        self._dfp_last_question = ""
        self._dfp_render_cache: List[Dict[str, Any]] = []
        self._dfp_busy = False
        self.setWindowTitle("%s — 聊天" % DFP_APP_TITLE)
        self.setWindowIcon(dfp_app_icon())
        self.setMinimumSize(720, 520)
        self._dfp_build_ui()
        self._dfp_connect_signals()
        self.dfp_apply_settings()
        self.dfp_load_sessions()
        self.dfp_ensure_session()
        self.dfp_render_messages()
        self.dfp_refresh_ai_state()
        self.dfp_reset_progress("准备就绪")

    # --- 界面 ---
    def _dfp_build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.lbl_title = QLabel("💬 和 %s 聊天" % dfp_truncate_text(self._dfp_settings.dfp_get("pet_nickname", DFP_DEFAULT_NICKNAME), 12))
        self.lbl_title.setFont(dfp_desktop_font(13, True))
        head.addWidget(self.lbl_title)
        self.lbl_ai = QLabel("正在检查 AI 状态…")
        self.lbl_ai.setStyleSheet("color: %s;" % DFP_UI_COLORS["sub_text"])
        head.addWidget(self.lbl_ai, 1)
        btn_test = QPushButton("🔌 测试连接")
        btn_test.setToolTip("查询 DeepSeek 账户余额（不消耗 token）")
        btn_test.clicked.connect(self.dfp_test_api)
        head.addWidget(btn_test)
        btn_export = QPushButton("📤 导出")
        btn_export.clicked.connect(self.dfp_export_dialogue)
        head.addWidget(btn_export)
        root.addLayout(head)

        splitter = QSplitter(dfp_enum_value(0, lambda: Qt.Orientation.Horizontal, lambda: Qt.Horizontal))
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(6)
        left_layout.addWidget(QLabel("会话列表"))
        self.lst_sessions = QListWidget()
        self.lst_sessions.setMinimumWidth(190)
        left_layout.addWidget(self.lst_sessions, 1)
        row = QGridLayout()
        row.setSpacing(4)
        buttons = (
            ("＋ 新会话", self.dfp_new_session),
            ("✏️ 重命名", self.dfp_rename_session),
            ("📌 置顶", self._dfp_toggle_pin),
            ("🧹 清空", self.dfp_clear_session),
            ("🗑️ 删除", self.dfp_delete_session),
            ("🔄 刷新", lambda: self.dfp_load_sessions()),
        )
        for index, (text, handler) in enumerate(buttons):
            button = QPushButton(text)
            button.clicked.connect(handler)
            row.addWidget(button, index // 2, index % 2)
        left_layout.addLayout(row)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(6)
        self.txt_messages = QTextBrowser()
        self.txt_messages.setOpenExternalLinks(True)
        right_layout.addWidget(self.txt_messages, 1)

        self.txt_input = QPlainTextEdit()
        self.txt_input.setPlaceholderText("说点什么吧…（Enter 发送，Shift+Enter 换行，Ctrl+Enter 也可以发送）")
        self.txt_input.setMaximumHeight(110)
        self.txt_input.installEventFilter(self)
        right_layout.addWidget(self.txt_input)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.btn_send = QPushButton("📨 发送")
        self.btn_send.clicked.connect(self.dfp_send_current)
        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.dfp_cancel_current)
        self.btn_retry = QPushButton("🔁 重发上一条")
        self.btn_retry.clicked.connect(self.dfp_retry_last)
        self.btn_offline = QPushButton("🐟 离线说一句")
        self.btn_offline.setToolTip("不联网，让肥鱼娘用自带台词说一句话")
        self.btn_offline.clicked.connect(self.dfp_offline_say)
        for button in (self.btn_send, self.btn_stop, self.btn_retry, self.btn_offline):
            actions.addWidget(button)
        actions.addStretch(1)
        self.lbl_chars = QLabel("0 字")
        self.lbl_chars.setStyleSheet("color: %s;" % DFP_UI_COLORS["sub_text"])
        actions.addWidget(self.lbl_chars)
        right_layout.addLayout(actions)

        self.bar_progress = QProgressBar()
        self.bar_progress.setRange(0, 100)
        self.bar_progress.setValue(0)
        self.bar_progress.setFormat("准备就绪")
        self.bar_progress.setTextVisible(True)
        right_layout.addWidget(self.bar_progress)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        right_layout.addWidget(self.lbl_status)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self.txt_input.textChanged.connect(self._dfp_on_input_changed)

    def _dfp_connect_signals(self) -> None:
        self.lst_sessions.currentRowChanged.connect(self._dfp_on_session_changed)
        self._dfp_chat.dfpRequestStarted.connect(self._dfp_on_request_started)
        self._dfp_chat.dfpReplyChunk.connect(self._dfp_on_reply_chunk)
        self._dfp_chat.dfpReplyProgress.connect(self._dfp_on_reply_progress)
        self._dfp_chat.dfpReplyDone.connect(self._dfp_on_reply_done)
        self._dfp_chat.dfpReplyError.connect(self._dfp_on_reply_error)
        self._dfp_chat.dfpRequestFinished.connect(self.dfp_on_request_finished)

    def _dfp_on_input_changed(self) -> None:
        text = self.txt_input.toPlainText()
        self.lbl_chars.setText("%d 字" % len(text))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt 命名
        if watched is self.txt_input and event.type() == dfp_enum_value(None, lambda: QEvent.Type.KeyPress, lambda: QEvent.KeyPress):
            key = event.key()
            enter = dfp_enum_value(0, lambda: Qt.Key.Key_Return, lambda: Qt.Key_Return)
            if key in (enter, 0x01000004) and not (event.modifiers() & dfp_enum_value(0, lambda: Qt.KeyboardModifier.ShiftModifier, lambda: Qt.ShiftModifier)):
                self.dfp_send_current()
                return True
        return super().eventFilter(watched, event)

    # --- 设置与几何 ---
    def dfp_apply_settings(self) -> None:
        nickname = str(self._dfp_settings.dfp_get("pet_nickname", DFP_DEFAULT_NICKNAME))
        self.lbl_title.setText("💬 和 %s 聊天" % dfp_truncate_text(nickname, 12))
        self._dfp_chat.dfp_apply_settings()
        self.dfp_refresh_ai_state()

    def dfp_restore_geometry(self) -> None:
        rect = dfp_safe_window_geometry(
            self._dfp_settings.dfp_get("chat_window_geometry", ""),
            QRect(0, 0, 880, 620),
            always_center=True,
        )
        dfp_place_window(self, rect)
        if self._dfp_settings.dfp_get("chat_window_maximized", False):
            self.showMaximized()

    def dfp_save_geometry(self) -> None:
        self._dfp_settings.dfp_set("chat_window_maximized", bool(self.isMaximized()))
        rect = dfp_window_geometry_for_save(self, QRect(0, 0, 880, 620))
        self._dfp_settings.dfp_set("chat_window_geometry", dfp_geometry_to_text(rect))

    def dfp_refresh_ai_state(self) -> None:
        ready, reason = self._dfp_chat.dfp_check_ready()
        self.lbl_ai.setText(("✅ " if ready else "⚠️ ") + reason)
        self.lbl_ai.setStyleSheet("color: %s;" % (DFP_UI_COLORS["success"] if ready else DFP_UI_COLORS["warn"]))
        self.btn_send.setEnabled(bool(self.txt_input.toPlainText().strip()))
        self.btn_stop.setEnabled(self._dfp_busy)
        self.btn_retry.setEnabled(bool(self._dfp_last_question) and not self._dfp_busy)

    # --- 进度条 ---
    def dfp_set_progress(self, value: int, maximum: int, text: str = "") -> None:
        self.bar_progress.setRange(0, max(1, int(maximum)))
        self.bar_progress.setValue(int(dfp_clamp(value, 0, max(1, int(maximum)))))
        if text:
            self.bar_progress.setFormat(text)

    def dfp_set_busy_progress(self, text: str = "正在思考…") -> None:
        self.bar_progress.setRange(0, 0)
        self.bar_progress.setFormat(text)

    def dfp_reset_progress(self, text: str = "准备就绪") -> None:
        self.bar_progress.setRange(0, 100)
        self.bar_progress.setValue(0)
        self.bar_progress.setFormat(text)

    def dfp_set_status(self, text: str, level: str = "info") -> None:
        colors = {
            "info": DFP_UI_COLORS["sub_text"],
            "success": DFP_UI_COLORS["success"],
            "warn": DFP_UI_COLORS["warn"],
            "error": DFP_UI_COLORS["error"],
        }
        self.lbl_status.setText(str(text))
        self.lbl_status.setStyleSheet("color: %s;" % colors.get(level, DFP_UI_COLORS["sub_text"]))
        if level in ("warn", "error"):
            try:
                self.dfpStatusChanged.emit(str(text), level)
            except Exception:
                pass

    # --- 会话 ---
    def dfp_load_sessions(self, select_id: str = "") -> None:
        target = select_id or self._dfp_session_id
        self.lst_sessions.blockSignals(True)
        self.lst_sessions.clear()
        sessions = self._dfp_database.dfp_list_sessions(200)
        row_to_select = -1
        for index, item in enumerate(sessions):
            title = str(item.get("title") or "未命名")
            pin = "📌 " if item.get("pinned") else ""
            label = "%s%s\n%d 条 · %s" % (pin, dfp_truncate_text(title, 18), int(item.get("message_count") or 0), dfp_fmt_ts(item.get("updated_at"), "%m-%d %H:%M"))
            entry = QListWidgetItem(label)
            entry.setData(dfp_enum_value(0, lambda: Qt.ItemDataRole.UserRole, lambda: Qt.UserRole), str(item.get("id")))
            self.lst_sessions.addItem(entry)
            if target and str(item.get("id")) == str(target):
                row_to_select = index
        self.lst_sessions.blockSignals(False)
        if row_to_select >= 0:
            self.lst_sessions.setCurrentRow(row_to_select)
        elif self.lst_sessions.count() > 0:
            self.lst_sessions.setCurrentRow(0)

    def dfp_current_session_id(self) -> str:
        item = self.lst_sessions.currentItem()
        if item is None:
            return ""
        return str(item.data(dfp_enum_value(0, lambda: Qt.ItemDataRole.UserRole, lambda: Qt.UserRole)) or "")

    def dfp_ensure_session(self) -> str:
        session_id = self.dfp_current_session_id() or self._dfp_session_id
        if session_id:
            self._dfp_session_id = session_id
            return session_id
        return self.dfp_new_session(silent=True)

    def dfp_new_session(self, silent: bool = False) -> str:
        session_id = self._dfp_database.dfp_create_session(dfp_fmt_ts(fmt="%m-%d %H:%M") + " 的对话")
        greeting = self._dfp_brain.dfp_pick_line("greet")
        self._dfp_database.dfp_seed_session(session_id, greeting)
        self._dfp_session_id = session_id
        self.dfp_load_sessions(session_id)
        self.dfp_render_messages()
        if not silent:
            self.dfp_set_status("已新建会话：%s" % greeting, "info")
            self.dfp_reset_progress("新会话已创建")
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("新建会话 %s" % session_id[:8])
        return session_id

    def dfp_rename_session(self) -> None:
        session_id = self.dfp_ensure_session()
        session = self._dfp_database.dfp_get_session(session_id) or {}
        text, ok = QInputDialog.getText(self, "重命名会话", "新标题：", QLineEdit.Normal, str(session.get("title") or ""))
        if not ok or not str(text).strip():
            return
        self._dfp_database.dfp_rename_session(session_id, str(text).strip())
        self.dfp_load_sessions(session_id)
        self.dfp_set_status("会话已重命名", "success")

    def _dfp_toggle_pin(self) -> None:
        session_id = self.dfp_ensure_session()
        session = self._dfp_database.dfp_get_session(session_id) or {}
        pinned = not bool(session.get("pinned"))
        self._dfp_database.dfp_pin_session(session_id, pinned)
        self.dfp_load_sessions(session_id)
        self.dfp_set_status("已置顶" if pinned else "已取消置顶", "success")

    def dfp_delete_session(self) -> None:
        session_id = self.dfp_current_session_id()
        if not session_id:
            return
        answer = dfp_confirm(self, "删除会话", "确定要删除这个会话和它的全部消息吗？")
        if answer != dfp_enum_value(0, lambda: QMessageBox.StandardButton.Yes, lambda: QMessageBox.Yes):
            return
        self._dfp_database.dfp_delete_session(session_id)
        self._dfp_session_id = ""
        self.dfp_load_sessions()
        self.dfp_ensure_session()
        self.dfp_render_messages()
        self.dfp_set_status("会话已删除", "success")

    def dfp_clear_session(self) -> None:
        session_id = self.dfp_ensure_session()
        answer = dfp_confirm(self, "清空消息", "只清空这个会话的消息（会话本身保留）？")
        if answer != dfp_enum_value(0, lambda: QMessageBox.StandardButton.Yes, lambda: QMessageBox.Yes):
            return
        count = self._dfp_database.dfp_clear_session_messages(session_id)
        self.dfp_render_messages()
        self.dfp_load_sessions(session_id)
        self.dfp_set_status("已清空 %d 条消息" % count, "success")

    def _dfp_on_session_changed(self, current: int, previous: int = 0) -> None:
        if current < 0:
            return
        self._dfp_session_id = self.dfp_current_session_id()
        self.dfp_render_messages()
        self.dfp_reset_progress("已切换会话")

    # --- 渲染 ---
    def _dfp_format_html(self, text: str) -> str:
        escaped = html.escape(str(text or ""))
        escaped = escaped.replace("  ", "&nbsp;&nbsp;")
        return escaped.replace("\n", "<br>")

    def dfp_render_messages(self) -> None:
        session_id = self._dfp_session_id
        messages: List[Dict[str, Any]] = []
        if session_id:
            messages = self._dfp_database.dfp_list_messages(session_id, limit=400, ascending=True)
        self._dfp_render_cache = [dict(item) for item in messages]
        if self._dfp_stream_text:
            self._dfp_render_cache.append(
                {"role": "assistant", "content": self._dfp_stream_text, "streaming": True, "created_at": dfp_now_ts()}
            )
        nickname = str(self._dfp_settings.dfp_get("pet_nickname", DFP_DEFAULT_NICKNAME))
        parts = [
            "<style>",
            "body{font-family:'Microsoft YaHei UI','Microsoft YaHei',sans-serif;font-size:13px;color:%s;}" % DFP_UI_COLORS["text"],
            ".card{margin:6px 0;padding:8px 10px;border-radius:10px;background:%s;border:1px solid %s;}" % (DFP_UI_COLORS["card"], DFP_UI_COLORS["border"]),
            ".me{background:%s;}" % DFP_UI_COLORS["accent_soft"],
            ".who{color:%s;font-weight:bold;}" % DFP_UI_COLORS["accent"],
            ".when{color:%s;font-size:11px;}" % DFP_UI_COLORS["sub_text"],
            "</style>",
        ]
        if not self._dfp_render_cache:
            parts.append("<p style='color:%s'>还没有消息，说点什么吧～</p>" % DFP_UI_COLORS["sub_text"])
        for item in self._dfp_render_cache:
            role = str(item.get("role") or "user")
            who = "🧑 主人" if role == "user" else ("🐟 %s" % nickname if role == "assistant" else "⚙️ 系统")
            css = "me" if role == "user" else ""
            when = dfp_fmt_ts(item.get("created_at"), "%m-%d %H:%M")
            model = str(item.get("model") or "")
            tail = ("　<span class='when'>%s</span>" % model) if model and model != "offline" else ""
            content = self._dfp_format_html(item.get("content") or "")
            if item.get("streaming"):
                content += "<span style='color:%s'>▌</span>" % DFP_UI_COLORS["accent"]
            parts.append(
                "<div class='card %s'><span class='who'>%s</span>　<span class='when'>%s</span>%s<br>%s</div>"
                % (css, who, when, tail, content)
            )
        self.txt_messages.setHtml("".join(parts))
        scrollbar = self.txt_messages.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def dfp_append_local_message(self, role: str, text: str, model: str = "offline") -> int:
        session_id = self.dfp_ensure_session()
        return self._dfp_database.dfp_add_message(session_id, role, text, 0, model, True)

    def dfp_offline_say(self) -> None:
        text = self._dfp_brain.dfp_pick_line("idle")
        self.dfp_append_local_message("assistant", text)
        self.dfp_render_messages()
        self.dfp_load_sessions(self._dfp_session_id)
        self.dfp_set_status("离线台词：%s" % text, "info")
        self.dfp_reset_progress("离线台词已生成")

    def dfp_retry_last(self) -> None:
        if self._dfp_busy:
            self.dfp_set_status("上一次请求还在进行中", "warn")
            return
        if not self._dfp_last_question:
            self.dfp_set_status("还没有可以重发的问题", "warn")
            return
        self.txt_input.setPlainText(self._dfp_last_question)
        self.dfp_send_current()

    def dfp_test_api(self) -> None:
        if not self._dfp_client.dfp_has_key():
            self.dfp_set_status("未配置 API Key，无法测试", "warn")
            self.dfp_reset_progress("未配置 API Key")
            return
        self.dfp_set_busy_progress("正在查询账户余额…")
        self.dfp_set_status("正在连接 %s …" % self._dfp_client.dfp_api_base(), "info")
        try:
            data = self._dfp_client.dfp_balance()
        except Exception as exc:
            self.dfp_set_status("测试失败：%s" % exc, "error")
            self.dfp_reset_progress("测试失败")
            return
        infos = data.get("balance_infos") or []
        text = "可用：%s" % ("是" if data.get("is_available") else "否")
        if infos:
            first = infos[0] or {}
            text += "｜总余额 %s %s" % (first.get("total_balance", "?"), first.get("currency", ""))
        self.dfp_set_status("连接正常：%s" % text, "success")
        self.dfp_reset_progress("连接测试通过")
        self.dfp_refresh_ai_state()

    # --- 发送 ---
    def dfp_send_current(self) -> None:
        if self._dfp_busy:
            self.dfp_set_status("上一条还在生成，请先停止或稍候", "warn")
            return
        text = self.txt_input.toPlainText().strip()
        if not text:
            self.dfp_set_status("先输入内容再发送", "warn")
            return
        session_id = self.dfp_ensure_session()
        self._dfp_last_question = text
        self._dfp_database.dfp_add_message(session_id, "user", text, 0, "user", True)
        self.txt_input.clear()
        self._dfp_maybe_auto_title(session_id, text)

        ready, reason = self._dfp_chat.dfp_check_ready()
        extra_system = self._dfp_brain.dfp_ai_context_line() if self._dfp_settings.dfp_get("ai_include_pet_state", True) else ""
        if not ready:
            self._dfp_database.dfp_add_message(session_id, "assistant", reason, 0, "offline", True)
            self.dfp_set_status("离线模式：%s" % reason, "warn")
            self.dfp_render_messages()
            self.dfp_load_sessions(session_id)
            self.dfp_reset_progress("已离线回复")
            return

        try:
            messages = self._dfp_chat.dfp_build_messages(session_id, text, extra_system)
        except Exception as exc:
            self.dfp_set_status("组装上下文失败：%s" % exc, "error")
            return
        self._dfp_stream_text = ""
        self._dfp_busy = True
        self.btn_send.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.dfp_set_busy_progress("正在思考…")
        self.dfp_set_status("已发送 %d 字，正在等待回复…" % len(text), "info")
        self.dfp_render_messages()
        try:
            self.dfpVoiceWanted.emit("ask")
        except Exception:
            pass
        try:
            self._dfp_stream_request = self._dfp_chat.dfp_send(messages)
        except Exception as exc:
            self._dfp_busy = False
            self.btn_send.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self._dfp_database.dfp_add_message(session_id, "assistant", "发送失败：%s" % exc, 0, "offline", False)
            self.dfp_set_status("发送失败：%s" % exc, "error")
            self.dfp_reset_progress("发送失败")
            self.dfp_render_messages()

    def _dfp_maybe_auto_title(self, session_id: str, first_text: str) -> None:
        if not self._dfp_settings.dfp_get("ai_auto_title", True):
            return
        session = self._dfp_database.dfp_get_session(session_id) or {}
        title = str(session.get("title") or "")
        if not title.endswith("的对话"):
            return
        self._dfp_database.dfp_rename_session(session_id, dfp_truncate_text(first_text, 18))

    def dfp_cancel_current(self) -> None:
        if not self._dfp_busy or not self._dfp_stream_request:
            return
        self._dfp_chat.dfp_cancel(self._dfp_stream_request)
        self.dfp_set_status("已请求停止…", "warn")

    # --- 请求回调 ---
    def _dfp_on_request_started(self, request_id: str) -> None:
        if request_id != self._dfp_stream_request:
            return
        self.dfp_set_status("连接成功，正在接收回复…", "info")

    def _dfp_on_reply_chunk(self, request_id: str, piece: str) -> None:
        if request_id != self._dfp_stream_request:
            return
        self._dfp_stream_text += piece
        self.dfp_set_busy_progress("正在接收… 已收到 %d 字" % len(self._dfp_stream_text))
        self.dfp_render_messages()

    def _dfp_on_reply_progress(self, request_id: str, received: int) -> None:
        if request_id != self._dfp_stream_request:
            return
        self.dfp_set_busy_progress("正在接收… 已收到 %d 字" % int(received))

    def _dfp_on_reply_done(self, request_id: str, text: str, meta: Dict[str, Any]) -> None:
        if request_id != self._dfp_stream_request:
            return
        session_id = self._dfp_session_id
        final = str(text or self._dfp_stream_text or "").strip() or "（这次没有收到内容）"
        usage = meta.get("usage") or {}
        tokens = dfp_safe_int(usage.get("total_tokens"), 0)
        model = str(meta.get("model") or "")
        self._dfp_database.dfp_add_message(session_id, "assistant", final, tokens, model, True)
        self._dfp_stream_text = ""
        self._dfp_busy = False
        self.btn_stop.setEnabled(False)
        self.btn_send.setEnabled(bool(self.txt_input.toPlainText().strip()))
        self.dfp_render_messages()
        self.dfp_load_sessions(session_id)
        self.dfp_reset_progress("回复完成，共 %d token" % tokens)
        self.dfp_set_status("收到回复（%s，%d token）" % (model or "未知模型", tokens), "success")
        try:
            self._dfp_brain.dfp_register_chat(tokens)
            self._dfp_database.dfp_add_event("chat", "对话完成 %d token" % tokens)
        except Exception:
            pass
        speak = bool(self._dfp_settings.dfp_get("ai_speak_reply", True))
        if speak:
            limit = dfp_safe_int(self._dfp_settings.dfp_get("ai_speak_max_chars", 60), 60)
            try:
                self._dfp_brain.dfp_say("idle", dfp_truncate_text(final.replace("\n", " "), limit))
            except Exception:
                pass
        try:
            self.dfpVoiceWanted.emit("done")
        except Exception:
            pass

    def _dfp_on_reply_error(self, request_id: str, message: str) -> None:
        if request_id != self._dfp_stream_request:
            return
        session_id = self._dfp_session_id
        if str(message) == "已取消":
            partial = self._dfp_stream_text.strip()
            if partial:
                self._dfp_database.dfp_add_message(session_id, "assistant", partial + "\n\n（已手动停止）", 0, "cancelled", False)
            self.dfp_set_status("已停止生成", "warn")
            self.dfp_reset_progress("已停止")
        else:
            self._dfp_database.dfp_add_message(session_id, "assistant", "【连接出错】%s" % message, 0, "error", False)
            self.dfp_set_status(str(message), "error")
            self.dfp_reset_progress("请求失败")
            try:
                self._dfp_database.dfp_add_event("ai_error", str(message))
            except Exception:
                pass
            if self._dfp_settings.dfp_get("offline_lines_enabled", True):
                fallback = self._dfp_brain.dfp_pick_line("error")
                self._dfp_database.dfp_add_message(session_id, "assistant", fallback, 0, "offline", True)
        self._dfp_stream_text = ""
        self._dfp_busy = False
        self.btn_stop.setEnabled(False)
        self.btn_send.setEnabled(bool(self.txt_input.toPlainText().strip()))
        self.dfp_render_messages()
        self.dfp_load_sessions(session_id)

    def dfp_on_request_finished(self, request_id: str) -> None:
        if request_id == self._dfp_stream_request:
            self._dfp_stream_request = ""
        self._dfp_busy = bool(self._dfp_chat.dfp_is_busy())
        self.btn_stop.setEnabled(self._dfp_busy)
        self.btn_retry.setEnabled(bool(self._dfp_last_question) and not self._dfp_busy)

    # --- 导出 ---
    def dfp_export_dialogue(self) -> None:
        sessions = self._dfp_database.dfp_list_sessions(500)
        if not sessions:
            self.dfp_set_status("没有可导出的会话", "warn")
            return
        fmt = str(self._dfp_settings.dfp_get("chat_export_format", "markdown") or "markdown")
        suffix = dict((key, ext) for key, _label, ext in DFP_EXPORT_FORMATS).get(fmt, "md")
        default_name = "肥鱼娘全部对话_%s.%s" % (dfp_fmt_ts(fmt="%Y%m%d_%H%M%S"), suffix)
        target, _selected = QFileDialog.getSaveFileName(
            self,
            "导出全部对话",
            os.path.join(dfp_export_dir(), default_name),
            ";;".join("%s (*.%s)" % (label, ext) for _key, label, ext in DFP_EXPORT_FORMATS),
        )
        if not target:
            return
        try:
            dfp_ensure_dir(os.path.dirname(target) or ".")
            total = len(sessions)
            self.dfp_set_progress(0, total, "导出中…")
            lines: List[str] = []
            payload: List[Dict[str, Any]] = []
            for index, session in enumerate(sessions, start=1):
                session_id = str(session.get("id"))
                messages = self._dfp_database.dfp_list_messages(session_id, limit=2000, ascending=True)
                payload.append(
                    {
                        "title": session.get("title"),
                        "created_at": dfp_fmt_ts(session.get("created_at")),
                        "updated_at": dfp_fmt_ts(session.get("updated_at")),
                        "message_count": session.get("message_count"),
                        "tokens": session.get("tokens"),
                        "messages": [
                            {"role": item.get("role"), "content": item.get("content"), "time": dfp_fmt_ts(item.get("created_at"))}
                            for item in messages
                        ],
                    }
                )
                lines.append("## %s" % session.get("title"))
                lines.append("_创建：%s　更新：%s　消息：%s 条_" % (dfp_fmt_ts(session.get("created_at")), dfp_fmt_ts(session.get("updated_at")), session.get("message_count")))
                for item in messages:
                    who = "主人" if str(item.get("role")) == "user" else str(self._dfp_settings.dfp_get("pet_nickname", DFP_DEFAULT_NICKNAME))
                    lines.append("**%s**（%s）：%s" % (who, dfp_fmt_ts(item.get("created_at"), "%m-%d %H:%M"), str(item.get("content") or "").replace("\n", " ")))
                lines.append("")
                self.dfp_set_progress(index, total, "导出中… %d/%d" % (index, total))
            if fmt == "json":
                content = json.dumps(payload, ensure_ascii=False, indent=2)
            elif fmt == "html":
                body = []
                for session in payload:
                    body.append("<h2>%s</h2>" % html.escape(str(session["title"])))
                    for item in session["messages"]:
                        body.append("<p><b>%s</b>（%s）：%s</p>" % (html.escape(str(item["role"])), item["time"], html.escape(str(item["content"]))))
                content = "<!doctype html><meta charset='utf-8'><title>肥鱼娘对话导出</title>%s" % "".join(body)
            else:
                content = "\n".join(lines)
            if not dfp_atomic_write_text(target, content):
                raise IOError("写入失败：%s" % target)
            size = os.path.getsize(target) if os.path.isfile(target) else 0
            self.dfp_reset_progress("导出完成：%s" % dfp_human_bytes(size))
            self.dfp_set_status("已导出 %d 个会话到 %s" % (len(sessions), target), "success")
            try:
                self._dfp_database.dfp_add_event("export", os.path.basename(target))
            except Exception:
                pass
        except Exception as exc:
            self.dfp_reset_progress("导出失败")
            self.dfp_set_status("导出失败：%s" % exc, "error")

    # --- 显示 ---
    def dfp_show_and_raise(self) -> None:
        if not self.isVisible():
            self.dfp_restore_geometry()
        self.show()
        self.raise_()
        self.activateWindow()
        self.dfp_refresh_ai_state()
        self.txt_input.setFocus()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.dfp_save_geometry()
        super().closeEvent(event)


DFP_HELP_HTML = """
<h3 style="color:#4D6BFE">DeepSeek 肥鱼娘桌宠　v%(version)s</h3>
<p>一只住在桌面上的 Q 版小鲸鱼拟人少女（形象为原创绘制，非官方立绘）。她会呼吸、眨眼、摆尾、喷水，
也会饿、会困、会想你。默认用「鲸宝形象」里那套素材（12 张立绘 + 9 个表情 + 106 个动作）；
素材缺失时会自动退回代码实时绘制的矢量形象，放大到任何尺寸都清晰。</p>
<b>鼠标操作</b>
<ul>
<li><b>左键拖动</b>：把桌宠拎到任意位置（松手后自动记住位置）</li>
<li><b>单击</b>：摸摸头（心情 +、亲密度 +、会说一句话）</li>
<li><b>双击</b>：陪它玩（经验 +、会跳舞）</li>
<li><b>滚轮</b>：直接缩放大小（0.35× ~ 3.0×）</li>
<li><b>右键</b>：打开功能菜单（投喂 / 喝水 / 玩耍 / 睡觉 / 聊天 / 设置 …）</li>
</ul>
<b>快捷键</b>
<ul>
<li><code>Ctrl+O</code> 打开聊天窗口　<code>Ctrl+,</code> 打开设置</li>
<li><code>Ctrl+H</code> 显示 / 隐藏桌宠　<code>Ctrl+L</code> 锁定 / 解锁位置</li>
<li><code>Ctrl+Shift+S</code> 立即备份数据　<code>Ctrl+Alt+C</code> 回到屏幕中央</li>
<li><code>Ctrl+Q</code> 退出程序（托盘菜单里也有全部功能）</li>
</ul>
<b>状态系统</b>
<ul>
<li>心情 / 饱食 / 精力 / 亲密度随时间真实衰减（关掉程序期间同样计算），睡觉会恢复精力</li>
<li>达到一定经验会升级，等级越高称号越好听；聊天、摸头、投喂、玩耍都会给经验</li>
<li>所有数值都能在「状态面板」里看到进度条，也可以在设置里调整衰减速度</li>
</ul>
<b>AI 聊天</b>
<ul>
<li>在程序目录的 <code>.env</code> 中填写 <code>DEEPSEEK_API_KEY=sk-xxxx</code>（多个用逗号分隔会自动轮换）</li>
<li>聊天窗口支持多会话、流式输出、停止生成、导出 Markdown / 文本 / JSON / 网页</li>
<li>没有 Key 也不会坏掉：会退回到内置离线台词库继续说话</li>
<li>人设（system prompt）可以在设置里随时改，默认已经把「肥鱼娘」的性格写好了</li>
</ul>
<b>形象素材（鲸宝形象）</b>
<ul>
<li>桌宠会显示的图都住在程序目录的 <code>鲸宝形象\</code> 下：<code>立绘\</code> 12 张换装、
    <code>表情\</code> 9 个 GIF（眨眼 / 微笑 / 挥手 / 打哈欠 / 困倦 / 醒来 / 被拎起 / 爱心 / 摇晃）、
    <code>动作\</code> 106 个动作片、<code>启动图.png</code></li>
<li><b>台词换表情</b>：说到哪一类话就摆哪副表情（撒娇 → 爱心、吃饱 → 微笑、累了 → 打哈欠、
    夜里 → 困倦、刚醒 → 醒来、被藏起来 → 被拎起、难过 → 摇晃、发呆 → 眨眼）</li>
<li><b>台词换立绘</b>：只对「场合 / 时段」类台词换装（临睡 → 睡衣、忙活 → 实验服、玩 → 运动装、
    升级 → 魔法装、被夸 → 正式装、季节类按当月换夏装 / 冬装 / 新中式 / 运势），
    换完保持 1 分钟，然后自动回你在设置里选的那张</li>
<li>两个开关都在「设置 → 形象素材」里；想固定一张立绘，就把「立绘跟着台词换装」关掉</li>
<li>换形象：设置里的下拉框，或菜单「换形象」；素材包（如素材4）里的立绘也会一起列出来</li>
<li>旧形象与设计稿母版分别归档在 <code>鲸宝形象\_退役\</code> 与 <code>鲸宝形象\_源稿\</code>，
    程序扫描时会跳过它们（下划线开头的目录一律不当素材）</li>
</ul>
<b>数据与安全</b>
<ul>
<li>状态、对话、事件都存进 <code>pet_data/pet_data.db</code>（SQLite + WAL），按设置自动备份并可清理</li>
<li>API Key 与数据库口令只从 <code>.env</code> 读取，不写进代码；对话内容默认加密后落库</li>
<li>任何改动都会即时写入设置文件（500ms 防抖 + 原子写入），下次启动自动恢复，不会丢功能也不会丢设置</li>
</ul>
""" % {"version": __version__}


# =============================================================================
#  二十三、设置窗口（★ 所有控件都即时写回 DFPSettingsStore，没有「确定」按钮）
# =============================================================================


class DFPSettingsDialog(QDialog):
    """设置：改哪个控件就立刻生效并落盘，下次启动照旧。"""

    dfpActionRequested = Signal(str)  # center_pet / corner_pet / open_data_dir ...
    dfpTaskRequested = Signal(str)    # backup_now / cleanup_backup / vacuum / export_all ...

    def __init__(
        self,
        settings: DFPSettingsStore,
        brain: DFPPetBrain,
        database: DFPDatabase,
        client: DFPDeepSeekClient,
        backup_manager: DFPBackupManager,
        logger: Optional[DFPLogger] = None,
        library: Optional[DFPAssetLibrary] = None,
    ):
        super().__init__(None)
        self._dfp_settings = settings
        self._dfp_brain = brain
        self._dfp_database = database
        self._dfp_client = client
        self._dfp_backups = backup_manager
        self._dfp_logger = logger
        self._dfp_library = library
        self._dfp_widgets: Dict[str, QWidget] = {}
        self._dfp_loading = False
        self._dfp_task_signals = DFPTaskSignals()
        self._dfp_task_pool = QThreadPool(self)
        self._dfp_task_pool.setMaxThreadCount(1)
        self._dfp_active_task = ""
        self._dfp_task_signals.dfpTaskStarted.connect(self._dfp_on_task_started)
        self._dfp_task_signals.dfpTaskProgress.connect(self._dfp_on_task_progress)
        self._dfp_task_signals.dfpTaskDone.connect(self._dfp_on_task_done)
        self._dfp_task_signals.dfpTaskFailed.connect(self._dfp_on_task_failed)
        self.setWindowTitle("设置 — %s v%s" % (DFP_APP_TITLE, __version__))
        self.setWindowIcon(dfp_app_icon())
        self.setMinimumSize(680, 560)
        self._dfp_build_ui()
        self._dfp_settings.dfpSaved.connect(self._dfp_on_settings_saved)
        self.dfp_reload_values()
        dfp_ensure_dir(dfp_export_dir())
        dfp_ensure_dir(dfp_backup_dir())
        dfp_ensure_dir(dfp_log_dir())

    # --- 控件工厂 ---
    def _dfp_group(self, title: str) -> Tuple[QGroupBox, QFormLayout]:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setContentsMargins(10, 8, 10, 8)
        form.setSpacing(6)
        form.setLabelAlignment(dfp_enum_value(0, lambda: Qt.AlignmentFlag.AlignRight, lambda: Qt.AlignRight))
        return box, form

    def _dfp_spin(
        self,
        form: QFormLayout,
        label: str,
        key: str,
        minimum: float = 0.0,
        maximum: float = 100.0,
        step: float = 1.0,
        decimals: int = 0,
        suffix: str = "",
        tooltip: str = "",
    ) -> QWidget:
        if decimals > 0:
            box = QDoubleSpinBox()
            box.setDecimals(int(decimals))
            box.setSingleStep(float(step))
        else:
            box = QSpinBox()
            box.setSingleStep(int(max(1, step)))
        box.setRange(minimum, maximum)
        if suffix:
            box.setSuffix(suffix)
        box.setFixedWidth(150)
        if tooltip:
            box.setToolTip(tooltip)
        box.valueChanged.connect(lambda value, name=key: self._dfp_apply(name, value))
        self._dfp_widgets[key] = box
        form.addRow(label, box)
        return box

    def _dfp_check(self, form: QFormLayout, label: str, key: str, tooltip: str = "") -> QCheckBox:
        box = QCheckBox(label)
        if tooltip:
            box.setToolTip(tooltip)
        box.toggled.connect(lambda value, name=key: self._dfp_apply(name, value))
        self._dfp_widgets[key] = box
        form.addRow("", box)
        return box

    def _dfp_combo(self, form: QFormLayout, label: str, key: str, choices: Sequence[Tuple[str, str]], tooltip: str = "") -> QComboBox:
        box = QComboBox()
        for value, text in choices:
            box.addItem(text, value)
        box.setFixedWidth(180)
        if tooltip:
            box.setToolTip(tooltip)
        box.currentIndexChanged.connect(lambda index, widget=box, name=key: self._dfp_apply(name, widget.itemData(index)))
        self._dfp_widgets[key] = box
        form.addRow(label, box)
        return box

    def _dfp_line(self, form: QFormLayout, label: str, key: str, tooltip: str = "", placeholder: str = "") -> QLineEdit:
        box = QLineEdit()
        box.setPlaceholderText(placeholder)
        if tooltip:
            box.setToolTip(tooltip)
        box.textChanged.connect(lambda value, name=key: self._dfp_apply(name, value))
        self._dfp_widgets[key] = box
        form.addRow(label, box)
        return box

    def _dfp_scroll(self, widget: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(dfp_enum_value(0, lambda: QFrame.Shape.NoFrame, lambda: QFrame.NoFrame))
        area.setWidget(widget)
        return area

    # --- 写回设置 ---
    def _dfp_apply(self, key: str, value: Any) -> None:
        if self._dfp_loading:
            return
        self._dfp_settings.dfp_set(key, value)
        self._dfp_flash("%s = %s　· 已保存" % (key, value))

    def _dfp_flash(self, text: str) -> None:
        if hasattr(self, "lbl_flash"):
            self.lbl_flash.setText(dfp_truncate_text(str(text), 90))

    def _dfp_on_settings_saved(self, path: str) -> None:
        self._dfp_flash("设置已写入磁盘 · 第 %d 次 · %s" % (self._dfp_settings.dfp_save_count(), os.path.basename(path)))

    # --- 界面 ---
    def _dfp_build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._dfp_scroll(self._dfp_build_appearance_tab()), "🎨 外观")
        self.tabs.addTab(self._dfp_scroll(self._dfp_build_behavior_tab()), "🎮 行为")
        self.tabs.addTab(self._dfp_scroll(self._dfp_build_ai_tab()), "🤖 AI 对话")
        self.tabs.addTab(self._dfp_scroll(self._dfp_build_position_tab()), "📍 位置")
        self.tabs.addTab(self._dfp_scroll(self._dfp_build_data_tab()), "🗄️ 数据")
        self.tabs.addTab(self._dfp_scroll(self._dfp_build_trainer_tab()), "🧰 修改器")
        self.tabs.addTab(self._dfp_scroll(self._dfp_build_mahjong_tab()), "🀄 算番")
        self.tabs.addTab(self._dfp_scroll(self._dfp_build_about_tab()), "ℹ️ 关于")
        root.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        self.lbl_flash = QLabel("任何改动都会即时保存，下次启动自动恢复")
        self.lbl_flash.setStyleSheet("color: %s;" % DFP_UI_COLORS["sub_text"])
        bottom.addWidget(self.lbl_flash, 1)
        self.bar_task = QProgressBar()
        self.bar_task.setRange(0, 100)
        self.bar_task.setValue(0)
        self.bar_task.setFixedWidth(200)
        self.bar_task.setFormat("空闲")
        bottom.addWidget(self.bar_task)
        btn_reset = QPushButton("恢复默认设置")
        btn_reset.clicked.connect(self.dfp_reset_defaults)
        bottom.addWidget(btn_reset)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    def _dfp_build_appearance_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.setContentsMargins(2, 2, 2, 2)

        box, form = self._dfp_group("体型与显示")
        self._dfp_spin(form, "大小倍数", "pet_scale", 0.35, 3.0, 0.05, 2, "×", "滚轮也能直接缩放；记住最后一次的大小")
        self._dfp_spin(form, "不透明度", "pet_opacity", 0.15, 1.0, 0.05, 2, "", "越低越透明")
        self._dfp_combo(form, "配色方案", "pet_palette", DFPPetRenderer().dfp_palette_choices(), "换一身颜色")
        self._dfp_spin(form, "动画帧率", "pet_anim_fps", 5, 60, 5, 0, " FPS", "越高越流畅，也越费一点点电；睡觉时会自动降帧")
        self._dfp_check(form, "总是显示在最前面", "pet_always_on_top")
        self._dfp_check(form, "水平翻转（面朝另一边）", "pet_flip")
        self._dfp_check(form, "显示地面阴影", "pet_show_shadow")
        self._dfp_check(form, "偶尔喷水", "pet_show_spout")
        self._dfp_check(form, "头顶显示昵称牌", "pet_show_nametag")
        layout.addWidget(box)

        box2, form2 = self._dfp_group("气泡")
        self._dfp_check(form2, "允许说话气泡", "bubble_enabled")
        self._dfp_spin(form2, "气泡停留", "bubble_duration_ms", 1000, 60000, 500, 0, " 毫秒", "气泡显示多久后开始淡出")
        self._dfp_spin(form2, "气泡最大宽度", "bubble_max_width", 140, 900, 10, 0, " 像素")
        self._dfp_spin(form2, "气泡字号", "bubble_font_size", 7, 26, 1, 0, " 磅")
        layout.addWidget(box2)

        box3, form3 = self._dfp_group("昵称")
        self._dfp_line(form3, "昵称", "pet_nickname", "桌宠自称之外对你的称呼会变，窗口标题也会跟着变", "肥鱼娘")
        layout.addWidget(box3)

        box4 = QGroupBox("实时预览")
        box4_layout = QVBoxLayout(box4)
        box4_layout.setContentsMargins(10, 8, 10, 8)
        self.lbl_preview = QLabel()
        self.lbl_preview.setAlignment(dfp_enum_value(0, lambda: Qt.AlignmentFlag.AlignCenter, lambda: Qt.AlignCenter))
        self.lbl_preview.setMinimumHeight(230)
        box4_layout.addWidget(self.lbl_preview)
        btn_preview = QPushButton("刷新预览")
        btn_preview.clicked.connect(self.dfp_update_preview)
        box4_layout.addWidget(btn_preview)
        layout.addWidget(box4)

        box5 = QGroupBox("形象素材（大肥鱼素材表）")
        form5 = QFormLayout(box5)
        form5.setContentsMargins(10, 8, 10, 8)
        form5.setSpacing(6)
        self._dfp_combo(
            form5,
            "渲染方式",
            "pet_render_mode",
            (("assets", "素材表（推荐，当前形象）"), ("auto", "素材优先，缺失时用代码绘制"), ("vector", "只用代码绘制")),
            "默认使用素材表里的形象、表情动画与动作片",
        )
        self._dfp_line(form5, "素材目录", "asset_root", "留空 = 程序目录下的「大肥鱼素材表」", "（默认）大肥鱼素材表")
        self._dfp_combo(
            form5,
            "基础形象（正面 / 背面 / 侧面）",
            "asset_character",
            self._dfp_character_choices(),
            "没台词时画的那张。新设计给的是 3 张多视角待机图（`鲸宝形象\类别\` 里的 base_idle / base_back / base_left）；切换后立即生效",
        )
        # ★ v1.1.35：新形象（`类别\` 那 78 张）——「说到哪一类话就换哪一张图」
        self._dfp_check(
            form5,
            "台词换成对应的形象（78 张新形象）",
            "look_expression_follow",
            "说到哪一类话就换成哪一张新形象图（连表情 / 动作 / 随身道具一起，共 78 张）："
            "撒娇 → 9 张子情绪轮着来（求摸头 / 求抱抱 / 闹小脾气…），想你 → 比心，睡了 → 蜷成一团，"
            "忙 → 安静陪坐，升级 → 撒花，出错 → 冒汗……（映射表 `DFP_SAY_FACE_MAP`，65 个类别全覆盖）。"
            "换完保持 4.5 秒，然后自动回你上面选的那张基础形象。"
            "★ 睡觉 / 忙 / 等 / 休息 / 庆祝 / 出错这六个状态也是同一批图（见下面两个开关）。",
        )
        self._dfp_check(form5, "清单缺失时自动扫描重建", "asset_auto_scan")
        self._dfp_spin(form5, "动作片缩放", "asset_anim_zoom", 0.0, 3.0, 0.05, 2, " ×", "0 = 自动（16:9 动作自动放大到与立绘同尺寸）")
        # ★ v1.1.36：状态形象（睡觉 / 忙 / 等 / 休息 / 庆祝 / 出错）—— 用的就是上面那批新形象图
        self._dfp_check(
            form5,
            "忙 / 等 / 休息 / 庆祝 / 出错时换成对应的新形象",
            "whale_form_enabled",
            "干活时「安静陪坐」、等回复时「歪头想问」、任务成功时「升级撒花」、失败时「出错冒汗」—— 都是新形象集（鲸宝形象\\类别）里的图。关掉就始终用你上面选的那张基础形象。",
        )
        self._dfp_check(
            form5,
            "睡觉时换成「蜷成一团」那张新形象",
            "whale_form_sleep",
            "睡着时一直挂着「蜷成一团」（新形象集里那张），醒了自动回基础形象；只在上面的总开关开着时生效",
        )
        self.lbl_assets = QLabel("")
        self.lbl_assets.setWordWrap(True)
        form5.addRow(self.lbl_assets)
        row_asset = QHBoxLayout()
        for text, action in (("🔄 重新扫描素材表", "rescan_assets"), ("📜 重新载入台词库", "reload_lines"), ("📛 重新载入等级称号", "reload_levels"), ("🐋 看看状态形象", "whale_form"), ("📂 打开素材目录", "open_asset_dir"), ("🖼 打开形象目录", "open_character_dir"), ("🧾 素材统计", "asset_stats")):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, name=action: self.dfpActionRequested.emit(name))
            row_asset.addWidget(button)
        row_asset.addStretch(1)
        form5.addRow(row_asset)
        self.lbl_lines = QLabel("")
        self.lbl_lines.setWordWrap(True)
        form5.addRow(self.lbl_lines)
        self.lbl_levels = QLabel("")
        self.lbl_levels.setWordWrap(True)
        form5.addRow(self.lbl_levels)
        layout.addWidget(box5)

        box6 = QGroupBox("形象声音（voice_*.mp3）")
        form6 = QFormLayout(box6)
        form6.setContentsMargins(10, 8, 10, 8)
        form6.setSpacing(6)
        self._dfp_check(form6, "启用形象声音", "voice_enabled")
        self._dfp_spin(form6, "音量", "voice_volume", 0.0, 1.0, 0.05, 2, "", "0 = 静音，1 = 最大")
        self._dfp_check(form6, "摸头 / 点击时说话（poke）", "voice_on_poke")
        self._dfp_check(form6, "投喂 / 确认时说话（confirm）", "voice_on_confirm")
        self._dfp_check(form6, "任务完成 / 升级时说话（done）", "voice_on_done")
        self._dfp_check(form6, "发送问题 / 呼唤时说话（ask）", "voice_on_ask")
        self._dfp_check(
            form6,
            "自言自语时也出声（idle）",
            "voice_on_idle",
            "蹭到素材包（如素材4）里的 idle 语音时才会响；没装包时什么也不会发生",
        )
        self._dfp_check(
            form6,
            "任务 / 异常提示音（notice）",
            "voice_on_notice",
            "提示音 6 类：任务完成 / 失败 / 被打断 / 需要帮忙 / 余额变少 / 需要确认（弹确认框前）；录音名 `voice_notice_completed_1.mp3` 这种",
        )
        self._dfp_check(
            form6,
            "播放素材包自带的声音（素材4 / 素材6）",
            "voice_use_pack",
            "★ 默认**关**：素材包里别人配的 `line-*.wav` / `v_*.wav` / `notice-*.wav` 一律不播，全部用你自己录的（素材2 与 `鲸宝音频\\`）；\n"
            "想听回包里那一套就打开它（自录的仍然优先，不会被盖掉）",
        )
        self._dfp_check(
            form6,
            "程序启动时说一句（startup）",
            "voice_on_startup",
            "每次开程序时念一句启动台词（音频名 `voice_startup_1.mp3` 这种，放在素材目录里）；没录就只显示气泡台词",
        )
        # ★ v1.1.33：情绪 / 场景类语音 —— 录音清单第六节那几个「建议新增类别」已经**接好线**了
        self._dfp_check(
            form6,
            "撒娇时说话（act_cute）",
            "voice_on_act_cute",
            "心情好 + 关系亲近的时候它自己会撒娇；右键菜单「💗　撒娇一下」也走这一类",
        )
        self._dfp_check(form6, "饿了时说话（hungry）", "voice_on_hungry", "饱食度过低、它自己找话说的时候")
        self._dfp_check(form6, "累了 / 困了时说话（tired）", "voice_on_tired", "精力过低的时候")
        self._dfp_check(
            form6,
            "想你 / 亲昵时说话（love）",
            "voice_on_love",
            "被晾很久（>6 小时「想你」）或关系亲近时；「想你」与「亲昵」共用 love 这一类",
        )
        self._dfp_check(form6, "难过 / 出错时说话（sad）", "voice_on_sad", "心情过低、或接口 / 任务出错时")
        self._dfp_check(
            form6,
            "打招呼 / 回来时说话（greet）",
            "voice_on_greet",
            "Ctrl+H 或托盘把它唤回来时，以及按时段问候（早上 / 中午 / 下午 / 傍晚 / 夜里）",
        )
        self._dfp_check(
            form6,
            "告别 / 被藏起来时说话（bye）",
            "voice_on_bye",
            "Ctrl+H 或托盘把它藏起来之前说一句；音频名 `voice_bye_1.mp3` 这种",
        )
        self._dfp_check(form6, "语音台词显示在气泡里（字幕）", "voice_show_text", "语音文件里没有人听得懂的文字，台词是人工听写在素材清单 voices[].text 里的；打开后会盖掉原来的离线台词")
        # ★ v1.1.33：类别多了（六类 + 七类情绪 + 提示音）⇒ 试听按钮改**网格排布**
        #   （挤在一行会把整个设置窗口顶宽；每行 4 个，tooltip 照旧由 `_dfp_voice_tip()` 给）
        grid_voice = QGridLayout()
        grid_voice.setSpacing(4)
        for index, (text, action) in enumerate((
            ("▶️ 试听 poke", "voice_poke"),
            ("▶️ 试听 confirm", "voice_confirm"),
            ("▶️ 试听 done", "voice_done"),
            ("▶️ 试听 ask", "voice_ask"),
            ("▶️ 试听 idle", "voice_idle"),
            ("▶️ 试听 startup", "voice_startup"),
            ("▶️ 试听 act_cute", "voice_act_cute"),
            ("▶️ 试听 hungry", "voice_hungry"),
            ("▶️ 试听 tired", "voice_tired"),
            ("▶️ 试听 love", "voice_love"),
            ("▶️ 试听 greet", "voice_greet"),
            ("▶️ 试听 bye", "voice_bye"),
            ("▶️ 试听 sad", "voice_sad"),
            ("🔔 试听提示音", "voice_notice"),
        )):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, name=action: self.dfpActionRequested.emit(name))
            tip = self._dfp_voice_tip(action[6:])
            if tip:
                button.setToolTip(tip)
            grid_voice.addWidget(button, index // 4, index % 4)
        form6.addRow(grid_voice)
        layout.addWidget(box6)

        layout.addStretch(1)
        return page

    def _dfp_character_choices(self) -> List[Tuple[str, str]]:
        """形象下拉的可选项：素材清单里的形象 + 素材包（素材4）的 skins。"""
        library = self._dfp_library
        if library is None:
            return [("", "（默认）")]
        items = library.dfp_characters()
        choices: List[Tuple[str, str]] = [("", "（默认）%s" % library.dfp_character_label(items[0] if items else None))]
        for item in items:
            choices.append((str(item.get("id") or ""), library.dfp_character_label(item)))
        return choices

    def _dfp_voice_tip(self, category: str) -> str:
        """试听按钮的悬停提示：这一类有几条、分别说什么。

        台词是人工听写在 `素材清单.json` 的 `voices[].text` 里的（语音文件本身没有任何文字信息），
        没听写的会标出来，方便以后补齐。

        ★ v1.1.14：`notice` 不是一条语音，而是素材包里的 6 类提示音
          （`notice_completed` / `notice_failed` / …）。悬停必须把它们一起列出来，
          否则「🔔 试听提示音」会去查一个根本不存在的类别，连提示框都是空的。
        """
        library = self._dfp_library
        if library is None or not library.dfp_is_ready():
            return ""
        wanted = str(category or "").strip()
        if wanted == DFP_ASSET_PACK_NOTICE_PREFIX.rstrip("-"):
            categories = [
                "%s%s" % (DFP_ASSET_PACK_NOTICE_CATEGORY_PREFIX, name)
                for name in DFP_ASSET_PACK_NOTICE_STATUSES
            ]
        else:
            categories = [wanted]
        multi = len(categories) > 1
        rows = []
        unknown = 0
        for name in categories:
            tag = ""
            if multi:
                status = name[len(DFP_ASSET_PACK_NOTICE_CATEGORY_PREFIX):]
                tag = "[%s] " % DFP_ASSET_PACK_NOTICE_LABELS.get(status, status)
            for item in library.dfp_voices(name):
                filename = os.path.basename(str(item.get("file") or ""))
                if not filename:
                    continue
                text = str(item.get("text") or "")
                if text:
                    rows.append("· %s%s：%s" % (tag, filename, text))
                elif str(item.get("kind") or "") == "notice":
                    # ★ v1.1.14：素材包里的提示音是纯音效，本来就没台词，别写成「暂未听写」
                    rows.append("· %s%s：（音效，无台词）" % (tag, filename))
                elif str(item.get("source") or "").startswith("user:"):
                    # ★ v1.1.23：自己录在「鲸宝音频」里的（还没登记台词）
                    rows.append("· %s%s：（你自己的录音）" % (tag, filename))
                else:
                    unknown += 1
                    rows.append("· %s%s：（暂未听写台词）" % (tag, filename))
        if not rows:
            # ★ v1.1.22：还没录音的类别（例如刚设计好台词、正准备录的 `startup`）要给一句话说明，
            #   别留个空提示框让人以为按钮坏了。★ v1.1.24：素材包退役后，这句就是常态。
            count = len(dfp_lines(wanted))
            if count:
                return (
                    "%s 这一类还没有语音文件 —— 台词已经备好（%d 条），见 `大肥鱼素材表\\录音清单.md` 里的 `%s` 一节；"
                    "录成 `voice_%s_1.mp3` 起、放进 `%s\\` 就能出声。" % (wanted, count, wanted, wanted, DFP_USER_VOICE_DIR_NAME)
                )
            return (
                "%s 这一类还没有语音文件 —— 录音名照 `voice_%s_1.mp3` 这种写，"
                "丢进 `%s\\`（见 `大肥鱼素材表\\录音清单.md`），点「重新扫描素材表」就生效。"
                % (wanted, wanted, DFP_USER_VOICE_DIR_NAME)
            )
        head = "提示音 %d 类共 %d 条" % (len(categories), len(rows)) if multi else "%s 类共 %d 条" % (wanted, len(rows))
        if unknown:
            head += "，其中 %d 条暂未听写台词" % unknown
        return "%s\n%s" % (head, "\n".join(rows))

    def dfp_update_preview(self) -> None:
        """预览：素材模式下直接显示素材表里的立绘，否则用代码绘制的兜底形象。"""
        mode = str(self._dfp_settings.dfp_get("pet_render_mode", "assets"))
        library = self._dfp_library
        if mode != "vector" and library is not None and library.dfp_is_ready():
            item = library.dfp_character()
            if item:
                pixmap = library.dfp_pixmap(str(item.get("file") or ""), QSize(200, 200))
                if not pixmap.isNull():
                    self.lbl_preview.setPixmap(pixmap)
                    return None
        renderer = DFPPetRenderer(str(self._dfp_settings.dfp_get("pet_palette", "ocean")))
        state = DFPRenderState()
        state.time = 0.8
        state.tail_phase = 0.2
        state.breathe = 0.6
        state.spout = 0.7
        state.nametag = str(self._dfp_settings.dfp_get("pet_nickname", "")) if self._dfp_settings.dfp_get("pet_show_nametag", False) else ""
        state.nametag_color = renderer._c("accent").name()
        state.show_shadow = bool(self._dfp_settings.dfp_get("pet_show_shadow", True))
        pixmap = renderer.dfp_render_pixmap(state, 200, 236)
        self.lbl_preview.setPixmap(pixmap)
        return None

    def _dfp_build_behavior_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.setContentsMargins(2, 2, 2, 2)

        box, form = self._dfp_group("自主行为")
        self._dfp_check(form, "自己走来走去", "auto_walk_enabled")
        self._dfp_spin(form, "走动间隔", "auto_walk_interval_sec", 8, 3600, 5, 0, " 秒")
        self._dfp_check(form, "跟着鼠标跑", "follow_mouse_enabled", "光标靠近时它会朝你游过去（很黏人）")
        self._dfp_check(form, "偶尔自言自语", "idle_talk_enabled")
        self._dfp_spin(form, "说话间隔", "idle_talk_interval_sec", 15, 7200, 5, 0, " 秒")
        self._dfp_check(form, "偶尔做小动作", "idle_action_enabled")
        self._dfp_spin(form, "动作间隔", "idle_action_interval_sec", 5, 3600, 5, 0, " 秒")
        layout.addWidget(box)

        box2, form2 = self._dfp_group("数值衰减（每小时）")
        self._dfp_spin(form2, "饱食下降", "satiety_decay_per_hour", 0.0, 60.0, 0.5, 1, " /小时")
        self._dfp_spin(form2, "精力下降", "energy_decay_per_hour", 0.0, 60.0, 0.5, 1, " /小时")
        self._dfp_spin(form2, "心情下降", "mood_decay_per_hour", 0.0, 60.0, 0.5, 1, " /小时")
        self._dfp_spin(form2, "亲密度衰减", "intimacy_decay_per_hour", 0.0, 60.0, 0.1, 1, " /小时", "亲密度超过 60 后不再衰减")
        self._dfp_spin(form2, "睡觉恢复精力", "sleep_recover_per_hour", 0.0, 100.0, 1.0, 1, " /小时")
        layout.addWidget(box2)

        box3, form3 = self._dfp_group("作息")
        self._dfp_check(form3, "到点自动睡觉 / 自动醒来", "auto_sleep_enabled")
        self._dfp_spin(form3, "睡觉时间", "auto_sleep_hour", 0, 23, 1, 0, " 点")
        self._dfp_spin(form3, "起床时间", "auto_wake_hour", 0, 23, 1, 0, " 点")
        layout.addWidget(box3)

        box4, form4 = self._dfp_group("窗口与交互")
        self._dfp_check(form4, "记住上次的位置", "remember_position")
        self._dfp_check(form4, "锁定位置（不能拖动）", "lock_position")
        self._dfp_check(form4, "拖放后吸附到屏幕边缘", "snap_to_edge")
        self._dfp_check(form4, "保持窗口在屏幕内", "keep_on_screen")
        self._dfp_check(form4, "单击摸摸头", "click_pet_enabled")
        self._dfp_check(form4, "双击陪它玩", "double_click_play_enabled")
        self._dfp_check(form4, "滚轮缩放", "wheel_zoom_enabled")
        self._dfp_check(form4, "允许拖动", "drag_enabled")
        self._dfp_check(form4, "提示音（系统提示音）", "sound_enabled")
        self._dfp_check(form4, "没联网时使用内置台词", "offline_lines_enabled")
        layout.addWidget(box4)

        box_dream, form_dream = self._dfp_group("梦境")
        self._dfp_check(
            form_dream,
            "允许做梦",
            "dream_enabled",
            "醒来时若距上次做梦够久，会复述一段梦（梦的骨架在 `大肥鱼素材表\\梦境.json`，没有就用内置的）",
        )
        self._dfp_spin(form_dream, "做梦间隔", "dream_interval_hours", 0.5, 48.0, 0.5, 1, " 小时")
        self._dfp_check(form_dream, "醒来复述梦境", "dream_on_wake")
        row_dream = QHBoxLayout()
        for text, action in (("🌀 重新载入梦境库", "reload_dreams"), ("🌙 现在做一个梦", "dream_now")):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, name=action: self.dfpActionRequested.emit(name))
            row_dream.addWidget(button)
        row_dream.addStretch(1)
        form_dream.addRow(row_dream)
        self.lbl_dream = QLabel("")
        self.lbl_dream.setWordWrap(True)
        form_dream.addRow(self.lbl_dream)
        layout.addWidget(box_dream)

        # ★ v1.1.26：灵魂（会记得 / 有心事 / 会主动开口 / 关系会成长）
        box_soul, form_soul = self._dfp_group("灵魂（会记得 · 有心事 · 会主动开口 · 会成长）")
        self._dfp_check(
            form_soul,
            "让她有灵魂",
            "soul_enabled",
            "关掉就只剩「会饿会困」：不再记里程碑、不攒心事、也不主动开口（不影响梦境）",
        )
        self._dfp_spin(form_soul, "主动开口间隔", "soul_interval_min", 5, 720, 5, 0, " 分钟")
        row_soul = QHBoxLayout()
        for text, action in (("💗 现在说一句", "soul_now"), ("🫧 重新载入灵魂库", "reload_soul")):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, name=action: self.dfpActionRequested.emit(name))
            row_soul.addWidget(button)
        row_soul.addStretch(1)
        form_soul.addRow(row_soul)
        self.lbl_soul = QLabel("")
        self.lbl_soul.setWordWrap(True)
        form_soul.addRow(self.lbl_soul)
        layout.addWidget(box_soul)

        # ★ v1.1.28：三魂七魄（她的「精气神」）
        box_spirits, form_spirits = self._dfp_group("魂魄（三魂七魄 · 精气神）")
        label_spirits = QLabel(
            "三魂管精神（胎光·本命 / 爽灵·灵慧 / 幽精·情感），七魄管身体（尸狗·警觉 / 伏矢·吐纳 / 雀阴·亲昵 / "
            "吞贼·吸收 / 非毒·抗性 / 除秽·清洁 / 臭肺·气息）。\n出生时按「3 旺 / 4 平 / 3 弱」抽一张命盘，"
            "终身不变；灵气只涨不跌（摸头 / 投喂 / 陪玩 / 睡觉 / 打理都养魂）。跨过旺弱门槛她就会说一句，"
            "十个都养到 90 以上会说一句「十全」。\n文案在 `大肥鱼素材表\\魂魄.json`，改完点「重新载入魂魄库」即生效。"
        )
        label_spirits.setWordWrap(True)
        form_spirits.addRow(label_spirits)
        row_spirits = QHBoxLayout()
        for text, action in (("🪷 看看她的三魂七魄", "spirits_view"),
                             ("💬 魂魄想说一句", "spirits_now"),
                             ("📜 重新载入魂魄库", "reload_spirits"),
                             ("♻️ 重新投胎", "spirits_reborn")):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, name=action: self.dfpActionRequested.emit(name))
            row_spirits.addWidget(button)
        row_spirits.addStretch(1)
        form_spirits.addRow(row_spirits)
        self.lbl_spirits = QLabel("")
        self.lbl_spirits.setWordWrap(True)
        form_spirits.addRow(self.lbl_spirits)
        layout.addWidget(box_spirits)

        # ★ v1.1.29：修真层（道 / 性 / 元神 / 五脏六腑 / 气血 / 命·运·数 / 因果·业·缘 / 神·仙·鬼·妖）
        box_dao, form_dao = self._dfp_group("道（道 · 性 · 元神 · 五脏六腑 · 气血 · 命运数 · 因果业缘 · 神仙鬼妖）")
        label_dao = QLabel(
            "道是道行与境界（池鱼 → 逐浪 → 吞云 → 化鲸 → 过龙门 → 乘云 → 登仙，只升不降）；"
            "性是心性（顽 / 野 / 平 / 静 / 澄）；元神五阶靠道行与打坐养。\n"
            "五脏六腑十一个各有虚与实，气血两条（睡补气、食补血），不同互动养的脏腑不一样。\n"
            "命（本命数 1~9 对应一个命格）、运（每日运势，当天不变）、数（本命数 / 寿数 / 缘数，出生定下终身不变）"
            "——都在下方「看看她的道」里。\n"
            "因果：施食 / 亲昵 / 陪玩 / 同眠 / 唤名 / 疏慢 / 受惊 / 贪眠 都算一个「因」，过 30 分钟才「结果」；"
            "结果时改一笔业（善 - 恶 = 净业值，六档）。缘有缘分与缘谱（主人 / 梦 / 月 / 星 / 灯 / 影）。\n"
            "神 · 仙 · 鬼 · 妖四个向：神性靠道行与善业长，仙阶要「过龙门」才长得动，"
            "鬼气夜里长白天退、气血虚更容易招，妖气靠陪玩与撒娇长、睡觉与静心会退。\n"
            "★ 这一层不改任何既有数值（饱食 / 精力 / 经验 / 亲密度 / 衰减全不碰）；关掉开关整层都不动。"
        )
        label_dao.setWordWrap(True)
        form_dao.addRow(label_dao)
        self._dfp_check(form_dao, "让她修（道 · 气 · 业 · 缘）", "dao_enabled",
                        "关掉之后修真层不再演化、也不再说这一层的话（灵魂与三魂七魄照旧）")
        row_dao = QHBoxLayout()
        for text, action in (("☯️ 看看她的道", "dao_view"),
                             ("💬 道想说一句", "dao_now"),
                             ("📜 重新载入道库", "reload_dao"),
                             ("♻️ 重定命数", "dao_reborn")):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, name=action: self.dfpActionRequested.emit(name))
            row_dao.addWidget(button)
        row_dao.addStretch(1)
        form_dao.addRow(row_dao)
        self.lbl_dao = QLabel("")
        self.lbl_dao.setWordWrap(True)
        form_dao.addRow(self.lbl_dao)
        layout.addWidget(box_dao)

        box5, form5 = self._dfp_group("素材行为（自主行为扩展包）")
        self._dfp_check(
            form5,
            "让桌宠自己挑素材表里的动作片来演",
            "behavior_asset_enabled",
            "素材表里每个动作片 = 一个自主行为，共 100+ 个；关掉就只用内置的几种动作",
        )
        self._dfp_spin(form5, "换动作间隔", "behavior_asset_interval_sec", 8, 3600, 5, 0, " 秒")
        self._dfp_check(form5, "节日动作只在对应节日出现", "behavior_seasonal_enabled", "如中秋、端午、圣诞、元宵等只在对应时段随机")
        self._dfp_line(form5, "只看这些类别", "behavior_asset_categories", "空格分隔，如：吃喝 玩耍 休息；留空 = 全部类别", "留空 = 全部")
        self.lbl_behavior = QLabel("")
        self.lbl_behavior.setWordWrap(True)
        form5.addRow(self.lbl_behavior)
        layout.addWidget(box5)
        layout.addStretch(1)
        return page

    def _dfp_build_trainer_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.setContentsMargins(2, 2, 2, 2)

        box, form = self._dfp_group("数值锁定（锁定后不再自动衰减，也不被互动改动）")
        self._dfp_check(form, "锁定心情", "lock_mood")
        self._dfp_check(form, "锁定饱食", "lock_satiety")
        self._dfp_check(form, "锁定精力", "lock_energy")
        self._dfp_check(form, "锁定亲密度", "lock_intimacy")
        self._dfp_check(form, "锁定等级", "lock_level")
        self._dfp_check(form, "锁定经验", "lock_exp")
        layout.addWidget(box)

        box2 = QGroupBox("内置修改器")
        box2_layout = QVBoxLayout(box2)
        box2_layout.setContentsMargins(10, 8, 10, 8)
        box2_layout.addWidget(QLabel("可以直接把心情 / 饱食 / 精力 / 亲密度 / 等级经验拉满，也可以锁定不变。"))
        row = QHBoxLayout()
        btn_open = QPushButton("🧰 打开完整修改器…")
        btn_open.clicked.connect(lambda: self.dfpActionRequested.emit("open_trainer"))
        row.addWidget(btn_open)
        btn_max = QPushButton("全部拉满")
        btn_max.clicked.connect(lambda: self.dfpActionRequested.emit("max_all"))
        row.addWidget(btn_max)
        btn_max_level = QPushButton("一键满级")
        btn_max_level.clicked.connect(lambda: self.dfpActionRequested.emit("max_level"))
        row.addWidget(btn_max_level)
        btn_lock = QPushButton("全部锁定")
        btn_lock.clicked.connect(lambda: self.dfpActionRequested.emit("lock_all"))
        row.addWidget(btn_lock)
        btn_unlock = QPushButton("全部解锁")
        btn_unlock.clicked.connect(lambda: self.dfpActionRequested.emit("unlock_all"))
        row.addWidget(btn_unlock)
        row.addStretch(1)
        box2_layout.addLayout(row)
        self.lbl_trainer = QLabel("")
        self.lbl_trainer.setWordWrap(True)
        box2_layout.addWidget(self.lbl_trainer)
        layout.addWidget(box2)

        box3, form3 = self._dfp_group("数值衰减速度（锁定后这些设置对该字段无效）")
        self._dfp_spin(form3, "饱食下降", "satiety_decay_per_hour", 0.0, 60.0, 0.5, 1, " /小时")
        self._dfp_spin(form3, "精力下降", "energy_decay_per_hour", 0.0, 60.0, 0.5, 1, " /小时")
        self._dfp_spin(form3, "心情下降", "mood_decay_per_hour", 0.0, 60.0, 0.5, 1, " /小时")
        self._dfp_spin(form3, "亲密度衰减", "intimacy_decay_per_hour", 0.0, 60.0, 0.1, 1, " /小时")
        layout.addWidget(box3)
        layout.addStretch(1)
        return page

    def _dfp_build_ai_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.setContentsMargins(2, 2, 2, 2)

        box, form = self._dfp_group("接口")
        self._dfp_check(form, "启用 AI 对话", "ai_enabled", "关掉后只能用离线台词")
        self._dfp_combo(
            form,
            "模型",
            "ai_model",
            (("deepseek-chat", "deepseek-chat（通用对话）"), ("deepseek-reasoner", "deepseek-reasoner（推理）")),
        )
        self._dfp_spin(form, "温度", "ai_temperature", 0.0, 2.0, 0.1, 2, "", "越高越放飞")
        self._dfp_spin(form, "top_p", "ai_top_p", 0.0, 1.0, 0.05, 2, "", "1.0 表示不启用采样裁剪")
        self._dfp_spin(form, "最大回复长度", "ai_max_tokens", 16, 8192, 16, 0, " token")
        self._dfp_spin(form, "超时", "ai_timeout", 5, 600, 5, 0, " 秒")
        self._dfp_spin(form, "失败重试次数", "ai_max_retry", 0, 6, 1, 0, " 次")
        self._dfp_spin(form, "并发请求数", "ai_request_concurrency", 1, 8, 1, 0, " 个")
        self._dfp_check(form, "流式输出（打字机效果）", "ai_stream")
        self._dfp_spin(form, "重复惩罚", "ai_frequency_penalty", -2.0, 2.0, 0.1, 2)
        self._dfp_spin(form, "话题惩罚", "ai_presence_penalty", -2.0, 2.0, 0.1, 2)
        layout.addWidget(box)

        box2, form2 = self._dfp_group("对话行为")
        self._dfp_spin(form2, "携带上下文轮数", "ai_context_rounds", 1, 60, 1, 0, " 轮")
        self._dfp_check(form2, "回复后让桌宠念出来", "ai_speak_reply", "会把回复截断成一句话放到气泡里")
        self._dfp_spin(form2, "气泡最多念", "ai_speak_max_chars", 10, 200, 5, 0, " 字")
        self._dfp_check(form2, "把当前状态告诉 AI", "ai_include_pet_state", "让回答能照顾到它饿不饿、困不困")
        self._dfp_check(form2, "用第一句话自动命名会话", "ai_auto_title")
        layout.addWidget(box2)

        box3 = QGroupBox("Key 与连接")
        box3_layout = QVBoxLayout(box3)
        box3_layout.setContentsMargins(10, 8, 10, 8)
        self.lbl_key = QLabel("")
        self.lbl_key.setWordWrap(True)
        box3_layout.addWidget(self.lbl_key)

        # ★ v1.1.9：直接在这里输入 / 粘贴 API Key（写回 .env，**不写进设置文件**）
        self._dfp_key_dirty = False
        row_input = QHBoxLayout()
        row_input.addWidget(QLabel("API Key"))
        self.edit_api_key = QLineEdit()
        self.edit_api_key.setEchoMode(
            dfp_enum_value(0, lambda: QLineEdit.EchoMode.Password, lambda: QLineEdit.Password)
        )
        self.edit_api_key.setPlaceholderText("粘贴 Key（多个用逗号分隔）；留空保存 = 清空")
        self.edit_api_key.textEdited.connect(self._dfp_on_key_edited)
        self.edit_api_key.textChanged.connect(self._dfp_update_key_hint)
        btn_show_key = QPushButton("👁 显示")
        btn_show_key.setCheckable(True)
        btn_show_key.setToolTip("切换输入框里 Key 的可见性（默认打码，防止被人看到）")
        btn_show_key.toggled.connect(self._dfp_toggle_key_visible)
        row_input.addWidget(self.edit_api_key, 1)
        row_input.addWidget(btn_show_key)
        box3_layout.addLayout(row_input)
        self.lbl_key_input = QLabel("")
        self.lbl_key_input.setWordWrap(True)
        box3_layout.addWidget(self.lbl_key_input)

        row_key_buttons = QHBoxLayout()
        btn_save_key = QPushButton("💾 保存 Key 到 .env")
        btn_save_key.setToolTip("把输入框里的内容写进 .env 的 DEEPSEEK_API_KEYS，并重载接口配置（写前会备份 .env.bak）")
        btn_save_key.clicked.connect(self._dfp_save_api_key)
        btn_reset_key = QPushButton("↩️ 还原为 .env 的值")
        btn_reset_key.setToolTip("把输入框恢复成 .env 里当前生效的 Key（丢掉未保存的改动）")
        btn_reset_key.clicked.connect(self._dfp_reload_key_input)
        row_key_buttons.addWidget(btn_save_key)
        row_key_buttons.addWidget(btn_reset_key)
        row_key_buttons.addStretch(1)
        box3_layout.addLayout(row_key_buttons)

        # ★ v1.1.12：余额变化提醒（总可用余额变了就在气泡里说一句：少了红、多了绿，再说最新余额）
        form_watch = QFormLayout()
        form_watch.setContentsMargins(0, 4, 0, 0)
        form_watch.setSpacing(6)
        form_watch.setLabelAlignment(
            dfp_enum_value(0, lambda: Qt.AlignmentFlag.AlignRight, lambda: Qt.AlignRight)
        )
        self._dfp_check(
            form_watch,
            "余额变了提醒我",
            "balance_watch_enabled",
            "打开后每隔一段时间查一次「总可用余额」：变少了在气泡里用红字说一句少了多少钱，"
            "变多了用绿字说一句，然后显示最新的总可用余额。查询接口不消耗 Token。默认开启。",
        )
        self._dfp_spin(
            form_watch,
            "查余额间隔",
            "balance_watch_interval_sec",
            30,
            3600,
            30,
            0,
            " 秒",
            "多久查一次余额（最短 30 秒，默认 300 秒）。只在「余额变了提醒我」开着时生效。",
        )
        box3_layout.addLayout(form_watch)

        row = QHBoxLayout()
        btn_env = QPushButton("📝 打开 .env")
        btn_env.clicked.connect(lambda: self.dfpActionRequested.emit("open_env"))
        btn_reload = QPushButton("🔄 重新载入 .env")
        btn_reload.clicked.connect(self._dfp_reload_env)
        btn_test = QPushButton("🔌 测试连接")
        btn_test.clicked.connect(self.dfp_test_connection)
        # ★ v1.1.16：手动查余额 / 翻扣钱记录（和右键菜单「💰 余额与 AI」同一个入口）
        btn_balance = QPushButton("🔎 立即查询余额")
        btn_balance.setToolTip("不去等定时查询，现在就查一次总可用余额（接口不消耗 Token，查完一定报个数）")
        btn_balance.clicked.connect(lambda: self.dfpActionRequested.emit("check_balance"))
        btn_log = QPushButton("📉 扣钱记录")
        btn_log.setToolTip("看看以前什么时候扣了多少钱、扣完还剩多少（时间精确到毫秒）")
        btn_log.clicked.connect(lambda: self.dfpActionRequested.emit("balance_log"))
        for button in (btn_env, btn_reload, btn_test, btn_balance, btn_log):
            row.addWidget(button)
        row.addStretch(1)
        box3_layout.addLayout(row)
        layout.addWidget(box3)
        self._dfp_reload_key_input()

        box4 = QGroupBox("人设（system prompt）")
        box4_layout = QVBoxLayout(box4)
        box4_layout.setContentsMargins(10, 8, 10, 8)
        self.txt_prompt = QPlainTextEdit()
        self.txt_prompt.setMinimumHeight(160)
        self.txt_prompt.setPlainText(str(self._dfp_settings.dfp_get("ai_system_prompt", DFP_DEFAULT_PERSONA)))
        box4_layout.addWidget(self.txt_prompt)
        row2 = QHBoxLayout()
        btn_save_prompt = QPushButton("💾 保存人设")
        btn_save_prompt.clicked.connect(self._dfp_commit_prompt)
        btn_default_prompt = QPushButton("↩️ 恢复默认人设")
        btn_default_prompt.clicked.connect(self._dfp_restore_default_prompt)
        row2.addWidget(btn_save_prompt)
        row2.addWidget(btn_default_prompt)
        row2.addStretch(1)
        box4_layout.addLayout(row2)
        layout.addWidget(box4)
        layout.addStretch(1)
        return page

    def _dfp_reload_env(self) -> None:
        self._dfp_client.dfp_reload_env()
        self._dfp_reload_key_input()
        self.dfp_refresh_dynamic_info()
        self._dfp_flash("已重新载入 .env：%d 个 Key" % self._dfp_client.dfp_key_count())

    # --- ★ v1.1.9：设置窗口里直接填 API Key（写回 .env，不进设置文件）---
    def _dfp_key_text_list(self) -> List[str]:
        """把输入框里的内容拆成 Key 列表（逗号 / 分号 / 空白都行，自动去重）。"""
        if not hasattr(self, "edit_api_key"):
            return []
        keys: List[str] = []
        for item in re.split(r"[,;\s]+", self.edit_api_key.text() or ""):
            text = item.strip().strip("'\"")
            if text and text not in keys:
                keys.append(text)
        return keys

    def _dfp_on_key_edited(self, _text: str = "") -> None:
        self._dfp_key_dirty = True
        self._dfp_update_key_hint()

    def _dfp_update_key_hint(self, _text: str = "") -> None:
        """输入框下面的实时提示：识别到几个 Key、打码预览、保存位置。"""
        if not hasattr(self, "lbl_key_input"):
            return
        keys = self._dfp_key_text_list()
        if not keys:
            hint = "留空 = 清空 Key（桌宠会退回离线台词库，仍然能摸头投喂）"
        else:
            shown = "、".join(dfp_mask_secret(key) for key in keys[:3])
            if len(keys) > 3:
                shown += " 等 %d 个" % len(keys)
            hint = "识别到 %d 个 Key：%s" % (len(keys), shown)
            odd = [key for key in keys if not dfp_looks_like_api_key(key)]
            if odd:
                hint += "；其中 %d 个看着不像 Key（可能贴错，仍可保存）" % len(odd)
        self.lbl_key_input.setText(
            "%s\n保存位置：%s（写前会先备份 .env.bak；改完点「保存 Key 到 .env」即时生效，不用重启）"
            % (hint, dfp_env_path())
        )

    def _dfp_load_key_input(self) -> None:
        """把输入框填成 .env 里当前生效的 Key（不触发「已修改」标记）。"""
        if not hasattr(self, "edit_api_key"):
            return
        keys = dfp_collect_api_keys()
        self.edit_api_key.blockSignals(True)
        try:
            self.edit_api_key.setText(",".join(keys))
        finally:
            self.edit_api_key.blockSignals(False)
        self._dfp_key_dirty = False
        self._dfp_update_key_hint()

    def _dfp_reload_key_input(self) -> None:
        self._dfp_load_key_input()

    def _dfp_toggle_key_visible(self, shown: bool) -> None:
        if not hasattr(self, "edit_api_key"):
            return
        self.edit_api_key.setEchoMode(
            dfp_enum_value(0, lambda: QLineEdit.EchoMode.Normal, lambda: QLineEdit.Normal)
            if shown
            else dfp_enum_value(0, lambda: QLineEdit.EchoMode.Password, lambda: QLineEdit.Password)
        )

    def _dfp_save_api_key(self) -> None:
        """把输入框里的 Key 写回 .env，并立即让客户端用上（不用重启）。"""
        keys = self._dfp_key_text_list()
        yes_button = dfp_enum_value(0, lambda: QMessageBox.StandardButton.Yes, lambda: QMessageBox.Yes)
        no_button = dfp_enum_value(0, lambda: QMessageBox.StandardButton.No, lambda: QMessageBox.No)
        if not keys:
            answer = dfp_confirm(
                self,
                "清空 API Key？",
                "输入框是空的：保存后会清空 .env 里的 Key，桌宠将退回离线台词库（仍可摸头 / 投喂 / 玩）。\n要继续吗？",
                yes_button | no_button,
                no_button,
            )
            if answer != yes_button:
                self._dfp_flash("已取消：没有修改 .env")
                return
        odd = len([key for key in keys if not dfp_looks_like_api_key(key)])
        ok, message = dfp_env_save_api_keys(keys)
        if not ok:
            self._dfp_flash("写入 .env 失败：%s" % message)
            return
        self._dfp_client.dfp_reload_env()
        self._dfp_key_dirty = False
        self._dfp_load_key_input()
        self.dfp_refresh_dynamic_info()
        self.dfpActionRequested.emit("env_keys_saved")
        if keys:
            self._dfp_flash(
                "已保存 %d 个 Key 到 .env%s；接口已重载（%s）"
                % (len(keys), "（其中 %d 个看着不像 Key）" % odd if odd else "", message)
            )
        else:
            self._dfp_flash("已清空 Key（%s）" % message)

    def dfp_test_connection(self) -> None:
        if not self._dfp_client.dfp_has_key():
            self._dfp_flash("未配置 API Key")
            return
        self.dfp_set_operation_progress(0, 0, "正在查询余额…")

        def task(report):
            report(30, 100, "正在连接…")
            data = self._dfp_client.dfp_balance()
            report(100, 100, "完成")
            return data

        def done(_name, result):
            infos = (result or {}).get("balance_infos") or []
            text = "可用：%s" % ("是" if (result or {}).get("is_available") else "否")
            if infos:
                first = infos[0] or {}
                text += "｜余额 %s %s" % (first.get("total_balance", "?"), first.get("currency", ""))
            self._dfp_flash("连接正常 · %s" % text)

        def failed(_name, message):
            self._dfp_flash("连接失败：%s" % message)

        self.dfp_run_task("test_api", task, done, failed)

    def _dfp_restore_default_prompt(self) -> None:
        self.txt_prompt.setPlainText(DFP_DEFAULT_PERSONA)
        self._dfp_commit_prompt()

    def _dfp_commit_prompt(self) -> None:
        text = self.txt_prompt.toPlainText().strip()
        if not text:
            self._dfp_flash("人设不能为空")
            return
        self._dfp_settings.dfp_set("ai_system_prompt", text)
        self._dfp_flash("人设已保存（%d 字）" % len(text))

    def _dfp_build_position_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.setContentsMargins(2, 2, 2, 2)
        box, form = self._dfp_group("窗口位置")
        self.lbl_position = QLabel("")
        self.lbl_position.setWordWrap(True)
        form.addRow(self.lbl_position)
        layout.addWidget(box)

        box2 = QGroupBox("操作")
        box2_layout = QVBoxLayout(box2)
        box2_layout.setContentsMargins(10, 8, 10, 8)
        row = QHBoxLayout()
        btn_center = QPushButton("🎯 移到屏幕中央")
        btn_center.clicked.connect(lambda: self.dfpActionRequested.emit("center_pet"))
        btn_corner = QPushButton("📐 移到右下角")
        btn_corner.clicked.connect(lambda: self.dfpActionRequested.emit("corner_pet"))
        btn_refresh = QPushButton("🔄 刷新位置信息")
        btn_refresh.clicked.connect(self.dfp_refresh_position_info)
        for button in (btn_center, btn_corner, btn_refresh):
            row.addWidget(button)
        row.addStretch(1)
        box2_layout.addLayout(row)
        box2_layout.addWidget(QLabel("提示：桌宠位置会被记住；如果它跑到屏幕外，用「移到屏幕中央」就能找回来。"))
        layout.addWidget(box2)
        layout.addStretch(1)
        return page

    def dfp_refresh_position_info(self) -> None:
        if self._dfp_loading:
            return
        parts = [
            "桌宠窗口：%s" % (self._dfp_settings.dfp_get("window_geometry", "") or "（未记录，按默认放下）"),
            "记住位置：%s｜锁定：%s｜吸附边缘：%s｜保持在屏幕内：%s"
            % (
                "是" if self._dfp_settings.dfp_get("remember_position", True) else "否",
                "是" if self._dfp_settings.dfp_get("lock_position", False) else "否",
                "是" if self._dfp_settings.dfp_get("snap_to_edge", False) else "否",
                "是" if self._dfp_settings.dfp_get("keep_on_screen", True) else "否",
            ),
            "屏幕：%s" % "；".join("%dx%d @ (%d,%d)" % (rect.width(), rect.height(), rect.x(), rect.y()) for rect in dfp_screen_available_rects()),
            "聊天窗口：%s｜设置窗口：%s" % (self._dfp_settings.dfp_get("chat_window_geometry", "") or "（未记录）", self._dfp_settings.dfp_get("settings_window_geometry", "") or "（未记录）"),
        ]
        self.lbl_position.setText("\n".join(parts))

    def _dfp_build_data_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.setContentsMargins(2, 2, 2, 2)

        box, form = self._dfp_group("数据库")
        self.lbl_database = QLabel("")
        self.lbl_database.setWordWrap(True)
        form.addRow(self.lbl_database)
        layout.addWidget(box)

        box2, form2 = self._dfp_group("自动备份")
        self._dfp_check(form2, "启用自动备份", "auto_backup_enabled")
        self._dfp_spin(form2, "备份间隔", "auto_backup_interval_min", 5, 1440, 5, 0, " 分钟")
        self._dfp_spin(form2, "最多保留", "backup_keep_count", 1, 200, 1, 0, " 份")
        self._dfp_check(form2, "对话内容加密后落库", "db_encrypt_messages", "口令取 .env 的 DB_PASSWORD；改动只影响新写入的消息")
        layout.addWidget(box2)

        box3, form3 = self._dfp_group("清理与日志")
        self._dfp_spin(form3, "事件日志保留", "keep_events_days", 1, 3650, 1, 0, " 天")
        self._dfp_spin(form3, "状态采样保留", "keep_state_days", 1, 3650, 1, 0, " 天")
        self._dfp_spin(form3, "状态采样间隔", "state_sample_interval_min", 1, 1440, 1, 0, " 分钟")
        self._dfp_check(form3, "记录事件日志", "event_log_enabled")
        self._dfp_combo(form3, "导出格式", "chat_export_format", tuple((key, label) for key, label, _ext in DFP_EXPORT_FORMATS))
        self._dfp_combo(form3, "日志级别", "log_level", (("DEBUG", "DEBUG"), ("INFO", "INFO"), ("WARNING", "WARNING"), ("ERROR", "ERROR")))
        self._dfp_check(form3, "日志写入文件", "log_to_file")
        layout.addWidget(box3)

        box4 = QGroupBox("数据操作（后台执行，底部有进度条）")
        box4_layout = QVBoxLayout(box4)
        box4_layout.setContentsMargins(10, 8, 10, 8)
        actions = (
            ("💾 立即备份", "backup_now", "把数据库完整备份到 backups\\"),
            ("🧽 清理旧备份", "cleanup_backup", "只保留设置里指定的份数"),
            ("🗜️ 压缩数据库", "vacuum", "VACUUM，回收空间"),
            ("🧹 清理旧事件", "cleanup_events", "按保留天数删除"),
            ("🧹 清理旧采样", "cleanup_samples", "按保留天数删除"),
            ("📤 导出全部对话", "export_all", "按当前导出格式写出到 exports\\"),
            ("♻️ 重置桌宠状态", "reset_state", "等级与数值回到初始，对话保留"),
        )
        grid = QGridLayout()
        grid.setSpacing(6)
        for index, (text, name, tip) in enumerate(actions):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, action=name: self.dfpTaskRequested.emit(action))
            grid.addWidget(button, index // 2, index % 2)
        box4_layout.addLayout(grid)
        row = QHBoxLayout()
        for text, action in (("📂 打开数据目录", "open_data_dir"), ("📂 打开备份目录", "open_backup_dir"), ("📂 打开日志目录", "open_log_dir")):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, name=action: self.dfpActionRequested.emit(name))
            row.addWidget(button)
        row.addStretch(1)
        box4_layout.addLayout(row)
        layout.addWidget(box4)
        layout.addStretch(1)
        return page

    def _dfp_build_mahjong_tab(self) -> QWidget:
        """🀄 算番：手机 / 平板浏览器算番 + 桌宠读番数（★ v1.1.18）。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.setContentsMargins(2, 2, 2, 2)

        box, form = self._dfp_group("国标麻将算番 Web 版")
        self._dfp_check(
            form,
            "启动桌宠时开启算番 Web 版",
            "mahjong_web_enabled",
            "把国标麻将算番器挂在局域网上：手机 / 平板浏览器打开 http://电脑IP:端口 就能直接算番，不用装任何东西。",
        )
        self._dfp_check(
            form,
            "允许手机 / 平板访问（同一 WiFi）",
            "mahjong_web_lan",
            "关掉就只听本机 127.0.0.1（手机连不上）；第一次打开时 Windows 会问防火墙，选「允许」。",
        )
        self._dfp_spin(
            form,
            "端口",
            "mahjong_web_port",
            1024,
            65535,
            1,
            0,
            "",
            "默认 %d；被别的程序占用时程序会自动往后找一个" % DFP_MAHJONG_DEFAULT_PORT,
        )
        layout.addWidget(box)

        box2, form2 = self._dfp_group("算完让桌宠读番数")
        self._dfp_check(
            form2,
            "读出总番数（用「%s」目录里的数字音频）" % DFP_NUMBER_DIR_NAME,
            "mahjong_read_fan",
            "数字音频放在 `%s`：1~9 + 十/百/千/万/十万/百万/千万/亿。105 这类要读「零」，自己录一个 `%s.mp3` 放进去就正了。"
            % (DFP_NUMBER_DIR_NAME, DFP_NUMBER_ZERO),
        )
        layout.addWidget(box2)

        row = QHBoxLayout()
        btn_open = QPushButton("🀄 算番 Web 版…")
        btn_open.setToolTip("看本机 / 手机地址，开关服务，试读番数")
        btn_open.clicked.connect(lambda: self.dfpActionRequested.emit("mahjong_web"))
        row.addWidget(btn_open)
        btn_read = QPushButton("🔊 试读「88 番」")
        btn_read.setToolTip("用数字音频读一遍 88，听听合不合心意")
        btn_read.clicked.connect(lambda: self.dfpActionRequested.emit("mahjong_read_test"))
        row.addWidget(btn_read)
        btn_check = QPushButton("🔄 重新检查")
        btn_check.setToolTip("重新看一遍 `%s\` 与 `%s\` 里的文件齐不齐" % (DFP_MAHJONG_DIR_NAME, DFP_NUMBER_DIR_NAME))
        btn_check.clicked.connect(self.dfp_refresh_mahjong_note)
        row.addWidget(btn_check)
        row.addStretch(1)
        layout.addLayout(row)

        self.lbl_mahjong = QLabel("")
        self.lbl_mahjong.setWordWrap(True)
        layout.addWidget(self.lbl_mahjong)
        layout.addStretch(1)
        self.dfp_refresh_mahjong_note()
        return page

    def dfp_mahjong_note_text(self) -> str:
        """算番 Web 版的自检说明（文件齐不齐 / 数字音频有哪些）。"""
        lines: List[str] = []
        missing = dfp_mahjong_missing()
        if missing:
            lines.append("🀄 算番文件：缺少 %s（应放在 `%s\`）" % ("、".join(missing), DFP_MAHJONG_DIR_NAME))
        else:
            lines.append("🀄 算番文件：齐全（`%s\`，与算番器项目逐字节一致）" % DFP_MAHJONG_DIR_NAME)
        wanted = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "十", "百", "千", "万", "十万", "百万", "千万", "亿", DFP_NUMBER_ZERO)
        found = [name for name in wanted if dfp_number_clip_path(name)]
        lines.append("🔊 数字音频：找到 %d 个（%s）" % (len(found), "、".join(found) if found else "一个都没有"))
        # ★ v1.1.20：读番会念「合计 … 番」，这两条是否齐了也报一下
        missing_words = [
            name
            for name, path in (
                (DFP_MAHJONG_READ_PREFIX, dfp_number_clip_path(DFP_MAHJONG_READ_PREFIX)),
                (DFP_MAHJONG_READ_SUFFIX, dfp_number_clip_path(DFP_MAHJONG_READ_SUFFIX)),
            )
            if not path
        ]
        lines.append("🀄 读番前后缀：念「%s…%s」—— %s（音频放「%s」或「%s」都认）" % (
            DFP_MAHJONG_READ_PREFIX,
            DFP_MAHJONG_READ_SUFFIX,
            "两条都齐了" if not missing_words else "缺 %s（缺谁就跳过谁）" % "、".join(missing_words),
            DFP_NUMBER_DIR_NAME,
            DFP_NUMBER_EXTRA_DIR_NAME,
        ))
        if not dfp_number_clip_path(DFP_NUMBER_ZERO):
            lines.append("　　※ 没有 `%s.mp3` —— 105 这类会读成「一百五」；自己录一个放到 `%s\` 里即可。" % (DFP_NUMBER_ZERO, DFP_NUMBER_DIR_NAME))
        return "\n".join(lines)

    def dfp_refresh_mahjong_note(self) -> None:
        if hasattr(self, "lbl_mahjong"):
            self.lbl_mahjong.setText(self.dfp_mahjong_note_text())

    def _dfp_build_about_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.setContentsMargins(2, 2, 2, 2)

        box = QGroupBox("运行信息")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(10, 8, 10, 8)
        self.txt_about = QTextBrowser()
        self.txt_about.setOpenExternalLinks(True)
        self.txt_about.setMinimumHeight(230)
        box_layout.addWidget(self.txt_about)
        row = QHBoxLayout()
        btn_copy = QPushButton("📋 复制诊断信息")
        btn_copy.clicked.connect(self.dfp_copy_diagnostics)
        btn_refresh = QPushButton("🔄 刷新")
        btn_refresh.clicked.connect(self.dfp_refresh_dynamic_info)
        row.addWidget(btn_copy)
        row.addWidget(btn_refresh)
        row.addStretch(1)
        box_layout.addLayout(row)
        layout.addWidget(box)

        box2 = QGroupBox("最近日志")
        box2_layout = QVBoxLayout(box2)
        box2_layout.setContentsMargins(10, 8, 10, 8)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(140)
        box2_layout.addWidget(self.txt_log)
        layout.addWidget(box2)

        box3 = QGroupBox("使用说明")
        box3_layout = QVBoxLayout(box3)
        box3_layout.setContentsMargins(10, 8, 10, 8)
        help_box = QTextBrowser()
        help_box.setHtml(DFP_HELP_HTML)
        help_box.setMinimumHeight(240)
        box3_layout.addWidget(help_box)
        layout.addWidget(box3)
        layout.addStretch(1)
        return page

    def dfp_refresh_dynamic_info(self) -> None:
        try:
            if hasattr(self, "lbl_assets"):
                if self._dfp_library is None:
                    self.lbl_assets.setText("素材库未初始化")
                else:
                    stats = self._dfp_library.dfp_stats()
                    categories = "、".join("%s %d" % pair for pair in stats["categories"][:8])
                    self.lbl_assets.setText(
                        "目录：%s%s\n%s（%s）\n%s\n%s\n可选形象：%d 个\n动作类别：%s\n来源：%s"
                        % (
                            stats["root"],
                            "" if stats["manifest_exists"] else "\n（清单不存在，已自动扫描）",
                            self._dfp_library.dfp_summary(),
                            "就绪" if stats["ready"] else (stats["error"] or "未就绪"),
                            stats.get("pack_summary") or "素材包：无",
                            stats.get("form_summary") or "鲸鱼形态：无",
                            dfp_safe_int(stats.get("characters_all"), 0),
                            categories or "—",
                            dfp_truncate_text(stats["source"], 80) or "—",
                        )
                    )
            if hasattr(self, "lbl_lines"):
                _lines_error = dfp_lines_error()
                self.lbl_lines.setText(
                    "台词库：%s%s\n文件：%s\n改完 JSON 点左边「重新载入台词库」立即生效（不用重启）"
                    % (
                        dfp_lines_summary(),
                        "（读取失败：%s）" % _lines_error if _lines_error else "",
                        dfp_lines_path() or "（未找到 JSON，正在用内置台词）",
                    )
                )
            if hasattr(self, "lbl_levels"):
                _levels_error = dfp_levels_error()
                self.lbl_levels.setText(
                    "等级称号：%s%s\n文件：%s\n每段一个称号，段内等级共用（当前等级上限 Lv%d；改完 JSON 点「重新载入等级称号」生效）"
                    % (
                        dfp_levels_summary(),
                        "（读取失败：%s）" % _levels_error if _levels_error else "",
                        dfp_levels_path() or "（未找到 等级.json，正在用内置称号）",
                        DFP_MAX_LEVEL,
                    )
                )
            if hasattr(self, "lbl_dream"):
                _dream_error = dfp_dreams_error()
                self.lbl_dream.setText(
                    "梦境库：%s%s\n文件：%s\n每颗情绪种子四段（入梦 / 梦身 / 醒来 / 余韵）；梦里的记忆碎片、称号、等级、"
                    "认识天数、时段、今日次数、最近做的事都会被换成她的真实数据。\n改完 JSON 点左边「重新载入梦境库」立即生效（不用重启）"
                    % (
                        dfp_dreams_summary(),
                        "（读取失败：%s）" % _dream_error if _dream_error else "",
                        dfp_dreams_path() or "（未找到 梦境.json，正在用内置梦境）",
                    )
                )
            if hasattr(self, "lbl_soul"):
                _soul_error = dfp_soul_error()
                self.lbl_soul.setText(
                    "灵魂库：%s%s\n文件：%s\n现状：%s\n关系阶段只升不降（阈值 %s）；心事攒着不说，等到时机才说出口（最多 %d 条）。\n"
                    "改完 JSON 点左边「重新载入灵魂库」立即生效（不用重启）"
                    % (
                        dfp_soul_summary(),
                        "（读取失败：%s）" % _soul_error if _soul_error else "",
                        dfp_soul_path() or "（未找到 灵魂.json，正在用内置灵魂）",
                        self._dfp_brain.dfp_soul_summary_text(),
                        " → ".join("%s%g" % (dfp_soul_stage_label(name), need) for name, need in DFP_SOUL_STAGE_SCORES),
                        DFP_SOUL_NOTE_LIMIT,
                    )
                )
            if hasattr(self, "lbl_spirits"):
                _spirit_error = dfp_spirits_error()
                _spirit_rows = self._dfp_brain.dfp_spirits_rows()
                self.lbl_spirits.setText(
                    "魂魄库：%s%s\n文件：%s\n命盘（出生抽的，终身不变）：%s\n现状：%s\n"
                    "跨过旺（≥%d）/ 弱（≤%d）门槛时她会说一句；十个都 ≥%d 说一句「十全」。\n"
                    "改完 JSON 点左边「重新载入魂魄库」立即生效（不用重启）；「重新投胎」会重抽命盘。"
                    % (
                        dfp_spirits_summary(),
                        "（读取失败：%s）" % _spirit_error if _spirit_error else "",
                        dfp_spirits_path() or "（未找到 魂魄.json，正在用内置魂魄）",
                        "、".join("%s%s" % (row["name"], row["band_label"]) for row in _spirit_rows),
                        self._dfp_brain.dfp_spirits_summary_text().replace("\n", "　｜　"),
                        int(DFP_SPIRIT_HIGH), int(DFP_SPIRIT_LOW), int(DFP_SPIRIT_ALL_READY),
                    )
                )
            if hasattr(self, "lbl_dao"):
                _dao_error = dfp_dao_error()
                _dao_rows = self._dfp_brain.dfp_dao_tier_rows()
                self.lbl_dao.setText(
                    "道库：%s%s\n文件：%s\n命与数：%s\n现状：%s\n"
                    "档位（%s）；脏腑 11 个各有虚（≤%d）与实（≥%d）；因果埋下后 %d 分钟结果并改一笔业。\n"
                    "关掉「让她修」整层都不动；改完 JSON 点左边「重新载入道库」立即生效（不用重启）；"
                    "「重定命数」会重抽定数（灵魂 / 三魂七魄 / 关系都留着）。"
                    % (
                        dfp_dao_summary(),
                        "（读取失败：%s）" % _dao_error if _dao_error else "",
                        dfp_dao_path() or "（未找到 道.json，正在用内置道库）",
                        self._dfp_brain.dfp_dao_ming_line(),
                        self._dfp_brain.dfp_dao_summary_text().replace("\n", "　｜　"),
                        "、".join("%s %s" % (row["label"], row["name"]) for row in _dao_rows),
                        int(DFP_SPIRIT_LOW), int(DFP_SPIRIT_HIGH),
                        int(DFP_DAO_KARMA_RIPEN_SEC / 60.0),
                    )
                )
            if hasattr(self, "lbl_behavior"):
                self.lbl_behavior.setText(
                    "已触发自主行为 %d 次；最近：%s"
                    % (
                        self._dfp_brain.dfp_behavior_count(),
                        "、".join(self._dfp_brain.dfp_recent_behaviors()[-4:]) or "尚无",
                    )
                )
            if hasattr(self, "lbl_trainer"):
                self.lbl_trainer.setText(self._dfp_brain.dfp_trainer_summary())
        except Exception:
            pass
        try:
            if hasattr(self, "lbl_key"):
                self.lbl_key.setText(
                    "接口：%s\nKey：%s（共 %d 个，自动轮换）\n模型：%s｜超时：%s 秒\n配置文件：%s"
                    % (
                        self._dfp_client.dfp_api_base(),
                        self._dfp_client.dfp_masked_key(),
                        self._dfp_client.dfp_key_count(),
                        self._dfp_settings.dfp_get("ai_model", "deepseek-chat"),
                        self._dfp_settings.dfp_get("ai_timeout", 60),
                        dfp_env_path(),
                    )
                )
                # 输入框没被改过就跟 .env 保持一致（改过就不动，免得写掉用户正在输入的内容）
                if hasattr(self, "edit_api_key") and not getattr(self, "_dfp_key_dirty", False):
                    self._dfp_load_key_input()
        except Exception:
            pass
        try:
            stats = self._dfp_database.dfp_statistics()
            backups = self._dfp_backups.dfp_list()
            self.lbl_database.setText(
                "数据库：%s\n大小：%s（含 WAL）｜累计写入 %d 次\n会话 %d 个｜消息 %d 条｜token 合计 %s\n事件 %d 条｜状态采样 %d 条\n备份 %d 份，共 %s｜备份目录：%s"
                % (
                    stats["db_path"],
                    dfp_human_bytes(stats["db_size"]),
                    stats["write_count"],
                    stats["session_count"],
                    stats["message_count"],
                    dfp_human_number(stats["token_total"]),
                    stats["event_count"],
                    stats["sample_count"],
                    len(backups),
                    dfp_human_bytes(self._dfp_backups.dfp_total_size()),
                    self._dfp_backups.dfp_dir(),
                )
            )
        except Exception:
            pass
        try:
            api_stats = self._dfp_client.dfp_statistics()
            brain_snapshot = self._dfp_brain.dfp_state_snapshot()
            self.txt_about.setHtml(
                "<b>%s</b>　v%s<br>Python %s　·　PySide6 %s<br>"
                "程序目录：%s<br>数据目录：%s<br>日志目录：%s<br>图标：%s<br><br>"
                "<b>桌宠</b>：%s<br>在线时长：%s<br><br>"
                "<b>接口统计</b>：请求 %d 次，成功 %d，失败 %d，重试 %d，累计 token %s，最近耗时 %.2fs%s<br>"
                "<b>屏幕</b>：%s"
                % (
                    DFP_APP_TITLE,
                    __version__,
                    sys.version.split()[0],
                    getattr(__import__("PySide6"), "__version__", "?"),
                    dfp_app_dir(),
                    dfp_data_dir(),
                    dfp_log_dir(),
                    dfp_icon_path() or "（未找到 icon.ico，使用程序内绘制图标）",
                    brain_snapshot.get("status_text", ""),
                    brain_snapshot.get("age_text", ""),
                    api_stats["requests"],
                    api_stats["success"],
                    api_stats["failed"],
                    api_stats["retries"],
                    dfp_human_number(api_stats["tokens"]),
                    api_stats["last_latency"],
                    ("　最近错误：%s" % api_stats["last_error"]) if api_stats["last_error"] else "",
                    "；".join("%dx%d @ (%d,%d)" % (rect.width(), rect.height(), rect.x(), rect.y()) for rect in dfp_screen_available_rects()),
                )
            )
        except Exception:
            pass
        try:
            if hasattr(self, "txt_log") and self._dfp_logger is not None:
                lines = ["%s [%s] %s" % (dfp_fmt_ts(item[0], "%m-%d %H:%M:%S"), item[1], item[2]) for item in self._dfp_logger.dfp_recent(120)]
                self.txt_log.setPlainText("\n".join(lines) or "暂无日志")
        except Exception:
            pass
        self.dfp_refresh_position_info()

    def dfp_copy_diagnostics(self) -> None:
        stats = self._dfp_database.dfp_statistics()
        api_stats = self._dfp_client.dfp_statistics()
        text = "\n".join(
            [
                "%s v%s" % (DFP_APP_TITLE, __version__),
                "Python %s / PySide6 %s / %s" % (sys.version.split()[0], getattr(__import__("PySide6"), "__version__", "?"), sys.platform),
                "程序目录：%s" % dfp_app_dir(),
                "数据目录：%s" % dfp_data_dir(),
                "设置文件：%s（已保存 %d 次）" % (self._dfp_settings.dfp_path(), self._dfp_settings.dfp_save_count()),
                "数据库：%s（%s，写入 %d 次）" % (stats["db_path"], dfp_human_bytes(stats["db_size"]), stats["write_count"]),
                "会话 %d｜消息 %d｜token %s｜事件 %d｜采样 %d" % (stats["session_count"], stats["message_count"], stats["token_total"], stats["event_count"], stats["sample_count"]),
                "接口：%s｜Key %d 个｜请求 %d 次（成功 %d / 失败 %d / 重试 %d）" % (
                    api_stats["api_base"], api_stats["key_count"], api_stats["requests"], api_stats["success"], api_stats["failed"], api_stats["retries"]),
                "启用设置项：%d" % len(self._dfp_settings.dfp_all()),
                "图标：%s" % (dfp_icon_path() or "未找到 icon.ico"),
            ]
        )
        QApplication.clipboard().setText(text)
        self._dfp_flash("诊断信息已复制到剪贴板")

    # --- 载入 / 重置 ---
    def dfp_reload_values(self) -> None:
        """把设置里的值刷到控件上（★ 载入期间必须屏蔽写回，否则会把旧值覆盖用户配置）。"""
        self._dfp_loading = True
        try:
            for key, widget in self._dfp_widgets.items():
                value = self._dfp_settings.dfp_get(key, DFP_DEFAULT_SETTINGS.get(key))
                if isinstance(widget, QCheckBox):
                    widget.setChecked(bool(value))
                elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    widget.setValue(type(widget.value())(value))
                elif isinstance(widget, QComboBox):
                    for index in range(widget.count()):
                        if str(widget.itemData(index)) == str(value):
                            widget.setCurrentIndex(index)
                            break
                elif isinstance(widget, QLineEdit):
                    widget.setText("" if value is None else str(value))
            if hasattr(self, "txt_prompt"):
                self.txt_prompt.setPlainText(str(self._dfp_settings.dfp_get("ai_system_prompt", DFP_DEFAULT_PERSONA)))
        finally:
            self._dfp_loading = False
        self.dfp_update_preview()
        self.dfp_refresh_dynamic_info()

    def dfp_reset_defaults(self) -> None:
        answer = dfp_confirm(
            self,
            "恢复默认设置",
            "把所有设置恢复为默认值？\n（桌宠位置、昵称、人设和统计数据都会重置，对话记录不受影响）",
        )
        if answer != dfp_enum_value(0, lambda: QMessageBox.StandardButton.Yes, lambda: QMessageBox.Yes):
            return
        self._dfp_settings.dfp_reset_to_defaults(keep=("stat_total_pets", "stat_total_feeds", "stat_total_chats", "stat_total_tokens", "stat_total_minutes"))
        self.dfp_reload_values()
        self.dfpActionRequested.emit("apply_all_settings")
        self._dfp_flash("已恢复默认设置并保存")

    # --- 后台任务 ---
    def dfp_set_operation_progress(self, value: int, maximum: int, text: str = "") -> None:
        if maximum <= 0:
            self.bar_task.setRange(0, 0)
        else:
            self.bar_task.setRange(0, int(maximum))
            self.bar_task.setValue(int(dfp_clamp(value, 0, maximum)))
        self.bar_task.setFormat(text or "%v/%m")

    def dfp_run_task(
        self,
        name: str,
        function: Callable[[Callable[[int, int, str], None]], Any],
        on_done: Optional[Callable[[str, Any], None]] = None,
        on_failed: Optional[Callable[[str, str], None]] = None,
    ) -> bool:
        if self._dfp_active_task:
            self._dfp_flash("已有任务在执行，请稍候")
            return False
        self._dfp_active_task = str(name)
        self._dfp_task_callbacks = {"done": on_done, "failed": on_failed}
        worker = DFPTaskWorker(name, function, self._dfp_task_signals)
        self._dfp_task_pool.start(worker)
        return True

    def _dfp_on_task_started(self, name: str) -> None:
        self.dfp_set_operation_progress(0, 100, "%s…" % name)
        self._dfp_flash("任务开始：%s" % name)

    def _dfp_on_task_progress(self, name: str, value: int, maximum: int, text: str) -> None:
        if name != self._dfp_active_task:
            return
        self.dfp_set_operation_progress(value, maximum, text or "%s…" % name)

    def _dfp_on_task_done(self, name: str, result: Any) -> None:
        self._dfp_active_task = ""
        callbacks = getattr(self, "_dfp_task_callbacks", {})
        handler = callbacks.get("done") if callbacks else None
        self._dfp_on_task_progress(name, 100, 100, "完成")
        if callable(handler):
            try:
                handler(name, result)
            except Exception:
                pass
        else:
            self._dfp_flash("完成：%s" % name)
        self.dfp_refresh_dynamic_info()

    def _dfp_on_task_failed(self, name: str, message: str) -> None:
        self._dfp_active_task = ""
        callbacks = getattr(self, "_dfp_task_callbacks", {})
        handler = callbacks.get("failed") if callbacks else None
        if callable(handler):
            try:
                handler(name, message)
            except Exception:
                pass
        else:
            self._dfp_flash("失败：%s（%s）" % (name, message))

    def dfp_is_busy(self) -> bool:
        return bool(self._dfp_active_task)

    def dfp_wait_task(self, timeout_ms: int = 5000) -> bool:
        try:
            return bool(self._dfp_task_pool.waitForDone(int(timeout_ms)))
        except Exception:
            return False

    def dfp_set_status_text(self, text: str) -> None:
        """给主控制器回写状态用（任务完成后显示结果）。"""
        self._dfp_flash(text)

    # --- 几何 ---
    def dfp_restore_geometry(self) -> None:
        rect = dfp_safe_window_geometry(
            self._dfp_settings.dfp_get("settings_window_geometry", ""),
            QRect(0, 0, 760, 620),
            always_center=True,
        )
        dfp_place_window(self, rect)
        if self._dfp_settings.dfp_get("settings_window_maximized", False):
            self.showMaximized()

    def dfp_save_geometry(self) -> None:
        self._dfp_settings.dfp_set("settings_window_maximized", bool(self.isMaximized()))
        rect = dfp_window_geometry_for_save(self, QRect(0, 0, 760, 620))
        self._dfp_settings.dfp_set("settings_window_geometry", dfp_geometry_to_text(rect))

    def dfp_show_and_raise(self) -> None:
        if not self.isVisible():
            self.dfp_restore_geometry()
        self.show()
        self.raise_()
        self.activateWindow()
        self.dfp_reload_values()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.dfp_save_geometry()
        super().closeEvent(event)

    def accept(self) -> None:
        self.dfp_save_geometry()
        super().accept()


# =============================================================================
#  二十三·补　内置修改器（直接拉满 / 锁定数值，让娱乐更自由）
# =============================================================================


class DFPTrainerDialog(QDialog):
    """内置修改器：四条属性 + 等级 + 经验可任意设置、锁定不变。

    锁定后 `DFPPetBrain._dfp_bump/dfp_add_exp` 会直接跳过该字段，
    所以衰减、互动增减都不会再改动它 —— 数值会稳稳停在你想停的地方。
    """

    dfpStateEdited = Signal(str, object)  # key, value（供控制器记日志/提示）

    def __init__(self, settings: DFPSettingsStore, brain: DFPPetBrain, library: Optional["DFPAssetLibrary"] = None, parent=None):
        super().__init__(parent)
        self._dfp_settings = settings
        self._dfp_brain = brain
        self._dfp_library = library
        self._dfp_loading = False
        self._dfp_sliders: Dict[str, Any] = {}
        self._dfp_spins: Dict[str, Any] = {}
        self._dfp_bars: Dict[str, QProgressBar] = {}
        self._dfp_locks: Dict[str, QCheckBox] = {}
        self.setWindowTitle("内置修改器 — %s" % DFP_APP_TITLE)
        self.setWindowIcon(dfp_app_icon())
        self._dfp_build_ui()
        # ★ v1.1.11：最小尺寸不能比「布局真正需要的最小尺寸」还小 ——
        #   以前写死 560 宽，而内容最少要 567 ⇒ 右边那几个字会被剪掉一点。
        try:
            need = self.minimumSizeHint()
            self.setMinimumSize(max(560, int(need.width())), max(480, int(need.height())))
        except Exception:
            self.setMinimumSize(580, 620)
        self._dfp_brain.dfpStateChanged.connect(self._dfp_on_state_changed)
        self.dfp_refresh()

    def _dfp_build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self.lbl_head = QLabel("")
        self.lbl_head.setFont(dfp_desktop_font(12, True))
        root.addWidget(self.lbl_head)

        box = QGroupBox("四条属性（拖滑块立即生效；勾「锁」后不再随时间或互动变化）")
        grid = QGridLayout(box)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setSpacing(6)
        for row, key in enumerate(DFP_STAT_KEYS):
            grid.addWidget(QLabel(DFP_STAT_LABELS[key]), row, 0)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setFormat("%p%")
            bar.setFixedWidth(120)
            grid.addWidget(bar, row, 1)
            slider = QSlider(dfp_enum_value(0, lambda: Qt.Orientation.Horizontal, lambda: Qt.Horizontal))
            slider.setRange(0, 100)
            slider.setMinimumWidth(180)
            slider.valueChanged.connect(lambda value, name=key: self._dfp_apply_value(name, value))
            grid.addWidget(slider, row, 2)
            spin = QSpinBox()
            spin.setRange(0, 100)
            spin.setFixedWidth(78)
            spin.setSuffix(" /100")
            spin.valueChanged.connect(lambda value, name=key: self._dfp_apply_value(name, value))
            grid.addWidget(spin, row, 3)
            full = QPushButton("满")
            full.setFixedWidth(42)
            full.clicked.connect(lambda _checked=False, name=key: self._dfp_apply_value(name, 100, force=True))
            grid.addWidget(full, row, 4)
            lock = QCheckBox("锁")
            lock.setToolTip("锁定后该数值不再自动衰减，也不再被互动改动")
            lock.toggled.connect(lambda checked, name=key: self._dfp_toggle_lock(name, checked))
            grid.addWidget(lock, row, 5)
            self._dfp_bars[key] = bar
            self._dfp_sliders[key] = slider
            self._dfp_spins[key] = spin
            self._dfp_locks[key] = lock
        root.addWidget(box)

        box2 = QGroupBox("等级与经验")
        grid2 = QGridLayout(box2)
        grid2.setContentsMargins(10, 10, 10, 10)
        grid2.setSpacing(6)
        grid2.addWidget(QLabel("等级"), 0, 0)
        self.spin_level = QSpinBox()
        self.spin_level.setRange(1, DFP_MAX_LEVEL)
        self.spin_level.setFixedWidth(90)
        self.spin_level.valueChanged.connect(lambda value: self._dfp_apply_level(value))
        grid2.addWidget(self.spin_level, 0, 1)
        self.bar_level = QProgressBar()
        self.bar_level.setRange(0, DFP_MAX_LEVEL)
        self.bar_level.setFormat("Lv%v / %m")
        grid2.addWidget(self.bar_level, 0, 2)
        btn_max_level = QPushButton("一键满级")
        btn_max_level.clicked.connect(self._dfp_max_level)
        grid2.addWidget(btn_max_level, 0, 3)
        lock_level = QCheckBox("锁等级")
        lock_level.toggled.connect(lambda checked: self._dfp_toggle_lock("level", checked))
        grid2.addWidget(lock_level, 0, 4)
        self._dfp_locks["level"] = lock_level

        grid2.addWidget(QLabel("经验"), 1, 0)
        self.spin_exp = QSpinBox()
        self.spin_exp.setRange(0, 999999)
        self.spin_exp.setFixedWidth(90)
        self.spin_exp.valueChanged.connect(lambda value: self._dfp_apply_exp(value))
        grid2.addWidget(self.spin_exp, 1, 1)
        self.bar_exp = QProgressBar()
        self.bar_exp.setRange(0, 100)
        self.bar_exp.setFormat("经验 %v / %m")
        grid2.addWidget(self.bar_exp, 1, 2)
        btn_max_exp = QPushButton("满经验")
        btn_max_exp.clicked.connect(self._dfp_max_exp)
        grid2.addWidget(btn_max_exp, 1, 3)
        lock_exp = QCheckBox("锁经验")
        lock_exp.toggled.connect(lambda checked: self._dfp_toggle_lock("exp", checked))
        grid2.addWidget(lock_exp, 1, 4)
        self._dfp_locks["exp"] = lock_exp
        self.lbl_title = QLabel("称号：—")
        grid2.addWidget(self.lbl_title, 2, 0, 1, 5)
        root.addWidget(box2)

        box3 = QGroupBox("一键操作")
        grid3 = QGridLayout(box3)
        grid3.setContentsMargins(10, 10, 10, 10)
        grid3.setSpacing(6)
        quick = (
            ("全部拉满", self._dfp_max_all),
            ("一键满级", self._dfp_max_level),
            ("全部锁定", lambda: self._dfp_lock_all(True)),
            ("全部解锁", lambda: self._dfp_lock_all(False)),
            ("清除饥饿疲惫", self._dfp_clear_needs),
            ("恢复初始状态", self._dfp_reset_state),
        )
        for index, (text, handler) in enumerate(quick):
            button = QPushButton(text)
            button.clicked.connect(handler)
            grid3.addWidget(button, index // 3, index % 3)
        root.addWidget(box3)

        self.txt_hint = QLabel("")
        self.txt_hint.setWordWrap(True)
        self.txt_hint.setStyleSheet("color: %s;" % DFP_UI_COLORS["sub_text"])
        root.addWidget(self.txt_hint)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    # --- 写值 ---
    def _dfp_apply_value(self, key: str, value: Any, force: bool = False) -> None:
        if self._dfp_loading:
            return
        number = dfp_clamp(dfp_safe_float(value, 0.0), 0.0, 100.0)
        if force and abs(dfp_safe_float(self._dfp_brain.dfp_state_value(key), 0.0) - number) < 0.01:
            # 值没变也要生效（锁定状态下点「满」）
            pass
        self._dfp_brain.dfp_set_state_value(key, number)
        self.dfpStateEdited.emit(key, number)
        self._dfp_sync_widgets()

    def _dfp_toggle_lock(self, key: str, checked: bool) -> None:
        if self._dfp_loading:
            return
        self._dfp_brain.dfp_set_value_lock(key, bool(checked))
        self.dfpStateEdited.emit("lock_%s" % key, bool(checked))
        self.dfp_refresh()

    def _dfp_apply_level(self, value: int) -> None:
        if self._dfp_loading:
            return
        self._dfp_brain.dfp_set_level(int(value))
        self.dfpStateEdited.emit("level", int(value))
        self.dfp_refresh()

    def _dfp_apply_exp(self, value: int) -> None:
        if self._dfp_loading:
            return
        self._dfp_brain.dfp_set_exp(int(value))
        self.dfpStateEdited.emit("exp", int(value))
        self.dfp_refresh()

    def _dfp_max_all(self) -> None:
        self._dfp_brain.dfp_max_all()
        self.dfpStateEdited.emit("max_all", True)
        self.dfp_refresh()

    def _dfp_max_level(self) -> None:
        self._dfp_brain.dfp_max_level()
        self.dfpStateEdited.emit("max_level", DFP_MAX_LEVEL)
        self.dfp_refresh()

    def _dfp_max_exp(self) -> None:
        self._dfp_brain.dfp_set_exp(self._dfp_brain.dfp_exp_needed())
        self.dfpStateEdited.emit("exp", self._dfp_brain.dfp_exp_needed())
        self.dfp_refresh()

    def _dfp_clear_needs(self) -> None:
        self._dfp_brain.dfp_max_all(tell=False)
        self.dfpStateEdited.emit("clear_needs", True)
        self.dfp_refresh()

    def _dfp_lock_all(self, enabled: bool) -> None:
        self._dfp_brain.dfp_lock_all(bool(enabled))
        self.dfpStateEdited.emit("lock_all", bool(enabled))
        self.dfp_refresh()

    def _dfp_reset_state(self) -> None:
        answer = dfp_confirm(self, "恢复初始状态", "把等级与四条属性恢复到初始值？（对话记录不受影响）")
        if answer != dfp_enum_value(0, lambda: QMessageBox.StandardButton.Yes, lambda: QMessageBox.Yes):
            return
        self._dfp_brain.dfp_reset_state()
        self.dfpStateEdited.emit("reset", True)
        self.dfp_refresh()

    # --- 刷新 ---
    def _dfp_on_state_changed(self, snapshot: Dict[str, Any]) -> None:
        self.dfp_refresh(snapshot)

    def dfp_refresh(self, snapshot: Optional[Dict[str, Any]] = None) -> None:
        data = snapshot or self._dfp_brain.dfp_state_snapshot()
        self._dfp_loading = True
        try:
            for key in DFP_STAT_KEYS:
                value = int(dfp_clamp(dfp_safe_float(data.get(key), 0.0), 0, 100))
                self._dfp_bars[key].setValue(value)
                self._dfp_sliders[key].setValue(value)
                self._dfp_spins[key].setValue(value)
                self._dfp_locks[key].setChecked(self._dfp_brain.dfp_value_locked(key))
            level = dfp_safe_int(data.get("level"), 1)
            self.spin_level.setValue(int(dfp_clamp(level, 1, DFP_MAX_LEVEL)))
            self.bar_level.setValue(int(dfp_clamp(level, 1, DFP_MAX_LEVEL)))
            need = max(1, dfp_safe_int(data.get("exp_needed"), 50))
            self.spin_exp.setRange(0, need)
            self.bar_exp.setRange(0, need)
            exp = dfp_safe_int(data.get("exp"), 0)
            self.spin_exp.setValue(min(exp, need))
            self.bar_exp.setValue(min(exp, need))
            self._dfp_locks["level"].setChecked(self._dfp_brain.dfp_value_locked("level"))
            self._dfp_locks["exp"].setChecked(self._dfp_brain.dfp_value_locked("exp"))
            self.lbl_title.setText("称号：%s　（最高 Lv%d）" % (self._dfp_brain.dfp_level_title(), DFP_MAX_LEVEL))
            self.lbl_head.setText(self._dfp_brain.dfp_trainer_summary())
        finally:
            self._dfp_loading = False
        locked = self._dfp_brain.dfp_locked_keys()
        hint = "锁定中的字段：%s（锁定后不衰减、也不被互动改动）" % "、".join(locked) if locked else "当前没有锁定任何字段；想稳住数值就勾上右边的「锁」。"
        if self._dfp_library is not None and not self._dfp_library.dfp_is_ready():
            hint += "　提示：素材表未就绪，桌宠正在用代码绘制兜底。"
        self.txt_hint.setText(hint)

    def _dfp_sync_widgets(self) -> None:
        snapshot = self._dfp_brain.dfp_state_snapshot()
        self._dfp_loading = True
        try:
            for key in DFP_STAT_KEYS:
                value = int(dfp_clamp(dfp_safe_float(snapshot.get(key), 0.0), 0, 100))
                self._dfp_bars[key].setValue(value)
                self._dfp_sliders[key].setValue(value)
                self._dfp_spins[key].setValue(value)
            self.lbl_head.setText(self._dfp_brain.dfp_trainer_summary())
        finally:
            self._dfp_loading = False

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self._dfp_brain.dfpStateChanged.disconnect(self._dfp_on_state_changed)
        except Exception:
            pass
        super().closeEvent(event)


# =============================================================================
#  二十四、单实例（第二次启动时把已有桌宠叫出来，而不是开两只）
# =============================================================================

DFP_SINGLE_INSTANCE_KEY = "DeepSeekFishPet.SingleInstance.v1"
_DFP_LOCAL_SERVER: Any = None
_DFP_RAISE_CALLBACK: Optional[Callable[[], None]] = None


def dfp_set_raise_callback(callback: Optional[Callable[[], None]]) -> None:
    global _DFP_RAISE_CALLBACK
    _DFP_RAISE_CALLBACK = callback


# ★ v1.1.14：「要弹确认框了」的提示音钩子
#   （素材包里的 notice-approval.wav 就挂在这里；桌宠没起来时列表为空，什么也不会发生）
_DFP_CONFIRM_NOTICES: List[Callable[[], None]] = []


def dfp_set_confirm_notice(callback: Optional[Callable[[], None]] = None) -> None:
    """注册确认框提示音（桌宠会先叫一声）。传 None 就取消。"""
    _DFP_CONFIRM_NOTICES.clear()
    if callback is not None:
        _DFP_CONFIRM_NOTICES.append(callback)


def dfp_confirm(parent: Any, title: str, text: str, buttons: Any = None, default: Any = None) -> Any:
    """统一的「确定 / 取消」确认框入口：**先叫一声**（提示音），再弹 `QMessageBox.question`。

    ★ 危险操作（删会话 / 清空 / 重置 / 清 Key）都走这里 —— 只改这一处就能统一行为，
      以后想加「确认前先抖一下」之类也只动这里。返回值与 `QMessageBox.question` 一致。
    """
    for callback in list(_DFP_CONFIRM_NOTICES):
        try:
            callback()
        except Exception:
            pass
    if buttons is None:
        return QMessageBox.question(parent, title, text)
    if default is None:
        return QMessageBox.question(parent, title, text, buttons)
    return QMessageBox.question(parent, title, text, buttons, default)


def dfp_handle_instance_message() -> None:
    global _DFP_LOCAL_SERVER
    server = _DFP_LOCAL_SERVER
    if server is None:
        return
    try:
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            if connection is None:
                break
            try:
                connection.readyRead.connect(connection.deleteLater)
                try:
                    connection.readAll()
                except Exception:
                    pass
                connection.disconnectFromServer()
            except Exception:
                pass
    except Exception:
        pass
    if callable(_DFP_RAISE_CALLBACK):
        try:
            _DFP_RAISE_CALLBACK()
        except Exception:
            pass


def dfp_single_instance_acquire() -> bool:
    """返回 True 表示本进程拿到了主实例；False 表示已有实例在运行（已通知它显示出来）。"""
    global _DFP_LOCAL_SERVER
    if not DFP_NETWORK_AVAILABLE:
        return True
    try:
        probe = QLocalSocket()
        probe.connectToServer(DFP_SINGLE_INSTANCE_KEY)
        if probe.waitForConnected(400):
            probe.write(b"raise\n")
            probe.flush()
            probe.waitForBytesWritten(300)
            probe.disconnectFromServer()
            return False
        try:
            probe.abort()
        except Exception:
            pass
    except Exception:
        return True
    try:
        server = QLocalServer()
        try:
            QLocalServer.removeServer(DFP_SINGLE_INSTANCE_KEY)
        except Exception:
            pass
        if not server.listen(DFP_SINGLE_INSTANCE_KEY):
            return True
        server.newConnection.connect(dfp_handle_instance_message)
        _DFP_LOCAL_SERVER = server
    except Exception:
        return True
    return True


def dfp_setup_high_dpi() -> None:
    """高 DPI：Qt6 已默认开启缩放，只需指定取整策略，不要再设已废弃的属性。

    ★ 必须在**创建 QApplication 之前**调用：Qt 会拒绝晚到的设置并往控制台打一行警告
      （`setHighDpiScaleFactorRoundingPolicy must be called before creating the QGuiApplication instance`）。
    """
    policy = dfp_enum_value(
        None,
        lambda: Qt.HighDpiScaleFactorRoundingPolicy.PassThrough,  # type: ignore[attr-defined]
        lambda: Qt.HighDpiScaleFactorRoundingPolicy.PassThrough,  # type: ignore[attr-defined]
    )
    if policy is None:
        return
    for target in (QApplication, QWidget):
        try:
            target.setHighDpiScaleFactorRoundingPolicy(policy)  # type: ignore[attr-defined]
            return
        except Exception:
            continue


# =============================================================================
#  二十四·补、控制台安静模式（v1.1.10）
#  ---------------------------------------------------------------------------
#  为什么：从源码运行（`1.bat`）时，控制台会被第三方日志刷屏，而它们对用户毫无意义：
#    * `qt.multimedia.ffmpeg: Using Qt multimedia with FFmpeg version …`
#    * `[mp3 @ …] Estimating duration from bitrate …` + `Input #0, mp3, from '完整文件路径'`
#      + `Metadata:` / `Duration:` / `Stream #0` 这一整段（还顺带把本机路径打出来了）
#    * **v1.1.30**：`[mp3float @ …] Could not update timestamps for skipped samples.`
#      （判 discarded samples —— 这是 FFmpeg 解码 MP3 的提示，不是错误：它要「跳过 / 丢掉」
#        编解码器补的那几个静音帧（encoder delay / padding）时时间戳对不上就打个招呼。
#        实测**素材2 与 数字\ 那批共 30 个 MP3 没写 Xing/Info 续播头**（自己录的 `鲸宝音频\`
#        那批有 Xing + LAME 头），所以循环播放 / 从头重播短音频时偶尔就会打几行。
#        **音频照放、不影响任何功能**，所以也归噪点）
#  这些都不是错误，看日志文件就够了。所以默认装一个「消息过滤器」把这类丢掉，
#  **真正的警告 / 错误（含 Traceback）一律照旧输出**。
#
#  想看全部输出：设置环境变量 `DFP_VERBOSE=1`，或启动时加 `--verbose`（`-v` / `--debug` 同效）。
#  自己设过 `QT_LOGGING_RULES` 时也不再覆盖，尊重用户的调试环境。
# =============================================================================
DFP_CONSOLE_VERBOSE_ENV = "DFP_VERBOSE"
DFP_CONSOLE_QUIET_RULES = "qt.multimedia.*=false"
DFP_CONSOLE_LOG_MAX_BYTES = 1024 * 1024  # console_*.log 只留档噪点，超过 1 MB 就不再追加
DFP_CONSOLE_NOISE_PATTERNS: Tuple[str, ...] = (
    "Using Qt multimedia with FFmpeg",
    "Estimating duration from bitrate",
    "Input #0",
    "  Metadata:",
    "    encoder ",
    "  Duration: ",
    "  Stream #0",
    "    Stream #",
    "Could not update timestamps for skipped samples",
    "Could not update timestamps for discarded samples",
    "setHighDpiScaleFactorRoundingPolicy must be called",
    "QFontDatabase: Cannot find font directory",
)
_DFP_CONSOLE_HANDLER_INSTALLED = False


def dfp_console_verbose_requested(argv: Sequence[str] = ()) -> bool:
    """要不要保留全部控制台输出：`DFP_VERBOSE=1` 或命令行 `--verbose` / `-v` / `--debug`。"""
    raw = str(os.environ.get(DFP_CONSOLE_VERBOSE_ENV, "") or "").strip().lower()
    if raw in ("1", "true", "yes", "on", "是", "开"):
        return True
    if raw in ("0", "false", "no", "off", "否", "关"):
        return False
    for item in argv or ():
        if str(item).strip().lower() in ("--verbose", "-v", "--debug"):
            return True
    return False


def dfp_console_message_needed(text: str) -> bool:
    """这条 Qt 消息值不值得打到控制台（噪点黑名单，命中就丢）。"""
    body = str(text or "")
    if not body.strip():
        return False
    for pattern in DFP_CONSOLE_NOISE_PATTERNS:
        if pattern in body:
            return False
    return True


def dfp_setup_quiet_console(argv: Sequence[str] = ()) -> bool:
    """让控制台安静下来（默认开）：关掉 Qt/FFmpeg 那几类没必要的日志。

    返回是否真的启用了安静模式（`DFP_VERBOSE=1` 时返回 False = 不过滤）。
    ★ 要在创建 QApplication 之前调用（Qt 的日志规则与消息处理器越早装越好）。
    """
    global _DFP_CONSOLE_HANDLER_INSTALLED
    if dfp_console_verbose_requested(argv):
        return False

    rules = str(os.environ.get("QT_LOGGING_RULES", "") or "").strip()
    if not rules:
        os.environ["QT_LOGGING_RULES"] = DFP_CONSOLE_QUIET_RULES
    try:
        QLoggingCategory.setFilterRules(os.environ["QT_LOGGING_RULES"])
    except Exception:
        pass

    if not _DFP_CONSOLE_HANDLER_INSTALLED:
        def _dfp_console_handler(mode: Any, context: Any, message: str) -> None:  # noqa: N802 - Qt 回调
            try:
                if dfp_console_message_needed(message):
                    sys.stderr.write("%s\n" % message)
                    sys.stderr.flush()
            except Exception:
                pass

        try:
            qInstallMessageHandler(_dfp_console_handler)
            _DFP_CONSOLE_HANDLER_INSTALLED = True
        except Exception:
            pass
    return True


def dfp_console_note(message: str) -> None:
    """「只在非安静模式才说」的控制台提示（默认安静，所以基本不会看到）。"""
    if dfp_console_verbose_requested():
        try:
            print(message)
        except Exception:
            pass


# --- stderr（fd 2）过滤器：拦掉「直接写 fd 2」的第三方噪点 -------------------------------
# FFmpeg 的 `[mp3 @ …] Estimating duration from bitrate …` 是**绕开 Qt、直接写 fd 2** 的，
# Qt 的消息过滤器抓不到；所以把 fd 2 接到管道上，自己读回来过滤：
#   噪点 → 只进日志文件；其它（含 Python Traceback / faulthandler）→ 照旧打到原来的控制台。
_DFP_STDERR_ORIGINAL_FD: Optional[int] = None
_DFP_STDERR_READ_FD: Optional[int] = None
_DFP_STDERR_STOP = threading.Event()
_DFP_STDERR_THREAD: Optional[threading.Thread] = None
_DFP_STDERR_LOG_PATH = ""


def dfp_stderr_filter_active() -> bool:
    return _DFP_STDERR_ORIGINAL_FD is not None


def dfp_stderr_filter_log_path() -> str:
    return _DFP_STDERR_LOG_PATH


def _dfp_stderr_emit(line: bytes, original: int, log_path: str) -> None:
    """一行 stderr：噪点只写日志，其余原样输出（并同样留一份日志）。"""
    text = line.decode("utf-8", "replace").rstrip("\r\n")
    if not text.strip():
        return
    needed = dfp_console_message_needed(text)
    if log_path:
        try:
            # 留档但不让它无限长大（超过上限就不再追加；这些本来只是第三方噪点）
            if not os.path.isfile(log_path) or os.path.getsize(log_path) < DFP_CONSOLE_LOG_MAX_BYTES:
                with open(log_path, "a", encoding="utf-8") as fh:
                    fh.write(text + "\n")
        except Exception:
            pass
    if not needed:
        return
    try:
        os.write(original, (text + "\n").encode("utf-8", "replace"))
    except Exception:
        pass


def dfp_stderr_filter_start(log_path: str = "") -> bool:
    """接管 stderr：把没必要的第三方日志丢掉，其余照旧（并留一份到 `log_path`）。

    只在「非 verbose」时启用；任何一步失败都当作没这回事，绝不影响程序。
    """
    global _DFP_STDERR_ORIGINAL_FD, _DFP_STDERR_READ_FD, _DFP_STDERR_THREAD, _DFP_STDERR_LOG_PATH
    if _DFP_STDERR_ORIGINAL_FD is not None:
        return True
    try:
        original = os.dup(2)
    except Exception:
        return False
    try:
        read_fd, write_fd = os.pipe()
        os.dup2(write_fd, 2)
        os.close(write_fd)
    except Exception:
        try:
            os.close(original)
        except Exception:
            pass
        return False
    try:
        os.set_blocking(read_fd, False)
    except Exception:
        pass
    if log_path:
        try:
            dfp_ensure_dir(os.path.dirname(log_path))
        except Exception:
            log_path = ""

    def _dfp_pump() -> None:
        buffer = bytearray()
        while not _DFP_STDERR_STOP.is_set():
            try:
                chunk = os.read(read_fd, 8192)
            except BlockingIOError:
                time.sleep(0.05)
                continue
            except Exception:
                break
            if not chunk:
                time.sleep(0.05)
                continue
            buffer.extend(chunk)
            while b"\n" in buffer:
                line, _, rest = bytes(buffer).partition(b"\n")
                buffer = bytearray(rest)
                _dfp_stderr_emit(line, original, log_path)
        if buffer:
            _dfp_stderr_emit(bytes(buffer), original, log_path)

    thread = threading.Thread(target=_dfp_pump, name="dfp-stderr-filter", daemon=True)
    _DFP_STDERR_ORIGINAL_FD = original
    _DFP_STDERR_READ_FD = read_fd
    _DFP_STDERR_LOG_PATH = log_path
    _DFP_STDERR_STOP.clear()
    _DFP_STDERR_THREAD = thread
    thread.start()
    return True


def dfp_stderr_filter_stop() -> None:
    """还原 stderr（测试或调试用；程序退出时不需要）。"""
    global _DFP_STDERR_ORIGINAL_FD, _DFP_STDERR_READ_FD, _DFP_STDERR_THREAD
    _DFP_STDERR_STOP.set()
    original = _DFP_STDERR_ORIGINAL_FD
    read_fd = _DFP_STDERR_READ_FD
    _DFP_STDERR_ORIGINAL_FD = None
    _DFP_STDERR_READ_FD = None
    _DFP_STDERR_THREAD = None
    if original is not None:
        try:
            os.dup2(original, 2)
        except Exception:
            pass
    for fd in (read_fd, original):
        if fd is None:
            continue
        try:
            os.close(fd)
        except Exception:
            pass


# =============================================================================
#  二十四点三、修真层一览（★ v1.1.29：道 · 性 · 元神 / 五脏六腑 / 气血 / 命·运·数 / 因果·业·缘 / 神·仙·鬼·妖）
# =============================================================================


class DFPDaoDialog(QDialog):
    """「她的道」一览：六个分页 —— 道行与元神 / 五脏六腑 / 气血 / 命·运·数 / 因果·业·缘 / 神·仙·鬼·妖。

    用户口径（v1.1.29）：「让桌宠拥有 道、性、元神，五脏六腑，气血，命，运，数，因果，业,缘,神,仙,鬼,妖」。
    这十五个概念分成六页看，每一页都是**进度条 + 档位名 + 一句她管什么**，一眼能看出她现在什么状态。
    """

    dfpActionRequested = Signal(str)  # dao_now / dao_reborn（转给控制器）

    def __init__(self, brain: DFPPetBrain, settings: DFPSettingsStore, logger: DFPLogger, parent: Any = None) -> None:
        super().__init__(parent)
        self._dfp_brain = brain
        self._dfp_settings = settings
        self._dfp_logger = logger
        self._dfp_tabs = QTabWidget()
        self._dfp_tabs.setDocumentMode(True)
        self._dfp_bars: Dict[str, Any] = {}
        self._dfp_labels: Dict[str, Any] = {}
        self.setWindowTitle("%s · 她的道" % DFP_APP_TITLE)
        self.setMinimumWidth(620)
        self.setWindowIcon(dfp_app_icon())
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.lbl_head = QLabel("")
        self.lbl_head.setWordWrap(True)
        layout.addWidget(self.lbl_head)

        self._dfp_tabs.addTab(self._dfp_page_self(), "道 · 性 · 元神")
        self._dfp_tabs.addTab(self._dfp_page_zangfu(), "五脏六腑")
        self._dfp_tabs.addTab(self._dfp_page_qixue(), "气血")
        self._dfp_tabs.addTab(self._dfp_page_ming(), "命 · 运 · 数")
        self._dfp_tabs.addTab(self._dfp_page_yinguo(), "因果 · 业 · 缘")
        self._dfp_tabs.addTab(self._dfp_page_four(), "神 · 仙 · 鬼 · 妖")
        layout.addWidget(self._dfp_tabs, 1)

        self.lbl_note = QLabel("")
        self.lbl_note.setWordWrap(True)
        layout.addWidget(self.lbl_note)

        row = QHBoxLayout()
        for text, action in (("💬 让她说一句", "dao_now"), ("♻️ 重定命数", "dao_reborn")):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, name=action: self.dfpActionRequested.emit(name))
            row.addWidget(button)
        row.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        row.addWidget(close_button)
        layout.addLayout(row)
        self.dfp_refresh()

    # --- 六个分页 ---
    def _dfp_make_bar(self, key: str, title: str, tip: str, parent_layout: Any, maximum: int = 100) -> None:
        """一行：「中文名 | 档位 | 进度条」，并把它登记进 `_dfp_bars` / `_dfp_labels`。"""
        row = QHBoxLayout()
        name_label = QLabel(title)
        name_label.setMinimumWidth(56)
        row.addWidget(name_label)
        band_label = QLabel("-")
        band_label.setMinimumWidth(72)
        row.addWidget(band_label)
        bar = QProgressBar()
        bar.setRange(0, max(1, int(maximum)))
        bar.setValue(0)
        bar.setFormat("%v")
        bar.setMinimumWidth(260)
        row.addWidget(bar, 1)
        if tip:
            name_label.setToolTip(tip)
            bar.setToolTip(tip)
        row.addStretch(0)
        parent_layout.addLayout(row)
        self._dfp_bars[key] = bar
        self._dfp_labels[key] = band_label

    def _dfp_page(self) -> Any:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(4)
        return page, layout

    def _dfp_page_self(self) -> Any:
        page, layout = self._dfp_page()
        self.lbl_dao_line = QLabel("")
        self.lbl_dao_line.setWordWrap(True)
        layout.addWidget(self.lbl_dao_line)
        for key, title, tip in (
            ("dao", "道行", "道行值：互动与打坐都会长，境界只升不降"),
            ("xing", "心性", "心性：顽 / 野 / 平 / 静 / 澄（被晾着会变顽，被好好待着会变静）"),
            ("yuanshen", "元神", "元神：未凝 / 微光 / 成形 / 凝实 / 光明（睡觉与打坐养）"),
        ):
            self._dfp_make_bar(key, title, tip, layout, int(DFP_DAO_REALM_MAX) if key == "dao" else 100)
        self.lbl_dao_realm = QLabel("")
        self.lbl_dao_realm.setWordWrap(True)
        layout.addWidget(self.lbl_dao_realm)
        layout.addStretch(1)
        return page

    def _dfp_page_zangfu(self) -> Any:
        page, layout = self._dfp_page()
        self.lbl_zangfu_line = QLabel("")
        self.lbl_zangfu_line.setWordWrap(True)
        layout.addWidget(self.lbl_zangfu_line)
        for name in dfp_dao_zangfu_names():
            self._dfp_make_bar("zangfu:%s" % name, name, dfp_dao_zangfu_zhu(name), layout)
        layout.addStretch(1)
        return page

    def _dfp_page_qixue(self) -> Any:
        page, layout = self._dfp_page()
        self.lbl_qixue_line = QLabel("")
        self.lbl_qixue_line.setWordWrap(True)
        layout.addWidget(self.lbl_qixue_line)
        self._dfp_make_bar("qi", "气", "睡觉补气最多，陪玩会耗气", layout)
        self._dfp_make_bar("blood", "血", "投喂补血最多，夜里会慢慢耗", layout)
        self.lbl_qixue_note = QLabel("")
        self.lbl_qixue_note.setWordWrap(True)
        layout.addWidget(self.lbl_qixue_note)
        layout.addStretch(1)
        return page

    def _dfp_page_ming(self) -> Any:
        page, layout = self._dfp_page()
        self.lbl_ming = QLabel("")
        self.lbl_ming.setWordWrap(True)
        layout.addWidget(self.lbl_ming)
        self._dfp_make_bar("yun", "今日运势", "按日期与本命数算：当天不变、隔天就换", layout, 4)
        self.lbl_shu = QLabel("")
        self.lbl_shu.setWordWrap(True)
        layout.addWidget(self.lbl_shu)
        layout.addStretch(1)
        return page

    def _dfp_page_yinguo(self) -> Any:
        page, layout = self._dfp_page()
        self.lbl_ye = QLabel("")
        self.lbl_ye.setWordWrap(True)
        layout.addWidget(self.lbl_ye)
        self._dfp_make_bar("yuan", "缘分", "陪伴会长缘；缘谱收她这辈子遇到的几种缘", layout)
        self.lbl_yuan = QLabel("")
        self.lbl_yuan.setWordWrap(True)
        layout.addWidget(self.lbl_yuan)
        self.lbl_karma = QLabel("")
        self.lbl_karma.setWordWrap(True)
        layout.addWidget(self.lbl_karma)
        layout.addStretch(1)
        return page

    def _dfp_page_four(self) -> Any:
        page, layout = self._dfp_page()
        self.lbl_four = QLabel("")
        self.lbl_four.setWordWrap(True)
        layout.addWidget(self.lbl_four)
        for key, title, tip in (
            ("shen", "神性", "靠道行与善业长：未通 / 有灵 / 通感 / 神清 / 神明"),
            ("xian", "仙阶", "要「过龙门」且神性 ≥50 才长得动：凡骨 → 位列仙班"),
            ("gui", "鬼气", "夜里长、天亮退；气血虚更容易招阴（睡觉会退）"),
            ("yao", "妖气", "陪玩与撒娇会长，睡觉与静心会退：驯顺 / 微野 / 妖气 / 大妖"),
        ):
            self._dfp_make_bar(key, title, tip, layout)
        layout.addStretch(1)
        return page

    # --- 重画 ---
    def dfp_refresh(self) -> None:
        """按当前道档案重画全部分页（设置窗口与托盘都能点开来）。"""
        brain = self._dfp_brain
        try:
            if hasattr(self, "lbl_head"):
                self.lbl_head.setText(
                    "<b>道</b>：道行与七段境界（鱼 → 鲸 → 过龙门 → 仙）；<b>性</b>：心性五档；<b>元神</b>五阶。<br>"
                    "<b>五脏六腑</b> 十一个各有虚（≤%d）与实（≥%d）；<b>气血</b> 两条（睡补气、食补血）。<br>"
                    "<b>命 · 运 · 数</b>：本命数定命格、每日运势当天不变、定数出生定下终身不变。<br>"
                    "<b>因果 · 业 · 缘</b>：一个「因」过 %d 分钟才结果，结果时改一笔业；<b>神 · 仙 · 鬼 · 妖</b> 四个向。<br>"
                    "%s"
                    % (int(DFP_SPIRIT_LOW), int(DFP_SPIRIT_HIGH), int(DFP_DAO_KARMA_RIPEN_SEC / 60.0),
                       brain.dfp_dao_summary_text().replace("\n", "<br>"))
                )
            if hasattr(self, "lbl_note"):
                self.lbl_note.setText(
                    "文案在 `大肥鱼素材表\\道.json`（改完点设置里的「重新载入道库」即生效）。\n"
                    "★ 这一层不改任何既有数值（饱食 / 精力 / 经验 / 亲密度 / 衰减全不碰）；"
                    "关掉设置里的「让她修」整层都不动。"
                )

            def _set(key, value, maximum=None, text=None):
                bar = self._dfp_bars.get(key)
                label = self._dfp_labels.get(key)
                if bar is not None:
                    if maximum:
                        bar.setRange(0, max(1, int(maximum)))
                    bar.setValue(int(round(max(0.0, dfp_safe_float(value, 0.0)))))
                if label is not None:
                    label.setText(str(text if text is not None else int(round(dfp_safe_float(value, 0.0)))))

            dao_value = brain.dfp_dao_value("dao")
            _set("dao", min(dao_value, DFP_DAO_REALM_MAX), int(DFP_DAO_REALM_MAX), brain.dfp_dao_realm())
            _set("xing", brain.dfp_dao_value("xing"), None, dfp_dao_tier_name("xing", brain.dfp_dao_value("xing")))
            _set("yuanshen", brain.dfp_dao_value("yuanshen"), None,
                 dfp_dao_tier_name("yuanshen", brain.dfp_dao_value("yuanshen")))
            if hasattr(self, "lbl_dao_line"):
                self.lbl_dao_line.setText("<b>道行 %.0f</b>（境界 %s）；今日运势 <b>%s</b>。" % (
                    dao_value, brain.dfp_dao_realm(), brain.dfp_dao_yun_name()))
            if hasattr(self, "lbl_dao_realm"):
                _next_name, _next_left = brain.dfp_dao_realm_next()
                self.lbl_dao_realm.setText(
                    "境界台阶：%s\n%s"
                    % (" → ".join("%s%g" % (item["name"], item["need"]) for item in dfp_dao_realms()),
                       ("下一段「%s」还差 %.0f 道行。" % (_next_name, _next_left)) if _next_name else "已经是最后一段了。"))

            for name in dfp_dao_zangfu_names():
                row = next((item for item in brain.dfp_dao_zangfu_rows() if item["name"] == name), {})
                _set("zangfu:%s" % name, row.get("value", 0.0), None,
                     "%s %s" % (row.get("band_label", ""), int(round(dfp_safe_float(row.get("value"), 0.0)))))
            if hasattr(self, "lbl_zangfu_line"):
                _weak = brain.dfp_dao_zangfu_weakest()
                self.lbl_zangfu_line.setText(
                    "五脏：%s ｜ 六腑：%s<br><b>现在最虚的是「%s」（%s）</b>；"
                    "虚（≤%d）与实（≥%d）都会专门说一句，中间那段是「平」。"
                    % (" / ".join(name for name in DFP_DAO_WUZANG), " / ".join(name for name in DFP_DAO_LIUFU),
                       _weak, dfp_dao_zangfu_zhu(_weak), int(DFP_SPIRIT_LOW), int(DFP_SPIRIT_HIGH)))

            qixue = brain.dfp_dao_qixue()
            _set("qi", qixue["qi"], None, "气 %.0f" % qixue["qi"])
            _set("blood", qixue["blood"], None, "血 %.0f" % qixue["blood"])
            if hasattr(self, "lbl_qixue_line"):
                self.lbl_qixue_line.setText("<b>气血：%s</b>（气 %.0f / 血 %.0f，均值 %.0f）"
                                           % (qixue["name"], qixue["qi"], qixue["blood"], qixue["mean"]))
            if hasattr(self, "lbl_qixue_note"):
                self.lbl_qixue_note.setText(
                    "五档：%s\n睡觉补气最多（+18），投喂补血最多（+6）；陪玩耗气、夜里气血慢慢走下。"
                    % " → ".join(dfp_dao_tier_names("qixue")))

            yun = brain.dfp_dao_yun()
            _set("yun", yun.get("value", 0), 4, str(yun.get("name") or ""))
            if hasattr(self, "lbl_ming"):
                self.lbl_ming.setText(
                    "<b>命</b>：%s（本命数 %d）<br><b>数</b>：%s ｜ 寿数 %d 天 ｜ 缘数 %d"
                    % (brain.dfp_dao_ming_ge(), brain.dfp_dao_shu(), brain.dfp_dao_shu_name(),
                       dfp_safe_int(brain.dfp_dao_birth().get("shou"), 0),
                       dfp_safe_int(brain.dfp_dao_birth().get("yuan"), 0)))
            if hasattr(self, "lbl_shu"):
                self.lbl_shu.setText(
                    "定数（出生定下、终身不变）：%s\n运势五档：%s（今天是「%s」，日期 %s）"
                    % (brain.dfp_dao_ming_line(), " → ".join(dfp_dao_tier_names("yun")),
                       yun.get("name"), yun.get("day")))

            ye = brain.dfp_dao_ye()
            _set("yuan", brain.dfp_dao_value("yuan"), None,
                 dfp_dao_tier_name("yuan", brain.dfp_dao_value("yuan")))
            if hasattr(self, "lbl_ye"):
                self.lbl_ye.setText(
                    "<b>业：%s</b>（善 %.1f － 恶 %.1f ＝ 净 %.1f）<br>业六档：%s"
                    % (ye["name"], ye["good"], ye["bad"], ye["net"], " → ".join(dfp_dao_tier_names("ye"))))
            if hasattr(self, "lbl_yuan"):
                _found = list(brain.dfp_dao_state().get("found_yuan") or [])
                self.lbl_yuan.setText(
                    "<b>缘：%s</b>（缘分数 %.1f）<br>缘谱 %d / %d 类：%s"
                    % (dfp_dao_tier_name("yuan", brain.dfp_dao_value("yuan")), brain.dfp_dao_value("yuan"),
                       len(_found), len(dfp_dao_yuan_kinds()),
                       "、".join("%s%s" % (name, "✓" if name in _found else "✗") for name in dfp_dao_yuan_kinds())))
            if hasattr(self, "lbl_karma"):
                rows = brain.dfp_dao_karma_rows()
                pending = [row for row in rows if not row["done"]]
                done = [row for row in rows if row["done"]]
                self.lbl_karma.setText(
                    "<b>因果</b>：未结 %d 笔 / 已结 %d 笔（一个因过 %d 分钟才结果）<br>未结：%s<br>已结：%s"
                    % (len(pending), len(done), int(DFP_DAO_KARMA_RIPEN_SEC / 60.0),
                       "；".join("%s（还差 %.0f 分）" % (row["cause"], row["left"] / 60.0) for row in pending) or "（没有）",
                       "；".join("%s → %s" % (row["cause"], row["detail"]) for row in done[-4:]) or "（没有）"))

            for key, block in (("shen", "shen"), ("xian", "xian"), ("gui", "gui"), ("yao", "yao")):
                _set(key, brain.dfp_dao_value(key), None, dfp_dao_tier_name(block, brain.dfp_dao_value(key)))
            if hasattr(self, "lbl_four"):
                self.lbl_four.setText(
                    "<b>神 · 仙 · 鬼 · 妖</b>四个向<br>神性 %s ｜ 仙阶 %s ｜ 鬼气 %s ｜ 妖气 %s<br>"
                    "仙阶要「过龙门」且神性 ≥%d 才长得动；鬼气夜里长、气血虚更容易招；妖气陪玩与撒娇长、睡觉与静心退。"
                    % (dfp_dao_tier_name("shen", brain.dfp_dao_value("shen")),
                       dfp_dao_tier_name("xian", brain.dfp_dao_value("xian")),
                       dfp_dao_tier_name("gui", brain.dfp_dao_value("gui")),
                       dfp_dao_tier_name("yao", brain.dfp_dao_value("yao")),
                       int(DFP_DAO_XIAN_GATE_SHEN)))
        except Exception:
            pass


# =============================================================================
#  二十四点四、三魂七魄（★ v1.1.28：她的「精气神」一览）
# =============================================================================

class DFPSpiritsDialog(QDialog):
    """三魂七魄一览：十个魂魄各自的档位（旺/平/弱）、数值、出生命盘、管什么。

    用户口径（v1.1.28）：「给桌宠加三魂七魄」—— 三魂管精神、七魄管身体；
    出生抽一张命盘（3 旺 / 4 平 / 3 弱）终身不变，灵气只涨不跌，跨门槛会说话。
    """

    dfpActionRequested = Signal(str)  # spirits_view / spirits_now / spirits_reborn

    def __init__(self, brain: DFPPetBrain, settings: DFPSettingsStore, logger: DFPLogger, parent: Any = None) -> None:
        super().__init__(parent)
        self._dfp_brain = brain
        self._dfp_settings = settings
        self._dfp_logger = logger
        self._dfp_bars: Dict[str, Any] = {}
        self.setWindowTitle("%s · 三魂七魄" % DFP_APP_TITLE)
        self.setMinimumWidth(560)
        self.setWindowIcon(dfp_app_icon())
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.lbl_head = QLabel("")
        self.lbl_head.setWordWrap(True)
        layout.addWidget(self.lbl_head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        for index, title in enumerate(("组", "魂魄", "管什么", "档位", "灵气")):
            label = QLabel("<b>%s</b>" % title)
            grid.addWidget(label, 0, index)
        for row_index, key in enumerate(DFP_SPIRITS_ALL, start=1):
            grid.addWidget(QLabel(dfp_spirit_group(key)), row_index, 0)
            name_label = QLabel(dfp_spirit_name(key))
            name_label.setMinimumWidth(48)
            grid.addWidget(name_label, row_index, 1)
            sense_label = QLabel(dfp_spirit_sense(key))
            sense_label.setMinimumWidth(110)
            grid.addWidget(sense_label, row_index, 2)
            band_label = QLabel("-")
            band_label.setMinimumWidth(34)
            grid.addWidget(band_label, row_index, 3)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(50)
            bar.setFormat("%v")
            bar.setMinimumWidth(240)
            grid.addWidget(bar, row_index, 4)
            self._dfp_bars[key] = (band_label, bar)
        layout.addLayout(grid)

        self.lbl_note = QLabel("")
        self.lbl_note.setWordWrap(True)
        layout.addWidget(self.lbl_note)

        row = QHBoxLayout()
        for text, action in (("💬 让她说一句", "spirits_now"), ("♻️ 重新投胎", "spirits_reborn")):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, name=action: self.dfpActionRequested.emit(name))
            row.addWidget(button)
        row.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        row.addWidget(close_button)
        layout.addLayout(row)
        self.dfp_refresh()

    def dfp_refresh(self) -> None:
        """按当前档案重画十行（设置窗口与托盘都能点开来）。"""
        try:
            rows = self._dfp_brain.dfp_spirits_rows()
        except Exception:
            rows = []
        for row in rows:
            widgets = self._dfp_bars.get(str(row["key"]))
            if widgets is None:
                continue
            band_label, bar = widgets
            band_label.setText(str(row["band_label"]))
            bar.setValue(int(round(dfp_safe_float(row["value"], 0.0))))
            band = str(row["band"])
            color = "#2E8B57" if band == "high" else ("#B22222" if band == "low" else "#1F4E79")
            bar.setStyleSheet("QProgressBar{color:%s;}" % color)
        summary = ""
        try:
            summary = self._dfp_brain.dfp_spirits_summary_text()
        except Exception:
            summary = ""
        if hasattr(self, "lbl_head"):
            self.lbl_head.setText(
                "<b>三魂管精神</b>（胎光·本命 / 爽灵·灵慧 / 幽精·情感）；"
                "<b>七魄管身体</b>（尸狗·警觉 / 伏矢·吐纳 / 雀阴·亲昵 / 吞贼·吸收 / 非毒·抗性 / 除秽·清洁 / 臭肺·气息）。<br>"
                "<b>命盘</b>是出生时抽的（3 旺 / 4 平 / 3 弱），终身不变；<b>灵气</b>只涨不跌（摸头 / 投喂 / 陪玩 / 睡觉 / 打理都养魂）。<br>"
                "%s" % summary.replace("\n", "<br>")
            )
        if hasattr(self, "lbl_note"):
            self.lbl_note.setText(
                "旺 = ≥%d、弱 = ≤%d：跨过门槛她就会专门说一句；十个都 ≥%d 会说一句「十全」。\n"
                "文案在 `大肥鱼素材表\\魂魄.json`（改完点设置里的「重新载入魂魄库」即生效）。"
                % (int(DFP_SPIRIT_HIGH), int(DFP_SPIRIT_LOW), int(DFP_SPIRIT_ALL_READY))
            )


# =============================================================================
#  二十四点五、状态形象预览（睡觉 / 忙 / 等 / 休息 / 庆祝 / 出错 → 新形象集里的图）
# =============================================================================


class DFPWhaleFormDialog(QDialog):
    """「状态形象」一览：每个状态长什么样 + 什么时候用 + 试一下。

    用户口径（v1.1.15）：「增加素材5，形态是鲸鱼状态，非人形形态，看看在什么时候适合使用。」
    ★ v1.1.36：图源从「素材5 的旧鲸鱼 SVG」换成**新形象集里的对应图**（用户 2026-10-03：
    「怎么还有旧的，不是下架了吗？而且新的已经覆盖了吧。为什么不用新的？」）——
    所以这个窗口里看到的每一张，就是桌宠上真的会画的那一张。
    """

    def __init__(
        self,
        library: DFPAssetLibrary,
        settings: DFPSettingsStore,
        logger: Optional[DFPLogger] = None,
        flash: Optional[Callable[..., bool]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._dfp_library = library
        self._dfp_settings = settings
        self._dfp_logger = logger
        self._dfp_flash = flash
        self._dfp_tried: List[str] = []
        self.setWindowTitle("状态形象（新形象图 · 睡觉 / 忙 / 等 / 休息 / 庆祝 / 出错）")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.lbl_head = QLabel("")
        self.lbl_head.setWordWrap(True)
        layout.addWidget(self.lbl_head)

        self.box_rows = QGroupBox("六种状态")
        self.grid_rows = QGridLayout(self.box_rows)
        self.grid_rows.setContentsMargins(10, 10, 10, 10)
        self.grid_rows.setHorizontalSpacing(10)
        self.grid_rows.setVerticalSpacing(6)
        layout.addWidget(self.box_rows)

        self.lbl_hint = QLabel("")
        self.lbl_hint.setWordWrap(True)
        layout.addWidget(self.lbl_hint)

        row = QHBoxLayout()
        btn_rescan = QPushButton("🔄 重新扫描素材表")
        btn_rescan.clicked.connect(lambda: self.dfp_refresh(True))
        row.addWidget(btn_rescan)
        row.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        row.addWidget(btn_close)
        layout.addLayout(row)

        self._dfp_icon_row = 0
        self.dfp_rebuild()
        self.dfp_refresh()

    # --- 内容 ---
    def dfp_clear_rows(self) -> None:
        while self.grid_rows.count():
            item = self.grid_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def dfp_rebuild(self) -> None:
        """按当前素材库重建每一行（图标 + 名字 + 什么时候用 + 试一下按钮）。"""
        self.dfp_clear_rows()
        items = self._dfp_library.dfp_forms() if self._dfp_library is not None else []
        self._dfp_rows: List[Dict[str, Any]] = []
        for row, item in enumerate(items):
            state = str(item.get("state") or "")
            label = str(item.get("label") or state)
            when = str(item.get("when") or "")
            icon = QLabel()
            pixmap = self._dfp_library.dfp_form_pixmap(state, 56)
            if not pixmap.isNull():
                icon.setPixmap(pixmap.scaled(56, 56, dfp_enum_value(0, lambda: Qt.AspectRatioMode.KeepAspectRatio, lambda: Qt.KeepAspectRatio), dfp_enum_value(0, lambda: Qt.TransformationMode.SmoothTransformation, lambda: Qt.SmoothTransformation)))
            icon.setFixedSize(60, 60)
            icon.setAlignment(dfp_enum_value(0, lambda: Qt.AlignmentFlag.AlignCenter, lambda: Qt.AlignCenter))
            icon.setToolTip("形象：%s（%s）\n%s" % (item.get("file"), item.get("source"), when))
            name = QLabel("<b>%s</b><br/><span style='color:#666'>%s</span>" % (label, state))
            name.setTextFormat(dfp_enum_value(0, lambda: Qt.TextFormat.RichText, lambda: Qt.RichText))
            note = QLabel(when)
            note.setWordWrap(True)
            note.setMinimumWidth(240)
            button = QPushButton("试一下 5 秒")
            button.clicked.connect(lambda _checked=False, key=state: self._dfp_try(key))
            button.setMinimumHeight(28)
            self.grid_rows.addWidget(icon, row, 0)
            self.grid_rows.addWidget(name, row, 1)
            self.grid_rows.addWidget(note, row, 2)
            self.grid_rows.addWidget(button, row, 3)
            self._dfp_rows.append({"state": state, "icon": icon, "button": button})
        self.grid_rows.setColumnStretch(2, 1)

    def dfp_refresh(self, rescan: bool = False) -> None:
        if rescan and self._dfp_library is not None:
            self._dfp_library.dfp_reload()
            self.dfp_rebuild()
        items = self._dfp_library.dfp_forms() if self._dfp_library is not None else []
        packs = self._dfp_library.dfp_form_sources() if self._dfp_library is not None else []
        enabled = bool(self._dfp_settings.dfp_get("whale_form_enabled", True)) if self._dfp_settings is not None else True
        self.lbl_head.setText(
            "下面每个状态用的都是<b>新形象集</b>（<code>鲸宝形象\\类别\\</code>，共 78 张）里的图：<b>%s</b>。<br/>"
            "它们不会取代你在设置里选的基础形象，只在下面这些时候切过去；睡着时会一直挂着（醒了自动回基础形象）。"
            % ("、".join(packs) if packs else "无")
        )
        if not items:
            self.lbl_hint.setText("没有找到形象图 → 检查 <code>鲸宝形象\\类别\\</code> 是否装了那 78 张；"
                                  "缺图的那些状态会被自动跳过。")
        elif not enabled:
            self.lbl_hint.setText("⚠ 现在「状态形象」总开关是关的（设置 → 形象素材 → 忙 / 等 / 休息 / 庆祝 / 出错时换成对应的新形象）——"
                                  "点「试一下」不会有反应，先在设置里打开。")
        else:
            tried = "、".join(DFP_FORM_LABELS.get(key, key) for key in self._dfp_tried[-4:]) or "—"
            self.lbl_hint.setText("共 %d 种；刚才试过：%s" % (len(items), tried))
        self.box_rows.setTitle("六种状态（只有 %d 种）" % len(items) if items else "六种状态")

    def _dfp_try(self, state: str) -> None:
        """点「试一下」：让桌宠真的切成这个形态 5 秒。"""
        ok = False
        if callable(self._dfp_flash):
            try:
                ok = bool(self._dfp_flash(state, 5.0))
            except Exception as exc:
                if self._dfp_logger is not None:
                    self._dfp_logger.dfp_warning("试形态失败（%s）：%s: %s" % (state, type(exc).__name__, exc))
                ok = False
        if ok:
            self._dfp_tried.append(state)
        self.dfp_refresh()

    def dfp_tried(self) -> List[str]:
        return list(self._dfp_tried)


# =============================================================================
#  二十四点六、余额变化记录（「扣钱清单」）
# =============================================================================


class DFPBalanceLogDialog(QDialog):
    """余额变化记录：什么时候、扣了多少（或进了多少）、变化后余额是多少。

    用户口径（v1.1.16）：「增加立刻查询 DS余额，还有查看之前扣钱的列表（记录为 年月日毫秒）。」
    ⇒ 每行的时间就是 `YYYY-MM-DD HH:MM:SS.mmm`（毫秒保留 3 位），扣钱红、进账绿。
    """

    DFP_COLUMNS = ("时间", "变化", "变化后余额", "来源")

    def __init__(self, database: DFPDatabase, settings: DFPSettingsStore, logger: Optional[DFPLogger] = None, parent=None):
        super().__init__(parent)
        self._dfp_database = database
        self._dfp_settings = settings
        self._dfp_logger = logger
        self._dfp_export_path = ""
        self._dfp_center = dfp_enum_value(0, lambda: Qt.AlignmentFlag.AlignCenter, lambda: Qt.AlignCenter)
        self.setWindowTitle("余额变化记录（扣钱清单）")
        self.resize(720, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.lbl_head = QLabel("")
        self.lbl_head.setWordWrap(True)
        layout.addWidget(self.lbl_head)

        self.tbl_log = QTableWidget(0, len(self.DFP_COLUMNS))
        self.tbl_log.setHorizontalHeaderLabels(list(self.DFP_COLUMNS))
        self.tbl_log.setEditTriggers(dfp_enum_value(0, lambda: QAbstractItemView.EditTrigger.NoEditTriggers, lambda: QAbstractItemView.NoEditTriggers))
        self.tbl_log.setSelectionBehavior(dfp_enum_value(0, lambda: QAbstractItemView.SelectionBehavior.SelectRows, lambda: QAbstractItemView.SelectRows))
        self.tbl_log.verticalHeader().setVisible(False)
        header = self.tbl_log.horizontalHeader()
        header.setStretchLastSection(False)
        for index in (0, 1, 2, 3):
            header.setSectionResizeMode(index, dfp_enum_value(0, lambda: QHeaderView.ResizeMode.Interactive, lambda: QHeaderView.Interactive))
        self.tbl_log.setColumnWidth(0, 200)
        self.tbl_log.setColumnWidth(1, 110)
        self.tbl_log.setColumnWidth(2, 150)
        self.tbl_log.setColumnWidth(3, 110)
        layout.addWidget(self.tbl_log, 1)

        row = QHBoxLayout()
        self.chk_spend_only = QCheckBox("只看扣钱")
        self.chk_spend_only.toggled.connect(lambda _checked: self.dfp_refresh())
        row.addWidget(self.chk_spend_only)
        row.addStretch(1)
        btn_refresh = QPushButton("🔄 刷新")
        btn_refresh.clicked.connect(lambda: self.dfp_refresh(True))
        row.addWidget(btn_refresh)
        btn_export = QPushButton("📄 导出 CSV")
        btn_export.clicked.connect(self.dfp_export_csv)
        row.addWidget(btn_export)
        btn_clear = QPushButton("🧹 清空记录")
        btn_clear.clicked.connect(self.dfp_clear)
        row.addWidget(btn_clear)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        row.addWidget(btn_close)
        layout.addLayout(row)

        self.lbl_footer = QLabel("")
        self.lbl_footer.setWordWrap(True)
        layout.addWidget(self.lbl_footer)
        self.dfp_refresh()

    # --- 数据 ---
    def dfp_rows(self) -> List[Dict[str, Any]]:
        """当前该显示的记录（已整理成正负号/颜色）。"""
        try:
            raw = self._dfp_database.dfp_list_balance_log(DFP_BALANCE_LOG_KEEP, bool(self.chk_spend_only.isChecked()))
        except Exception as exc:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("读余额记录失败：%s: %s" % (type(exc).__name__, exc))
            raw = []
        return [dfp_balance_log_row(row) for row in raw]

    def dfp_refresh(self, silent: bool = False) -> None:
        """重新读库画表（`silent=True` 时不清掉导出/清空后的提示）。"""
        rows = self.dfp_rows()
        self.tbl_log.setRowCount(len(rows))
        for index, item in enumerate(rows):
            values = (item["time"], item["delta_text"], item["total_text"], item["source"] or "—")
            for column, text in enumerate(values):
                cell = QTableWidgetItem(str(text))
                if column in (0, 1, 2):
                    cell.setTextAlignment(self._dfp_center)
                if column == 1:
                    # 扣钱红、进账绿 —— 颜色走统一色表（和气泡里的颜色一致）
                    cell.setForeground(QBrush(dfp_color(item["color"])))
                self.tbl_log.setItem(index, column, cell)
        self.chk_spend_only.setText("只看扣钱（%d 条）" % len(rows))
        self.lbl_head.setText(
            "每次「总可用余额」真的变了就记一条，时间精确到毫秒。<br>"
            "查余额走的是 <code>/user/balance</code>，<b>不消耗 Token</b>；最多保留最近 %d 条。" % DFP_BALANCE_LOG_KEEP
        )
        summary = self.dfp_summary(rows)
        if rows or not silent:
            if rows:
                self.lbl_footer.setText(summary)
            else:
                self.lbl_footer.setText(summary + "　——　还没有记录：余额没变过，或者刚清空过")

    def dfp_summary(self, rows: Optional[List[Dict[str, Any]]] = None) -> str:
        """一行汇总：共几条、扣了多少次共多少钱、进了多少次共多少钱、最近一次。"""
        items = self.dfp_rows() if rows is None else list(rows)
        spend = [item for item in items if item["spend"]]
        gain = [item for item in items if not item["spend"] and abs(item["delta"]) > DFP_BALANCE_DELTA_EPSILON]
        currency = (items[0]["currency"] if items else "") or "CNY"
        parts = ["共 %d 条" % len(items)]
        if spend:
            parts.append("扣钱 %d 次合计 -%.2f %s" % (len(spend), sum(abs(item["delta"]) for item in spend), currency))
        if gain:
            parts.append("进账 %d 次合计 +%.2f %s" % (len(gain), sum(item["delta"] for item in gain), currency))
        if items:
            parts.append("最近一次：%s %s" % (items[0]["time"], items[0]["delta_text"]))
        return "｜".join(parts)

    def dfp_clear(self) -> bool:
        count = len(self.dfp_rows())
        if count:
            answer = dfp_confirm(self, "清空余额记录", "要清掉这 %d 条余额变化记录吗？清掉就不能再翻回去了。" % count)
            if answer != dfp_enum_value(0, lambda: QMessageBox.StandardButton.Yes, lambda: QMessageBox.Yes):
                return False
        removed = self._dfp_database.dfp_clear_balance_log()
        self._dfp_export_path = ""
        self.dfp_refresh()
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("余额记录已清空：%d 条" % removed)
        return True

    def dfp_export_csv(self) -> str:
        """导出成 CSV（带 BOM + CRLF，Excel 直接双击不乱码）。"""
        rows = self.dfp_rows()
        if not rows:
            self.lbl_footer.setText("还没有记录可以导出")
            return ""
        lines = ["时间,变化,变化后余额,币种,来源"]
        for item in rows:
            lines.append(
                "%s,%s,%.2f,%s,%s"
                % (item["time"], item["delta_text"], item["total"], item["currency"], item["source"])
            )
        path = os.path.join(dfp_export_dir(), "余额变化记录_%s.csv" % datetime.now().strftime("%Y%m%d_%H%M%S"))
        try:
            dfp_ensure_dir(os.path.dirname(path))
            with open(path, "wb") as handle:
                handle.write(("\r\n".join(lines) + "\r\n").encode("utf-8-sig"))
        except Exception as exc:
            self.lbl_footer.setText("导出失败：%s: %s" % (type(exc).__name__, exc))
            return ""
        self._dfp_export_path = path
        self.lbl_footer.setText("已导出 %d 条 → %s" % (len(rows), path))
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("余额记录已导出：%s" % path)
        return path

    def dfp_export_path(self) -> str:
        return self._dfp_export_path


# =============================================================================
#  二十五、启动画面（加载时也有进度条，不让人对着白屏等）
# =============================================================================


def dfp_splash_override_path(root: str) -> str:
    """显式放的启动图（`启动图.png` 这类）；没放就返回空串。

    ★ v1.1.32：除了素材表根目录，还会找「形象图自己的家」`鲸宝形象\`
      （形象图都搬过去之后，启动图也跟着住那儿）。
    """
    for folder in [str(root or "")] + dfp_character_dirs():
        if not folder:
            continue
        for name in DFP_SPLASH_IMAGE_NAMES:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return path
    return ""


def dfp_splash_image_path(settings: DFPSettingsStore, logger: Optional[DFPLogger] = None) -> str:
    """启动画面该用哪张图（绝对路径；空串＝画手绘的那只）。

    顺序：① 素材表根目录的「启动图.*」（想固定用某张就放这里）
          ② 当前形象立绘（跟桌宠一致，认设置里的 `asset_character`）
    任何一步出错都只记日志、返回空串 —— 启动画面绝不能因为一张图挂掉启动。
    """
    try:
        library = DFPAssetLibrary(settings, logger)
        root = library.dfp_root()
        override = dfp_splash_override_path(root)
        if override:
            return override
        if library.dfp_load():
            item = library.dfp_character()
            relative = str((item or {}).get("file") or "")
            if relative:
                return library.dfp_abs(relative)
        elif logger is not None:
            logger.dfp_info("启动画面没有可用立绘（%s），用手绘兜底图" % library.dfp_last_error())
    except Exception as error:
        if logger is not None:
            logger.dfp_warning("启动画面取图失败，改用手绘兜底图：%s" % error)
    return ""


def dfp_splash_apply_image(splash: "DFPSplashScreen", settings: DFPSettingsStore, logger: Optional[DFPLogger] = None) -> str:
    """把启动画面左边那张图换成真实图片；返回实际用的文件路径（空串＝还是手绘）。"""
    if splash is None:
        return ""
    path = dfp_splash_image_path(settings, logger)
    if not path:
        return ""
    if not splash.dfp_set_image_file(path):
        if logger is not None:
            logger.dfp_warning("启动画面图片读不出来，改用手绘兜底图：%s" % path)
        return ""
    return path


class DFPSplashScreen(QWidget):
    """启动画面：透明无边框，展示形象 + 进度条 + 当前在做什么。"""

    def __init__(self):
        super().__init__(None)
        self._dfp_percent = 0
        self._dfp_started = time.monotonic()
        flags = (
            dfp_enum_value(0, lambda: Qt.WindowType.SplashScreen, lambda: Qt.SplashScreen)
            | dfp_enum_value(0, lambda: Qt.WindowType.FramelessWindowHint, lambda: Qt.FramelessWindowHint)
            | dfp_enum_value(0, lambda: Qt.WindowType.WindowStaysOnTopHint, lambda: Qt.WindowStaysOnTopHint)
        )
        self.setWindowFlags(flags)
        self.setAttribute(dfp_enum_value(0, lambda: Qt.WidgetAttribute.WA_TranslucentBackground, lambda: Qt.WA_TranslucentBackground), True)
        self.setWindowIcon(dfp_app_icon())
        self.setFixedSize(420, 260)
        self._dfp_renderer = DFPPetRenderer(str(DFP_DEFAULT_SETTINGS.get("pet_palette", "ocean")))
        self._dfp_state = DFPRenderState()
        self._dfp_state.time = 0.5
        self._dfp_state.spout = 0.8
        self._dfp_text = "正在准备…"
        self._dfp_pixmap = self._dfp_renderer.dfp_render_pixmap(self._dfp_state, 130, 154)
        # ★ v1.1.17：拿得到真实立绘就画真的，拿不到才画上面这张手绘的
        self._dfp_image: Optional[QPixmap] = None

    def dfp_set_image(self, pixmap: Optional[QPixmap]) -> bool:
        """把左边那张图换成真实立绘（按 `DFP_SPLASH_IMAGE_BOX` 缩放，保持比例）。

        换了之后手绘那张就不再画了；传空 / 读不出来的图返回 False，保持原样。
        """
        if pixmap is None or pixmap.isNull() or pixmap.width() <= 0 or pixmap.height() <= 0:
            return False
        box = QSize(int(DFP_SPLASH_IMAGE_BOX[2]), int(DFP_SPLASH_IMAGE_BOX[3]))
        scaled = pixmap.scaled(
            box,
            dfp_enum_value(0, lambda: Qt.AspectRatioMode.KeepAspectRatio, lambda: Qt.KeepAspectRatio),
            dfp_enum_value(0, lambda: Qt.TransformationMode.SmoothTransformation, lambda: Qt.SmoothTransformation),
        )
        if scaled.isNull():
            return False
        self._dfp_image = scaled
        self.update()
        return True

    def dfp_set_image_file(self, path: str) -> bool:
        """按文件路径换图（读不出来就当没给）。"""
        text = str(path or "")
        if not text or not os.path.isfile(text):
            return False
        return self.dfp_set_image(QPixmap(text))

    def dfp_has_image(self) -> bool:
        """现在画的是不是真实图片（False＝手绘兜底那张）。"""
        return self._dfp_image is not None and not self._dfp_image.isNull()

    def dfp_image_size(self) -> Tuple[int, int]:
        """画出去的图实际多大（诊断 / 测试用）。"""
        if not self.dfp_has_image():
            return (0, 0)
        return (int(self._dfp_image.width()), int(self._dfp_image.height()))

    def dfp_update(self, percent: int, text: str = "") -> None:
        self._dfp_percent = int(dfp_clamp(percent, 0, 100))
        if text:
            self._dfp_text = str(text)
        self.update()
        try:
            QApplication.processEvents()
        except Exception:
            pass

    def dfp_finish(self, minimum_ms: int = 500) -> None:
        self.dfp_update(100, "准备完成，桌宠马上出现～")
        remaining = minimum_ms - int((time.monotonic() - self._dfp_started) * 1000)
        if remaining > 0:
            time.sleep(min(0.5, remaining / 1000.0))
        self.close()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(dfp_enum_value(0, lambda: QPainter.RenderHint.Antialiasing, lambda: QPainter.Antialiasing), True)
            card = QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)
            painter.setPen(dfp_line_pen(dfp_color_alpha(DFP_UI_COLORS["accent"], 200), 1.5))
            painter.setBrush(QBrush(dfp_color_alpha("#FFFFFF", 244)))
            painter.drawRoundedRect(card, 16.0, 16.0)
            image = self._dfp_image
            if image is not None and not image.isNull():
                # 真实立绘：在 DFP_SPLASH_IMAGE_BOX 里居中
                painter.drawPixmap(
                    int(DFP_SPLASH_IMAGE_BOX[0]) + (int(DFP_SPLASH_IMAGE_BOX[2]) - image.width()) // 2,
                    int(DFP_SPLASH_IMAGE_BOX[1]) + (int(DFP_SPLASH_IMAGE_BOX[3]) - image.height()) // 2,
                    image,
                )
            else:
                painter.drawPixmap(16, 46, self._dfp_pixmap)
            painter.setPen(QPen(dfp_color(DFP_UI_COLORS["text"])))
            painter.setFont(dfp_desktop_font(15, True))
            painter.drawText(QRectF(160.0, 24.0, 240.0, 30.0), dfp_text_flags(0x0001, 0x0080), DFP_APP_TITLE)
            painter.setFont(dfp_desktop_font(9))
            painter.setPen(QPen(dfp_color(DFP_UI_COLORS["sub_text"])))
            painter.drawText(QRectF(160.0, 54.0, 240.0, 24.0), dfp_text_flags(0x0001, 0x0080), "版本 v%s" % __version__)
            painter.setFont(dfp_desktop_font(10))
            painter.setPen(QPen(dfp_color(DFP_UI_COLORS["text"])))
            painter.drawText(QRectF(160.0, 86.0, 246.0, 60.0), dfp_text_flags(0x0001, 0x1000), self._dfp_text)
            bar = QRectF(160.0, 156.0, 246.0, 16.0)
            painter.setPen(dfp_no_pen())
            painter.setBrush(QBrush(dfp_color(DFP_UI_COLORS["track"])))
            painter.drawRoundedRect(bar, 8.0, 8.0)
            width = bar.width() * self._dfp_percent / 100.0
            if width > 0:
                painter.setBrush(QBrush(dfp_color(DFP_UI_COLORS["accent"])))
                painter.drawRoundedRect(QRectF(bar.left(), bar.top(), max(8.0, width), bar.height()), 8.0, 8.0)
            painter.setFont(dfp_desktop_font(9))
            painter.setPen(QPen(dfp_color(DFP_UI_COLORS["sub_text"])))
            painter.drawText(QRectF(160.0, 180.0, 246.0, 22.0), dfp_text_flags(0x0001, 0x0080), "加载中 %d%%" % self._dfp_percent)
        finally:
            painter.end()


# =============================================================================
#  二十六、状态面板（四条属性进度条 + 今日统计 + 快捷操作）
# =============================================================================


class DFPStatusWindow(QDialog):
    """桌宠状态：全部用进度条说话，一眼看出它过得怎么样。"""

    dfpActionRequested = Signal(str)

    def __init__(self, settings: DFPSettingsStore, brain: DFPPetBrain, database: DFPDatabase):
        super().__init__(None)
        self._dfp_settings = settings
        self._dfp_brain = brain
        self._dfp_database = database
        self._dfp_bars: Dict[str, QProgressBar] = {}
        self._dfp_labels: Dict[str, QLabel] = {}
        self.setWindowTitle("桌宠状态 — %s" % DFP_APP_TITLE)
        self.setWindowIcon(dfp_app_icon())
        # ★ v1.1.11：最小高度只保证「能滚动」，不再用一个比内容还矮的硬下限
        #   （以前写死 460，而内容实际要 600 多像素 ⇒ 存下来的 520 高会把最下面
        #    一排按钮 / 「窗口总在最前」剪掉，用户看到的就是「下面的字看不全」）。
        self.setMinimumSize(460, 300)
        self._dfp_build_ui()
        self._dfp_timer = QTimer(self)
        self._dfp_timer.setInterval(2000)
        self._dfp_timer.timeout.connect(lambda: self.dfp_refresh(self._dfp_brain.dfp_state_snapshot()))
        self._dfp_timer.start()
        self.dfp_refresh(self._dfp_brain.dfp_state_snapshot())

    def _dfp_build_ui(self) -> None:
        # 内容放在一个单独的 widget 里，外面套滚动区：窗口再矮也不会把字剪掉
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self.lbl_head = QLabel("肥鱼娘现在怎么样啦")
        self.lbl_head.setFont(dfp_desktop_font(13, True))
        root.addWidget(self.lbl_head)

        box = QGroupBox("状态进度条")
        box_layout = QGridLayout(box)
        box_layout.setContentsMargins(10, 10, 10, 10)
        box_layout.setSpacing(8)
        row = 0
        for key in DFP_STAT_KEYS:
            label = QLabel("%s（%s）" % (DFP_STAT_LABELS[key], DFP_STAT_QUESTIONS[key]))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat("%p%")
            bar.setTextVisible(True)
            value_label = QLabel("--")
            value_label.setFixedWidth(70)
            box_layout.addWidget(label, row, 0)
            box_layout.addWidget(bar, row, 1)
            box_layout.addWidget(value_label, row, 2)
            self._dfp_bars[key] = bar
            self._dfp_labels[key] = value_label
            row += 1
        self.bar_exp = QProgressBar()
        self.bar_exp.setRange(0, 100)
        self.bar_exp.setFormat("经验 %v/%m")
        box_layout.addWidget(QLabel("升级进度"), row, 0)
        box_layout.addWidget(self.bar_exp, row, 1)
        self.lbl_exp = QLabel("--")
        self.lbl_exp.setFixedWidth(70)
        box_layout.addWidget(self.lbl_exp, row, 2)
        root.addWidget(box)

        box2 = QGroupBox("概况")
        box2_layout = QVBoxLayout(box2)
        box2_layout.setContentsMargins(10, 10, 10, 10)
        self.lbl_summary = QLabel("")
        self.lbl_summary.setWordWrap(True)
        box2_layout.addWidget(self.lbl_summary)
        self.bar_today = QProgressBar()
        self.bar_today.setRange(0, 100)
        self.bar_today.setFormat("今日互动 %v 次")
        box2_layout.addWidget(self.bar_today)
        root.addWidget(box2)

        box3 = QGroupBox("快捷操作")
        box3_layout = QGridLayout(box3)
        box3_layout.setContentsMargins(10, 10, 10, 10)
        box3_layout.setSpacing(6)
        actions = (
            ("🐟 投喂小鱼干", "feed"),
            ("💧 喝点水", "drink"),
            ("🎮 陪它玩", "play"),
            ("✋ 摸摸头", "pet"),
            ("😴 睡 / 醒", "sleep"),
            ("💬 打开聊天", "chat"),
            ("🧰 内置修改器", "trainer"),
            ("🎬 自己找个动作", "behavior"),
            ("🌙 做个梦", "dream"),
            ("⚙️ 打开设置", "settings"),
            ("🎯 移到屏幕中央", "center_pet"),
        )
        for index, (text, name) in enumerate(actions):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, action=name: self.dfpActionRequested.emit(action))
            # ★ 给个最小高度：窗口矮的时候按钮会被压扁，里面的字就“看不全”了
            button.setMinimumHeight(32)
            box3_layout.addWidget(button, index // 2, index % 2)
        root.addWidget(box3)

        self.chk_top = QCheckBox("窗口总在最前")
        self.chk_top.toggled.connect(lambda checked, name="status_window_on_top": self._dfp_set_on_top(checked))
        root.addWidget(self.chk_top)
        root.addStretch(1)

        # 外层：滚动区（内容比窗口高时出现滚动条，而不是把底部剪掉）
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(dfp_enum_value(0, lambda: QFrame.Shape.NoFrame, lambda: QFrame.NoFrame))
        self.area.setHorizontalScrollBarPolicy(dfp_enum_value(0, lambda: Qt.ScrollBarPolicy.ScrollBarAsNeeded, lambda: Qt.ScrollBarAsNeeded))
        self.area.setWidget(content)
        outer.addWidget(self.area)
        self._dfp_content = content

    def _dfp_set_on_top(self, enabled: bool) -> None:
        try:
            flags = self.windowFlags()
            hint = dfp_enum_value(0, lambda: Qt.WindowType.WindowStaysOnTopHint, lambda: Qt.WindowStaysOnTopHint)
            self.setWindowFlags(flags | hint if enabled else flags & ~hint)
            self.show()
        except Exception:
            pass

    def dfp_refresh(self, snapshot: Dict[str, Any]) -> None:
        snapshot = snapshot or {}
        for key in DFP_STAT_KEYS:
            value = dfp_safe_float(snapshot.get(key), 0.0)
            bar = self._dfp_bars[key]
            bar.setValue(int(value))
            bar.setFormat("%.0f%%" % value)
            self._dfp_labels[key].setText("%.0f / 100" % value)
        need = max(1, dfp_safe_int(snapshot.get("exp_needed"), 50))
        exp = dfp_safe_int(snapshot.get("exp"), 0)
        self.bar_exp.setRange(0, need)
        self.bar_exp.setValue(min(need, exp))
        self.bar_exp.setFormat("经验 %d/%d" % (exp, need))
        self.lbl_exp.setText("Lv%d" % dfp_safe_int(snapshot.get("level"), 1))
        self.lbl_head.setText("%s：Lv%d · %s%s" % (
            self._dfp_settings.dfp_get("pet_nickname", DFP_DEFAULT_NICKNAME),
            dfp_safe_int(snapshot.get("level"), 1),
            snapshot.get("level_title", ""),
            "（睡觉中）" if snapshot.get("asleep") else "",
        ))
        today = {}
        try:
            today = self._dfp_database.dfp_daily_today()
        except Exception:
            today = {}
        parts = [
            "首次见面：%s" % dfp_fmt_ts(dfp_safe_float(snapshot.get("born_at"), 0.0)),
            "累计：摸头 %d 次｜投喂 %d 次｜聊天 %d 次｜token %s"
            % (
                dfp_safe_int(snapshot.get("total_pets"), 0),
                dfp_safe_int(snapshot.get("total_feeds"), 0),
                dfp_safe_int(snapshot.get("total_chats"), 0),
                dfp_human_number(dfp_safe_int(snapshot.get("total_tokens"), 0)),
            ),
            "今日：互动 %d 次｜投喂 %d｜聊天 %d｜token %s"
            % (
                dfp_safe_int(today.get("pets", 0), 0) + dfp_safe_int(today.get("plays", 0), 0),
                dfp_safe_int(today.get("feeds", 0), 0),
                dfp_safe_int(today.get("chats", 0), 0),
                dfp_human_number(dfp_safe_int(today.get("tokens", 0), 0)),
            ),
            "上次互动：%s" % dfp_fmt_ts(dfp_safe_float(snapshot.get("last_interaction"), 0.0)),
        ]
        self.lbl_summary.setText("\n".join(parts))
        total_today = dfp_safe_int(today.get("pets", 0), 0) + dfp_safe_int(today.get("plays", 0), 0) + dfp_safe_int(today.get("feeds", 0), 0)
        self.bar_today.setRange(0, max(10, total_today))
        self.bar_today.setValue(total_today)
        self.bar_today.setFormat("今日互动 %d 次" % total_today)

    def dfp_needed_size(self) -> QSize:
        """把内容完整显示出来需要多大（用来避免「存下来的窗口太小 ⇒ 字被剪」）。"""
        try:
            hint = self._dfp_content.sizeHint() if hasattr(self, "_dfp_content") else self.sizeHint()
        except Exception:
            hint = QSize(0, 0)
        width = max(520, int(hint.width()) + 24)
        height = max(420, int(hint.height()) + 8)
        return QSize(width, height)

    def dfp_restore_geometry(self) -> None:
        """恢复几何：**尺寸以「装得下全部内容」为准**，位置沿用既有行为（居中）。

        ★ v1.1.11 根因：`dfp_safe_window_geometry(..., always_center=True)` 会直接用
          传进去的 `default_geometry` 当尺寸（居中时不用保存的那个尺寸），
          而这里以前传的是写死的 560×520 —— 而内容要 600 多像素
          ⇒ 每次打开底部都被剪掉（用户报的「下面的字看不全」就是这么来的）。
        """
        need = self.dfp_needed_size()
        rect = dfp_safe_window_geometry(
            self._dfp_settings.dfp_get("status_window_geometry", ""),
            QRect(0, 0, max(560, need.width()), max(520, need.height())),
            always_center=True,
        )
        dfp_place_window(self, rect)
        if self._dfp_settings.dfp_get("status_window_maximized", False):
            self.showMaximized()

    def dfp_save_geometry(self) -> None:
        self._dfp_settings.dfp_set("status_window_maximized", bool(self.isMaximized()))
        rect = dfp_window_geometry_for_save(self, QRect(0, 0, 560, 640))
        self._dfp_settings.dfp_set("status_window_geometry", dfp_geometry_to_text(rect))

    def dfp_show_and_raise(self, snapshot: Optional[Dict[str, Any]] = None) -> None:
        if not self.isVisible():
            self.dfp_restore_geometry()
        self.dfp_refresh(snapshot or self._dfp_brain.dfp_state_snapshot())
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._dfp_timer.stop()
        self.dfp_save_geometry()
        super().closeEvent(event)


# =============================================================================
#  二十七、关于窗口
# =============================================================================


class DFPAboutDialog(QDialog):
    def __init__(self, settings: DFPSettingsStore, database: DFPDatabase, client: DFPDeepSeekClient, brain: DFPPetBrain):
        super().__init__(None)
        self.setWindowTitle("关于 — %s" % DFP_APP_TITLE)
        self.setWindowIcon(dfp_app_icon())
        self.setMinimumSize(620, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        stats = database.dfp_statistics()
        api_stats = client.dfp_statistics()
        info = (
            "<p><b>版本</b>：v%s　<b>Python</b>：%s　<b>PySide6</b>：%s</p>"
            "<p><b>程序目录</b>：%s<br><b>数据目录</b>：%s<br><b>设置文件</b>：%s<br><b>数据库</b>：%s（%s）<br><b>图标</b>：%s</p>"
            "<p><b>桌宠</b>：%s</p>"
            "<p><b>接口</b>：%s，Key %d 个，请求 %d 次（成功 %d / 失败 %d）</p>"
            % (
                __version__,
                sys.version.split()[0],
                getattr(__import__("PySide6"), "__version__", "?"),
                dfp_app_dir(),
                dfp_data_dir(),
                settings.dfp_path(),
                stats["db_path"],
                dfp_human_bytes(stats["db_size"]),
                dfp_icon_path() or "未找到 icon.ico（使用程序内绘制图标）",
                brain.dfp_status_text(),
                api_stats["api_base"],
                api_stats["key_count"],
                api_stats["requests"],
                api_stats["success"],
                api_stats["failed"],
            )
        )
        view.setHtml(info + "<hr>" + DFP_HELP_HTML)
        layout.addWidget(view, 1)
        buttons = QDialogButtonBox(dfp_enum_value(0, lambda: QDialogButtonBox.StandardButton.Close, lambda: QDialogButtonBox.Close))
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


# =============================================================================
#  二十八、总控制器（把窗口、菜单、托盘、定时器、后台任务串起来）
# =============================================================================


class DFPAppController(QObject):
    """一切交互的总调度：所有 UI 操作都在主线程完成。"""

    def __init__(
        self,
        app: QApplication,
        logger: DFPLogger,
        settings: DFPSettingsStore,
        database: DFPDatabase,
        brain: DFPPetBrain,
        client: DFPDeepSeekClient,
        chat_manager: DFPChatManager,
        backup_manager: DFPBackupManager,
        splash: Optional[DFPSplashScreen] = None,
    ):
        super().__init__()
        self._dfp_app = app
        self._dfp_logger = logger
        self._dfp_settings = settings
        self._dfp_database = database
        self._dfp_brain = brain
        self._dfp_client = client
        self._dfp_chat = chat_manager
        self._dfp_backups = backup_manager
        self._dfp_splash = splash
        self._dfp_pet: Optional[DFPPetWidget] = None
        self._dfp_bubble: Optional[DFPBubbleWidget] = None
        self._dfp_chat_window: Optional[DFPChatWindow] = None
        self._dfp_settings_window: Optional[DFPSettingsDialog] = None
        self._dfp_status_window: Optional[DFPStatusWindow] = None
        self._dfp_trainer_window: Optional[DFPTrainerDialog] = None
        # ★ v1.1.28：三魂七魄一览窗口（惰性建，关掉后保留引用）
        self._dfp_spirits_window: Optional[DFPSpiritsDialog] = None
        # ★ v1.1.29：「她的道」一览窗口
        self._dfp_dao_window: Optional[DFPDaoDialog] = None
        self._dfp_assets = DFPAssetLibrary(settings, logger)
        self._dfp_voice = DFPVoicePlayer(settings, self._dfp_assets, logger)
        # ★ v1.1.18：国标麻将算番 Web 版（手机 / 平板算番 + 桌宠读番）
        self._dfp_mahjong = DFPMahjongWeb(settings, logger, self)
        self._dfp_mahjong.dfpScored.connect(self._dfp_on_mahjong_score)
        self._dfp_number_speaker = DFPNumberSpeaker(settings, logger, self)
        self._dfp_mahjong_dialog: Optional[DFPMahjongWebDialog] = None
        # ★ v1.1.21：算番回传可能很密 —— 气泡立刻更新，念番数等它停一下（见 `_dfp_read_pending_fan`）
        self._dfp_pending_fan: Optional[int] = None
        self._dfp_fan_reading: Optional[int] = None
        self._dfp_fan_read_timer = QTimer(self)
        self._dfp_fan_read_timer.setSingleShot(True)
        self._dfp_fan_read_timer.setInterval(max(100, dfp_safe_int(DFP_MAHJONG_READ_DELAY_MS, 900)))
        self._dfp_fan_read_timer.timeout.connect(self._dfp_read_pending_fan)
        self._dfp_tray: Optional[QSystemTrayIcon] = None
        self._dfp_menu: Optional[QMenu] = None
        self._dfp_quitting = False
        self._dfp_pet_visible_before_hide = True
        self._dfp_walk_target_x = 0
        self._dfp_walk_direction = 0
        self._dfp_last_geometry_text = ""
        self._dfp_session_started = dfp_now_ts()
        self._dfp_task_busy = ""
        self._dfp_workers: List[DFPTaskWorker] = []
        self._dfp_task_signals = DFPTaskSignals()
        self._dfp_task_pool = QThreadPool(self)
        self._dfp_task_pool.setMaxThreadCount(2)
        self._dfp_task_signals.dfpTaskProgress.connect(self._dfp_on_controller_task_progress)
        self._dfp_task_signals.dfpTaskDone.connect(self._dfp_on_controller_task_done)
        self._dfp_task_signals.dfpTaskFailed.connect(self._dfp_on_controller_task_failed)

        # 定时器：自动备份 / 自动走动 / 跟随鼠标 / 走位动画 / 几何保存
        self._dfp_backup_timer = QTimer(self)
        self._dfp_backup_timer.timeout.connect(lambda: self.dfp_run_backup("自动备份"))
        self._dfp_walk_timer = QTimer(self)
        self._dfp_walk_timer.setInterval(int(dfp_safe_float(DFP_DEFAULT_SETTINGS["auto_walk_interval_sec"], 45) * 1000))
        self._dfp_walk_timer.timeout.connect(self._dfp_begin_auto_walk)
        self._dfp_motion_timer = QTimer(self)
        self._dfp_motion_timer.setInterval(40)
        self._dfp_motion_timer.timeout.connect(self._dfp_on_motion_tick)
        self._dfp_follow_timer = QTimer(self)
        self._dfp_follow_timer.setInterval(320)
        self._dfp_follow_timer.timeout.connect(self._dfp_on_follow_tick)
        self._dfp_geometry_timer = QTimer(self)
        self._dfp_geometry_timer.setInterval(10000)
        self._dfp_geometry_timer.timeout.connect(self._dfp_save_pet_geometry)

        # ★ v1.1.12：余额变化提醒（查「总可用余额」→ 变了就在气泡里说一句：少了红、多了绿）
        self._dfp_last_balance: Optional[float] = None
        self._dfp_last_balance_currency = ""
        self._dfp_balance_busy = False
        # ★ v1.1.16：手动查询 / 记账用的现场信息
        self._dfp_balance_manual = False
        self._dfp_balance_source = DFP_BALANCE_LOG_SOURCE_AUTO
        self._dfp_balance_last_total = 0.0
        self._dfp_balance_last_currency = ""
        self._dfp_balance_last_delta = 0.0
        self._dfp_balance_log_dialog: Optional[DFPBalanceLogDialog] = None
        self._dfp_balance_signals = DFPTaskSignals(self)
        self._dfp_balance_signals.dfpTaskDone.connect(self._dfp_on_balance_checked)
        self._dfp_balance_signals.dfpTaskFailed.connect(self._dfp_on_balance_failed)
        self._dfp_balance_timer = QTimer(self)
        self._dfp_balance_timer.timeout.connect(self.dfp_check_balance_change)
        # ★ v1.1.14：试听「提示音」按钮的轮换游标（一次一个，6 类轮着听）
        self._dfp_notice_audition_index = 0
        # ★ v1.1.15：状态形象预览窗口（控制器留一个引用，免得被 GC）
        self._dfp_form_dialog: Optional[DFPWhaleFormDialog] = None

    # --- 构建 ---
    def dfp_build(self) -> None:
        # ★ 台词库：优先读 `台词库.json`（改了 JSON 不用改代码、不用重新编译）
        dfp_lines_load(self._dfp_settings, force=True)
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("台词库已载入：%s" % dfp_lines_summary())
            if dfp_lines_error():
                self._dfp_logger.dfp_warning("台词库 JSON 有问题，已回退内置：%s" % dfp_lines_error())
        # ★ 等级称号：优先读 `等级.json`（同上，改了不用重编译）
        dfp_levels_load(self._dfp_settings, force=True)
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("等级称号已载入：%s" % dfp_levels_summary())
            if dfp_levels_error():
                self._dfp_logger.dfp_warning("等级.json 有问题，已回退内置称号：%s" % dfp_levels_error())
        # ★ 梦境库：优先读 `梦境.json`（同上，梦的骨架可以自己改）
        dfp_dreams_load(self._dfp_settings, force=True)
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("梦境库已载入：%s" % dfp_dreams_summary())
            if dfp_dreams_error():
                self._dfp_logger.dfp_warning("梦境.json 有问题，已回退内置：%s" % dfp_dreams_error())
        # ★ v1.1.28 魂魄库：优先读 `魂魄.json`（三魂七魄 的 旺 / 平 / 弱 各自文案）
        dfp_spirits_load(self._dfp_settings, force=True)
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("魂魄库已载入：%s" % dfp_spirits_summary())
            if dfp_spirits_error():
                self._dfp_logger.dfp_warning("魂魄.json 有问题，已回退内置：%s" % dfp_spirits_error())
        # ★ v1.1.29 道库：优先读 `道.json`（道行境界 / 五脏六腑 / 因果 / 神鬼妖怪 的文案）
        dfp_dao_load(self._dfp_settings, force=True)
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("道库已载入：%s" % dfp_dao_summary())
            if dfp_dao_error():
                self._dfp_logger.dfp_warning("道.json 有问题，已回退内置：%s" % dfp_dao_error())
        self._dfp_assets.dfp_load()
        self._dfp_pet = DFPPetWidget(self._dfp_logger)
        self._dfp_pet.dfp_renderer().dfp_set_palette(str(self._dfp_settings.dfp_get("pet_palette", "ocean")))
        self._dfp_pet.dfp_set_asset_library(self._dfp_assets)
        self._dfp_pet.dfp_set_render_mode(str(self._dfp_settings.dfp_get("pet_render_mode", "assets")))
        self._dfp_bubble = DFPBubbleWidget()
        self._dfp_bubble.dfp_attach(lambda: self._dfp_pet.dfp_bubble_anchor_rect() if self._dfp_pet else QRect())

        self._dfp_pet.dfpClicked.connect(self.dfp_on_pet_clicked)
        self._dfp_pet.dfpDoubleClicked.connect(self.dfp_on_pet_double_clicked)
        self._dfp_pet.dfpWheelZoom.connect(self.dfp_on_pet_wheel)
        self._dfp_pet.dfpPressed.connect(self._dfp_on_pet_pressed)
        self._dfp_pet.dfpDragStarted.connect(self._dfp_on_pet_drag_started)
        self._dfp_pet.dfpContextMenu.connect(self.dfp_on_pet_context_menu)
        self._dfp_pet.dfpMoved.connect(self._dfp_on_pet_moved)
        self._dfp_pet.dfpDragFinished.connect(self.dfp_on_pet_drag_finished)
        self._dfp_pet.dfpActionFinished.connect(self._dfp_on_action_finished)

        self._dfp_brain.dfpSaid.connect(self.dfp_on_brain_say)
        self._dfp_brain.dfpStateChanged.connect(self._dfp_on_state_changed)
        self._dfp_brain.dfpLevelUp.connect(self._dfp_on_level_up)
        self._dfp_brain.dfpExpressionWanted.connect(self._dfp_on_brain_expression)
        self._dfp_brain.dfpActionWanted.connect(self._dfp_on_brain_action)
        self._dfp_brain.dfpBehaviorWanted.connect(self._dfp_on_behavior_wanted)
        self._dfp_brain.dfpEventLogged.connect(self._dfp_on_brain_event)
        self._dfp_brain.dfpSleepChanged.connect(self._dfp_on_sleep_changed)
        self._dfp_brain.dfp_set_behavior_provider(self._dfp_pick_behavior)

        self._dfp_chat.dfp_set_history_provider(lambda session_id, rounds: self._dfp_database.dfp_recent_dialogue(session_id, rounds))
        self._dfp_settings.dfpChanged.connect(self.dfp_on_setting_changed)

        self._dfp_build_shortcuts()
        self._dfp_build_pet_menu()
        self._dfp_build_tray()
        dfp_set_raise_callback(self.dfp_handle_raise)
        # ★ v1.1.14：弹确认框前先叫一声（素材包里的 notice-approval）
        dfp_set_confirm_notice(self._dfp_play_notice_approval)

    def _dfp_build_shortcuts(self) -> None:
        bindings = (
            ("Ctrl+O", self.dfp_open_chat),
            ("Ctrl+,", self.dfp_open_settings),
            ("Ctrl+H", self.dfp_toggle_pet_visible),
            ("Ctrl+L", self.dfp_toggle_lock),
            ("Ctrl+B", self.dfp_query_balance_now),
            ("Ctrl+Shift+S", lambda: self.dfp_run_backup("手动备份")),
            ("Ctrl+Alt+C", self.dfp_center_pet),
            ("Ctrl+Shift+P", self.dfp_open_status),
            ("Ctrl+Q", self.dfp_request_quit),
        )
        self._dfp_shortcuts = []
        for keys, handler in bindings:
            try:
                shortcut = QShortcut(QKeySequence(keys), self._dfp_pet)
                shortcut.activated.connect(handler)
                self._dfp_shortcuts.append(shortcut)
            except Exception:
                continue

    def _dfp_build_pet_menu(self) -> None:
        """右键菜单：常用三项留在外面，其余按用途收进子菜单（v1.1.16）。

        ★ 铁律：**第一个 action 必须是那个灰掉的头（昵称 + 等级）** ——
          `dfp_apply_all_settings()` 直接改 `self._dfp_menu.actions()[0]` 的文字，
          换昵称 / 升级才会即时生效；以后重构菜单别把这一条挤掉。
        ★ 托盘用的是同一个 QMenu 对象（见 `_dfp_build_tray`），所以改这里两个一起变。
        """
        menu = QMenu()
        nickname = str(self._dfp_settings.dfp_get("pet_nickname", DFP_DEFAULT_NICKNAME))
        menu.addAction("🐟 %s　Lv%d" % (dfp_truncate_text(nickname, 10), dfp_safe_int(self._dfp_brain.dfp_state_value("level", 1), 1))).setEnabled(False)
        menu.addSeparator()
        # —— 天天要点的三样，留在最外面 ——
        menu.addAction("💬　聊天…\tCtrl+O", self.dfp_open_chat)
        menu.addAction("📊　状态面板…\tCtrl+Shift+P", self.dfp_open_status)
        menu.addAction("⚙️　设置…\tCtrl+,", self.dfp_open_settings)
        menu.addSeparator()

        # —— 🐾 互动 ——
        sub_act = menu.addMenu("🐾　互动")
        sub_act.addAction("🐟　投喂小鱼干", self.dfp_feed)
        sub_act.addAction("💧　喝点水", self.dfp_drink)
        sub_act.addAction("🎮　陪它玩", self.dfp_play)
        sub_act.addAction("✋　摸摸头", self.dfp_pet_head)
        sub_act.addAction("💗　撒娇一下", self.dfp_coax)
        sub_act.addAction("😴　睡觉 / 叫醒", self.dfp_toggle_sleep)
        sub_act.addAction("🌙　做个梦（立即）", self.dfp_dream_now)
        sub_act.addAction("💗　让她说说心里话", self.dfp_soul_now)
        sub_act.addAction("🪷　看看她的三魂七魄…", self.dfp_open_spirits_dialog)
        sub_act.addAction("💬　魂魄想说一句", self.dfp_spirits_now)
        sub_act.addAction("☯️　看看她的道…", self.dfp_open_dao_dialog)
        sub_act.addAction("💬　道想说一句", self.dfp_dao_now)

        # —— 🪄 表演（素材动作）——
        sub_show = menu.addMenu("🪄　表演（素材）")
        sub_show.addAction("🎲　随便做个动作", self.dfp_random_behavior)
        sub_show.addAction("🎬　看它自己找事做（立即触发）", self.dfp_trigger_behavior)

        # —— 🐋 形象 ——
        sub_look = menu.addMenu("🐋　形象")
        sub_look.addAction("🔮　换个形象（素材包）", self.dfp_cycle_character)
        sub_look.addAction("🐋　状态形象（新形象图）…", self.dfp_open_form_dialog)
        sub_look.addAction("🎨　换配色（代码绘制时生效）", self._dfp_cycle_palette)
        sub_look.addAction("🔄　水平翻转", self._dfp_toggle_flip)
        sub_look.addSeparator()
        sub_look.addAction("🔍　放大 10%", lambda: self.dfp_zoom_by(1))
        sub_look.addAction("🔎　缩小 10%", lambda: self.dfp_zoom_by(-1))
        sub_look.addSeparator()
        sub_look.addAction("💬　气泡开关", self._dfp_toggle_bubble)
        sub_look.addAction("🔊　声音开关", self._dfp_toggle_voice)

        # —— 💰 余额与 AI（v1.1.16 新增：立刻查一次 / 翻以前的扣钱记录）——
        sub_money = menu.addMenu("💰　余额与 AI")
        sub_money.addAction("🔎　立即查询 DS 余额\tCtrl+B", self.dfp_query_balance_now)
        sub_money.addAction("📉　扣钱记录…（余额变化清单）", self.dfp_open_balance_log_dialog)

        # —— 🪟 窗口 ——
        sub_win = menu.addMenu("🪟　窗口")
        sub_win.addAction("📌　窗口置顶开关", self._dfp_toggle_always_on_top)
        sub_win.addAction("🎯　回到屏幕中央\tCtrl+Alt+C", self.dfp_center_pet)
        sub_win.addAction("📐　移到右下角", self.dfp_move_pet_to_corner)
        sub_win.addAction("🔒　锁定 / 解锁位置\tCtrl+L", self.dfp_toggle_lock)
        sub_win.addAction("👁　隐藏 / 显示\tCtrl+H", self.dfp_toggle_pet_visible)

        # —— 🧰 工具与维护 ——
        sub_tool = menu.addMenu("🧰　工具")
        sub_tool.addAction("🧰　内置修改器…（拉满 / 锁定）", self.dfp_open_trainer)
        sub_tool.addAction("🔄　重新扫描素材表", self.dfp_scan_assets)
        sub_tool.addAction("📜　重新载入台词库（台词库.json）", self.dfp_reload_lines)
        sub_tool.addAction("📛　重新载入等级称号（等级.json）", self.dfp_reload_levels)
        sub_tool.addAction("🌀　重新载入梦境库（梦境.json）", self.dfp_reload_dreams)
        sub_tool.addAction("🫧　重新载入灵魂库（灵魂.json）", self.dfp_reload_soul)
        sub_tool.addAction("📜　重新载入魂魄库（魂魄.json）", self.dfp_reload_spirits)
        sub_tool.addAction("♻️　重新投胎（重抽命盘）", self.dfp_spirits_reborn)
        sub_tool.addAction("📜　重新载入道库（道.json）", self.dfp_reload_dao)
        sub_tool.addAction("♻️　重定命数（重抽定数）", self.dfp_dao_reborn)
        sub_tool.addSeparator()
        sub_tool.addAction("📂　打开素材目录", lambda: self._dfp_open_path(self._dfp_assets.dfp_root()))
        # ★ v1.1.32：形象图有了自己的家 —— 给一个直达入口（与「打开素材目录」并列）
        sub_tool.addAction("🖼　打开形象目录（鲸宝形象）", lambda: self._dfp_open_path(dfp_character_dir_path()))
        sub_tool.addAction("📂　打开数据目录", self.dfp_open_data_dir)
        sub_tool.addAction("💾　立即备份\tCtrl+Shift+S", lambda: self.dfp_run_backup("手动备份"))
        sub_tool.addSeparator()
        sub_tool.addAction("🀄　算番 Web 版…（手机也能用）", self.dfp_open_mahjong_dialog)
        sub_tool.addSeparator()
        sub_tool.addAction("ℹ️　关于", self.dfp_show_about)

        menu.addSeparator()
        menu.addAction("❌　退出程序\tCtrl+Q", self.dfp_request_quit)
        self._dfp_menu = menu

    def _dfp_build_tray(self) -> None:
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                self._dfp_logger.dfp_warning("系统托盘不可用，托盘菜单将被跳过")
                return
            tray = QSystemTrayIcon(dfp_app_icon(), self)
            tray.setToolTip("%s v%s" % (DFP_APP_TITLE, __version__))
            tray.setContextMenu(self._dfp_menu)
            tray.activated.connect(self._dfp_on_tray_activated)
            tray.show()
            self._dfp_tray = tray
        except Exception:
            self._dfp_logger.dfp_exception("创建托盘图标失败")

    def _dfp_on_tray_activated(self, reason: Any) -> None:
        trigger = dfp_enum_value(None, lambda: QSystemTrayIcon.ActivationReason.Trigger, lambda: QSystemTrayIcon.Trigger)
        double = dfp_enum_value(None, lambda: QSystemTrayIcon.ActivationReason.DoubleClick, lambda: QSystemTrayIcon.DoubleClick)
        if reason in (trigger, double):
            self.dfp_toggle_pet_visible()

    # --- 启动 ---
    def dfp_start(self) -> None:
        self.dfp_apply_all_settings()
        gap = self._dfp_session_gap()
        geometry = self._dfp_default_pet_geometry(self._dfp_pet)
        dfp_place_window(self._dfp_pet, geometry)
        self._dfp_last_geometry_text = dfp_geometry_to_text(geometry)
        self._dfp_pet.show()
        dfp_ensure_window_on_screen(self._dfp_pet)
        self._dfp_pet.dfp_apply_scale(float(self._dfp_settings.dfp_get("pet_scale", 1.0)), keep_anchor=False)
        self._dfp_brain.dfp_start()
        self._dfp_brain.dfp_save_state(force=True)
        self._dfp_settings.dfp_set("stat_total_minutes", dfp_safe_int(self._dfp_settings.dfp_get("stat_total_minutes", 0), 0))
        self._dfp_settings.dfp_set("last_start_ts", dfp_now_ts())
        if not self._dfp_settings.dfp_get("first_run_done", False):
            self._dfp_settings.dfp_set("first_run_done", True)
            first_lines = [
                "你好呀主人！我是「%s」，从今天起就住在你的桌面上了～" % self._dfp_settings.dfp_get("pet_nickname", DFP_DEFAULT_NICKNAME),
                "把鼠标放到我身上可以拖动我，点我一下我会很开心，右键有菜单哦。",
                "想聊天就按 Ctrl+O；想改我的样子、性格、作息，按 Ctrl+, 打开设置。",
            ]
            self._dfp_bubble.dfp_show_text("　".join(first_lines), 14000)
            self._dfp_pet.dfp_set_expression(DFPExpression.HAPPY, 5.0)
        else:
            if gap > 300:
                self._dfp_brain.dfp_say(
                    "greet",
                    "主人，我们有 %s 没见了…我好想你！" % dfp_human_duration(gap),
                )
            else:
                # ★ v1.1.22：普通启动说**启动台词**（`startup` 类别，语音也在这一类）；
                #   这一类万一被删了就退回原来的 greet，不会一句不说。
                line = self._dfp_brain.dfp_pick_line(DFP_STARTUP_CATEGORY) or self._dfp_brain.dfp_pick_line("greet")
                if line:
                    self.dfp_on_brain_say(line, DFP_STARTUP_CATEGORY if line else "greet")
        try:
            self._dfp_database.dfp_add_event("start", "程序启动 v%s" % __version__)
        except Exception:
            pass
        self._dfp_geometry_timer.start()
        # ★ v1.1.18：算番 Web 版（手机 / 平板浏览器打开就能算番；算完桌宠读番数）
        self.dfp_sync_mahjong()
        self._dfp_logger.dfp_info("桌宠已启动，位置 %s，缩放 %.2f，渲染 %s，素材 %s" % (
            dfp_geometry_to_text(self._dfp_pet.geometry()), self._dfp_pet.dfp_scale(),
            self._dfp_pet.dfp_render_mode(), self._dfp_assets.dfp_summary() if self._dfp_assets.dfp_is_ready() else "不可用"))
        self.dfp_notify("大肥鱼已就位 v%s" % __version__, "success")
        if self._dfp_assets.dfp_is_ready() and not self._dfp_settings.dfp_get("pet_render_mode", "assets") == "vector":
            self._dfp_play_voice("confirm")

    def _dfp_session_gap(self) -> float:
        last = dfp_safe_float(self._dfp_settings.dfp_get("last_start_ts", 0.0), 0.0)
        if last <= 0.0:
            return 0.0
        return max(0.0, dfp_now_ts() - last)

    # --- 退出 ---
    def dfp_shutdown(self) -> None:
        if self._dfp_quitting:
            return
        self._dfp_quitting = True
        # ★ 退出时注销确认框提示音回调（否则桌宠没了还会被叫）
        dfp_set_confirm_notice(None)
        self._dfp_logger.dfp_info("开始退出流程…")
        self._dfp_geometry_timer.stop()
        self._dfp_backup_timer.stop()
        self._dfp_walk_timer.stop()
        self._dfp_motion_timer.stop()
        self._dfp_follow_timer.stop()
        self._dfp_balance_timer.stop()
        # ★ v1.1.18：算番 Web 版与数字朗读都收掉（否则退出后端口还占着）
        try:
            self._dfp_fan_read_timer.stop()
        except Exception:
            pass
        try:
            self._dfp_number_speaker.dfp_stop()
        except Exception:
            pass
        try:
            self._dfp_mahjong.dfp_stop()
        except Exception:
            pass
        self._dfp_save_pet_geometry()
        # 累计本次在线时长
        run_seconds = max(0.0, dfp_now_ts() - self._dfp_session_started)
        self._dfp_settings.dfp_set("last_run_seconds", round(run_seconds, 1))
        total_minutes = dfp_safe_int(self._dfp_settings.dfp_get("stat_total_minutes", 0), 0) + int(run_seconds / 60.0)
        self._dfp_settings.dfp_set("stat_total_minutes", total_minutes)
        try:
            self._dfp_database.dfp_bump_daily("minutes", int(run_seconds / 60.0))
        except Exception:
            pass
        try:
            self._dfp_brain.dfp_save_state(force=True)
            self._dfp_brain.dfp_stop()
        except Exception:
            pass
        self._dfp_chat.dfp_shutdown()
        if self._dfp_settings.dfp_get("auto_backup_enabled", True):
            self._dfp_backup_now("exit", silent=True)
        for window in (self._dfp_chat_window, self._dfp_settings_window, self._dfp_status_window):
            try:
                if window is not None:
                    window.close()
            except Exception:
                continue
        try:
            if self._dfp_bubble is not None:
                self._dfp_bubble.dfp_hide_now()
                self._dfp_bubble.close()
            if self._dfp_pet is not None:
                self._dfp_pet.hide()
                self._dfp_pet.close()
            if self._dfp_tray is not None:
                self._dfp_tray.hide()
        except Exception:
            pass
        self._dfp_settings.dfp_save_now()
        try:
            self._dfp_database.dfp_add_event("quit", "在线 %s" % dfp_human_duration(run_seconds))
        except Exception:
            pass
        self._dfp_database.dfp_close()
        self._dfp_client.dfp_close()
        self._dfp_logger.dfp_info("已退出（本次在线 %s）" % dfp_human_duration(run_seconds))

    def dfp_request_quit(self) -> None:
        # ★ v1.1.14：退出前响一声「被打断」提示音（日志里也能看出这次是手动退出）
        self.dfp_play_notice("interrupted")
        self.dfp_shutdown()
        try:
            self._dfp_app.quit()
        except Exception:
            pass

    def dfp_handle_raise(self) -> None:
        """另一个实例尝试启动时把桌宠显示出来。"""
        if self._dfp_pet is None:
            return
        if not self._dfp_pet.isVisible():
            self.dfp_toggle_pet_visible()
        self._dfp_pet.raise_()
        self._dfp_pet.show()
        self.dfp_notify("已经在运行啦，我在这儿～", "info")

    # --- 设置应用 ---
    def dfp_apply_all_settings(self) -> None:
        pet = self._dfp_pet
        if pet is None:
            return
        settings = self._dfp_settings
        pet.dfp_renderer().dfp_set_palette(str(settings.dfp_get("pet_palette", "ocean")))
        pet._dfp_state.palette = pet.dfp_renderer().dfp_palette_name()
        pet.dfp_apply_scale(float(settings.dfp_get("pet_scale", 1.0)), keep_anchor=False)
        pet.dfp_set_opacity(float(settings.dfp_get("pet_opacity", 1.0)))
        pet.dfp_set_always_on_top(bool(settings.dfp_get("pet_always_on_top", True)))
        pet.dfp_set_flip(bool(settings.dfp_get("pet_flip", False)))
        pet.dfp_set_shadow(bool(settings.dfp_get("pet_show_shadow", True)))
        pet.dfp_set_spout(bool(settings.dfp_get("pet_show_spout", True)))
        pet.dfp_set_locked(bool(settings.dfp_get("lock_position", False)))
        pet.dfp_set_drag_enabled(bool(settings.dfp_get("drag_enabled", True)))
        pet.dfp_set_keep_on_screen(bool(settings.dfp_get("keep_on_screen", True)))
        pet.dfp_set_wheel_zoom_enabled(bool(settings.dfp_get("wheel_zoom_enabled", True)))
        pet.dfp_set_frame_rate(dfp_safe_int(settings.dfp_get("pet_anim_fps", 30), 30))
        nickname = str(settings.dfp_get("pet_nickname", DFP_DEFAULT_NICKNAME))
        pet.dfp_set_nametag(nickname if settings.dfp_get("pet_show_nametag", False) else "")
        pet.dfp_set_nametag_color(pet.dfp_renderer()._c("accent").name())
        pet.dfp_set_asleep(bool(self._dfp_brain.dfp_state_value("asleep", False)))
        if self._dfp_bubble is not None:
            self._dfp_bubble.dfp_set_max_width(dfp_safe_int(settings.dfp_get("bubble_max_width", 320), 320))
            self._dfp_bubble.dfp_set_font_size(dfp_safe_int(settings.dfp_get("bubble_font_size", 10), 10))
            self._dfp_bubble.dfp_set_colors(
                border=pet.dfp_renderer()._c("accent").name(),
                fill="#FFFFFF",
                text=DFP_UI_COLORS["text"],
            )
            if not settings.dfp_get("bubble_enabled", True):
                self._dfp_bubble.dfp_hide_now()
        self._dfp_logger.dfp_set_level(str(settings.dfp_get("log_level", "INFO")))
        self._dfp_logger.dfp_set_to_file(bool(settings.dfp_get("log_to_file", True)))
        self._dfp_database.dfp_configure_secrets(dfp_secret_password(), bool(settings.dfp_get("db_encrypt_messages", True)))
        self._dfp_chat.dfp_apply_settings()
        self._dfp_apply_asset_settings()
        self._dfp_restart_timers()
        for window in (self._dfp_chat_window, self._dfp_settings_window, self._dfp_status_window):
            if window is not None and hasattr(window, "dfp_apply_settings"):
                try:
                    window.dfp_apply_settings()
                except Exception:
                    continue
        if self._dfp_menu is not None:
            actions = self._dfp_menu.actions()
            if actions:
                actions[0].setText("🐟 %s　Lv%d" % (dfp_truncate_text(nickname, 10), dfp_safe_int(self._dfp_brain.dfp_state_value("level", 1), 1)))

    def _dfp_apply_asset_settings(self) -> None:
        """把素材/渲染相关设置刷到窗口与语音播放器上。"""
        pet = self._dfp_pet
        if pet is not None:
            pet.dfp_set_render_mode(str(self._dfp_settings.dfp_get("pet_render_mode", "assets")))
            pet.dfp_set_anim_zoom(dfp_safe_float(self._dfp_settings.dfp_get("asset_anim_zoom", 0.0), 0.0))
            pet.dfp_set_asset_library(self._dfp_assets)
            # ★ v1.1.15：形态开关也在这里刷（用 dfp_apply_all_settings / 启动时一并生效）；
        #   ★ v1.1.36：开关名字没改（兼容老设置），含义变成「状态形象用不用新形象图」
            pet.dfp_set_form_enabled(bool(self._dfp_settings.dfp_get("whale_form_enabled", True)))
            pet.dfp_set_form_sleep(bool(self._dfp_settings.dfp_get("whale_form_sleep", True)))
            # ★ v1.1.34：台词换表情 / 换立绘两个开关（关掉时把挂着的那一套收掉）
            self._dfp_apply_look_settings()
        self._dfp_voice.dfp_set_volume(dfp_safe_float(self._dfp_settings.dfp_get("voice_volume", 0.8), 0.8))

    def _dfp_apply_form_settings(self) -> None:
        """鲸鱼形态两个开关的即时生效（关掉时把挂着的形态也清掉，不能留个鲸鱼在那里）。"""
        pet = self._dfp_pet
        if pet is None:
            return
        enabled = bool(self._dfp_settings.dfp_get("whale_form_enabled", True))
        pet.dfp_set_form_enabled(enabled)
        pet.dfp_set_form_sleep(bool(self._dfp_settings.dfp_get("whale_form_sleep", True)))
        if not enabled:
            pet.dfp_clear_form()
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_info("状态形象已关闭，始终用设置里选的基础形象")

    # --- ★ v1.1.36：状态形象（睡觉 / 忙 / 等 / 休息 / 庆祝 / 出错 → 新形象集里的图）---
    def dfp_flash_form(self, state: str, seconds: Optional[float] = None, silent: bool = False) -> bool:
        """临时把桌宠换成某个非人形形态（`seconds=None` 用 `DFP_FORM_FLASH_SECONDS` 的默认时长）。

        优先级：已经在挂的形态比它高就先不变（比如正在「出错」，不要被紧随其后的「忙碌」盖掉）。
        """
        pet = self._dfp_pet
        wanted = str(state or "").strip().lower()
        # ★ 测试里常有「只实现了一部分方法的替身桌宠」，所以整段都按能力探测写（不留裸 except）
        if pet is None or not wanted or not hasattr(pet, "dfp_set_form"):
            return False
        if not bool(self._dfp_settings.dfp_get("whale_form_enabled", True)):
            return False
        if self._dfp_assets.dfp_form(wanted) is None:
            if not silent and self._dfp_logger is not None:
                self._dfp_logger.dfp_debug("没有这个形态（%s），跳过" % wanted)
            return False
        current = pet.dfp_form_state()
        if current and current != wanted:
            if DFP_FORM_PRIORITY.get(current, 0) > DFP_FORM_PRIORITY.get(wanted, 0):
                return False
        span = DFP_FORM_FLASH_SECONDS.get(wanted, 4.0) if seconds is None else float(seconds)
        ok = pet.dfp_set_form(wanted, span)
        if ok and not silent and self._dfp_logger is not None:
            self._dfp_logger.dfp_info("状态形象：%s（%s）" % (DFP_FORM_LABELS.get(wanted, wanted), "常驻" if not span else "%.1f 秒" % span))
        return ok

    def dfp_clear_form(self, state: str = "") -> bool:
        """清掉形态（给了 `state` 就只在正好是它的时候清）。"""
        pet = self._dfp_pet
        if pet is None or not hasattr(pet, "dfp_clear_form"):
            return False
        return pet.dfp_clear_form(state)

    def dfp_form_state(self) -> str:
        pet = self._dfp_pet
        if pet is None or not hasattr(pet, "dfp_form_state"):
            return ""
        return pet.dfp_form_state()

    def dfp_form_summary(self) -> str:
        return self._dfp_assets.dfp_form_summary()

    def _dfp_default_pet_geometry(self, pet: Optional[DFPPetWidget]) -> QRect:
        size = (pet.dfp_canvas_size(float(self._dfp_settings.dfp_get("pet_scale", 1.0))) if pet is not None else QSize(220, 260))
        default = QRect(0, 0, size.width(), size.height())
        if self._dfp_settings.dfp_get("remember_position", True):
            saved = self._dfp_settings.dfp_get("window_geometry", "")
        else:
            saved = ""
        return dfp_safe_window_geometry(saved, default, min_width=40, min_height=40)

    def _dfp_restart_timers(self) -> None:
        settings = self._dfp_settings
        if settings.dfp_get("auto_backup_enabled", True):
            interval = max(1, dfp_safe_int(settings.dfp_get("auto_backup_interval_min", 60), 60)) * 60000
            self._dfp_backup_timer.setInterval(interval)
            if not self._dfp_backup_timer.isActive():
                self._dfp_backup_timer.start()
        else:
            self._dfp_backup_timer.stop()
        if settings.dfp_get("auto_walk_enabled", True):
            interval = max(8, dfp_safe_int(settings.dfp_get("auto_walk_interval_sec", 45), 45)) * 1000
            self._dfp_walk_timer.setInterval(interval)
            if not self._dfp_walk_timer.isActive():
                self._dfp_walk_timer.start()
        else:
            self._dfp_walk_timer.stop()
            self._dfp_motion_timer.stop()
        if settings.dfp_get("follow_mouse_enabled", False):
            if not self._dfp_follow_timer.isActive():
                self._dfp_follow_timer.start()
        else:
            self._dfp_follow_timer.stop()
        # ★ v1.1.12：余额变化提醒定时器（关掉 / 没 Key 就停）
        if self.dfp_balance_watch_enabled():
            self._dfp_balance_timer.setInterval(self.dfp_balance_interval_ms())
            if not self._dfp_balance_timer.isActive():
                self._dfp_balance_timer.start()
            if self._dfp_last_balance is None:
                # 刚打开时先悄悄记下基线（这一次不提示，免得一启动就说「少了/多了」）
                self.dfp_check_balance_change()
        else:
            self._dfp_balance_timer.stop()
            self._dfp_last_balance = None

    # --- ★ v1.1.12：余额变化提醒（总可用余额变了就在气泡里说一句）---
    def dfp_balance_watch_enabled(self) -> bool:
        """余额变化提醒是否生效：设置开着 **且** 至少有一个 Key。"""
        if not self._dfp_settings.dfp_get("balance_watch_enabled", True):
            return False
        try:
            return bool(self._dfp_client.dfp_key_count())
        except Exception:
            return False

    def dfp_balance_interval_ms(self) -> int:
        seconds = dfp_safe_int(self._dfp_settings.dfp_get("balance_watch_interval_sec", 300), 300)
        return int(dfp_clamp(seconds, 30, 3600) * 1000)

    def dfp_check_balance_change(self, manual: bool = False, source: str = "") -> bool:
        """后台查一次余额（不占 UI 线程）。返回是否真的发起了请求。

        ★ 查询 `/user/balance` **不消耗 Token**；失败只写日志，不弹窗、不动基线。
        ★ v1.1.16：`manual=True` = 用户主动点「立即查询」——
          ① 不看「余额变了提醒我」那个开关（关掉了也允许手动查）；
          ② 查完**一定会说一句**（哪怕没变化，也报一下当前余额），见 `_dfp_on_balance_checked`。
        ★ v1.1.16：`source` 可以指定这笔账的来源（如「聊天窗口」，见 `dfp_note_chat_spend`）；
          不传就按 manual 自动取「手动查询」/「定时查询」。
        """
        if self._dfp_balance_busy:
            return False
        # ★ v1.1.15：查余额 = 「忙一下」（短形态，回来就被结果盖成庆祝/出错）
        self.dfp_flash_form("working", 4.0, silent=True)
        if manual:
            if not self.dfp_balance_keys_available():
                return False
            self._dfp_balance_manual = True
            self._dfp_balance_source = str(source or DFP_BALANCE_LOG_SOURCE_MANUAL)
        else:
            if not self.dfp_balance_watch_enabled():
                return False
            self._dfp_balance_source = str(source or DFP_BALANCE_LOG_SOURCE_AUTO)
        self._dfp_balance_busy = True
        worker = DFPTaskWorker(
            "余额变化检查",
            lambda report: self._dfp_client.dfp_balance(),
            self._dfp_balance_signals,
        )
        self._dfp_task_pool.start(worker)
        return True

    def dfp_balance_keys_available(self) -> bool:
        """有没有可用的 Key（手动查询只看这一条，不看提醒开关）。"""
        try:
            return bool(self._dfp_client.dfp_key_count())
        except Exception:
            return False

    def dfp_note_chat_spend(self) -> bool:
        """一次对话刚结束：顺手懾悄查一下余额，把这次花的钱记成「聊天窗口」（v1.1.16）。

        为什么要查：余额变化只有**查询那一刻**才看得见。不查就只能等下一次定时查询，
        那时可能已经混了好几笔、来源也说不清。聊天完立刻查（`/user/balance` 不消耗 Token），
        「扣钱记录」里就能看到一行「聊天窗口 -0.03」。
        ★ 只在「余额变了提醒我」开着时查；正在查 / 没 Key 就跳过，绝不打扰、不报错。
        """
        if self._dfp_balance_busy or not self.dfp_balance_watch_enabled():
            return False
        return self.dfp_check_balance_change(manual=False, source=DFP_BALANCE_LOG_SOURCE_CHAT)

    def dfp_query_balance_now(self) -> bool:
        """菜单 / 按钮入口：立刻查一次 DS 余额（查完一定报数）。"""
        if self._dfp_balance_busy:
            self.dfp_notify("正在查余额，等这一下下…", "info")
            return False
        if not self.dfp_balance_keys_available():
            self.dfp_notify("还没有可用的 API Key —— 先在「设置 → AI 对话」里填一个才能查余额", "warn")
            return False
        started = self.dfp_check_balance_change(manual=True)
        if started:
            self.dfp_notify("正在查询 DeepSeek 余额…", "info")
        return started

    def dfp_handle_balance(self, data: Any, source: str = "") -> str:
        """拿到一次余额结果后的**纯逻辑**部分（好测）：返回要显示的通知文本（没变化就返回 ""）。

        - 第一次成功拿到 → 只记基线，**不提示**（刚启动 / 刚打开开关时不吵人）；
        - 之后变了（≥ 0.01）→ 更新基线、**记一条扣钱/进账记录**，并返回「少了/多了多少钱 + 最新总可用余额」；
        - 没变化 / 解析不出来 → 更新基线（解析不出来保持不变），返回 ""。
        """
        total, currency = dfp_balance_total(data)
        if not currency and not total:
            return ""  # 解析不出余额：不当成「变成 0 元」，基线保持不动
        # 给「手动查询」用的现场信息（不管变没变，界面都要能报出当前余额）
        self._dfp_balance_last_total = total
        self._dfp_balance_last_currency = currency
        self._dfp_balance_last_delta = 0.0
        if self._dfp_last_balance is None:
            self._dfp_last_balance = total
            self._dfp_last_balance_currency = currency or self._dfp_last_balance_currency
            self._dfp_logger.dfp_info(
                "余额基线：%s" % dfp_balance_total_text(total, currency)
            )
            return ""
        previous = self._dfp_last_balance
        self._dfp_last_balance = total
        if currency:
            self._dfp_last_balance_currency = currency
        delta = total - previous
        self._dfp_balance_last_delta = delta
        if abs(delta) <= DFP_BALANCE_DELTA_EPSILON:
            return ""
        self._dfp_logger.dfp_info(
            "余额变化：%s → %s（%s）"
            % (dfp_balance_total_text(previous, currency), dfp_balance_total_text(total, currency), dfp_balance_delta_text(delta, currency))
        )
        # ★ v1.1.16：记一笔（时间按「年月日 时:分:秒.毫秒」存下来，菜单里能翻「之前扣的钱」）
        self._dfp_record_balance_change(delta, total, currency, source or self._dfp_balance_source)
        return dfp_format_balance_change(delta, total, currency, html=True)

    def _dfp_record_balance_change(self, delta: float, total: float, currency: str = "", source: str = "") -> int:
        """把一次余额变化写进数据库（失败只记日志，不影响提醒本身）。"""
        try:
            row_id = self._dfp_database.dfp_add_balance_log(delta, total, currency, source)
            removed = self._dfp_database.dfp_prune_balance_log(DFP_BALANCE_LOG_KEEP)
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_info(
                    "余额已记账：%s %s（%s）%s"
                    % (dfp_timestamp_ms(), dfp_balance_delta_text(delta, currency), source or "未知来源",
                       "，清理旧记录 %d 条" % removed if removed else "")
                )
            return row_id
        except Exception:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_exception("余额记账失败")
            return 0

    def _dfp_on_balance_checked(self, task_name: str, result: Any) -> None:
        """后台查余额成功（主线程）。"""
        if task_name != "余额变化检查":
            return
        self._dfp_balance_busy = False
        manual = bool(self._dfp_balance_manual)
        self._dfp_balance_manual = False
        try:
            notice = self.dfp_handle_balance(result)
        except Exception as exc:
            self._dfp_logger.dfp_warning("余额变化提醒处理失败：%s: %s" % (type(exc).__name__, exc))
            return
        if notice:
            self.dfp_show_balance_notice(notice)
            # ★ v1.1.14：余额变少时额外响一声（notice-low_balance）；变多的时候不响
            if "少了" in str(notice):
                self.dfp_play_notice("low_balance")
            return
        if manual:
            # ★ v1.1.16：手动查一定要有回音（没变化也要报当前余额，不然用户不知道查没查）
            current = dfp_safe_float(self._dfp_balance_last_total, 0.0)
            currency = str(self._dfp_balance_last_currency or "")
            if not currency and not current:
                self.dfp_notify("余额没查到内容，等会儿再试试？", "warn")
                return
            delta = dfp_safe_float(self._dfp_balance_last_delta, 0.0)
            if abs(delta) <= DFP_BALANCE_DELTA_EPSILON:
                self.dfp_show_balance_notice(
                    '<div style="color:%s; font-weight:bold;">余额没有变化</div>'
                    '<div style="color:%s;">%s</div>'
                    % (DFP_BALANCE_COLOR_FLAT, DFP_UI_COLORS["text"], dfp_balance_total_text(current, currency))
                )
            else:
                self.dfp_show_balance_notice(dfp_format_balance_change(delta, current, currency, html=True))

    def _dfp_on_balance_failed(self, task_name: str, error: str) -> None:
        """后台查余额失败：只写日志（网络抖动不该打扰用户，基线原地不动）。"""
        if task_name != "余额变化检查":
            return
        self._dfp_balance_busy = False
        manual = bool(self._dfp_balance_manual)
        self._dfp_balance_manual = False
        self._dfp_logger.dfp_info("余额查询失败（忽略）：%s" % str(error or ""))
        if manual:
            # 手动查失败还是要给个回音（不然点下去像是没反应）
            self.dfp_notify("余额没查成：%s" % dfp_truncate_text(str(error or ""), 60), "warn")

    def dfp_apply_walk_settings(self) -> None:
        """只刷新「走动间隔」这一个定时器（拖拽结束后调用）。"""
        interval = max(8, dfp_safe_int(self._dfp_settings.dfp_get("auto_walk_interval_sec", 45), 45)) * 1000
        settings = self._dfp_settings
        if settings.dfp_get("auto_walk_enabled", True):
            self._dfp_walk_timer.setInterval(interval)
            if not self._dfp_walk_timer.isActive():
                self._dfp_walk_timer.start()
        else:
            self._dfp_walk_timer.stop()
            self._dfp_motion_timer.stop()
        if settings.dfp_get("follow_mouse_enabled", False):
            if not self._dfp_follow_timer.isActive():
                self._dfp_follow_timer.start()
        else:
            self._dfp_follow_timer.stop()

    def dfp_show_notice_bubble(self, text: str, min_ms: int = 6000) -> bool:
        """把一条 HTML 通知画到气泡里（余额变化 / 算番结果都用它）。"""
        body = str(text or "").strip()
        if not body:
            return False
        if not self._dfp_settings.dfp_get("bubble_enabled", True):
            return False
        bubble = self._dfp_bubble
        pet = self._dfp_pet
        if bubble is None or pet is None or not pet.isVisible():
            return False
        duration = dfp_safe_int(self._dfp_settings.dfp_get("bubble_duration_ms", 6000), 6000)
        bubble.dfp_show_html(body, max(duration, dfp_safe_int(min_ms, 6000)))
        pet.dfp_begin_talk(2.4)
        return True

    def dfp_show_balance_notice(self, notice: str) -> bool:
        """把「余额变化」这条通知画到气泡里（HTML：少了红、多了绿 + 最新余额）。"""
        return self.dfp_show_notice_bubble(notice)

    # --- 🀄 算番 Web 版（手机 / 平板算番 + 桌宠读番，★ v1.1.18）---
    def dfp_mahjong(self) -> "DFPMahjongWeb":
        return self._dfp_mahjong

    def dfp_number_speaker(self) -> "DFPNumberSpeaker":
        return self._dfp_number_speaker

    def dfp_sync_mahjong(self) -> None:
        """按设置把算番 Web 服务开 / 关（启动时调一次；端口或监听范围改了会重建）。

        ★ 重建最稳：端口换了、或者「允许手机访问」开关变了，都靠先停再起。
        """
        if not bool(self._dfp_settings.dfp_get("mahjong_web_enabled", True)):
            if self._dfp_mahjong.dfp_running():
                self._dfp_mahjong.dfp_stop()
            return
        if self._dfp_mahjong.dfp_running():
            self._dfp_mahjong.dfp_stop()
        self._dfp_mahjong.dfp_start()

    def _dfp_on_mahjong_score(self, total: int, pattern: str, message: str) -> None:
        """手机 / 平板算完番（**主线程**）：气泡马上报，念番数则**等它停下来**再念。

        ★ v1.1.21：用户连点「计分」时，算番回传每 0~2 秒一次，而一句「合计一百八十九番」
          要念 4~6 秒 —— 每次都从头念 = 听着像没读。所以改成：
          ① 气泡立刻更新（看得见，不等）；② 番数记成「待念」，**停 0.9 秒**才真的念最后一次。
        """
        number = max(0, dfp_safe_int(total, 0))
        text = DFP_MAHJONG_FORMAT % number
        extra = str(pattern or message or "").strip()
        if extra:
            text += "　<span style=\"color:%s;\">%s</span>" % (DFP_UI_COLORS.get("text", "#333333"), html.escape(extra))
        self.dfp_show_notice_bubble(text)
        self._dfp_pending_fan = number
        if bool(self._dfp_settings.dfp_get("mahjong_read_fan", True)):
            self._dfp_fan_read_timer.start()

    def _dfp_read_pending_fan(self) -> None:
        """节流定时器到点：念**最后一次**那个番数（中途反复算只念最后那一次）。"""
        number = self._dfp_pending_fan
        self._dfp_pending_fan = None
        if number is None:
            return
        speaker = self._dfp_number_speaker
        if not dfp_should_read_fan(speaker.dfp_speaking(), self._dfp_fan_reading, number):
            self._dfp_logger.dfp_info("同一个番数（%d 番）正在念，不打断也不重念" % number)
            return
        spoken = self.dfp_read_fan(number)
        self._dfp_fan_reading = number if spoken else None
        # ★ v1.1.19：读不出来一定要留痕（用户报过「有时候没读出番数」，日志里能看出是哪一步）
        if not spoken:
            self._dfp_logger.dfp_info("算番回传没读出来（%d 番）：%s" % (
                number, speaker.dfp_error_reason() or "没有可用音频片段"))

    def dfp_read_fan(self, value: Any) -> str:
        """用数字音频把番数读出来（★ v1.1.20：固定念成「合计 … 番」）。

        返回读出的中文（没读成返回空串）。★ 这个函数是「立刻念」，节流在
        `_dfp_read_pending_fan()` 里（手机算番走那条；「试读」按钮直接走这里）。
        """
        if not bool(self._dfp_settings.dfp_get("mahjong_read_fan", True)):
            return ""
        return self._dfp_number_speaker.dfp_speak_number(value, DFP_MAHJONG_READ_PREFIX, DFP_MAHJONG_READ_SUFFIX)

    def dfp_test_read_number(self, value: Any = 88) -> str:
        """试读一个番数（设置窗口 / 算番窗口的「试读」按钮用）。"""
        number = max(0, dfp_safe_int(value, 0))
        spoken = self.dfp_read_fan(number)
        if not spoken:
            self.dfp_notify("没读出来：%s" % (self._dfp_number_speaker.dfp_error_reason() or "没找到数字音频"), "warn")
            return ""
        self.dfp_notify("试读：%s →「%s」" % (number, spoken), "success")
        return spoken

    def dfp_open_mahjong_dialog(self) -> None:
        """打开「🀄 算番 Web 版」窗口（看地址 / 开关服务 / 试读）。"""
        dialog = self._dfp_mahjong_dialog
        if dialog is None:
            dialog = DFPMahjongWebDialog(self, self._dfp_pet)
            self._dfp_mahjong_dialog = dialog
        dialog.dfp_refresh()
        try:
            dialog.adjustSize()
            pet = self._dfp_pet
            if pet is not None and pet.isVisible():
                geo = pet.geometry()
                dfp_place_window(dialog, QRect(max(0, geo.left() - dialog.width() - 12), geo.top(), dialog.width(), dialog.height()))
                dfp_ensure_window_on_screen(dialog, force_center=True)
        except Exception:
            pass
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def dfp_asset_library(self) -> DFPAssetLibrary:
        return self._dfp_assets

    def dfp_behavior_ready(self) -> bool:
        return bool(self._dfp_assets.dfp_is_ready() and self._dfp_settings.dfp_get("behavior_asset_enabled", True))

    def dfp_on_setting_changed(self, key: str, value: Any) -> None:
        """设置项变化时的即时响应（新增设置项请在这里加分支）。"""
        # ★ v1.1.18：算番 Web 版这几项与桌宠窗口无关（放到 pet 判空前，pet 还没建也要生效）
        if key in ("mahjong_web_enabled", "mahjong_web_lan", "mahjong_web_port"):
            self.dfp_sync_mahjong()
            return
        if key == "mahjong_read_fan":
            if not value:
                self._dfp_fan_read_timer.stop()
                self._dfp_pending_fan = None
                self._dfp_number_speaker.dfp_stop()
            return
        pet = self._dfp_pet
        if pet is None:
            return
        if key == "pet_scale":
            pet.dfp_apply_scale(float(value), keep_anchor=True)
            self._dfp_save_pet_geometry()
        elif key == "pet_opacity":
            pet.dfp_set_opacity(float(value))
        elif key == "pet_always_on_top":
            pet.dfp_set_always_on_top(bool(value))
        elif key == "pet_flip":
            pet.dfp_set_flip(bool(value))
        elif key == "pet_show_shadow":
            pet.dfp_set_shadow(bool(value))
        elif key == "pet_show_spout":
            pet.dfp_set_spout(bool(value))
        elif key == "pet_anim_fps":
            pet.dfp_set_frame_rate(dfp_safe_int(value, 30))
        elif key == "pet_palette":
            pet.dfp_renderer().dfp_set_palette(str(value))
            pet.update()
            if self._dfp_bubble is not None:
                self._dfp_bubble.dfp_set_colors(border=pet.dfp_renderer()._c("accent").name())
        elif key == "pet_nickname":
            pet.dfp_set_nametag(str(value) if self._dfp_settings.dfp_get("pet_show_nametag", False) else "")
            if self._dfp_chat_window is not None:
                self._dfp_chat_window.dfp_apply_settings()
        elif key == "pet_show_nametag":
            pet.dfp_set_nametag(str(self._dfp_settings.dfp_get("pet_nickname", "")) if value else "")
        elif key == "lock_position":
            pet.dfp_set_locked(bool(value))
        elif key == "drag_enabled":
            pet.dfp_set_drag_enabled(bool(value))
        elif key == "keep_on_screen":
            pet.dfp_set_keep_on_screen(bool(value))
            if value:
                dfp_ensure_window_on_screen(pet)
                self._dfp_save_pet_geometry()
        elif key == "wheel_zoom_enabled":
            pet.dfp_set_wheel_zoom_enabled(bool(value))
        elif key == "bubble_max_width":
            if self._dfp_bubble is not None:
                self._dfp_bubble.dfp_set_max_width(dfp_safe_int(value, 320))
        elif key == "bubble_font_size":
            if self._dfp_bubble is not None:
                self._dfp_bubble.dfp_set_font_size(dfp_safe_int(value, 10))
        elif key == "bubble_enabled":
            if not value and self._dfp_bubble is not None:
                self._dfp_bubble.dfp_hide_now()
        elif key == "log_level":
            self._dfp_logger.dfp_set_level(str(value))
        elif key == "log_to_file":
            self._dfp_logger.dfp_set_to_file(bool(value))
        elif key == "db_encrypt_messages":
            self._dfp_database.dfp_configure_secrets(dfp_secret_password(), bool(value))
        elif key == "ai_request_concurrency":
            self._dfp_chat.dfp_apply_settings()
        elif key in ("auto_backup_enabled", "auto_backup_interval_min", "auto_walk_enabled", "auto_walk_interval_sec", "follow_mouse_enabled"):
            self._dfp_restart_timers()
        elif key in ("balance_watch_enabled", "balance_watch_interval_sec"):
            # ★ v1.1.12：余额变化提醒开关 / 间隔
            self._dfp_restart_timers()
        elif key in ("dream_enabled", "dream_interval_hours", "dream_on_wake"):
            # ★ 梦境：间隔与开关都是「用到的时候才读」的，这里只留一笔日志方便排查
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_info("梦境设置变更：%s = %s" % (key, value))
        elif key in ("pet_render_mode", "asset_anim_zoom", "asset_root"):
            if key == "asset_root":
                # ★ 换素材目录：只「按清单重新载入」（目录里没清单才会兜底扫描）。
                #   以前这里是 dfp_scan_assets() → 无条件 dfp_rebuild()，
                #   于是**每次改素材目录都会用兜底扫描把清单整份重写**：
                #   中文动作名/表情归属全丢，表情 9 条被写成 16 条，
                #   heart/sleepy 指到静态表情包与光标图上（用户真实踩到过）。
                self._dfp_assets.dfp_reload()
                # 台词库 / 等级称号 JSON 一并在素材目录里，跟着重载
                dfp_lines_load(self._dfp_settings, force=True)
                dfp_levels_load(self._dfp_settings, force=True)
            self._dfp_apply_asset_settings()
        elif key in ("voice_enabled", "voice_volume"):
            self._dfp_voice.dfp_set_volume(dfp_safe_float(self._dfp_settings.dfp_get("voice_volume", 0.8), 0.8))
        elif key == "asset_character":
            # ★ v1.1.14：换形象（素材包 skins 也算形象）→ 立即换到桌宠身上
            self.dfp_apply_character()
        elif key == "look_expression_follow":
            # ★ v1.1.34：台词换表情 / 换立绘两个开关（关掉时把挂着的那一套收掉）
            self._dfp_apply_look_settings()
        elif key in ("whale_form_enabled", "whale_form_sleep"):
            # ★ v1.1.15：状态形象两个开关立即生效（关掉时顺手把挂着的状态清掉）
            self._dfp_apply_form_settings()
        elif key == "voice_show_text":
            # 语音台词（字幕）是播放那一刻才读的，这里无需提前应用；
            # 但按约定新建设置项要有生效分支，顺便在日志里留一笔方便排查。
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_info("语音台词字幕：%s" % ("开启" if value else "关闭"))
        elif key == "behavior_asset_interval_sec":
            try:
                self._dfp_brain._dfp_schedule_next_action()
            except Exception:
                pass
        elif key.startswith("lock_"):
            self.dfp_notify("%s%s" % (key[5:], "已锁定" if value else "已解锁"), "info")
        elif key == "remember_position" and not value:
            self._dfp_settings.dfp_set("window_geometry", "")

    # --- 互动入口 ---
    def dfp_on_pet_clicked(self) -> None:
        if not self._dfp_settings.dfp_get("click_pet_enabled", True):
            return
        self.dfp_pet_head()

    def dfp_on_pet_double_clicked(self) -> None:
        if not self._dfp_settings.dfp_get("double_click_play_enabled", True):
            return
        self.dfp_play()

    def dfp_on_pet_context_menu(self, global_pos: QPoint) -> None:
        if self._dfp_menu is not None:
            self._dfp_menu.exec(QPoint(global_pos))

    def dfp_on_pet_wheel(self, step: int) -> None:
        self.dfp_zoom_by(step)

    def dfp_on_pet_drag_finished(self) -> None:
        pet = self._dfp_pet
        if self._dfp_settings.dfp_get("snap_to_edge", False):
            self._dfp_snap_to_edge()
        # ★ 松手时必须夹回屏幕：否则拖到屏幕上方后 y 会变成负数，
        #   窗口整个看不见也点不到（用户真实报过 y=-337）。
        if pet is not None and self._dfp_settings.dfp_get("keep_on_screen", True):
            dfp_ensure_window_on_screen(pet)
        self._dfp_save_pet_geometry()
        self._dfp_brain.dfp_react_dragged()
        self.dfp_apply_walk_settings()

    def _dfp_on_pet_moved(self, position: QPoint) -> None:
        if self._dfp_pet is None:
            return
        # ★ 走位途中也要夹取：先把几何过一遍安全校正再比/再存，
        #   免得把屏幕外坐标（如 y=-337）写进设置，下次启动就找不到它了。
        rect = dfp_window_geometry_for_save(self._dfp_pet, QRect(self._dfp_pet.geometry()))
        text = dfp_geometry_to_text(rect)
        if text != self._dfp_last_geometry_text:
            self._dfp_last_geometry_text = text
            if self._dfp_settings.dfp_get("remember_position", True):
                self._dfp_settings.dfp_set("window_geometry", text)

    def _dfp_on_action_finished(self, action_name: str) -> None:
        if self._dfp_pet is not None and not self._dfp_pet.dfp_is_asleep():
            self._dfp_pet.dfp_set_expression(DFPExpression.NORMAL, 1.0)

    def _dfp_snap_to_edge(self) -> None:
        pet = self._dfp_pet
        if pet is None:
            return
        rect = QRect(pet.frameGeometry())
        areas = dfp_screen_available_rects()
        if not areas:
            return
        area = areas[0]
        for candidate in areas:
            if candidate.contains(rect.center()):
                area = candidate
                break
        left_gap = abs(rect.left() - area.left())
        right_gap = abs(area.right() - rect.right())
        top_gap = abs(rect.top() - area.top())
        bottom_gap = abs(area.bottom() - rect.bottom())
        minimum = min(left_gap, right_gap, top_gap, bottom_gap)
        if minimum == left_gap:
            rect.moveLeft(area.left())
        elif minimum == right_gap:
            rect.moveRight(area.right())
        elif minimum == top_gap:
            rect.moveTop(area.top())
        else:
            rect.moveBottom(area.bottom())
        dfp_place_window(pet, rect)

    def dfp_zoom_by(self, step: int) -> None:
        current = float(self._dfp_settings.dfp_get("pet_scale", 1.0))
        self.dfp_set_scale(current + 0.1 * (1 if step > 0 else -1))

    def dfp_set_scale(self, value: float) -> None:
        new_value = dfp_clamp(float(value), DFPPetWidget.DFP_MIN_SCALE, DFPPetWidget.DFP_MAX_SCALE)
        self._dfp_settings.dfp_set("pet_scale", round(new_value, 3))
        self.dfp_notify("大小：%.2f×" % new_value, "info")

    def _dfp_toggle_bubble(self) -> None:
        enabled = not bool(self._dfp_settings.dfp_get("bubble_enabled", True))
        self._dfp_settings.dfp_set("bubble_enabled", enabled)
        self.dfp_notify("气泡已%s" % ("打开" if enabled else "关闭"), "info")

    def _dfp_toggle_flip(self) -> None:
        self._dfp_settings.dfp_set("pet_flip", not bool(self._dfp_settings.dfp_get("pet_flip", False)))

    def _dfp_toggle_always_on_top(self) -> None:
        self._dfp_settings.dfp_set("pet_always_on_top", not bool(self._dfp_settings.dfp_get("pet_always_on_top", True)))

    def _dfp_cycle_palette(self) -> None:
        keys = list(DFP_PALETTES.keys())
        current = str(self._dfp_settings.dfp_get("pet_palette", "ocean"))
        index = keys.index(current) + 1 if current in keys else 0
        key = keys[index % len(keys)]
        self._dfp_settings.dfp_set("pet_palette", key)
        self.dfp_notify("配色：%s" % DFP_PALETTES[key]["label"], "info")

    # --- ★ v1.1.14：换形象（素材包里的 skins 也算形象）---
    def dfp_character_choices(self) -> List[Tuple[str, str]]:
        """可选的立绘清单：`(id, 显示名)`；第一项是「（默认）」。"""
        choices: List[Tuple[str, str]] = [("", "（默认）%s" % self._dfp_assets.dfp_character_label(self._dfp_default_character()))]
        for item in self._dfp_assets.dfp_characters():
            choices.append((str(item.get("id") or ""), self._dfp_assets.dfp_character_label(item)))
        return choices

    def _dfp_default_character(self) -> Optional[Dict[str, Any]]:
        """清单里的第一个形象（即「默认」那个，与设不设置无关）。"""
        items = self._dfp_assets.dfp_characters()
        return dict(items[0]) if items else None

    def dfp_cycle_character(self) -> None:
        """按菜单顺序换下一个形象（含「默认」共 N+1 个，绕圈）。"""
        choices = [item for item in self.dfp_character_choices()]
        ids = [str(pair[0]) for pair in choices]
        current = str(self._dfp_settings.dfp_get("asset_character", "") or "").strip()
        index = ids.index(current) + 1 if current in ids else 0
        target = ids[index % len(ids)]
        self._dfp_settings.dfp_set("asset_character", target)

    def dfp_apply_character(self) -> None:
        """把当前设置里的形象立刻刷到桌宠身上（切形象时调）。"""
        pet = self._dfp_pet
        if pet is None:
            return
        # ★ v1.1.35：手动换形象时先收掉「台词」挂上去的那张形象图，
        #   否则你选了新形象却看不出换了（下一句台词会重新挂）
        try:
            pet.dfp_clear_say_face()
        except Exception as exc:
            self._dfp_logger.dfp_warning("切换形象时清台词形象失败：%s: %s" % (type(exc).__name__, exc))
        # 先回到「常态 + 停掉动作片」，否则它可能还在演上一条表情/动作，看不出立绘换了
        try:
            pet.dfp_stop_asset_movie()
            pet.dfp_set_expression(DFPExpression.NORMAL, 0.0)
        except Exception as exc:
            self._dfp_logger.dfp_warning("切换形象时复位表情失败：%s: %s" % (type(exc).__name__, exc))
        self._dfp_apply_asset_settings()
        self.dfp_notify("形象：%s" % self._dfp_assets.dfp_character_label(self._dfp_assets.dfp_character()), "info")

    def dfp_toggle_pet_visible(self) -> None:
        pet = self._dfp_pet
        if pet is None:
            return
        if pet.isVisible():
            self._dfp_pet_visible_before_hide = True
            # ★ v1.1.33：藏起来之前说一句告别（`voice_bye_*`，录了才有声；气泡随即被收起来，语音照旧播完）
            self._dfp_play_voice("bye")
            pet.hide()
            if self._dfp_bubble is not None:
                self._dfp_bubble.dfp_hide_now()
        else:
            pet.show()
            if self._dfp_settings.dfp_get("keep_on_screen", True):
                dfp_ensure_window_on_screen(pet)
            # ★ v1.1.33：回来时打声招呼（`voice_greet_*`）
            self._dfp_play_voice("greet")
            self.dfp_notify("我回来啦～", "info")

    def dfp_toggle_lock(self) -> None:
        locked = not bool(self._dfp_settings.dfp_get("lock_position", False))
        self._dfp_settings.dfp_set("lock_position", locked)
        self.dfp_notify("位置已锁定，拖不动啦" if locked else "位置已解锁", "info")

    def dfp_toggle_sleep(self) -> None:
        asleep = self._dfp_brain.dfp_toggle_sleep()
        self.dfp_notify("晚安…zZ" if asleep else "我睡醒啦！", "info")

    def dfp_pet_head(self) -> None:
        text = self._dfp_brain.dfp_pet_head()
        self._dfp_play_voice("poke")
        if self._dfp_settings.dfp_get("sound_enabled", False):
            self._dfp_play_beep()

    def dfp_feed(self) -> None:
        self._dfp_brain.dfp_feed(drink=False)
        self._dfp_play_voice("confirm")

    def dfp_drink(self) -> None:
        self._dfp_brain.dfp_feed(drink=True)
        self._dfp_play_voice("confirm")

    def dfp_play(self) -> None:
        self._dfp_brain.dfp_play()
        self._dfp_play_voice("poke")

    def dfp_coax(self) -> None:
        """逗它撒娇（右键菜单入口）：说一句 act_cute 台词，不动数值、不计入互动统计。"""
        self._dfp_brain.dfp_say("act_cute")

    # --- 形象声音 ---
    def dfp_play_notice(self, status: str) -> Optional[str]:
        """播一条任务状态提示音（素材包里的 `notice-*.wav`）。

        状态口径（与素材包清单里的 6 类一一对应）：
          completed   任务/备份/导出跑完了        ← 后台任务成功
          failed      任务失败                    ← 后台任务失败
          interrupted 被打断                      ← 退出程序、停止生成
          needs_help  需要帮忙（接口/Key 出问题） ← 聊天或接口报错
          low_balance 余额变少                    ← 余额提醒说「少了」的那一刻
          approval    需要确认                    ← 弹确认框之前
        `voice_on_notice` 关掉就不响；没装素材包也什么都不发生。
        """
        name = str(status or "").strip().lower()
        if not name:
            return None
        # ★ v1.1.15：提示音与状态形象一起给（一个单点接线：六类状态各自对应一张新形象图）。
        #   ★ 形态一定要放在「播放器在不在」的前面判：没装多媒体后端 / 关掉声音时，
        #     形态照样应该换（两件事互不依赖）。
        #   low_balance 不在表里（余额少了不必变形，免得吵）。
        form = DFP_NOTICE_FORM.get(name, "")
        if form:
            self.dfp_flash_form(form, silent=True)
        if self._dfp_voice is None:
            return None
        played = self._dfp_voice.dfp_play("%s%s" % (DFP_ASSET_PACK_NOTICE_CATEGORY_PREFIX, name))
        if played is None:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_debug("提示音未播放（%s）：可能未启用 / 素材包没装 / 无解码器" % name)
            return None
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("提示音（%s）：%s" % (DFP_ASSET_PACK_NOTICE_LABELS.get(name, name), played))
        return played

    def _dfp_play_notice_approval(self) -> None:
        """确认框出现前的提示音（由 `dfp_confirm()` 回调）。"""
        self.dfp_play_notice("approval")

    def _dfp_play_voice(self, category: str) -> Optional[str]:
        """播一条语音；**返回播放的文件名**（没播成返回 None）。

        ★ v1.1.33：开始返回结果了 —— 自言自语那一趟要先试「情绪类」（饿 / 累 / 想你 / 撒娇…），
          没录就退回 `idle`，靠的就是这个返回值判断「刚才到底有没有播出去」。
        """
        if self._dfp_voice is None:
            return None
        played = self._dfp_voice.dfp_play(str(category))
        if played is None:
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_debug("语音未播放（%s）：可能未启用/素材缺失/无解码器" % category)
            return None
        # ★ 语音台词（人工听写的 voices[].text）：先进日志，方便回溯「刚才说了什么」；
        #   设置里开了「语音台词显示在气泡里」就把它当字幕显示出来。
        text = str(self._dfp_voice.dfp_last_text() or "")
        if not text:
            return played
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_debug("语音台词（%s）：%s" % (category, text))
        if bool(self._dfp_settings.dfp_get("voice_show_text", False)):
            self._dfp_show_bubble(text)
        return played

    def _dfp_on_chat_voice(self, category: str) -> None:
        """聊天窗口的语音请求（v1.1.15 起包了一层）：顺手挂/摘「等回复」的状态形象。

        `ask` = 刚发出去、正等回复 → 挂「等待」形态（不自动过期，等 done / 出错来摘）；
        `done` = 回复到了 → 摘掉。
        """
        name = str(category or "").strip().lower()
        if name == "ask":
            self.dfp_flash_form("waiting", 0.0, silent=True)
        elif name == "done":
            self.dfp_clear_form("waiting")
        self._dfp_play_voice(category)

    def dfp_voice_text(self, category: str) -> str:
        """给界面用：当前素材表里某一类语音即将播出的台词（随机一条），没有就返回空串。"""
        if self._dfp_voice is None:
            return ""
        library = self._dfp_voice.dfp_library()
        if library is None or not library.dfp_is_ready():
            return ""
        item = library.dfp_voice(str(category))
        return str((item or {}).get("text") or "")

    def dfp_voice_lines(self, category: str = "") -> List[Dict[str, str]]:
        """给界面用：某类（留空＝全部）语音的「文件名 + 台词」清单。"""
        if self._dfp_voice is None:
            return []
        library = self._dfp_voice.dfp_library()
        if library is None or not library.dfp_is_ready():
            return []
        rows: List[Dict[str, str]] = []
        for item in library.dfp_voices(str(category)):
            name = os.path.basename(str(item.get("file") or ""))
            if not name:
                continue
            rows.append({"file": name, "text": str(item.get("text") or "")})
        return rows


    def _dfp_toggle_voice(self) -> None:
        enabled = not bool(self._dfp_settings.dfp_get("voice_enabled", True))
        self._dfp_settings.dfp_set("voice_enabled", enabled)
        self.dfp_notify("形象声音已%s" % ("打开" if enabled else "关闭"), "info")
        if enabled:
            self._dfp_play_voice("confirm")

    def dfp_voice_player(self) -> DFPVoicePlayer:
        return self._dfp_voice

    def dfp_reload_lines(self, notify: bool = True) -> str:
        """重新载入 `台词库.json`（改完台词点一下就生效，不用重启）。返回摘要。"""
        dfp_lines_load(self._dfp_settings, force=True)
        summary = dfp_lines_summary()
        if notify:
            if dfp_lines_error():
                self.dfp_notify("台词库 JSON 读取失败，已回退内置：%s" % dfp_lines_error(), "warn")
            else:
                self.dfp_notify("台词库已重载：%s" % summary, "success")
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("台词库已重载：%s" % summary)
        return summary

    def dfp_reload_dreams(self, notify: bool = True) -> str:
        """重新载入 `梦境.json`（改完梦境点一下就生效，不用重启）。返回摘要。"""
        dfp_dreams_load(self._dfp_settings, force=True)
        summary = dfp_dreams_summary()
        if notify:
            if dfp_dreams_error():
                self.dfp_notify("梦境.json 读取失败，已回退内置梦境：%s" % dfp_dreams_error(), "warn")
            else:
                self.dfp_notify("梦境库已重载：%s" % summary, "success")
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("梦境库已重载：%s" % summary)
        return summary

    def dfp_reload_levels(self, notify: bool = True) -> str:
        """重新载入 `等级.json`（改完称号点一下就生效，不用重启）。返回摘要。

        改完顺便把状态面板 / 修改器 / 设置窗口里的称号刷新一下，
        不然用户会看到「点了没反应」（实际是界面没重画）。
        """
        dfp_levels_load(self._dfp_settings, force=True)
        summary = dfp_levels_summary()
        # 改完把状态面板 / 修改器窗口里的称号重画一下，不然用户会看到「点了没反应」
        status_window = getattr(self, "_dfp_status_window", None)
        if status_window is not None:
            try:
                status_window.dfp_refresh(self._dfp_brain.dfp_state_snapshot())
            except Exception:
                pass
        trainer = getattr(self, "_dfp_trainer", None)
        if trainer is not None:
            try:
                trainer.dfp_refresh()
            except Exception:
                pass
        if notify:
            if dfp_levels_error():
                self.dfp_notify("等级称号 JSON 读取失败，已回退内置：%s" % dfp_levels_error(), "warn")
            else:
                self.dfp_notify("等级称号已重载：%s" % summary, "success")
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("等级称号已重载：%s" % summary)
        return summary

    # --- 素材与自主行为 ---
    def dfp_scan_assets(self, notify: bool = True) -> bool:
        ok = self._dfp_assets.dfp_rebuild()
        if self._dfp_pet is not None:
            self._dfp_pet.dfp_set_asset_library(self._dfp_assets)
        # ★ 台词库 JSON 就放在素材目录里，所以重扫素材顺便把它也重载一遍
        dfp_lines_load(self._dfp_settings, force=True)
        dfp_levels_load(self._dfp_settings, force=True)
        if notify:
            if ok:
                self.dfp_notify("素材表已重新扫描：%s" % self._dfp_assets.dfp_summary(), "success")
            else:
                self.dfp_notify("素材表扫描失败：%s" % (self._dfp_assets.dfp_last_error() or "目录不存在"), "warn")
        return ok

    def _dfp_pick_behavior(self, recent: Sequence[str] = ()) -> Optional[Dict[str, Any]]:
        """给大脑用：按类别权重随机挑一个素材动作片作为自主行为。"""
        library = self._dfp_assets
        if library is None or not library.dfp_is_ready():
            return None
        raw = str(self._dfp_settings.dfp_get("behavior_asset_categories", "") or "")
        wanted = [item for item in re.split(r"[\s,，、]+", raw) if item]
        seasonal = bool(self._dfp_settings.dfp_get("behavior_seasonal_enabled", True))
        available = library.dfp_animations(seasonal=seasonal)
        if wanted:
            available = [item for item in available if str(item.get("category")) in wanted]
        if not available:
            available = library.dfp_animations(seasonal=False)
        if not available:
            return None
        pool_categories = sorted({str(item.get("category") or "其他") for item in available})
        weights = [max(1, dfp_safe_int(DFP_BEHAVIOR_WEIGHTS.get(name, 2), 2)) for name in pool_categories]
        pick = random.random() * float(sum(weights))
        chosen = pool_categories[-1]
        running = 0.0
        for name, weight in zip(pool_categories, weights):
            running += weight
            if pick <= running:
                chosen = name
                break
        subset = [item for item in available if str(item.get("category") or "其他") == chosen]
        avoided = set(recent or ())
        fresh = [item for item in subset if str(item.get("id")) not in avoided]
        return dict(dfp_random_pick(fresh or subset, subset[0]))

    def _dfp_on_behavior_wanted(self, item: Any, label: str, duration: float) -> None:
        pet = self._dfp_pet
        if pet is None or not isinstance(item, dict) or pet.dfp_is_asleep():
            return
        if pet.dfp_is_locked() and pet.dfp_current_action() == DFPAction.DRAG:
            return
        ok = pet.dfp_play_asset(item, label, float(duration or 10.0))
        if not ok:
            return
        category = str(item.get("category") or "")
        # ★ v1.1.15：自主行为里「休息 / 工作」这两类换成状态形象（新形象集里的图）
        #   （用基础形象演「工作」看着像在发呆，换一张图就懂了）
        if category == "休息":
            self.dfp_flash_form("resting", float(duration or 10.0), silent=True)
        elif category == "工作":
            self.dfp_flash_form("working", float(duration or 10.0), silent=True)
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("自主行为：%s（%s）" % (label or item.get("id"), category))
        try:
            pet.setToolTip("正在做：%s\n（素材：%s）" % (label or item.get("id"), item.get("id")))
        except Exception:
            pass
        if category in ("交互", "玩耍"):
            self._dfp_play_voice("poke")
        if self._dfp_status_window is not None and self._dfp_status_window.isVisible():
            self._dfp_status_window.dfp_refresh(self._dfp_brain.dfp_state_snapshot())

    def dfp_random_behavior(self) -> None:
        """直接从素材表里随机表演一个动作片。"""
        item = self._dfp_pick_behavior(self._dfp_brain.dfp_recent_behaviors())
        if item is None:
            self.dfp_random_action()
            return
        label = str(item.get("label") or item.get("id") or "")
        self._dfp_on_behavior_wanted(item, label, 10.0)
        self.dfp_notify("看我的：%s" % label, "info")

    def dfp_trigger_behavior(self) -> None:
        """立即让大脑选一个自主行为（不等定时器）。"""
        self._dfp_brain._dfp_check_autonomous_action(dfp_now_ts() + 1e9)

    # --- 修改器 ---
    def dfp_open_trainer(self) -> None:
        if self._dfp_trainer_window is None:
            self._dfp_trainer_window = DFPTrainerDialog(self._dfp_settings, self._dfp_brain, self._dfp_assets)
            self._dfp_trainer_window.dfpStateEdited.connect(self._dfp_on_trainer_edited)
        self._dfp_trainer_window.dfp_refresh()
        self._dfp_trainer_window.show()
        self._dfp_trainer_window.raise_()
        self._dfp_trainer_window.activateWindow()

    def _dfp_on_trainer_edited(self, key: str, value: Any) -> None:
        if key == "max_all":
            self.dfp_notify(self._dfp_brain.dfp_pick_line("maxed"), "success")
        elif key == "max_level":
            self.dfp_notify("等级已拉满：Lv%s（%s）" % (value, self._dfp_brain.dfp_level_title()), "success")
        elif key == "lock_all":
            self.dfp_notify("已%s全部数值锁定" % ("开启" if value else "解除"), "info")
        elif key == "reset":
            self.dfp_notify("桌宠状态已恢复初始", "success")
        elif key.startswith("lock_"):
            self.dfp_notify("%s%s" % (key[5:], "已锁定" if value else "已解锁"), "info")
        if self._dfp_status_window is not None and self._dfp_status_window.isVisible():
            self._dfp_status_window.dfp_refresh(self._dfp_brain.dfp_state_snapshot())

    def dfp_random_action(self) -> None:
        if self._dfp_pet is None:
            return
        self._dfp_pet.dfp_cancel_asset_play()
        self._dfp_pet.dfp_random_idle_action()
        self.dfp_notify("看我的！", "info")

    def dfp_dream_now(self) -> None:
        """让它立即做一个梦（忽略做梦间隔；梦境功能关掉时什么也不做）。"""
        if self._dfp_brain is None:
            return
        text = self._dfp_brain.dfp_dream_now()
        if text:
            self.dfp_notify("做了个梦…", "info")

    # --- ★ v1.1.26：灵魂 ---
    def dfp_reload_soul(self, notify: bool = True) -> str:
        """重新载入 `灵魂.json`（改完灵魂库点一下就生效，不用重启）。返回摘要。"""
        dfp_soul_load(self._dfp_settings, force=True)
        summary = dfp_soul_summary()
        if notify:
            if dfp_soul_error():
                self.dfp_notify("灵魂.json 读取失败，已回退内置灵魂：%s" % dfp_soul_error(), "warn")
            else:
                self.dfp_notify("灵魂库已重载：%s" % summary, "success")
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("灵魂库已重载：%s" % summary)
        self._dfp_refresh_soul_label()
        return summary

    def dfp_soul_now(self) -> None:
        """让她现在就说一句心里话（忽略开启开关与间隔）。"""
        if self._dfp_brain is None:
            return
        text = self._dfp_brain.dfp_soul_moment(force=True)
        if not text:
            self.dfp_notify("她现在没什么想说的（灵魂库为空？可在设置里「重新载入灵魂库」）", "warn")
        self._dfp_refresh_soul_label()
        if self._dfp_status_window is not None and self._dfp_status_window.isVisible():
            self._dfp_status_window.dfp_refresh(self._dfp_brain.dfp_state_snapshot())

    def dfp_soul_reset(self) -> None:
        """把灵魂重新养一遍（心事与里程碑清空，小癖好与关系保留）。"""
        if self._dfp_brain is None:
            return
        self._dfp_brain.dfp_soul_reset()
        self._dfp_refresh_soul_label()
        self.dfp_notify("心事与里程碑已清空（小癖好和关系都留着）", "success")

    def _dfp_refresh_soul_label(self) -> None:
        """设置窗口里那一行灵魂现状（窗口没开着就默默跳过）。"""
        window = getattr(self, "_dfp_settings_window", None)
        if window is None or self._dfp_brain is None:
            return
        try:
            window.dfp_refresh_dynamic_info()
        except Exception:
            pass

    # --- ★ v1.1.28：三魂七魄 ---
    def dfp_reload_spirits(self, notify: bool = True) -> str:
        """重新载入 `魂魄.json`（改完魂魄库点一下就生效，不用重启）。返回摘要。"""
        dfp_spirits_load(self._dfp_settings, force=True)
        summary = dfp_spirits_summary()
        if notify:
            if dfp_spirits_error():
                self.dfp_notify("魂魄.json 读取失败，已回退内置魂魄：%s" % dfp_spirits_error(), "warn")
            else:
                self.dfp_notify("魂魄库已重载：%s" % summary, "success")
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("魂魄库已重载：%s" % summary)
        self._dfp_refresh_spirits_label()
        return summary

    def dfp_spirits_now(self) -> None:
        """让她就三魂七魄说一句（没攒着话就挑最旺/最弱的那一魄说）。"""
        if self._dfp_brain is None:
            return
        text = self._dfp_brain.dfp_spirits_now()
        if not text:
            self.dfp_notify("她现在说不出魂魄的话（魂魄库为空？可在设置里「重新载入魂魄库」）", "warn")
        self._dfp_refresh_spirits_label()
        if self._dfp_status_window is not None and self._dfp_status_window.isVisible():
            self._dfp_status_window.dfp_refresh(self._dfp_brain.dfp_state_snapshot())

    def dfp_spirits_reborn(self) -> None:
        """重新投胎：命盘重抽（3 旺 / 4 平 / 3 弱），灵气回到命盘值。"""
        if self._dfp_brain is None:
            return
        answer = dfp_confirm(
            self._dfp_pet,
            "重新投胎",
            "重新抽一张命盘？\n\n她的三魂七魄会按新命盘重置（说过的话、关系、心事都留着）。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._dfp_brain.dfp_spirit_reborn()
        self._dfp_refresh_spirits_label()
        self.dfp_notify("命盘已重抽：%s" % self._dfp_brain.dfp_spirits_summary_text().replace("\n", " ｜ "), "success")

    def dfp_open_spirits_dialog(self) -> None:
        """「三魂七魄」窗口（托盘 / 设置里都能点）。"""
        if self._dfp_brain is None:
            return
        dialog = getattr(self, "_dfp_spirits_window", None)
        try:
            if dialog is not None and dialog.isVisible():
                dialog.dfp_refresh()
                dialog.raise_()
                dialog.activateWindow()
                return
        except Exception:
            dialog = None
        try:
            dialog = DFPSpiritsDialog(self._dfp_brain, self._dfp_settings, self._dfp_logger, self._dfp_pet)
            dialog.dfpActionRequested.connect(self._dfp_on_settings_action)
        except Exception as exc:
            self.dfp_notify("三魂七魄窗口打开失败：%s" % exc, "warn")
            return
        self._dfp_spirits_window = dialog
        pet = self._dfp_pet
        if pet is not None and pet.isVisible():
            geo = pet.geometry()
            dfp_place_window(dialog, QRect(max(0, geo.left() - dialog.width() - 12), geo.top(), dialog.width(), dialog.height()))
            dfp_ensure_window_on_screen(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _dfp_refresh_spirits_label(self) -> None:
        """设置窗口里那几行魂魄现状（窗口没开着就默默跳过）。"""
        window = getattr(self, "_dfp_settings_window", None)
        if window is None or self._dfp_brain is None:
            return
        try:
            window.dfp_refresh_dynamic_info()
        except Exception:
            pass

    # --- ★ v1.1.29：修真层（道 / 性 / 元神 / 五脏六腑 / 气血 / 命·运·数 / 因果·业·缘 / 神·仙·鬼·妖）---
    def dfp_reload_dao(self, notify: bool = True) -> str:
        """重新载入 `道.json`（改完道库点一下就生效，不用重启）。返回摘要。"""
        dfp_dao_load(self._dfp_settings, force=True)
        summary = dfp_dao_summary()
        if notify:
            if dfp_dao_error():
                self.dfp_notify("道.json 读取失败，已回退内置道库：%s" % dfp_dao_error(), "warn")
            else:
                self.dfp_notify("道库已重载：%s" % summary, "success")
        if self._dfp_logger is not None:
            self._dfp_logger.dfp_info("道库已重载：%s" % summary)
        self._dfp_refresh_dao_label()
        return summary

    def dfp_dao_now(self) -> None:
        """让她就修真层说一句（没攒着话就说最当紧的：最虚的脏腑 / 今日运势 / 气血）。"""
        if self._dfp_brain is None:
            return
        text = self._dfp_brain.dfp_dao_now()
        if not text:
            self.dfp_notify("她现在说不出这一层的话（道库为空？可在设置里「重新载入道库」）", "warn")
        self._dfp_refresh_dao_label()
        if self._dfp_status_window is not None and self._dfp_status_window.isVisible():
            self._dfp_status_window.dfp_refresh(self._dfp_brain.dfp_state_snapshot())

    def dfp_dao_reborn(self) -> None:
        """重定命数：定数重抽（灵魂 / 三魂七魄 / 关系都留着）。"""
        if self._dfp_brain is None:
            return
        answer = dfp_confirm(
            self._dfp_pet,
            "重定命数",
            "重新定一次命数？\n\n本命数、命格、道行、气血、脏腑、业与缘都会回到起点（说过的话、灵魂、三魂七魄、关系都留着）。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._dfp_brain.dfp_dao_reborn()
        self._dfp_refresh_dao_label()
        self.dfp_notify("命数已重定：%s" % self._dfp_brain.dfp_dao_ming_line(), "success")

    def dfp_open_dao_dialog(self) -> None:
        """「她的道」窗口（托盘 / 设置里都能点）。"""
        if self._dfp_brain is None:
            return
        dialog = getattr(self, "_dfp_dao_window", None)
        try:
            if dialog is not None and dialog.isVisible():
                dialog.dfp_refresh()
                dialog.raise_()
                dialog.activateWindow()
                return
        except Exception:
            dialog = None
        try:
            dialog = DFPDaoDialog(self._dfp_brain, self._dfp_settings, self._dfp_logger, self._dfp_pet)
            dialog.dfpActionRequested.connect(self._dfp_on_settings_action)
        except Exception as exc:
            self.dfp_notify("「她的道」窗口打开失败：%s" % exc, "warn")
            return
        self._dfp_dao_window = dialog
        pet = self._dfp_pet
        if pet is not None and pet.isVisible():
            geo = pet.geometry()
            dfp_place_window(dialog, QRect(max(0, geo.left() - dialog.width() - 12), geo.top(), dialog.width(), dialog.height()))
            dfp_ensure_window_on_screen(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _dfp_refresh_dao_label(self) -> None:
        """设置窗口里那几行道层现状（窗口没开着就默默跳过）。"""
        window = getattr(self, "_dfp_settings_window", None)
        if window is None or self._dfp_brain is None:
            return
        try:
            window.dfp_refresh_dynamic_info()
        except Exception:
            pass

    def dfp_say(self, text: str, category: str = "idle") -> None:
        self._dfp_brain.dfp_say(category, text)
    def dfp_center_pet(self) -> None:
        """把桌宠摆回屏幕中央（★ 桌宠跑到屏幕外时的「救命」入口，托盘菜单里也能点）。"""
        pet = self._dfp_pet
        if pet is None:
            return
        areas = dfp_screen_available_rects()
        area = areas[0] if areas else QRect(0, 0, 1920, 1040)
        rect = QRect(pet.frameGeometry())
        rect.moveCenter(area.center())
        dfp_place_window(pet, rect)
        dfp_ensure_window_on_screen(pet)
        # 顺便确保它是显示着的（隐藏时也一并唤回来）
        if not pet.isVisible():
            self.dfp_toggle_pet_visible()
        self._dfp_save_pet_geometry()
        self.dfp_notify("已回到屏幕中央", "info")

    def dfp_move_pet_to_corner(self) -> None:
        pet = self._dfp_pet
        if pet is None:
            return
        areas = dfp_screen_available_rects()
        area = areas[0] if areas else QRect(0, 0, 1920, 1040)
        rect = QRect(pet.frameGeometry())
        rect.moveRight(area.right() - 30)
        rect.moveBottom(area.bottom() - 10)
        dfp_place_window(pet, rect)
        self._dfp_save_pet_geometry()
        self.dfp_notify("已移到右下角", "info")

    def _dfp_play_beep(self) -> None:
        try:
            self._dfp_app.beep()
        except Exception:
            pass

    def dfp_notify(self, text: str, level: str = "info") -> None:
        """统一提示：气泡 + 日志（+ 可选系统提示音），不弹模态框。"""
        text = str(text or "").strip()
        if not text:
            return
        if level in ("warn", "error"):
            self._dfp_logger.dfp_warning(text)
        else:
            self._dfp_logger.dfp_info(text)
        if self._dfp_settings.dfp_get("bubble_enabled", True):
            self._dfp_show_bubble(text)
        if level in ("warn", "error", "success") and self._dfp_settings.dfp_get("sound_enabled", False):
            self._dfp_play_beep()
        if self._dfp_tray is not None and level in ("warn", "error"):
            try:
                self._dfp_tray.showMessage(DFP_APP_TITLE, text, dfp_app_icon(), 4000)
            except Exception:
                pass

    # --- 大脑回调 ---
    def dfp_on_brain_say(self, text: str, category: str) -> None:
        self._dfp_show_bubble(text)
        name = str(category or "")
        # ★ v1.1.34：这一趟「真正说到哪一类」—— 自言自语时由大脑报上来（下面 `resolved`）
        resolved = ""
        if name == DFP_CHATTER_CATEGORY:
            # ★ v1.1.14：自己没事找话说的时候，顺便播一条语音
            # ★ v1.1.33：先看这一趟**真正说到哪一类**（饿 / 累 / 想你 / 难过 / 撒娇 / 时段…）——
            #   有对应的语音就播它；**没录就退回 idle**（与旧行为一致，不会突然变哑巴）。
            resolved = self._dfp_brain.dfp_last_line_category() if self._dfp_brain is not None else ""
            played = None
            wanted = dfp_say_voice_category(resolved)
            if wanted:
                played = self._dfp_play_voice(wanted)
            if not played:
                self._dfp_play_voice("idle")
        elif name == DFP_STARTUP_CATEGORY:
            # ★ v1.1.22：启动时也念一句（用户录了 `voice_startup_*` 就出声；没录就只是气泡）
            self._dfp_play_voice(DFP_STARTUP_CATEGORY)
        else:
            # ★ v1.1.33：显式类别的情绪 / 场景语音（撒娇 · 饿了 · 累了 · 想你 · 难过 · 打招呼 · 告别）——
            #   对应关系全写在 `DFP_SAY_VOICE_MAP`（加新类别只改那一张表）。
            wanted = dfp_say_voice_category(name)
            if wanted:
                self._dfp_play_voice(wanted)
        # ★ v1.1.35：按台词换成对应的**新形象图**（唯一映射表 `DFP_SAY_FACE_MAP`；
        #   自言自语用大脑报的**真正类别** `resolved`）
        self._dfp_apply_look(resolved or name)
        if self._dfp_pet is not None and category in ("pet", "feed", "love", "happy"):
            # 素材模式另有 `DFP_SAY_FACE_MAP`（更贴题）；这一句是**矢量模式的兜底**，
            # 也是「清单里没有对应形象图」时的保底
            self._dfp_pet.dfp_set_expression(DFPExpression.HAPPY, 3.0)

    def _dfp_apply_look(self, category: str) -> bool:
        """按台词类别换成对应的**形象图**（★ v1.1.35）。

        · `DFP_SAY_FACE_MAP` 里有这一类就换（保持 `DFP_SAY_FACE_HOLD` 秒后回落）；
        · 一个类别有多个候选时**轮着挑**（`dfp_face_for_category()` 自己计数）：
          撒娇那 9 张子情绪、难过那 2 张（含泪 / 大哭）、累了那 2 张（趴桌 / 晕倒）都靠它；
        · 开关 `look_expression_follow`（键名沿用 v1.1.34，语义已从「台词表情」变成
          「台词换形象」）关掉就一点都不换；
        · 没映射的类别 → 什么都不做（不乱动画面），返回 False。

        返回是否真的换了（测试与日志用）。
        """
        pet = self._dfp_pet
        library = self._dfp_assets
        name = str(category or "").strip()
        if pet is None or library is None or not name:
            return False
        if not bool(self._dfp_settings.dfp_get("look_expression_follow", True)):
            return False
        item = library.dfp_face_for_category(name)
        if not item:
            return False
        return bool(pet.dfp_show_say_face(str(item.get("id") or ""), DFP_SAY_FACE_HOLD))

    def _dfp_apply_look_settings(self) -> None:
        """把「台词换形象」那个开关刷到桌宠上。

        关掉时必须**把已经挂上去的那张收掉** —— 否则桌宠会一直停在最后那句台词的形象上，
        用户会以为开关坏了（与状态形象那个开关一个道理，见 `_dfp_apply_form_settings`）。
        """
        pet = self._dfp_pet
        if pet is None:
            return
        if not bool(self._dfp_settings.dfp_get("look_expression_follow", True)):
            pet.dfp_clear_say_face()

    def _dfp_show_bubble(self, text: str) -> None:
        if not self._dfp_settings.dfp_get("bubble_enabled", True):
            return
        if self._dfp_bubble is None or self._dfp_pet is None or not self._dfp_pet.isVisible():
            return
        duration = dfp_safe_int(self._dfp_settings.dfp_get("bubble_duration_ms", 6000), 6000)
        self._dfp_bubble.dfp_show_text(text, duration)
        self._dfp_pet.dfp_begin_talk(max(1.6, len(str(text)) * 0.16))

    def _dfp_on_brain_expression(self, expression: Any, hold: float) -> None:
        if self._dfp_pet is not None:
            self._dfp_pet.dfp_set_expression(expression, float(hold or 3.0))

    def _dfp_on_brain_action(self, action: Any, duration: float) -> None:
        if self._dfp_pet is not None and not self._dfp_pet.dfp_is_locked():
            self._dfp_pet.dfp_play_action(action, float(duration or 2.0))

    def _dfp_on_state_changed(self, snapshot: Dict[str, Any]) -> None:
        if self._dfp_status_window is not None and self._dfp_status_window.isVisible():
            self._dfp_status_window.dfp_refresh(snapshot)
        pet = self._dfp_pet
        if pet is not None:
            asleep = bool(snapshot.get("asleep"))
            if asleep != pet.dfp_is_asleep():
                pet.dfp_set_asleep(asleep)
            mood = dfp_safe_float(snapshot.get("mood"), 0.0)
            if mood <= 18.0 and pet.dfp_expression() == DFPExpression.NORMAL and not asleep:
                pet.dfp_set_expression(DFPExpression.SAD, 4.0)

    def _dfp_on_sleep_changed(self, asleep: bool) -> None:
        if self._dfp_pet is not None:
            self._dfp_pet.dfp_set_asleep(bool(asleep))
        if self._dfp_settings.dfp_get("sound_enabled", False):
            self._dfp_play_beep()

    def _dfp_on_brain_event(self, kind: str, detail: str) -> None:
        if not self._dfp_settings.dfp_get("event_log_enabled", True):
            return
        if kind in ("feed", "play", "sleep", "wake", "level_up"):
            if self._dfp_tray is not None:
                try:
                    self._dfp_tray.showMessage(DFP_APP_TITLE, "%s：%s" % (kind, detail), dfp_app_icon(), 3000)
                except Exception:
                    pass

    def _dfp_on_level_up(self, level: int) -> None:
        title = self._dfp_brain.dfp_level_title(level)
        self.dfp_notify("肥鱼娘升级啦！现在是 Lv%d（%s）" % (level, title), "success")
        # ★ v1.1.15：升级 = 值得庆祝一下（鲸鱼形态：鼓掌的整只小鲸鱼）
        self.dfp_flash_form("celebrate", 5.0, silent=True)
        if self._dfp_pet is not None:
            self._dfp_pet.dfp_play_action(DFPAction.DANCE, 3.4)
            self._dfp_pet.dfp_set_expression(DFPExpression.LOVE, 4.0)
        if self._dfp_menu is not None:
            actions = self._dfp_menu.actions()
            if actions:
                actions[0].setText(
                    "🐟 %s　Lv%d" % (dfp_truncate_text(str(self._dfp_settings.dfp_get("pet_nickname", "")), 10), level)
                )

    # --- 走动与跟随 ---
    def _dfp_on_pet_pressed(self) -> None:
        """鼠标按在桌宠身上：立刻停下走动，避免「伸手去摸它就跑了」。

        以前不暂停的后果：窗口一边自己移动、一边收鼠标事件，本地坐标一直在变，
        会被误判成拖拽 ⇒ 明明只是点一下，却变成拖拽（摸头完全不触发）。
        """
        pet = self._dfp_pet
        if pet is None:
            return
        if self._dfp_motion_timer.isActive():
            self._dfp_motion_timer.stop()
            if pet.dfp_current_action() == DFPAction.WALK:
                pet.dfp_play_action(DFPAction.IDLE)
        self._dfp_walk_target_x = pet.x()
        self._dfp_walk_direction = 0
        self._dfp_reschedule_walk_timer()

    def _dfp_on_pet_drag_started(self) -> None:
        """真正开始拖拽：同样停掉走动，免得“人机争抢”窗口位置。"""
        pet = self._dfp_pet
        if pet is None:
            return
        if self._dfp_motion_timer.isActive():
            self._dfp_motion_timer.stop()
        if self._dfp_follow_timer.isActive():
            self._dfp_follow_timer.stop()
        self.dfp_apply_walk_settings()

    def _dfp_reschedule_walk_timer(self) -> None:
        pet = self._dfp_pet
        if pet is None:
            return
        if not self._dfp_settings.dfp_get("auto_walk_enabled", True):
            self._dfp_walk_timer.stop()
            return
        interval = max(8, dfp_safe_int(self._dfp_settings.dfp_get("auto_walk_interval_sec", 45), 45)) * 1000
        self._dfp_walk_timer.start(interval)

    def _dfp_begin_auto_walk(self) -> None:
        pet = self._dfp_pet
        if pet is None or not pet.isVisible() or pet.dfp_is_locked():
            return
        if pet.dfp_is_asleep() or self._dfp_motion_timer.isActive():
            return
        # 鼠标就在它身上 / 正在被拖 / 正在被按住：别动，先把人让给用户
        if pet.dfp_is_dragging() or pet.dfp_is_pressed():
            return
        if pet.frameGeometry().contains(QCursor.pos()):
            return
        areas = dfp_screen_available_rects()
        area = areas[0] if areas else QRect(0, 0, 1920, 1040)
        for candidate in areas:
            if candidate.contains(pet.frameGeometry().center()):
                area = candidate
                break
        low = area.left() + 10
        high = max(low + 1, area.right() - pet.width() - 10)
        self._dfp_walk_target_x = random.randint(low, high)
        self._dfp_walk_direction = 1 if self._dfp_walk_target_x > pet.x() else -1
        pet.dfp_set_flip(self._dfp_walk_direction > 0)
        pet.dfp_play_action(DFPAction.WALK)
        self._dfp_motion_timer.start()
        try:
            self._dfp_database.dfp_bump_daily("walks", 1)
        except Exception:
            pass

    def _dfp_on_follow_tick(self) -> None:
        pet = self._dfp_pet
        if pet is None or not pet.isVisible() or pet.dfp_is_locked() or pet.dfp_is_asleep():
            return
        if self._dfp_motion_timer.isActive():
            return
        cursor = QCursor.pos()
        frame = QRect(pet.frameGeometry())
        dx = cursor.x() - frame.center().x()
        dy = cursor.y() - frame.center().y()
        distance = math.hypot(dx, dy)
        if distance < 140:
            if pet.dfp_current_action() == DFPAction.WALK:
                pet.dfp_play_action(DFPAction.IDLE)
            return
        step = 6 if distance < 400 else 12
        target = QRect(frame)
        target.moveLeft(frame.left() + (step if dx > 0 else -step))
        target.moveTop(frame.top() + int(dy * 0.06))
        pet.dfp_set_flip(dx > 0)
        if pet.dfp_current_action() not in (DFPAction.WALK, DFPAction.JUMP):
            pet.dfp_play_action(DFPAction.WALK)
        dfp_place_window(pet, target)
        if self._dfp_settings.dfp_get("keep_on_screen", True):
            dfp_ensure_window_on_screen(pet)

    def _dfp_on_motion_tick(self) -> None:
        pet = self._dfp_pet
        if pet is None:
            return
        frame = QRect(pet.frameGeometry())
        remaining = self._dfp_walk_target_x - frame.left()
        if abs(remaining) <= 3:
            self._dfp_motion_timer.stop()
            pet.dfp_play_action(DFPAction.IDLE)
            self._dfp_save_pet_geometry()
            return
        step = 4 if abs(remaining) > 60 else 2
        frame.moveLeft(frame.left() + (step if remaining > 0 else -step))
        dfp_place_window(pet, frame)
        if self._dfp_settings.dfp_get("keep_on_screen", True):
            dfp_ensure_window_on_screen(pet)
        if self._dfp_settings.dfp_get("remember_position", True):
            self._dfp_on_pet_moved(frame.topLeft())

    def _dfp_save_pet_geometry(self) -> None:
        pet = self._dfp_pet
        if pet is None or not self._dfp_settings.dfp_get("remember_position", True):
            return
        rect = dfp_window_geometry_for_save(pet, QRect(pet.geometry()))
        text = dfp_geometry_to_text(rect)
        if text and text != self._dfp_last_geometry_text:
            self._dfp_last_geometry_text = text
            self._dfp_settings.dfp_set("window_geometry", text)

    # --- 备份与后台任务 ---
    def dfp_run_backup(self, reason: str = "手动备份") -> None:
        self.dfp_run_task("backup_%s" % reason, self._dfp_task_backup)

    def _dfp_backup_now(self, reason: str, silent: bool = False) -> Optional[str]:
        try:
            path = self._dfp_backups.dfp_create(reason)
            keep = dfp_safe_int(self._dfp_settings.dfp_get("backup_keep_count", DFP_BACKUP_KEEP_DEFAULT), DFP_BACKUP_KEEP_DEFAULT)
            removed = self._dfp_backups.dfp_prune(keep)
            if path and not silent:
                self.dfp_notify(
                    "备份完成：%s（清理 %d 份旧备份）" % (os.path.basename(path), removed),
                    "success",
                )
            return path
        except Exception:
            self._dfp_logger.dfp_exception("备份失败")
            return None

    def dfp_run_task(
        self,
        name: str,
        function: Callable[[Callable[[int, int, str], None]], Any],
        on_done: Optional[Callable[[str, Any], None]] = None,
        on_failed: Optional[Callable[[str, str], None]] = None,
    ) -> bool:
        if self._dfp_task_busy:
            self.dfp_notify("已有后台任务在执行：%s，请稍候" % self._dfp_task_busy, "warn")
            return False
        self._dfp_task_busy = str(name)
        self._dfp_task_callbacks = {"done": on_done, "failed": on_failed}
        # ★ v1.1.15：后台任务一开跑就切成「忙碌」的小鲸鱼（跑完/失败会被 completed/failed 盖掉）
        #   30 秒是安全上限：万一 worker 没回报出结果，也不会一直挂着鲸鱼。
        #   长任务的进度回调里会再续一次（见 _dfp_on_controller_task_progress）。
        self.dfp_flash_form("working", 30.0, silent=True)
        worker = DFPTaskWorker(name, function, self._dfp_task_signals)
        self._dfp_keep_worker(worker)
        self._dfp_task_pool.start(worker)
        return True

    def _dfp_keep_worker(self, worker: DFPTaskWorker) -> None:
        self._dfp_workers.append(worker)
        if len(self._dfp_workers) > 20:
            del self._dfp_workers[: len(self._dfp_workers) - 20]

    def _dfp_on_controller_task_progress(self, name: str, value: int, maximum: int, text: str) -> None:
        if self._dfp_settings_window is not None:
            try:
                self._dfp_settings_window.dfp_set_operation_progress(value, maximum, text or name)
            except Exception:
                pass
        # ★ v1.1.15：任务跑得久就续一下「忙碌」形态（已经切到庆祝/出错时不会被降级）
        self.dfp_flash_form("working", 30.0, silent=True)

    def _dfp_on_controller_task_done(self, name: str, result: Any) -> None:
        self._dfp_task_busy = ""
        # ★ v1.1.14：任务跑完响一声（素材包里的 notice-completed）
        self.dfp_play_notice("completed")
        callbacks = getattr(self, "_dfp_task_callbacks", {})
        handler = callbacks.get("done") if callbacks else None
        if callable(handler):
            try:
                handler(name, result)
            except Exception:
                pass
        else:
            self.dfp_notify(str(result or ("%s 完成" % name)), "success")
        if self._dfp_settings_window is not None:
            try:
                self._dfp_settings_window.dfp_set_status_text(str(result or name))
                self._dfp_settings_window.dfp_refresh_dynamic_info()
            except Exception:
                pass

    def _dfp_on_controller_task_failed(self, name: str, message: str) -> None:
        self._dfp_task_busy = ""
        # ★ v1.1.14：任务失败响一声（notice-failed）
        self.dfp_play_notice("failed")
        callbacks = getattr(self, "_dfp_task_callbacks", {})
        handler = callbacks.get("failed") if callbacks else None
        if callable(handler):
            try:
                handler(name, message)
            except Exception:
                pass
        self.dfp_notify("任务失败：%s（%s）" % (name, message), "error")
        if self._dfp_settings_window is not None:
            try:
                self._dfp_settings_window.dfp_set_status_text("失败：%s" % message)
            except Exception:
                pass

    # --- 窗口 ---
    def dfp_open_chat(self) -> None:
        if self._dfp_chat_window is None:
            self._dfp_chat_window = DFPChatWindow(
                self._dfp_settings, self._dfp_database, self._dfp_brain, self._dfp_chat, self._dfp_client, self._dfp_logger
            )
            self._dfp_chat_window.dfpStatusChanged.connect(self._dfp_on_chat_status)
            self._dfp_chat_window.dfpVoiceWanted.connect(self._dfp_on_chat_voice)
        self._dfp_chat_window.dfp_show_and_raise()

    def _dfp_on_chat_status(self, text: str, level: str) -> None:
        self._dfp_show_bubble(text)
        if level == "error":
            self._dfp_logger.dfp_warning("聊天：%s" % text)
            # ★ v1.1.14：接口 / Key 出问题 → 播「需要帮忙」提示音
            self.dfp_play_notice("needs_help")
        elif level == "success" and str(text).startswith("收到回复"):
            # ★ v1.1.16：一次对话刚结束 → 顺手查一次余额，把这次花的钱记成「聊天窗口」
            self.dfp_note_chat_spend()

    def dfp_open_settings(self) -> None:
        if self._dfp_settings_window is None:
            self._dfp_settings_window = DFPSettingsDialog(
                self._dfp_settings, self._dfp_brain, self._dfp_database, self._dfp_client, self._dfp_backups, self._dfp_logger, self._dfp_assets
            )
            self._dfp_settings_window.dfpActionRequested.connect(self._dfp_on_settings_action)
            self._dfp_settings_window.dfpTaskRequested.connect(self._dfp_on_settings_task)
        self._dfp_settings_window.dfp_show_and_raise()

    def dfp_open_status(self) -> None:
        if self._dfp_status_window is None:
            self._dfp_status_window = DFPStatusWindow(self._dfp_settings, self._dfp_brain, self._dfp_database)
            self._dfp_status_window.dfpActionRequested.connect(self._dfp_on_status_action)
        self._dfp_status_window.dfp_show_and_raise(self._dfp_brain.dfp_state_snapshot())

    def dfp_show_about(self) -> None:
        dialog = DFPAboutDialog(self._dfp_settings, self._dfp_database, self._dfp_client, self._dfp_brain)
        dialog.exec()

    def dfp_open_form_dialog(self) -> None:
        """打开「状态形象」预览：六个状态各自什么时候用、换成哪张新形象，并可以逐个试一下。"""
        if self._dfp_form_dialog is None or not self._dfp_form_dialog.isVisible():
            self._dfp_form_dialog = DFPWhaleFormDialog(self._dfp_assets, self._dfp_settings, self._dfp_logger, self.dfp_flash_form)
        self._dfp_form_dialog.dfp_refresh()
        self._dfp_form_dialog.show()
        self._dfp_form_dialog.raise_()
        self._dfp_form_dialog.activateWindow()

    def dfp_open_balance_log_dialog(self) -> None:
        """打开「扣钱记录」（余额变化清单）：什么时候扣了多少、扣完还剩多少。

        ★ v1.1.16：只读数据库，不发任何网络请求 —— 查余额走「🔎 立即查询 DS 余额」。
        """
        if self._dfp_balance_log_dialog is None or not self._dfp_balance_log_dialog.isVisible():
            self._dfp_balance_log_dialog = DFPBalanceLogDialog(self._dfp_database, self._dfp_settings, self._dfp_logger)
        self._dfp_balance_log_dialog.dfp_refresh()
        self._dfp_balance_log_dialog.show()
        self._dfp_balance_log_dialog.raise_()
        self._dfp_balance_log_dialog.activateWindow()
        return self._dfp_balance_log_dialog

    def dfp_show_asset_stats(self) -> None:
        stats = self._dfp_assets.dfp_stats()
        categories = "、".join("%s %d" % pair for pair in stats["categories"])
        text = "\n".join(
            [
                "素材目录：%s" % stats["root"],
                "清单文件：%s（%s）" % (stats["manifest"], "存在" if stats["manifest_exists"] else "不存在"),
                "内容：形象 %d · 表情动画 %d · 动作片 %d · 语音 %d · 表情包 %d"
                % (stats["characters"], stats["expressions"], stats["animations"], stats["voices"], stats["memes"]),
                "动作类别：%s" % (categories or "—"),
                "素材包：%s" % (stats.get("pack_summary") or "无"),
                "状态形象：%s" % (stats.get("form_summary") or "无"),
                "来源：%s" % (stats["source"] or "—"),
                "状态：%s" % ("就绪" if stats["ready"] else (stats["error"] or "未就绪")),
            ]
        )
        QMessageBox.information(self._dfp_pet or self._dfp_settings_window, "素材表统计", text)
        if self._dfp_settings_window is not None:
            self._dfp_settings_window.dfp_refresh_dynamic_info()

    def dfp_open_data_dir(self) -> None:
        self._dfp_open_path(dfp_data_dir())

    def _dfp_open_path(self, path: str) -> None:
        try:
            dfp_ensure_dir(path)
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:
            self.dfp_notify("无法打开目录：%s" % exc, "error")

    def _dfp_open_file(self, path: str) -> None:
        try:
            if not os.path.isfile(path):
                dfp_atomic_write_text(path, "")
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:
            self.dfp_notify("无法打开文件：%s" % exc, "error")

    def _dfp_on_settings_action(self, action: str) -> None:
        action = str(action)
        if action == "center_pet":
            self.dfp_center_pet()
        elif action == "corner_pet":
            self.dfp_move_pet_to_corner()
        elif action == "open_data_dir":
            self._dfp_open_path(dfp_data_dir())
        elif action == "open_backup_dir":
            self._dfp_open_path(dfp_backup_dir())
        elif action == "open_log_dir":
            self._dfp_open_path(dfp_log_dir())
        elif action == "open_asset_dir":
            self._dfp_open_path(self._dfp_assets.dfp_root())
        elif action == "open_character_dir":
            self._dfp_open_path(dfp_character_dir_path())
        elif action == "rescan_assets":
            self.dfp_scan_assets()
        elif action == "reload_lines":
            self.dfp_reload_lines()
        elif action == "reload_levels":
            self.dfp_reload_levels()
        elif action == "reload_dreams":
            self.dfp_reload_dreams()
        elif action == "dream_now":
            # ★ v1.1.25：想马上看一个梦（不等间隔）
            self.dfp_dream_now()
        elif action == "reload_soul":
            # ★ v1.1.26：改完 灵魂.json 即时生效
            self.dfp_reload_soul()
        elif action == "soul_now":
            # ★ v1.1.26：不等间隔，让她现在就说一句心里话
            self.dfp_soul_now()
        elif action == "soul_reset":
            self.dfp_soul_reset()
        elif action == "reload_spirits":
            # ★ v1.1.28：改完 魂魄.json 即时生效
            self.dfp_reload_spirits()
        elif action == "spirits_now":
            # ★ v1.1.28：不等间隔，让她就三魂七魄说一句
            self.dfp_spirits_now()
        elif action == "spirits_reborn":
            # ★ v1.1.28：重抽命盘（3 旺 / 4 平 / 3 弱）
            self.dfp_spirits_reborn()
        elif action == "spirits_view":
            # ★ v1.1.28：三魂七魄一览
            self.dfp_open_spirits_dialog()
        elif action == "reload_dao":
            # ★ v1.1.29：改完 道.json 即时生效
            self.dfp_reload_dao()
        elif action == "dao_now":
            # ★ v1.1.29：不等间隔，让她就修真层说一句
            self.dfp_dao_now()
        elif action == "dao_reborn":
            # ★ v1.1.29：重定命数（定数重抽）
            self.dfp_dao_reborn()
        elif action == "dao_view":
            # ★ v1.1.29：「她的道」一览窗口
            self.dfp_open_dao_dialog()
        elif action == "asset_stats":
            self.dfp_show_asset_stats()
        elif action == "whale_form":
            # ★ v1.1.15：看看鲸鱼形态（七种状态分别什么时候用）
            self.dfp_open_form_dialog()
        elif action == "mahjong_web":
            # ★ v1.1.18：算番 Web 版（看地址 / 开关服务 / 试读）
            self.dfp_open_mahjong_dialog()
        elif action == "mahjong_read_test":
            # ★ v1.1.18：用数字音频试着读一个番数
            self.dfp_test_read_number(88)
        elif action == "check_balance":
            # ★ v1.1.16：立刻查一次 DS 余额（查完一定报个数）
            self.dfp_query_balance_now()
        elif action == "balance_log":
            # ★ v1.1.16：翻一翻以前的扣钱记录
            self.dfp_open_balance_log_dialog()
        elif action == "open_trainer":
            self.dfp_open_trainer()
        elif action == "max_all":
            self._dfp_brain.dfp_max_all()
            self.dfp_notify(self._dfp_brain.dfp_pick_line("maxed"), "success")
        elif action == "max_level":
            level = self._dfp_brain.dfp_max_level()
            self.dfp_notify("等级已拉满：Lv%d" % level, "success")
        elif action in ("lock_all", "unlock_all"):
            self._dfp_brain.dfp_lock_all(action == "lock_all")
            self.dfp_notify("已%s全部数值锁定" % ("开启" if action == "lock_all" else "解除"), "info")
        elif action == "voice_notice":
            # ★ v1.1.14：一次一个地轮着试听 6 类提示音（按钮少、都能听到）
            #   ★ 必须排在 `action.startswith("voice_")` 之前：那个分支按 `voice_<类别>`
            #     取名，会把 `voice_notice` 当成类别 `notice` 吞掉，这里永远走不到。
            statuses = list(DFP_ASSET_PACK_NOTICE_STATUSES)
            index = dfp_safe_int(self._dfp_notice_audition_index, 0) % len(statuses)
            status = statuses[index]
            self._dfp_notice_audition_index = (index + 1) % len(statuses)
            played = self.dfp_play_notice(status)
            label = DFP_ASSET_PACK_NOTICE_LABELS.get(status, status)
            if played:
                self.dfp_notify("提示音：%s（%s）" % (label, played), "info")
            else:
                self.dfp_notify("提示音：%s —— 没装上素材包 / 已关提示音" % label, "warn")
        elif action.startswith("voice_"):
            self._dfp_play_voice(action[6:])
        elif action == "open_env":
            env_path = dfp_env_path()
            if not os.path.isfile(env_path):
                dfp_atomic_write_text(
                    env_path,
                    "# DeepSeek 肥鱼娘桌宠配置文件\n"
                    "# API Key 与数据库口令都写在这里，不要写进代码\n"
                    "DEEPSEEK_API_KEY=\n"
                    "# 多个 Key 可以用逗号分隔：DEEPSEEK_API_KEYS=sk-aaa,sk-bbb\n"
                    "DEEPSEEK_API_BASE=https://api.deepseek.com\n"
                    "DEEPSEEK_MODEL=deepseek-chat\n"
                    "DEEPSEEK_TIMEOUT=60\n"
                    "# 数据库敏感字段加密口令（对话内容加密用），改成你自己的随机字符串\n"
                    "DB_PASSWORD=deepseek-fish-pet-default-secret\n",
                )
            self._dfp_open_file(env_path)
        elif action == "reload_env":
            self._dfp_client.dfp_reload_env()
        elif action == "env_keys_saved":
            # 设置窗口里把 API Key 保存进 .env 了（写盘与客户端重载在那边做完了）：
            # 这里只负责记日志 + 通知 + 让聊天窗口的「离线/在线」状态跟着变。
            count = self._dfp_client.dfp_key_count()
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_info("API Key 已更新：%d 个" % count)
            if self._dfp_chat_window is not None:
                try:
                    self._dfp_chat_window.dfp_refresh_ai_state()
                except Exception:
                    pass
            if count:
                self.dfp_notify("API Key 已更新：%d 个（自动轮换）" % count, "success")
            else:
                self.dfp_notify("API Key 已清空：桌宠退回离线台词库", "warn")
        elif action == "apply_all_settings":
            self.dfp_apply_all_settings()
        elif action == "toggle_pet":
            self.dfp_toggle_pet_visible()

    def _dfp_on_settings_task(self, task: str) -> None:
        mapping = {
            "backup_now": self._dfp_task_backup,
            "cleanup_backup": self._dfp_task_cleanup_backup,
            "cleanup_events": self._dfp_task_cleanup_events,
            "cleanup_samples": self._dfp_task_cleanup_samples,
            "vacuum": self._dfp_task_vacuum,
            "export_all": self._dfp_task_export_all,
            "reset_state": self._dfp_task_reset_state,
        }
        function = mapping.get(str(task))
        if function is None:
            self.dfp_notify("未知任务：%s" % task, "warn")
            return
        if self._dfp_settings_window is not None:
            self._dfp_settings_window.dfp_set_operation_progress(0, 100, "准备…")
        self.dfp_run_task(str(task), function)

    def _dfp_task_backup(self, report: Callable[[int, int, str], None]) -> str:
        report(15, 100, "复制数据库")
        path = self._dfp_backups.dfp_create("手动备份")
        if not path:
            raise IOError("数据库文件不存在，无法备份")
        report(70, 100, "清理旧备份")
        keep = dfp_safe_int(self._dfp_settings.dfp_get("backup_keep_count", DFP_BACKUP_KEEP_DEFAULT), DFP_BACKUP_KEEP_DEFAULT)
        removed = self._dfp_backups.dfp_prune(keep)
        report(100, 100, "完成")
        try:
            self._dfp_database.dfp_add_event("backup", os.path.basename(path))
            self._dfp_database.dfp_bump_daily("backups", 1)
        except Exception:
            pass
        return "备份完成：%s（%s，清理 %d 份）" % (os.path.basename(path), dfp_human_bytes(os.path.getsize(path)), removed)

    def _dfp_task_cleanup_backup(self, report: Callable[[int, int, str], None]) -> str:
        report(20, 100, "扫描备份")
        before = self._dfp_backups.dfp_list()
        report(60, 100, "清理中")
        keep = dfp_safe_int(self._dfp_settings.dfp_get("backup_keep_count", DFP_BACKUP_KEEP_DEFAULT), DFP_BACKUP_KEEP_DEFAULT)
        removed = self._dfp_backups.dfp_prune(keep)
        report(100, 100, "完成")
        return "清理完成：原有 %d 份，删除 %d 份，保留 %d 份" % (len(before), removed, min(keep, len(before)))

    def _dfp_task_cleanup_events(self, report: Callable[[int, int, str], None]) -> str:
        report(30, 100, "统计事件")
        days = dfp_safe_int(self._dfp_settings.dfp_get("keep_events_days", 90), 90)
        removed = self._dfp_database.dfp_purge_events(days)
        report(100, 100, "完成")
        return "已清理 %d 条超过 %d 天的事件" % (removed, days)

    def _dfp_task_cleanup_samples(self, report: Callable[[int, int, str], None]) -> str:
        report(30, 100, "统计采样")
        days = dfp_safe_int(self._dfp_settings.dfp_get("keep_state_days", 90), 90)
        removed = self._dfp_database.dfp_purge_state_samples(days)
        report(100, 100, "完成")
        return "已清理 %d 条超过 %d 天的状态采样" % (removed, days)

    def _dfp_task_vacuum(self, report: Callable[[int, int, str], None]) -> str:
        before = self._dfp_database.dfp_size_bytes()
        report(30, 100, "压缩中")
        ok = self._dfp_database.dfp_vacuum()
        after = self._dfp_database.dfp_size_bytes()
        report(100, 100, "完成")
        if not ok:
            raise IOError("VACUUM 失败")
        return "压缩完成：%s → %s" % (dfp_human_bytes(before), dfp_human_bytes(after))

    def _dfp_task_export_all(self, report: Callable[[int, int, str], None]) -> str:
        sessions = self._dfp_database.dfp_list_sessions(1000)
        if not sessions:
            raise IOError("没有可导出的会话")
        fmt = str(self._dfp_settings.dfp_get("chat_export_format", "markdown"))
        suffix = dict((key, ext) for key, _label, ext in DFP_EXPORT_FORMATS).get(fmt, "md")
        target = os.path.join(dfp_export_dir(), "肥鱼娘全部对话_%s.%s" % (dfp_fmt_ts(fmt="%Y%m%d_%H%M%S"), suffix))
        dfp_ensure_dir(dfp_export_dir())
        total = len(sessions)
        lines: List[str] = ["# 肥鱼娘对话导出（%s）" % dfp_fmt_ts(), ""]
        payload: List[Dict[str, Any]] = []
        for index, session in enumerate(sessions, start=1):
            session_id = str(session.get("id"))
            messages = self._dfp_database.dfp_list_messages(session_id, limit=2000, ascending=True)
            lines.append("## %s" % session.get("title"))
            for item in messages:
                who = "主人" if str(item.get("role")) == "user" else str(self._dfp_settings.dfp_get("pet_nickname", DFP_DEFAULT_NICKNAME))
                lines.append("**%s**（%s）：%s" % (who, dfp_fmt_ts(item.get("created_at"), "%m-%d %H:%M"), str(item.get("content") or "").replace("\n", " ")))
            lines.append("")
            payload.append({"title": session.get("title"), "messages": [dict(item) for item in messages]})
            report(index, total, "导出 %d/%d" % (index, total))
        content = json.dumps(payload, ensure_ascii=False, indent=2) if fmt == "json" else "\n".join(lines)
        if not dfp_atomic_write_text(target, content):
            raise IOError("写入失败：%s" % target)
        try:
            self._dfp_database.dfp_add_event("export", os.path.basename(target))
        except Exception:
            pass
        return "已导出 %d 个会话到 %s" % (len(sessions), target)

    def _dfp_task_reset_state(self, report: Callable[[int, int, str], None]) -> str:
        report(50, 100, "重置状态")
        self._dfp_brain.dfp_reset_state()
        report(100, 100, "完成")
        return "桌宠状态已重置为初始值（对话记录保留）"

    # --- 从其他窗口发来的动作 ---
    def dfp_test_api(self) -> None:
        if not self._dfp_client.dfp_has_key():
            self.dfp_notify("未配置 API Key，无法测试", "warn")
            return

        def task(report):
            report(30, 100, "正在连接…")
            data = self._dfp_client.dfp_balance()
            report(100, 100, "完成")
            return data

        def done(_name, result):
            infos = (result or {}).get("balance_infos") or []
            text = "可用：%s" % ("是" if (result or {}).get("is_available") else "否")
            if infos:
                first = infos[0] or {}
                text += "｜余额 %s %s" % (first.get("total_balance", "?"), first.get("currency", ""))
            self.dfp_notify("连接正常 · %s" % text, "success")

        self.dfp_run_task("test_api", task, done)

    def _dfp_on_status_action(self, action: str) -> None:
        action = str(action)
        if action == "feed":
            self.dfp_feed()
        elif action == "drink":
            self.dfp_drink()
        elif action == "play":
            self.dfp_play()
        elif action == "pet":
            self.dfp_pet_head()
        elif action == "sleep":
            self.dfp_toggle_sleep()
        elif action == "trainer":
            self.dfp_open_trainer()
        elif action == "behavior":
            self.dfp_random_behavior()
        elif action == "dream":
            self.dfp_dream_now()
        elif action == "chat":
            self.dfp_open_chat()
        elif action == "settings":
            self.dfp_open_settings()
        elif action == "center_pet":
            self.dfp_center_pet()
        if self._dfp_status_window is not None and self._dfp_status_window.isVisible():
            self._dfp_status_window.dfp_refresh(self._dfp_brain.dfp_state_snapshot())


# =============================================================================
#  二十九、国标麻将算番 Web 版（手机 / 平板浏览器算番 + 桌宠读番）
#  ★ v1.1.18：算番这件事由另一个项目负责 —— 源项目在 G:\Python_Code\Mahjong_Calculator，
#    **那个目录一个字节都不改**。这里用的是**复制**进 `mahjong\` 的四样东西：
#      · mahjong_core.py        纯算番内核（MahjongFanCalculator / Meld / Options）
#      · mahjong_api.py         内嵌单文件 Web 客户端 + HTTP 路由（stdlib http.server）
#      · 国标麻将标准规则.json    番种表
#      · 麻将图\*.png            牌面小图（Web 页面用）
#    桌宠这边只做三件事：
#      ① 把算番 Web 服务起在局域网上 —— 手机 / 平板浏览器打开地址就能算番；
#      ② 算完把「总番数」拿到手（运行时给 `Engine.score` 打一层包，**不改拷贝件**，
#         算番结果原样返回给浏览器，所以手机上算出来的番与算番器项目完全一致）；
#      ③ 用 `数字\*.mp3`（1~9 + 十/百/千/万/十万/百万/千万/亿）把总番数读出来。
# =============================================================================

def dfp_mahjong_dir() -> str:
    """`mahjong\\` 目录（先找程序目录，再找数据目录）。"""
    for base in (dfp_app_dir(), dfp_data_dir()):
        path = os.path.join(base, DFP_MAHJONG_DIR_NAME)
        if os.path.isdir(path):
            return path
    return os.path.join(dfp_app_dir(), DFP_MAHJONG_DIR_NAME)


def dfp_mahjong_rules_path() -> str:
    return os.path.join(dfp_mahjong_dir(), DFP_MAHJONG_RULES_FILE)


def dfp_mahjong_missing() -> List[str]:
    """算番 Web 版缺哪些文件（返回空列表 = 齐了）。"""
    directory = dfp_mahjong_dir()
    needed = (DFP_MAHJONG_API_FILE, DFP_MAHJONG_CORE_FILE, DFP_MAHJONG_RULES_FILE)
    return [name for name in needed if not os.path.isfile(os.path.join(directory, name))]


def dfp_mahjong_ready() -> bool:
    return not dfp_mahjong_missing()


def dfp_safe_category_name(value: str) -> bool:
    """目录名能不能当语音类别用（`poke` / `ask` 这种 ASCII 标识符才算）。

    ★ v1.1.23：用户在 `鲸宝音频\` 下按类别分了子目录（`鲸宝音频\poke\voice_poke_5.mp3`）。
      此时如果文件名没带 `voice_` 前缀，就拿**上一级目录名**当类别；
      但目录名得像个类别（纯英文 / 数字 / 下划线）—— 中文目录名、`.git` 这类一律不当。
    """
    text = str(value or "").strip()
    if not text or text.startswith("."):
        return False
    return all(char.isascii() and (char.isalnum() or char == "_") for char in text)


def dfp_character_dirs() -> List[str]:
    """形象图自己的家（`鲸宝形象\`）。

    ★ v1.1.32：与「鲸宝音频」完全对称 —— `鲸宝形象\立绘\`（形象本体）、`鲸宝形象\表情\`（表情动画）、
      `鲸宝形象\动作\`（106 个动作片）、`鲸宝形象\启动图.png`（启动画面）。
      程序目录与数据目录下的同名目录都认（顺序：程序目录在前）；目录不在就返回空表
      （此时一切照旧：相对路径全部回到素材表里找）。子目录只是推荐布局，程序递归扫。
    """
    dirs: List[str] = []
    for base in (dfp_app_dir(), dfp_data_dir()):
        path = os.path.join(base, DFP_CHARACTER_DIR_NAME)
        if os.path.isdir(path) and path not in dirs:
            dirs.append(path)
    return dirs


def dfp_character_dir_path() -> str:
    """形象图那个家的绝对路径（给「打开形象目录」这类入口用）。

    已有就用第一个；一个都没建时返回程序目录下那个 —— 菜单里点了是「打开」，
    不存在也不该把菜单项做成灰的（用户可以自己建，建完丢图进去重扫即可）。
    """
    dirs = dfp_character_dirs()
    if dirs:
        return dirs[0]
    return os.path.join(dfp_app_dir(), DFP_CHARACTER_DIR_NAME)


def _dfp_walk_image_roots(roots: List[Tuple[str, str]]):
    """依次扇多个根目录（保持 `os.walk` 的 (current, dirs, files) 三件套）。

    ★ v1.1.32：兜底扫描要同时扫素材表与 `鲸宝形象\`，但分类逻辑只该写一遍
      ⇒ 用一个生成器把两个根串起来，循环体一个字不用改。
    """
    for folder, _prefix in roots:
        if not os.path.isdir(folder):
            continue
        for current, dirs, files in os.walk(folder):
            yield current, dirs, files


def dfp_user_voice_dirs() -> List[str]:
    """用户自己录音的目录（`鲸宝音频\`）：形象语音 `voice_<类别>_<序号>.*`。

    ★ v1.1.23：用户要把「素材2 那种音色」的录音全部往这里放，好与素材包（素材4/6）的别人音色区分。
      程序目录与数据目录下的同名目录都认（顺序：程序目录在前），里面不是 `voice_` 开头的文件忽略
      （所以「数字读法」那几条 `1.mp3` / `合计.mp3` / `番.mp3` 不会混进来）。
    """
    dirs: List[str] = []
    for base in (dfp_app_dir(), dfp_data_dir()):
        path = os.path.join(base, DFP_USER_VOICE_DIR_NAME)
        if os.path.isdir(path) and path not in dirs:
            dirs.append(path)
    return dirs


def dfp_user_voice_texts() -> Dict[str, str]:
    """`鲸宝音频\\语音台词.json`：建议文件名（也兼容不带后缀）→ 该念哪句。

    ★ v1.1.23：`_scaffold\export_lines.py` 生成录音清单时会顺手写这份对照表，
      于是你录完的音档自动就有台词（字幕、悬停、去重都靠它），不用再手填。
      没有这份文件也不影响播放（只是字幕/悬停显示「你自己的录音」）。
    """
    found: Dict[str, str] = {}
    for folder in dfp_user_voice_dirs():
        payload = dfp_read_json(os.path.join(folder, DFP_USER_VOICE_TEXT_FILE), None)
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            name = str(key or "").strip()
            text = str(value or "").strip()
            if not name or name.startswith("_") or not text:
                continue
            found.setdefault(name, text)
            stem = os.path.splitext(name)[0]
            if stem:
                found.setdefault(stem, text)
    return found


def dfp_number_search_dirs() -> List[str]:
    """数字音频的查找目录（按优先级）：先「数字」目录，再「鲸宝音频」目录。

    ★ v1.1.20：用户把「合计 / 番」两条录音放在了「鲸宝音频」里，所以两个目录都找；
      同名时以「数字」目录为准（1~9 与十/百/千/万…都在那里）。
    """
    dirs: List[str] = []
    for base in (dfp_app_dir(), dfp_data_dir()):
        for name in (DFP_NUMBER_DIR_NAME, DFP_NUMBER_EXTRA_DIR_NAME):
            path = os.path.join(base, name)
            if os.path.isdir(path) and path not in dirs:
                dirs.append(path)
    return dirs


def dfp_number_dir() -> str:
    """`数字\\` 目录（数字音频：1~9 + 十/百/千/万/十万/百万/千万/亿）。"""
    for base in (dfp_app_dir(), dfp_data_dir()):
        path = os.path.join(base, DFP_NUMBER_DIR_NAME)
        if os.path.isdir(path):
            return path
    return os.path.join(dfp_app_dir(), DFP_NUMBER_DIR_NAME)


def dfp_number_clip_path(name: str) -> Optional[str]:
    """找一个数字音频文件的完整路径（找不到返回 None）。"""
    text = str(name or "").strip()
    if not text:
        return None
    for directory in dfp_number_search_dirs() or [dfp_number_dir()]:
        for suffix in (".mp3", ".wav", ".ogg", ".m4a", ".flac"):
            path = os.path.join(directory, text + suffix)
            if os.path.isfile(path):
                return path
    return None


def _dfp_section_parts(part: int) -> List[str]:
    """把四位以内的数拆成朗读片段（10~19 读「十几」，中间夹的零读出来）。

    例：8 → ['8']，15 → ['十', '5']，105 → ['1', '百', '零', '5']，320 → ['3', '百', '2', '十']。
    """
    number = int(part)
    if number <= 0:
        return [DFP_NUMBER_ZERO] if number == 0 else []
    text = str(number)
    length = len(text)
    pieces: List[str] = []
    gap = False  # 上一个非零位之后是否夹过零（要读「零」）
    for index, char in enumerate(text):
        digit = dfp_safe_int(char, 0)
        place = length - index - 1  # 千=3 百=2 十=1 个=0
        if digit == 0:
            if pieces:
                gap = True
            continue
        if gap:
            pieces.append(DFP_NUMBER_ZERO)
            gap = False
        if place == 1 and digit == 1 and not pieces:
            pieces.append(DFP_NUMBER_UNITS[1])  # 10~19：读「十几」，不读「一十几」
            continue
        pieces.append(str(digit))
        if place:
            pieces.append(DFP_NUMBER_UNITS[place])
    return pieces


def dfp_number_parts(value: Any) -> List[str]:
    """数字 → 朗读片段（片段名就是 `数字\\` 里的文件名，如 '1' / '百' / '万'）。

    例：8 → ['8']，24 → ['2', '十', '4']，105 → ['1', '百', '零', '5']，
        1000 → ['1', '千']，10500 → ['1', '万', '零', '5', '百']，100000 → ['十', '万']。
    """
    number = dfp_safe_int(value, 0)
    if value is None or (isinstance(value, str) and not value.strip()):
        return []
    if number < 0:
        return []
    if number == 0:
        return [DFP_NUMBER_ZERO]  # ★ v1.1.19：0 也读出来（「零」），不然会和「没读」混淆
    if number < 10000:
        return _dfp_section_parts(number)
    sections: List[int] = []
    while number > 0:
        sections.append(number % 10000)
        number //= 10000
    pieces: List[str] = []
    for index in range(len(sections) - 1, -1, -1):
        section = sections[index]
        if not section:
            continue
        head = _dfp_section_parts(section)
        if pieces and section < 1000:
            head = [DFP_NUMBER_ZERO] + head  # 一万零五 / 一百零五万
        pieces.extend(head)
        if index and index < len(DFP_NUMBER_BIG_UNITS):
            pieces.append(DFP_NUMBER_BIG_UNITS[index])
    return pieces


def dfp_number_text(value: Any) -> str:
    """数字 → 中文读法（给气泡 / 日志看，如 105 → 一百零五）。

    ★ 音频文件是按「1.mp3 / 十.mp3」命名的（见 `dfp_number_parts`），
      这里再把阿拉伯数字换成中文数字，只为了让日志和气泡里读起来像人话。
    """
    pieces: List[str] = []
    for part in dfp_number_parts(value):
        if len(part) == 1 and part.isdigit():
            pieces.append(DFP_NUMBER_CN[dfp_safe_int(part, 0)])
        else:
            pieces.append(part)
    return "".join(pieces)


def dfp_number_clips(value: Any, prefix: str = "", suffix: str = "") -> Tuple[List[str], List[str]]:
    """数字 → (要播的音频路径列表, 朗读片段列表)。

    ★ 片段没找到对应音频时（例如素材里没有 `零.mp3`）会**跳过那一段**，
      所以 105 没补「零」时会读成「一百五」—— 补一个 `数字\\零.mp3` 就正了。
    """
    parts: List[str] = []
    head = str(prefix or "").strip()
    tail = str(suffix or "").strip()
    if head:
        parts.append(head)
    parts.extend(dfp_number_parts(value))
    if tail:
        parts.append(tail)
    clips: List[str] = []
    for part in parts:
        path = dfp_number_clip_path(part)
        if path:
            clips.append(path)
    return clips, parts


def dfp_number_phrase_text(value: Any, prefix: str = "", suffix: str = "") -> str:
    """数字 → 带前后缀的中文读法（如 合计 + 24 + 番 → 「合计二十四番」）。"""
    return "%s%s%s" % (str(prefix or "").strip(), dfp_number_text(value), str(suffix or "").strip())


def dfp_should_read_fan(speaking: bool, speaking_number: Any, new_number: Any) -> bool:
    """要不要重念一遍番数？

    ★ v1.1.21：**正在念的番数和新来的一样就别打断** —— 用户连着算同一副牌时，
      旧行为会每次都从头重念（「合计…」刚起头就被掐掉），听着就像没读。
      换了番数（真在连算不同牌型）才重念。
    """
    if not speaking:
        return True
    if speaking_number is None:
        return True
    try:
        return int(speaking_number) != int(new_number)
    except Exception:
        return True


def dfp_lan_ip() -> str:
    """本机在局域网里的地址（拿不到就回 127.0.0.1；手机要用这个地址）。"""
    sock = None
    try:
        import socket as _socket

        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))  # 不发包，只为让系统选出出网的那块网卡
        address = str(sock.getsockname()[0] or "").strip()
        if address and not address.startswith("127."):
            return address
    except Exception:
        pass
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
    try:
        import socket as _socket

        host = _socket.gethostbyname(_socket.gethostname())
        if host and not str(host).startswith("127."):
            return str(host)
    except Exception:
        pass
    return "127.0.0.1"


def dfp_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """这个端口现在能不能用。

    ★ 为什么要自己探：`http.server` 家族的 `allow_reuse_address = 1`（SO_REUSEADDR），
      在 Windows 上**被别人占着的端口照样能绑上**（两个程序抢同一个端口，谁收包看运气），
      所以光靠「起服务失败」判断不出端口被占 —— 先用一个**不带** SO_REUSEADDR 的
      socket 探一下。探的时候分两种：
      ① 能绑上 → 空的，能用；
      ② 绑不上，但**没人在听**（只是上一轮的连接还在 TIME_WAIT）→ 也算能用
         （`http.server` 带 SO_REUSEADDR，这种能绑上）；
      ③ 绑不上、而且能连上（真有人在听）→ 不能用，换下一个端口。
    """
    bind_host = "0.0.0.0" if host in ("0.0.0.0", "::") else host
    sock = None
    try:
        import socket as _socket

        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.bind((bind_host, int(port)))
        return True
    except Exception:
        pass
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
    probe = None
    try:
        import socket as _socket

        probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        probe.settimeout(0.3)
        probe.connect(("127.0.0.1", int(port)))
        return False  # 有人在听：这个端口真的被占了
    except Exception:
        return True
    finally:
        if probe is not None:
            try:
                probe.close()
            except Exception:
                pass


class DFPNumberSpeaker(QObject):
    """把 `数字\\*.mp3` 按顺序读出来（一个数字一个文件，这里负责排队播）。

    ★ 自己一套 QMediaPlayer + QAudioOutput：与形象声音分开，互不打断。
    ★ 一首接一首靠 `mediaStatusChanged == EndOfMedia`；另配一个兜底定时器
      （按音频时长 + 一段余量），后端偶尔不报结束状态时也能往下走，不会卡在第一个字。
    ★★ v1.1.19 修的真 bug（用户报「算完番有时候没读出来」）：`player.stop()` 与 `setSource()`
      都会甩出一个**属于上一片**的 `EndOfMedia`，照它推进队列就会把**这一片**（甚至整句）
      推掉 —— 单片段数字（1~9 番最常见）直接被推到末尾 ⇒ **一声不出**。现在加
      「`_dfp_armed`：这一片真的开始播了吗」：只有见过 `LoadedMedia` / `BufferedMedia` /
      `PlayingState` 之后才认 `EndOfMedia`；一片都没播成还会**自动重试一次**，每步都写日志。
    """

    dfpFinished = Signal(str)  # 读完（参数 = 读出来的中文，如「二十四」）

    def __init__(self, settings: DFPSettingsStore, logger: Optional[DFPLogger] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._dfp_settings = settings
        self._dfp_logger = logger
        self._dfp_player: Any = None
        self._dfp_output: Any = None
        self._dfp_clips: List[str] = []
        self._dfp_parts: List[str] = []
        self._dfp_index = 0
        self._dfp_token = 0
        self._dfp_token_now = 0
        self._dfp_watch_token = 0
        self._dfp_active = False
        self._dfp_last_text = ""
        self._dfp_play_count = 0
        # ★ v1.1.19：防「上一片的结束状态把这一片推掉」
        self._dfp_armed = False        # 这一片真的开始播了吗
        self._dfp_armed_ever = False   # 这一轮有没有任何一片真的开始播过
        self._dfp_ended = 0            # 这一轮真正收到过几次「本片播完」
        self._dfp_started = 0          # 这一轮起播了几片
        self.dfp_silent_retry = 1      # 整句一片都没播出来时最多重试几次（公开可调，测试里置 0）
        self._dfp_retry_left = 0
        self._dfp_watchdog = QTimer(self)
        self._dfp_watchdog.setSingleShot(True)
        self._dfp_watchdog.timeout.connect(self._dfp_on_watchdog)

    # --- 查询 ---
    def dfp_last_text(self) -> str:
        return self._dfp_last_text

    def dfp_play_count(self) -> int:
        return self._dfp_play_count

    def dfp_speaking(self) -> bool:
        return bool(self._dfp_active)

    def _dfp_log(self, text: str) -> None:
        if self._dfp_logger is not None:
            try:
                self._dfp_logger.dfp_info(text)
            except Exception:
                pass

    def dfp_error_reason(self) -> str:
        """为什么读不了（能读返回空串）。"""
        if not bool(self._dfp_settings.dfp_get("voice_enabled", True)):
            return "「声音」总开关关着（设置 → 行为 → 声音）"
        if not dfp_multimedia_available():
            return "这台机器没有可用的多媒体后端（QtMultimedia）"
        if not os.path.isdir(dfp_number_dir()):
            return "没找到 `%s` 目录（数字音频）" % DFP_NUMBER_DIR_NAME
        if not [name for name in ("1", "2", "十", "百") if dfp_number_clip_path(name)]:
            return "`%s` 目录里没有数字音频（要 1~9 + 十/百/千/万…）" % DFP_NUMBER_DIR_NAME
        return ""

    def _dfp_ensure_player(self) -> Optional[Any]:
        if self._dfp_player is not None:
            return self._dfp_player
        if not dfp_multimedia_available():
            return None
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._dfp_player = QMediaPlayer()
            self._dfp_output = QAudioOutput()
            self._dfp_player.setAudioOutput(self._dfp_output)
            self._dfp_player.mediaStatusChanged.connect(self._dfp_on_status)
            # ★ v1.1.19：起播信号（用来「上锁」）与真实时长（用来定兜底时刻）都要用上
            self._dfp_player.playbackStateChanged.connect(self._dfp_on_playback_state)
            self._dfp_player.durationChanged.connect(self._dfp_on_duration)
            return self._dfp_player
        except Exception as exc:
            self._dfp_player = None
            if self._dfp_logger is not None:
                self._dfp_logger.dfp_warning("数字朗读器建不起来：%s" % exc)
            return None

    # --- 朗读 ---
    def dfp_speak_number(self, value: Any, prefix: str = "", suffix: str = "") -> str:
        """把数字读出来；返回读出来的中文（没读成返回空串）。

        ★ v1.1.20：可带前后缀 —— 读番就是「合计」+ 数字 + 「番」（音频放任一音频目录）。
        """
        reason = self.dfp_error_reason()
        text = dfp_number_phrase_text(value, prefix, suffix)
        if reason:
            self._dfp_log("不读番数（%s）：%s" % (text or value, reason))
            return ""
        clips, parts = dfp_number_clips(value, prefix, suffix)
        if not clips:
            self._dfp_log("不读番数（%s）：片段 %s 都没找到对应音频（目录 %s）" % (
                text or value, "+".join(parts) or "空", "、".join(dfp_number_search_dirs()) or dfp_number_dir()))
            return ""
        player = self._dfp_ensure_player()
        if player is None:
            self._dfp_log("不读番数（%s）：多媒体后端不可用" % (text or value))
            return ""
        self.dfp_stop()
        self._dfp_clips = list(clips)
        self._dfp_parts = list(parts)
        self._dfp_index = 0
        self._dfp_active = True
        self._dfp_last_text = text
        self._dfp_play_count += 1
        self._dfp_retry_left = max(0, dfp_safe_int(self.dfp_silent_retry, 1))
        self._dfp_log("开始读番数：%s（%d 片：%s）" % (
            text, len(clips), "、".join(os.path.basename(item) for item in clips)))
        self._dfp_start_current()
        return text

    def dfp_stop(self) -> None:
        """停下当前的朗读（下一个数字来的时候也会先停一下）。"""
        self._dfp_watchdog.stop()
        self._dfp_clips = []
        self._dfp_parts = []
        self._dfp_index = 0
        self._dfp_active = False
        self._dfp_armed = False
        self._dfp_armed_ever = False
        self._dfp_ended = 0
        self._dfp_started = 0
        self._dfp_token += 1
        if self._dfp_player is not None:
            try:
                self._dfp_player.stop()
            except Exception:
                pass

    def _dfp_start_current(self) -> None:
        if not self._dfp_clips or self._dfp_index >= len(self._dfp_clips):
            self._dfp_finish()
            return
        path = self._dfp_clips[self._dfp_index]
        player = self._dfp_player
        if player is None:
            self._dfp_finish()
            return
        try:
            from PySide6.QtCore import QUrl

            self._dfp_token += 1
            self._dfp_token_now = self._dfp_token
            # ★ 先把锁落下：下面 stop() / setSource() 甩出来的「上一片结束」一律不算数
            self._dfp_armed = False
            volume = dfp_clamp(dfp_safe_float(self._dfp_settings.dfp_get("voice_volume", 0.8), 0.8), 0.0, 1.0)
            if self._dfp_output is not None:
                self._dfp_output.setVolume(volume)
            player.stop()
            player.setSource(QUrl.fromLocalFile(path))
            player.setPlaybackRate(1.0)
            player.play()
            self._dfp_started += 1
            duration = dfp_safe_int(player.duration(), 0)
            self._dfp_watch_token = self._dfp_token_now
            self._dfp_watchdog.start(dfp_clamp(duration + 1500, 2000, 20000) if duration > 0 else 3500)
        except Exception as exc:
            self._dfp_log("数字音频播不出来（%s）：%s" % (os.path.basename(path), exc))
            self._dfp_index += 1
            self._dfp_start_current()

    def _dfp_next(self, token: Optional[int] = None) -> None:
        # ★ 带令牌：迟到的兜底定时器如果已经是「上一条」的，就不准再推进（会漏读一个字）
        if token is not None and token != self._dfp_watch_token:
            return
        self._dfp_index += 1
        if self._dfp_index >= len(self._dfp_clips):
            self._dfp_finish()
            return
        self._dfp_start_current()

    def _dfp_finish(self) -> None:
        self._dfp_watchdog.stop()
        clips = list(self._dfp_clips)
        played = self._dfp_ended
        text = self._dfp_last_text
        self._dfp_active = False
        self._dfp_armed = False
        self._dfp_index = 0
        # ★ 一片都没真的播出来 ⇒ 重试一次（音频后端偶发没接上）；只重试指定次数，不会死循环
        if clips and played == 0 and self._dfp_retry_left > 0:
            self._dfp_retry_left -= 1
            self._dfp_log("这一轮一片都没播出来，重试一次：%s" % (text or "-"))
            self._dfp_clips = clips
            self._dfp_index = 0
            self._dfp_ended = 0
            self._dfp_started = 0
            self._dfp_active = True
            QTimer.singleShot(350, self._dfp_retry_start)
            return
        self._dfp_clips = []
        self._dfp_parts = []
        if clips and played < len(clips):
            self._dfp_log("读完 %s：%d 片里只播出了 %d 片" % (text or "-", len(clips), played))
        if text:
            self.dfpFinished.emit(text)

    def _dfp_retry_start(self) -> None:
        """重试起播（延时调用，所以要自己再看一眼状态）。"""
        if not self._dfp_active:
            return
        try:
            self._dfp_start_current()
        except Exception as exc:
            self._dfp_log("重试起播失败：%s" % exc)
            self._dfp_finish()

    def _dfp_on_duration(self, duration: Any) -> None:
        """真实时长出来了，按它重排兜底时刻（后端有时读源头一步拿不到时长）。"""
        if not self._dfp_active:
            return
        value = dfp_safe_int(duration, 0)
        if value <= 0:
            return
        self._dfp_watchdog.start(dfp_clamp(value + 1500, 2000, 20000))

    def _dfp_on_playback_state(self, state: Any) -> None:
        """开始播了 ⇒ 解锁（「这一片真的在放」最可靠的信号）。"""
        if not self._dfp_active:
            return
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            playing = getattr(getattr(QMediaPlayer, "PlaybackState", QMediaPlayer), "PlayingState", None)
        except Exception:
            return
        if playing is not None and state == playing:
            self._dfp_armed = True
            self._dfp_armed_ever = True

    def _dfp_on_status(self, status: Any) -> None:
        """媒体状态变化。

        ★★ v1.1.19 的铁律：**没在读、或本片还没真的起播时，任何 `EndOfMedia` 一律不理** ——
          `player.stop()` 与 `setSource()` 都会甩出一个「上一片结束」的状态，
          照它推进队列会把这一片推掉（单片段数字就变成一声不出）。
        """
        if not self._dfp_active:
            return
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            media_status = getattr(QMediaPlayer, "MediaStatus", QMediaPlayer)
            loaded = getattr(media_status, "LoadedMedia", None)
            buffered = getattr(media_status, "BufferedMedia", None)
            end_status = getattr(media_status, "EndOfMedia", None)
            invalid = getattr(media_status, "InvalidMedia", None)
        except Exception:
            return
        if (loaded is not None and status == loaded) or (buffered is not None and status == buffered):
            self._dfp_armed = True
            self._dfp_armed_ever = True
            return
        if end_status is not None and status == end_status:
            if not self._dfp_armed:
                return  # 上一片留下的结束状态 —— 忽略，不然会把这一片（甚至整句）推掉
            self._dfp_ended += 1
            self._dfp_next()
            return
        if invalid is not None and status == invalid:
            self._dfp_log("数字音频读不了，跳过第 %d 片：%s" % (
                self._dfp_index + 1, os.path.basename(self._dfp_clips[self._dfp_index]) if self._dfp_index < len(self._dfp_clips) else "?"))
            self._dfp_next()

    def _dfp_on_watchdog(self) -> None:
        """兜底：后端没报结束状态时也往下走（否则会一直卡在同一个字上）。"""
        if not self._dfp_active:
            return
        self._dfp_next(self._dfp_watch_token)


class DFPMahjongWeb(QObject):
    """国标麻将算番 Web 服务（把复制进来的 `mahjong\\mahjong_api.py` 包一层）。

    ★ 不改拷贝件：只在运行时给类 `Engine.score` 打一层包 —— 算完先把总番数发信号出来，
      再把结果**原样**返回给浏览器，所以手机上的算番结果与算番器项目完全一致。
    ★ 服务跑在自己的线程里（http.server 的 serve_forever），算番请求到 `_dfp_on_score`
      时是在**工作线程**上 —— 那里只发 Qt 信号，界面和放音都在主线程的槽里做。
    """

    dfpScored = Signal(int, str, str)  # (总番数, 牌型 / 和牌方式, 说明)

    # ★ 活着（在跑）的实例都收一份算番回传：算番结果打点在 `Engine` 类上，
    #   类上只能记一个「通知对象」，所以改成记一张表，谁的服务在跑就通知谁。
    #   （正常只会有一个实例；测试里可能同时起好几个，这样不会互相抢。）
    _dfp_instances: List[Any] = []

    def __init__(self, settings: DFPSettingsStore, logger: Optional[DFPLogger] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._dfp_settings = settings
        self._dfp_logger = logger
        self._dfp_server: Any = None
        self._dfp_module: Any = None
        self._dfp_port = 0
        self._dfp_host = ""
        self._dfp_error = ""
        self._dfp_last: Dict[str, Any] = {}
        self._dfp_count = 0
        DFPMahjongWeb._dfp_instances.append(self)
        if len(DFPMahjongWeb._dfp_instances) > 12:
            DFPMahjongWeb._dfp_instances = [item for item in DFPMahjongWeb._dfp_instances if item.dfp_running()] + [self]

    # --- 查询 ---
    def dfp_running(self) -> bool:
        return self._dfp_server is not None

    def dfp_port(self) -> int:
        return dfp_safe_int(self._dfp_port, 0) or self._dfp_want_port()

    def dfp_error(self) -> str:
        return self._dfp_error

    def dfp_module(self) -> Any:
        """载入进来的 `mahjong_api` 模块（没起来就是 None；探针与测试要看它）。"""
        return self._dfp_module

    def dfp_last(self) -> Dict[str, Any]:
        return dict(self._dfp_last)

    def dfp_score_count(self) -> int:
        return int(self._dfp_count)

    def dfp_lan_host(self) -> str:
        """对外监听的主机（0.0.0.0 = 手机能连）。"""
        return self._dfp_host or ("0.0.0.0" if bool(self._dfp_settings.dfp_get("mahjong_web_lan", True)) else "127.0.0.1")

    def dfp_url(self) -> str:
        """本机浏览器里打开的地址。"""
        return "http://127.0.0.1:%d" % self.dfp_port()

    def dfp_lan_url(self) -> str:
        """手机 / 平板要打开的地址（局域网；没开「允许手机访问」时仍是本机地址）。"""
        if not bool(self._dfp_settings.dfp_get("mahjong_web_lan", True)):
            return self.dfp_url()
        return "http://%s:%d" % (dfp_lan_ip(), self.dfp_port())

    def dfp_status_text(self) -> str:
        if self.dfp_running():
            return "运行中 · 本机 %s · 手机 %s" % (self.dfp_url(), self.dfp_lan_url())
        if self._dfp_error:
            return "没启动 · %s" % self._dfp_error
        return "没启动（设置里打开「启动时开启算番 Web 版」即可）"

    def _dfp_want_port(self) -> int:
        return int(dfp_clamp(dfp_safe_int(self._dfp_settings.dfp_get("mahjong_web_port", DFP_MAHJONG_DEFAULT_PORT), DFP_MAHJONG_DEFAULT_PORT), 1024, 65535))

    def _dfp_log(self, text: str) -> None:
        if self._dfp_logger is not None:
            try:
                self._dfp_logger.dfp_info(text)
            except Exception:
                pass

    # --- 生命周期 ---
    def dfp_start(self) -> bool:
        if self.dfp_running():
            return True
        missing = dfp_mahjong_missing()
        directory = dfp_mahjong_dir()
        if missing:
            self._dfp_error = "缺少文件：%s" % "、".join(missing)
            self._dfp_log("算番 Web 版没启动：%s（目录 %s）" % (self._dfp_error, directory))
            return False
        try:
            if directory not in sys.path:
                sys.path.insert(0, directory)
            importlib.invalidate_caches()
            module = importlib.import_module("mahjong_api")
        except Exception as exc:
            self._dfp_error = "加载 mahjong_api 失败：%s" % exc
            self._dfp_log("算番 Web 版没启动：%s" % self._dfp_error)
            return False
        self._dfp_module = module
        self._dfp_patch_engine(module)
        host = self.dfp_lan_host()
        want = self._dfp_want_port()
        rules = dfp_mahjong_rules_path()
        last_error = ""
        for offset in range(max(1, DFP_MAHJONG_PORT_TRIES)):
            port = want + offset
            if port > 65535:
                break
            if not dfp_port_available(port, host):
                last_error = "端口 %d 被占用" % port
                continue
            server = None
            try:
                server = module.ApiServer(host=host, port=port, rules_path=rules, verbose=False, show=())
                server.start()
            except Exception as exc:
                last_error = str(exc)
                if server is not None:
                    try:
                        server.stop()
                    except Exception:
                        pass
                continue
            self._dfp_server = server
            self._dfp_port = dfp_safe_int(getattr(server, "port_actual", port), port) or port
            self._dfp_host = host
            self._dfp_error = ""
            if self._dfp_port != want:
                self._dfp_log("端口 %d 被占用，算番 Web 版改用 %d" % (want, self._dfp_port))
            self._dfp_log("算番 Web 版已启动：本机 %s ｜ 手机 %s ｜ 规则 %s" % (
                self.dfp_url(), self.dfp_lan_url(), os.path.basename(rules)))
            return True
        self._dfp_error = "端口 %d~%d 都试过了：%s" % (want, want + DFP_MAHJONG_PORT_TRIES - 1, last_error or "无法监听")
        self._dfp_log("算番 Web 版没启动：%s" % self._dfp_error)
        return False

    def dfp_stop(self) -> None:
        server, self._dfp_server = self._dfp_server, None
        if server is not None:
            try:
                server.stop()
            except Exception:
                pass
            self._dfp_log("算番 Web 版已停止")

    def _dfp_patch_engine(self, module: Any) -> None:
        """给 `Engine.score` 打一层包：算完先发信号，再把结果原样返回。

        ★ 只打一次（类上留 `_dfp_patched` 标记）。算完通知**所有在跑的实例**
          （见 `_dfp_instances` 的说明），没在跑的实例不打扰。
        """
        engine_cls = getattr(module, "Engine", None)
        if engine_cls is None:
            return
        if getattr(engine_cls, "_dfp_patched", False):
            return
        original = engine_cls.score

        def _dfp_score(engine_self: Any, body: Any, _original: Any = original):
            result = _original(engine_self, body)
            for instance in list(DFPMahjongWeb._dfp_instances):
                if not instance.dfp_running():
                    continue
                try:
                    instance._dfp_on_score(result)
                except Exception:
                    pass
            return result

        try:
            engine_cls.score = _dfp_score
            engine_cls._dfp_patched = True
        except Exception as exc:
            self._dfp_log("算番结果回传打点失败（不影响算番）：%s" % exc)

    def _dfp_on_score(self, payload: Any) -> None:
        """算完一次（**HTTP 工作线程**）：记一笔，再把信号排到主线程。"""
        data = dict(payload or {}) if isinstance(payload, dict) else {}
        total = dfp_safe_int(data.get("total"), 0)
        pattern = str(data.get("pattern") or "")
        message = str(data.get("message") or "")
        self._dfp_last = data
        self._dfp_count += 1
        self._dfp_log("手机算番：%d 番（%s）%s" % (total, pattern or "-", ("· " + message) if message and not pattern else ""))
        self.dfpScored.emit(total, pattern, message)


class DFPMahjongWebDialog(QDialog):
    """🀄 算番 Web 版：看地址（本机 / 手机）、开关服务、试读番数。"""

    def __init__(self, controller: Any, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._dfp_controller = controller
        self.setWindowTitle("🀄 国标麻将算番 Web 版")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        title = QLabel("手机 / 平板浏览器打开下面的地址，就能用国标麻将算番器；\n算完桌宠会把总番数读出来。")
        title.setWordWrap(True)
        layout.addWidget(title)

        self._dfp_status_label = QLabel("")
        self._dfp_status_label.setWordWrap(True)
        layout.addWidget(self._dfp_status_label)

        form = QFormLayout()
        self._dfp_local_edit = QLineEdit("")
        self._dfp_local_edit.setReadOnly(True)
        self._dfp_lan_edit = QLineEdit("")
        self._dfp_lan_edit.setReadOnly(True)
        form.addRow("本机地址", self._dfp_local_edit)
        form.addRow("手机 / 平板（同一 WiFi）", self._dfp_lan_edit)
        layout.addLayout(form)

        tip = QLabel(
            "① 手机要和电脑连同一个 WiFi；② 第一次打开 Windows 会问防火墙，选「允许」；\n"
            "③ 端口被占用时程序会自动往后找一个（当前默认 %d）；④ 读番用的音频在 `%s\\` 目录里。"
            % (DFP_MAHJONG_DEFAULT_PORT, DFP_NUMBER_DIR_NAME)
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        row = QHBoxLayout()
        self._dfp_open_btn = QPushButton("🌐 在电脑上打开")
        self._dfp_open_btn.clicked.connect(self._dfp_open_browser)
        row.addWidget(self._dfp_open_btn)
        self._dfp_copy_btn = QPushButton("📋 复制手机地址")
        self._dfp_copy_btn.clicked.connect(self._dfp_copy_lan)
        row.addWidget(self._dfp_copy_btn)
        self._dfp_toggle_btn = QPushButton("✅ 启动服务")
        self._dfp_toggle_btn.clicked.connect(self._dfp_toggle_server)
        row.addWidget(self._dfp_toggle_btn)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        self._dfp_read_btn = QPushButton("🔊 试读「88 番」")
        self._dfp_read_btn.clicked.connect(self._dfp_test_read)
        row2.addWidget(self._dfp_read_btn)
        row2.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row2.addWidget(close_btn)
        layout.addLayout(row2)

        self.dfp_refresh()

    # --- 刷新 ---
    def dfp_web(self) -> Optional[DFPMahjongWeb]:
        getter = getattr(self._dfp_controller, "dfp_mahjong", None)
        if callable(getter):
            return getter()
        return None

    def dfp_refresh(self) -> None:
        web = self.dfp_web()
        if web is None:
            self._dfp_status_label.setText("算番 Web 版不可用。")
            return
        self._dfp_status_label.setText("状态：%s" % web.dfp_status_text())
        self._dfp_local_edit.setText(web.dfp_url())
        self._dfp_lan_edit.setText(web.dfp_lan_url())
        self._dfp_toggle_btn.setText("⛔ 停止服务" if web.dfp_running() else "✅ 启动服务")
        missing = dfp_mahjong_missing()
        if missing:
            self._dfp_status_label.setText("状态：缺少文件 %s（应放在 `%s\\`）" % ("、".join(missing), DFP_MAHJONG_DIR_NAME))
            self._dfp_toggle_btn.setEnabled(False)
        else:
            self._dfp_toggle_btn.setEnabled(True)

    # --- 动作 ---
    def _dfp_open_browser(self) -> None:
        web = self.dfp_web()
        if web is None:
            return
        if not web.dfp_running() and not web.dfp_start():
            self.dfp_refresh()
            return
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl(web.dfp_url()))
        except Exception as exc:
            self._dfp_notify("打不开浏览器：%s" % exc, "warn")
        self.dfp_refresh()

    def _dfp_copy_lan(self) -> None:
        web = self.dfp_web()
        if web is None:
            return
        text = web.dfp_lan_url()
        try:
            QApplication.clipboard().setText(text)
            self._dfp_notify("已复制：%s（在手机浏览器里粘贴打开）" % text, "success")
        except Exception:
            self._dfp_notify("复制失败，地址是 %s" % text, "warn")

    def _dfp_toggle_server(self) -> None:
        web = self.dfp_web()
        if web is None:
            return
        if web.dfp_running():
            web.dfp_stop()
            self._dfp_notify("算番 Web 版已停止", "info")
        else:
            if web.dfp_start():
                self._dfp_notify("算番 Web 版已启动：%s" % web.dfp_lan_url(), "success")
            else:
                self._dfp_notify("算番 Web 版没启动：%s" % web.dfp_error(), "warn")
        self.dfp_refresh()

    def _dfp_test_read(self) -> None:
        reader = getattr(self._dfp_controller, "dfp_test_read_number", None)
        if callable(reader):
            reader(88)

    def _dfp_notify(self, text: str, level: str = "info") -> None:
        notify = getattr(self._dfp_controller, "dfp_notify", None)
        if callable(notify):
            try:
                notify(text, level)
            except Exception:
                pass


# =============================================================================
#  三十、程序入口
# =============================================================================


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    # ★ 先让控制台安静下来（Qt/FFmpeg 的日志规则与消息处理器要越早装越好）
    dfp_setup_quiet_console(argv)
    # 还有一类是绕开 Qt 直接写 fd 2 的（FFmpeg 探测音频），只能把 stderr 接下来自己过滤
    if not dfp_console_verbose_requested(argv):
        dfp_stderr_filter_start(os.path.join(dfp_log_dir(), "console_%s.log" % dfp_today_key()))
    app = QApplication.instance()
    if app is None:
        # ★ 高 DPI 取整策略必须在建 QApplication **之前**设，否则 Qt 会拒绝并打警告
        dfp_setup_high_dpi()
        app = QApplication(argv)
    try:
        app.setApplicationName(DFP_APP_NAME)
        app.setApplicationDisplayName(DFP_APP_TITLE)
        app.setOrganizationName(DFP_APP_ORG)
        app.setApplicationVersion(__version__)
        app.setQuitOnLastWindowClosed(False)
    except Exception:
        pass

    if not dfp_single_instance_acquire():
        # 另一只已经在跑了：窗口会被它显示出来，控制台就不要再唠嗑了
        dfp_console_note("已有 DeepSeek 肥鱼娘桌宠在运行，已通知它显示出来。")
        return 0

    dfp_ensure_dir(dfp_data_dir())
    splash = DFPSplashScreen()
    try:
        icon = dfp_app_icon()
        splash.setWindowIcon(icon)
        app.setWindowIcon(icon)
    except Exception:
        pass
    splash.dfp_update(6, "正在读取 .env 配置…")
    splash.show()

    logger = DFPLogger(dfp_log_dir(), "INFO", True)
    logger.dfp_info("=" * 24 + " 启动 %s v%s " % (DFP_APP_TITLE, __version__) + "=" * 24)
    dfp_load_env(force=True)
    asset_root = os.path.join(dfp_app_dir(), DFP_ASSET_DIR_NAME)
    logger.dfp_info("素材目录：%s（%s）" % (asset_root, "存在" if os.path.isdir(asset_root) else "不存在"))
    logger.dfp_info("声音播放：%s" % ("QtMultimedia 可用" if dfp_multimedia_available() else "不可用"))

    splash.dfp_update(20, "正在载入设置…")
    settings = DFPSettingsStore(dfp_settings_path(), logger)
    settings.dfp_load()
    logger.dfp_set_level(str(settings.dfp_get("log_level", "INFO")))
    logger.dfp_set_to_file(bool(settings.dfp_get("log_to_file", True)))

    # ★ v1.1.17：启动画面左边那张图 —— 有「启动图.<后缀>」就用它，否则用当前形象立绘，
    #   都没有才画手绘那只（老行为）。只读文件、不写盘，失败就当没给。
    _splash_image = dfp_splash_apply_image(splash, settings, logger)
    if _splash_image:
        logger.dfp_info("启动画面用图：%s" % _splash_image)

    # ★ 台词库：优先读 `台词库.json`（改了 JSON 不用改代码、不用重新编译）
    dfp_lines_load(settings, force=True)
    logger.dfp_info("台词库：%s" % dfp_lines_summary())
    _lines_error = dfp_lines_error()
    if _lines_error:
        logger.dfp_warning("台词库.json 读取失败，已回退内置台词：%s" % _lines_error)

    # ★ 等级称号：优先读 `等级.json`（同上一套机制，改完不用重编译）
    dfp_levels_load(settings, force=True)
    logger.dfp_info("等级称号：%s" % dfp_levels_summary())
    _levels_error = dfp_levels_error()
    if _levels_error:
        logger.dfp_warning("等级.json 读取失败，已回退内置称号：%s" % _levels_error)

    # ★ 梦境库：优先读 `梦境.json`（同上一套机制，梦的骨架可以自己改）
    dfp_dreams_load(settings, force=True)
    logger.dfp_info("梦境库：%s" % dfp_dreams_summary())
    _dreams_error = dfp_dreams_error()
    if _dreams_error:
        logger.dfp_warning("梦境.json 读取失败，已回退内置梦境库：%s" % _dreams_error)

    # ★ v1.1.26 灵魂库：优先读 `灵魂.json`（心事 / 里程碑 / 关系阶段感言 / 小癖好）
    dfp_soul_load(settings, force=True)
    logger.dfp_info("灵魂库：%s" % dfp_soul_summary())
    _soul_error = dfp_soul_error()
    if _soul_error:
        logger.dfp_warning("灵魂.json 读取失败，已回退内置灵魂库：%s" % _soul_error)

    # ★ v1.1.28 魂魄库：优先读 `魂魄.json`（三魂七魄 的 旺 / 平 / 弱 各自文案）
    dfp_spirits_load(settings, force=True)
    logger.dfp_info("魂魄库：%s" % dfp_spirits_summary())
    _spirits_error = dfp_spirits_error()
    if _spirits_error:
        logger.dfp_warning("魂魄.json 读取失败，已回退内置魂魄库：%s" % _spirits_error)

    # ★ v1.1.29 道库：优先读 `道.json`（道行境界 / 五脏六腑 / 因果 / 神仙鬼妖 的文案）
    dfp_dao_load(settings, force=True)
    logger.dfp_info("道库：%s" % dfp_dao_summary())
    _dao_error = dfp_dao_error()
    if _dao_error:
        logger.dfp_warning("道.json 读取失败，已回退内置道库：%s" % _dao_error)

    splash.dfp_update(38, "正在打开数据库…")
    database = DFPDatabase(dfp_database_path(), logger)
    database.dfp_configure_secrets(dfp_secret_password(), bool(settings.dfp_get("db_encrypt_messages", True)))
    if not database.dfp_open():
        logger.dfp_error("数据库打开失败，将以只读内存状态继续运行")
    if dfp_uses_default_secret():
        logger.dfp_warning("正在使用默认数据库口令，建议在 .env 里设置 DB_PASSWORD")

    splash.dfp_update(52, "正在唤醒大肥鱼…")
    brain = DFPPetBrain(settings, database, logger)
    client = DFPDeepSeekClient(logger)
    splash.dfp_update(62, "正在准备对话与形象…")
    chat_manager = DFPChatManager(client, settings, logger)
    backups = DFPBackupManager(dfp_database_path(), dfp_backup_dir(), logger)

    splash.dfp_update(76, "正在搭建界面…")
    controller = DFPAppController(app, logger, settings, database, brain, client, chat_manager, backups, splash)
    controller.dfp_build()

    splash.dfp_update(90, "正在放下桌宠…")
    controller.dfp_start()
    try:
        app.aboutToQuit.connect(controller.dfp_shutdown)
    except Exception:
        pass
    splash.dfp_finish(600)
    splash.close()
    logger.dfp_info("初始化完成，进入事件循环")
    return int(app.exec())


if __name__ == "__main__":
    sys.exit(main())
