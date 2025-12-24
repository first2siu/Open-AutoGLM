#!/usr/bin/env python3
"""
MobileAgent - AutoGLM Mobile Device Agent (WebSocket Client)
对标 AutoGLM 源码重构的手机端自动化代理

参考实现：
- phone_agent/adb/device.py - 设备操作
- phone_agent/adb/input.py - 文本输入
- phone_agent/actions/handler.py - 动作处理
"""

import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, Optional, Tuple

import websockets
import uiautomator2 as u2
from PIL import Image

# ==================== 配置区 ====================
SERVER_URL = os.getenv("AUTOGLM_SERVER_URL", "ws://10.25.144.51:8765")
DEVICE_ID = os.getenv("AUTOGLM_DEVICE_ID", f"termux_{uuid.uuid4().hex[:8]}")
LOG_LEVEL = os.getenv("AUTOGLM_LOG_LEVEL", "INFO")

# 截图配置
SCREENSHOT_QUALITY = int(os.getenv("AUTOGLM_SCREENSHOT_QUALITY", "45"))
SCREENSHOT_MAX_WIDTH = int(os.getenv("AUTOGLM_SCREENSHOT_MAX_WIDTH", "1080"))

# 连接配置
RECONNECT_DELAY = float(os.getenv("AUTOGLM_RECONNECT_DELAY", "5"))
ADB_CMD_TIMEOUT = int(os.getenv("AUTOGLM_ADB_CMD_TIMEOUT", "30"))

# ADB Keyboard 配置 (参考 input.py)
ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"
ADB_INPUT_B64_ACTION = "ADB_INPUT_B64"
ADB_CLEAR_TEXT_ACTION = "ADB_CLEAR_TEXT"

# 时序配置 (参考 timing.py)
DEFAULT_TAP_DELAY = float(os.getenv("AUTOGLM_TAP_DELAY", "1.0"))
DEFAULT_DOUBLE_TAP_DELAY = float(os.getenv("AUTOGLM_DOUBLE_TAP_DELAY", "1.0"))
DOUBLE_TAP_INTERVAL = float(os.getenv("AUTOGLM_DOUBLE_TAP_INTERVAL", "0.1"))
DEFAULT_LONG_PRESS_DELAY = float(os.getenv("AUTOGLM_LONG_PRESS_DELAY", "1.0"))
DEFAULT_SWIPE_DELAY = float(os.getenv("AUTOGLM_SWIPE_DELAY", "1.0"))
DEFAULT_BACK_DELAY = float(os.getenv("AUTOGLM_BACK_DELAY", "1.0"))
DEFAULT_HOME_DELAY = float(os.getenv("AUTOGLM_HOME_DELAY", "1.0"))
DEFAULT_LAUNCH_DELAY = float(os.getenv("AUTOGLM_LAUNCH_DELAY", "1.0"))

# 文本输入时序 (参考 input.py + handler.py)
KEYBOARD_SWITCH_DELAY = float(os.getenv("AUTOGLM_KEYBOARD_SWITCH_DELAY", "1.0"))
TEXT_CLEAR_DELAY = float(os.getenv("AUTOGLM_TEXT_CLEAR_DELAY", "1.0"))
TEXT_INPUT_DELAY = float(os.getenv("AUTOGLM_TEXT_INPUT_DELAY", "1.0"))
KEYBOARD_RESTORE_DELAY = float(os.getenv("AUTOGLM_KEYBOARD_RESTORE_DELAY", "1.0"))

# 日志配置
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ==================== 时序配置类 (对标 timing.py) ====================
@dataclass
class TimingConfig:
    """时序配置类，对标 phone_agent.config.timing.TimingConfig"""

    # 设备操作延迟
    default_tap_delay: float = DEFAULT_TAP_DELAY
    default_double_tap_delay: float = DEFAULT_DOUBLE_TAP_DELAY
    double_tap_interval: float = DOUBLE_TAP_INTERVAL
    default_long_press_delay: float = DEFAULT_LONG_PRESS_DELAY
    default_swipe_delay: float = DEFAULT_SWIPE_DELAY
    default_back_delay: float = DEFAULT_BACK_DELAY
    default_home_delay: float = DEFAULT_HOME_DELAY
    default_launch_delay: float = DEFAULT_LAUNCH_DELAY

    # 文本输入延迟
    keyboard_switch_delay: float = KEYBOARD_SWITCH_DELAY
    text_clear_delay: float = TEXT_CLEAR_DELAY
    text_input_delay: float = TEXT_INPUT_DELAY
    keyboard_restore_delay: float = KEYBOARD_RESTORE_DELAY


# 全局时序配置
TIMING_CONFIG = TimingConfig()

# ==================== 应用包名映射 (对标 config/apps.py) ====================
APP_PACKAGES = {
    # 社交应用
    "微信": "com.tencent.mm",
    "QQ": "com.tencent.mobileqq",
    "新浪微博": "com.sina.weibo",
    "小红书": "com.xingin.xhs",
    "钉钉": "com.alibaba.android.rimet",
    "抖音": "com.ss.android.ugc.aweme",
    "快手": "com.smile.gifmaker",

    # 购物应用
    "淘宝": "com.taobao.taobao",
    "天猫": "com.tmall.wireless",
    "京东": "com.jingdong.app.mall",
    "拼多多": "com.xunmeng.pinduoduo",
    "美团": "com.sankuai.meituan",
    "苏宁易购": "com.suning.shop.ebuy",
    "得物": "com.poizon.shop",

    # 支付应用
    "支付宝": "com.eg.android.AlipayGphone",
    "云闪付": "com.unionpay",
    "京东金融": "com.jingdong.app.finance",

    # 出行应用
    "高德地图": "com.autonavi.minimap",
    "百度地图": "com.baidu.BaiduMap",
    "滴滴出行": "com.sdu.didi.psnger",
    "哈啰出行": "com.youga.statistical",
    "铁路12306": "com.MobileTicket",

    # 视频应用
    "腾讯视频": "com.tencent.qqlive",
    "爱奇艺": "com.qiyi.video",
    "优酷": "com.youku.phone",
    "哔哩哔哩": "tv.danmaku.bili",
    "芒果TV": "com.hunantv.imgo.activity",
    "快手极速版": "com.kuaishou.nebula",

    # 音乐应用
    "网易云音乐": "com.netease.cloudmusic",
    "QQ音乐": "com.tencent.qqmusic",
    "酷狗音乐": "com.kugou.android",
    "酷我音乐": "cn.kuwo.player",

    # 阅读应用
    "今日头条": "com.ss.android.article.news",
    "网易新闻": "com.netease.newsreader.activity",
    "知乎": "com.zhihu.android",
    "豆瓣": "com.douban.frodo",
    "起点读书": "com.qidian.QDReader",

    # 工具应用
    "百度": "com.baidu.searchbox",
    "Chrome": "com.android.chrome",
    "QQ浏览器": "com.tencent.mtt",
    "UC浏览器": "com.uc.browser",
    "360浏览器": "com.qihoo.browser",
    "搜狗搜索": "com.sogou.activity",
    "华为浏览器": "com.huawei.browser",

    # 系统应用
    "设置": "com.android.settings",
    "文件管理": "com.android.filemanager",
    "计算器": "com.android.calculator2",
    "日历": "com.android.calendar",
    "时钟": "com.android.deskclock",
    "相机": "com.android.camera",
    "相册": "com.android.gallery3d",
    "电话": "com.android.contacts",
    "短信": "com.android.mms",
    "应用商店": "com.xiaomi.market",
}


# ==================== ADB 管理器 (对标 device.py + input.py) ====================
class ADBManager:
    """
    ADB 设备管理器 - 完整对标 AutoGLM 实现

    参考：
    - phone_agent/adb/device.py - 设备操作
    - phone_agent/adb/input.py - 文本输入
    """

    def __init__(self):
        self.adb_port = None
        self.device_id = None
        self.adb_available = False
        self.u2_conn = None
        self._current_ime = None  # 当前输入法标识
        self._screen_width = 1080  # 默认屏幕宽度
        self._screen_height = 2400  # 默认屏幕高度
        self._initialize_adb()

    def _initialize_adb(self):
        """利用 uiautomator2 自动抓取端口并初始化 ADB"""
        logger.info("🔄 正在通过 uiautomator2 探测无线调试端口...")

        try:
            # 1. 连接本地 u2 服务 (atx-agent)
            self.u2_conn = u2.connect()

            # 2. 尝试从系统 UI 获取无线调试端口
            self.adb_port = self._fetch_port_from_ui()

            if not self.adb_port:
                self.adb_port = os.getenv("ADB_PORT")

            if not self.adb_port:
                logger.error("❌ 无法获取 ADB 端口，请确保无线调试已开启且 atx-agent 已启动")
                self.adb_available = False
                return

            self.device_id = f"127.0.0.1:{self.adb_port}"

            # 3. 执行连接
            if self._connect_local_adb():
                logger.info(f"✅ ADB 已成功连接: {self.device_id}")
                self.adb_available = True
                # 获取屏幕尺寸
                self._update_screen_size()
            else:
                logger.error(f"❌ ADB 连接失败: {self.device_id}")
                self.adb_available = False
        except Exception as e:
            logger.error(f"⚠️ 初始化异常: {e}")
            self.adb_available = False

    def _update_screen_size(self):
        """更新屏幕尺寸"""
        try:
            success, stdout, _ = self._run_adb(["shell", "wm", "size"])
            if success and stdout:
                match = re.search(r'Physical size: (\d+)x(\d+)', stdout)
                if match:
                    self._screen_width = int(match.group(1))
                    self._screen_height = int(match.group(2))
                    logger.info(f"📱 屏幕尺寸: {self._screen_width}x{self._screen_height}")
        except Exception as e:
            logger.warning(f"⚠️ 获取屏幕尺寸失败: {e}")

    def _fetch_port_from_ui(self) -> Optional[str]:
        """专门针对小米手机优化的端口抓取逻辑"""
        try:
            logger.info("📡 正在针对小米手机进行深度探测...")

            self.u2_conn.shell("am start -n com.android.settings/.development.WirelessDebuggingSettings")
            time.sleep(2)

            if not self.u2_conn(textContains="127.0.0.1").exists:
                logger.info("📍 快捷跳转失败，尝试从开发者选项手动进入...")
                self.u2_conn.shell("am start -a android.settings.APPLICATION_DEVELOPMENT_SETTINGS")
                time.sleep(1.5)

                found = False
                for _ in range(5):
                    if self.u2_conn(text="无线调试").exists:
                        self.u2_conn(text="无线调试").click()
                        found = True
                        break
                    self.u2_conn.swipe_ext("up", scale=0.4)

                if not found:
                    logger.error("❌ 在开发者选项中未找到'无线调试'入口")
                    return None
                time.sleep(1.5)

            page_xml = self.u2_conn.dump_hierarchy()

            # 模式 A: 标准匹配 127.0.0.1:端口
            match = re.search(r'127\.0\.0\.1:(\d{5})', page_xml)
            if match:
                port = match.group(1)
                logger.info(f"🎯 成功抓取本地端口: {port}")
                return port

            # 模式 B: 匹配内网 IP 后的端口
            match_ip = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d{5})', page_xml)
            if match_ip:
                port = match_ip.group(2)
                logger.info(f"🎯 成功抓取局域网端口: {port}")
                return port

            # 模式 C: 暴力搜索 5 位数字
            ports = re.findall(r'(?<!\d)(\d{5})(?!\d)', page_xml)
            for p in ports:
                if p != "00000" and p != "12345":
                    logger.info(f"🎯 发现疑似端口数字: {p}")
                    return p

            return None
        except Exception as e:
            logger.error(f"⚠️ UI 探测失败: {e}")
            return None

    def _connect_local_adb(self) -> bool:
        """连接本地 ADB"""
        try:
            subprocess.run(["adb", "disconnect"], capture_output=True, timeout=2)
            result = subprocess.run(
                ["adb", "connect", self.device_id],
                capture_output=True, text=True, timeout=10
            )
            if "connected" in result.stdout.lower():
                verify = subprocess.run(
                    ["adb", "-s", self.device_id, "shell", "echo", "ok"],
                    capture_output=True, text=True, timeout=5
                )
                return verify.returncode == 0
            return False
        except Exception:
            return False

    def _run_adb(self, args: list) -> Tuple[bool, str, str]:
        """执行 ADB 命令"""
        if not self.adb_available:
            self._initialize_adb()
            if not self.adb_available:
                return False, "", "ADB Unavailable"

        cmd = ["adb", "-s", self.device_id] + args
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=ADB_CMD_TIMEOUT)
            if res.returncode != 0:
                if "offline" in res.stderr.lower() or "not found" in res.stderr.lower():
                    self.adb_available = False
            return res.returncode == 0, res.stdout, res.stderr
        except Exception as e:
            self.adb_available = False
            return False, "", str(e)

    # ==================== 截图功能 (对标 screenshot.py) ====================
    def screenshot(self) -> Dict[str, Any]:
        """
        截图 - 对标 phone_agent/adb/screenshot.py::get_screenshot
        返回: {"success": bool, "base64_data": str, "width": int, "height": int, "error": str}
        """
        temp_dir = tempfile.gettempdir()
        temp_file_name = f"sc_{uuid.uuid4().hex[:4]}.png"
        temp_path = os.path.join(temp_dir, temp_file_name)
        remote_path = "/data/local/tmp/autoglm_sc.png"

        try:
            # 截屏
            success, _, stderr = self._run_adb(["shell", "screencap", "-p", remote_path])
            if not success:
                return {"success": False, "error": f"Screencap fail: {stderr}"}

            # 拉取图片
            success, _, stderr = self._run_adb(["pull", remote_path, temp_path])
            if not success:
                return {"success": False, "error": f"Pull fail: {stderr}"}

            # 处理图片
            img = Image.open(temp_path)
            width, height = img.size

            # 缩放
            if img.width > SCREENSHOT_MAX_WIDTH:
                img.thumbnail((SCREENSHOT_MAX_WIDTH, img.height), Image.LANCZOS)

            # 转换格式
            if img.mode != "RGB":
                img = img.convert("RGB")

            # 编码为 base64
            buffered = BytesIO()
            img.save(buffered, format="JPEG", quality=SCREENSHOT_QUALITY)
            base64_data = base64.b64encode(buffered.getvalue()).decode("utf-8")

            os.remove(temp_path)
            logger.info(f"📸 截图上传中... (尺寸: {width}x{height})")

            return {
                "success": True,
                "base64_data": base64_data,
                "width": width,
                "height": height,
                "error": ""
            }
        except Exception as e:
            logger.error(f"截图异常: {e}")
            return {"success": False, "error": str(e)}

    # ==================== 坐标转换 (对标 handler.py) ====================
    def _convert_relative_to_absolute(
        self, element: list, screen_width: int, screen_height: int
    ) -> Tuple[int, int]:
        """
        将相对坐标 (0-1000) 转换为绝对像素坐标
        对标 phone_agent/actions/handler.py::_convert_relative_to_absolute
        """
        x = int(element[0] / 1000 * screen_width)
        y = int(element[1] / 1000 * screen_height)
        return x, y

    # ==================== 设备操作 (对标 device.py) ====================
    def tap(self, x: int, y: int, delay: Optional[float] = None) -> Dict[str, Any]:
        """
        点击 - 对标 phone_agent/adb/device.py::tap
        Args:
            x: X 坐标 (像素)
            y: Y 坐标 (像素)
            delay: 延迟时间 (秒)
        """
        if delay is None:
            delay = TIMING_CONFIG.default_tap_delay

        success, _, stderr = self._run_adb(["shell", "input", "tap", str(x), str(y)])
        if success:
            time.sleep(delay)
        return {"success": success, "error": stderr if not success else ""}

    def double_tap(
        self, x: int, y: int, delay: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        双击 - 对标 phone_agent/adb/device.py::double_tap
        Args:
            x: X 坐标 (像素)
            y: Y 坐标 (像素)
            delay: 延迟时间 (秒)
        """
        if delay is None:
            delay = TIMING_CONFIG.default_double_tap_delay

        # 第一次点击
        success1, _, stderr1 = self._run_adb(["shell", "input", "tap", str(x), str(y)])
        time.sleep(TIMING_CONFIG.double_tap_interval)

        # 第二次点击
        success2, _, stderr2 = self._run_adb(["shell", "input", "tap", str(x), str(y)])
        if success2:
            time.sleep(delay)

        return {
            "success": success1 and success2,
            "error": stderr1 if not success1 else stderr2 if not success2 else ""
        }

    def long_press(
        self, x: int, y: int, duration_ms: int = 3000, delay: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        长按 - 对标 phone_agent/adb/device.py::long_press
        Args:
            x: X 坐标 (像素)
            y: Y 坐标 (像素)
            duration_ms: 持续时间 (毫秒)
            delay: 延迟时间 (秒)
        """
        if delay is None:
            delay = TIMING_CONFIG.default_long_press_delay

        # 长按使用 swipe 实现，起点终点相同
        success, _, stderr = self._run_adb([
            "shell", "input", "swipe",
            str(x), str(y), str(x), str(y), str(duration_ms)
        ])
        if success:
            time.sleep(delay)
        return {"success": success, "error": stderr if not success else ""}

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: Optional[int] = None,
        delay: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        滑动 - 对标 phone_agent/adb/device.py::swipe
        Args:
            start_x: 起始 X 坐标 (像素)
            start_y: 起始 Y 坐标 (像素)
            end_x: 结束 X 坐标 (像素)
            end_y: 结束 Y 坐标 (像素)
            duration_ms: 滑动持续时间 (毫秒)，None 则自动计算
            delay: 延迟时间 (秒)
        """
        if delay is None:
            delay = TIMING_CONFIG.default_swipe_delay

        # 自动计算 duration_ms
        if duration_ms is None:
            dist_sq = (start_x - end_x) ** 2 + (start_y - end_y) ** 2
            duration_ms = int(dist_sq / 1000)
            duration_ms = max(1000, min(duration_ms, 2000))  # 限制在 1000-2000ms

        success, _, stderr = self._run_adb([
            "shell", "input", "swipe",
            str(start_x), str(start_y), str(end_x), str(end_y), str(duration_ms)
        ])
        if success:
            time.sleep(delay)
        return {"success": success, "error": stderr if not success else ""}

    def back(self, delay: Optional[float] = None) -> Dict[str, Any]:
        """
        返回键 - 对标 phone_agent/adb/device.py::back
        Args:
            delay: 延迟时间 (秒)
        """
        if delay is None:
            delay = TIMING_CONFIG.default_back_delay

        success, _, stderr = self._run_adb(["shell", "input", "keyevent", "4"])
        if success:
            time.sleep(delay)
        return {"success": success, "error": stderr if not success else ""}

    def home(self, delay: Optional[float] = None) -> Dict[str, Any]:
        """
        Home 键 - 对标 phone_agent/adb/device.py::home
        Args:
            delay: 延迟时间 (秒)
        """
        if delay is None:
            delay = TIMING_CONFIG.default_home_delay

        success, _, stderr = self._run_adb(["shell", "input", "keyevent", "KEYCODE_HOME"])
        if success:
            time.sleep(delay)
        return {"success": success, "error": stderr if not success else ""}

    def keyevent(self, keycode: str, delay: Optional[float] = 0.5) -> Dict[str, Any]:
        """
        通用按键事件 - 对标 handler.py::_send_keyevent
        Args:
            keycode: 按键码 (如 "KEYCODE_ENTER", "4", "66" 等)
            delay: 延迟时间 (秒)
        """
        success, _, stderr = self._run_adb(["shell", "input", "keyevent", keycode])
        if success:
            time.sleep(delay)
        return {"success": success, "error": stderr if not success else ""}

    def menu(self, delay: Optional[float] = None) -> Dict[str, Any]:
        """菜单键 (KEYCODE_MENU = 82)"""
        return self.keyevent("82", delay)

    def power(self, delay: Optional[float] = None) -> Dict[str, Any]:
        """电源键 (KEYCODE_POWER = 26)"""
        return self.keyevent("26", delay)

    def volume_up(self, delay: Optional[float] = None) -> Dict[str, Any]:
        """音量加 (KEYCODE_VOLUME_UP = 24)"""
        return self.keyevent("24", delay)

    def volume_down(self, delay: Optional[float] = None) -> Dict[str, Any]:
        """音量减 (KEYCODE_VOLUME_DOWN = 25)"""
        return self.keyevent("25", delay)

    def enter(self, delay: Optional[float] = None) -> Dict[str, Any]:
        """回车键 (KEYCODE_ENTER = 66)"""
        return self.keyevent("KEYCODE_ENTER", delay)

    def delete(self, count: int = 1, delay: float = 0.05) -> Dict[str, Any]:
        """
        删除键 - 连发退格键
        Args:
            count: 删除字符数
            delay: 每次按键间隔 (秒)
        """
        for _ in range(count):
            success, _, stderr = self._run_adb(["shell", "input", "keyevent", "67"])  # KEYCODE_DEL
            if not success:
                return {"success": False, "error": stderr}
            time.sleep(delay)
        return {"success": True, "error": ""}

    # ==================== 应用管理 (对标 device.py) ====================
    def launch_app(
        self, package: str, activity: Optional[str] = None, delay: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        启动应用 - 对标 phone_agent/adb/device.py::launch_app
        Args:
            package: 包名 (如 "com.android.chrome")
            activity: Activity 名 (可选，如 ".MainActivity")
            delay: 延迟时间 (秒)
        """
        if delay is None:
            delay = TIMING_CONFIG.default_launch_delay

        try:
            # 方法 1: 使用 monkey 启动 (对标 device.py)
            cmd = ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"]
            success, stdout, stderr = self._run_adb(cmd)

            if success:
                time.sleep(delay)
                return {"success": True, "error": ""}

            # 方法 2: 备选方案 - am start
            if activity:
                full_activity = activity if activity.startswith(".") else f".{activity}"
                component = f"{package}/{full_activity}"
            else:
                component = package

            cmd = ["shell", "am", "start", "-n", component]
            success, stdout, stderr = self._run_adb(cmd)

            if success:
                time.sleep(delay)
                return {"success": True, "error": ""}

            return {"success": False, "error": stderr or "Failed to launch app"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def stop_app(self, package: str) -> Dict[str, Any]:
        """
        停止应用
        Args:
            package: 包名
        """
        success, _, stderr = self._run_adb(["shell", "am", "force-stop", package])
        return {"success": success, "error": stderr if not success else ""}

    def clear_app(self, package: str) -> Dict[str, Any]:
        """
        清除应用数据
        Args:
            package: 包名
        """
        success, _, stderr = self._run_adb(["shell", "pm", "clear", package])
        return {"success": success, "error": stderr if not success else ""}

    def get_current_app(self) -> Dict[str, Any]:
        """
        获取当前前台应用 - 对标 phone_agent/adb/device.py::get_current_app
        解析 dumpsys window 输出，提取应用名称
        """
        success, stdout, _ = self._run_adb(["shell", "dumpsys", "window"])

        if success and stdout:
            # 遍历每一行，查找当前焦点窗口信息
            # 完全对标 ADB 版本的实现逻辑
            for line in stdout.split("\n"):
                if "mCurrentFocus" in line or "mFocusedApp" in line:
                    # 匹配包名到应用名称
                    for app_name, package in APP_PACKAGES.items():
                        if package in line:
                            logger.info(f"📱 当前应用: {app_name} ({package})")
                            return {
                                "success": True,
                                "package": package,
                                "app_name": app_name
                            }

            # 如果没有找到已知应用，尝试提取包名（返回 Unknown）
            # 这样可以让服务器端知道有某个应用在运行，即使不在映射表中
            match = re.search(r'mCurrentFocus.*\{.*\s+(\S+)/', stdout)
            if match:
                package = match.group(1)
                logger.info(f"📱 未知应用包名: {package}")
                return {
                    "success": True,
                    "package": package,
                    "app_name": "Unknown"
                }

            # 备选匹配模式
            match = re.search(r'mFocusedApp.+?(\S+)/\S+', stdout)
            if match:
                package = match.group(1)
                logger.info(f"📱 未知应用包名 (备选): {package}")
                return {
                    "success": True,
                    "package": package,
                    "app_name": "Unknown"
                }

        logger.warning("⚠️ 无法获取当前应用信息")
        return {"success": False, "package": "Unknown", "app_name": "Unknown"}

    # ==================== 屏幕状态 ====================
    def is_screen_on(self) -> Dict[str, Any]:
        """
        检查屏幕是否亮起
        """
        success, stdout, _ = self._run_adb(["shell", "dumpsys", "power"])
        if success:
            # 检查 Display Power State
            if "mScreenOn=true" in stdout or "Display Power: state=ON" in stdout:
                return {"success": True, "is_on": True}
            return {"success": True, "is_on": False}
        return {"success": False, "is_on": False, "error": "Failed to check screen state"}

    def unlock(self) -> Dict[str, Any]:
        """
        解锁屏幕 - 唤醒 + 上滑
        """
        # 1. 唤醒屏幕 (电源键)
        self.power(delay=0.5)

        # 2. 上滑解锁 (假设从底部中间上滑到顶部)
        width, height = self._screen_width, self._screen_height
        start_x, start_y = width // 2, height - 200
        end_x, end_y = width // 2, 200

        return self.swipe(start_x, start_y, end_x, end_y, duration_ms=500, delay=1.0)

    # ==================== 文本输入 (对标 input.py) ====================
    def _get_adb_prefix(self) -> list:
        """获取 ADB 命令前缀"""
        if self.device_id:
            return ["adb", "-s", self.device_id]
        return ["adb"]

    def detect_and_set_adb_keyboard(self) -> str:
        """
        检测并切换到 ADB Keyboard - 对标 phone_agent/adb/input.py::detect_and_set_adb_keyboard
        Returns:
            原始输入法标识，用于后续恢复
        """
        adb_prefix = self._get_adb_prefix()

        # 获取当前输入法
        result = subprocess.run(
            adb_prefix + ["shell", "settings", "get", "secure", "default_input_method"],
            capture_output=True, text=True, timeout=10
        )
        current_ime = (result.stdout + result.stderr).strip()

        logger.info(f"⌨️  当前输入法: {current_ime}")

        # 切换到 ADB Keyboard
        if ADB_KEYBOARD_IME not in current_ime:
            subprocess.run(
                adb_prefix + ["shell", "ime", "set", ADB_KEYBOARD_IME],
                capture_output=True, text=True, timeout=10
            )
            logger.info(f"✅ 已切换到 ADB Keyboard: {ADB_KEYBOARD_IME}")
            time.sleep(TIMING_CONFIG.keyboard_switch_delay)
        else:
            logger.info("✅ ADB Keyboard 已是当前输入法")

        # 预热键盘
        self.type_text("", skip_keyboard_switch=True)

        return current_ime

    def restore_keyboard(self, ime: str) -> None:
        """
        恢复原始输入法 - 对标 phone_agent/adb/input.py::restore_keyboard
        Args:
            ime: 输入法标识
        """
        adb_prefix = self._get_adb_prefix()

        # 如果不是 ADB Keyboard，则恢复
        if ADB_KEYBOARD_IME not in ime:
            subprocess.run(
                adb_prefix + ["shell", "ime", "set", ime],
                capture_output=True, text=True, timeout=10
            )
            logger.info(f"🔄 已恢复输入法: {ime}")
            time.sleep(TIMING_CONFIG.keyboard_restore_delay)

    def type_text(
        self, text: str, skip_keyboard_switch: bool = False
    ) -> Dict[str, Any]:
        """
        输入文本 - 对标 phone_agent/adb/input.py::type_text
        使用 ADB Keyboard 的 Base64 广播机制
        Args:
            text: 要输入的文本
            skip_keyboard_switch: 是否跳过键盘切换检查
        """
        try:
            # 切换到 ADB Keyboard
            if not skip_keyboard_switch:
                self._current_ime = self.detect_and_set_adb_keyboard()

            # Base64 编码文本
            encoded_text = base64.b64encode(text.encode("utf-8")).decode("utf-8")

            # 通过广播发送到 ADB Keyboard
            adb_prefix = self._get_adb_prefix()
            result = subprocess.run(
                adb_prefix + [
                    "shell", "am", "broadcast", "-a",
                    ADB_INPUT_B64_ACTION, "--es", "msg", encoded_text
                ],
                capture_output=True, text=True, timeout=10
            )

            time.sleep(TIMING_CONFIG.text_input_delay)

            if result.returncode == 0:
                logger.info(f"⌨️  输入文本: {text[:50]}{'...' if len(text) > 50 else ''}")
                return {"success": True, "error": ""}
            else:
                return {"success": False, "error": result.stderr or "Failed to type text"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def clear_text(self, restore_keyboard: bool = True) -> Dict[str, Any]:
        """
        清除文本 - 对标 phone_agent/adb/input.py::clear_text
        通过广播清除 ADB Keyboard 文本
        Args:
            restore_keyboard: 是否在清除后恢复原始输入法
        """
        try:
            # 切换到 ADB Keyboard
            original_ime = self.detect_and_set_adb_keyboard()

            # 发送清除文本广播
            adb_prefix = self._get_adb_prefix()
            result = subprocess.run(
                adb_prefix + ["shell", "am", "broadcast", "-a", ADB_CLEAR_TEXT_ACTION],
                capture_output=True, text=True, timeout=10
            )

            time.sleep(TIMING_CONFIG.text_clear_delay)

            # 恢复原始输入法
            if restore_keyboard:
                self.restore_keyboard(original_ime)

            if result.returncode == 0:
                logger.info("🗑️  已清除文本")
                return {"success": True, "error": ""}
            else:
                return {"success": False, "error": result.stderr or "Failed to clear text"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def input_with_keyboard(
        self, text: str, clear_first: bool = True
    ) -> Dict[str, Any]:
        """
        完整的文本输入流程 - 对标 handler.py::_handle_type
        包含：切换键盘 -> 清除文本 -> 输入文本 -> 恢复键盘
        Args:
            text: 要输入的文本
            clear_first: 是否先清除已有文本
        """
        try:
            # 1. 切换到 ADB Keyboard
            original_ime = self.detect_and_set_adb_keyboard()

            # 2. 清除已有文本
            if clear_first:
                self.clear_text(restore_keyboard=False)

            # 3. 输入新文本
            result = self.type_text(text, skip_keyboard_switch=True)

            # 4. 恢复原始键盘
            self.restore_keyboard(original_ime)

            return result

        except Exception as e:
            return {"success": False, "error": str(e)}


# ==================== WebSocket 代理 ====================
class MobileAgent:
    """
    Mobile Agent - WebSocket 客户端
    接收服务器指令并调用 ADBManager 执行
    """

    def __init__(self, server_url: str, device_id: str):
        self.server_url = server_url
        self.device_id = device_id
        self.adb = ADBManager()
        self.websocket = None

    async def connect(self) -> bool:
        """连接到 WebSocket 服务器"""
        try:
            self.websocket = await websockets.connect(self.server_url)
            await self.websocket.send(json.dumps({
                "msg_type": "register",
                "msg_id": str(uuid.uuid4()),
                "device_id": self.device_id,
                "device_info": {
                    "type": "termux",
                    "adb_status": self.adb.adb_available,
                    "screen_width": self.adb._screen_width,
                    "screen_height": self.adb._screen_height,
                }
            }))
            logger.info(f"🚀 设备已注册: {self.device_id}")
            return True
        except Exception as e:
            logger.error(f"❌ 连接服务器失败: {e}")
            return False

    async def handle_command(self, data: Dict[str, Any]):
        """
        处理服务器指令 - 对标 handler.py 的 action 解析
        支持的指令集完全对标 AutoGLM
        """
        msg_id = data.get("msg_id")
        cmd = data.get("command")
        params = data.get("params", {})

        res = {"success": False}
        try:
            # ==================== 基础交互指令 ====================
            if cmd == "tap":
                # 点击 - 支持相对坐标 (0-1000) 或绝对坐标 (像素)
                x = params.get("x", 0)
                y = params.get("y", 0)
                relative = params.get("relative", False)

                if relative:
                    x, y = self.adb._convert_relative_to_absolute(
                        [x, y],
                        self.adb._screen_width,
                        self.adb._screen_height
                    )

                res = self.adb.tap(x, y)

            elif cmd == "double_tap":
                # 双击
                x = params.get("x", 0)
                y = params.get("y", 0)
                relative = params.get("relative", False)

                if relative:
                    x, y = self.adb._convert_relative_to_absolute(
                        [x, y],
                        self.adb._screen_width,
                        self.adb._screen_height
                    )

                res = self.adb.double_tap(x, y)

            elif cmd == "long_press":
                # 长按
                x = params.get("x", 0)
                y = params.get("y", 0)
                duration_ms = params.get("duration_ms", 3000)
                relative = params.get("relative", False)

                if relative:
                    x, y = self.adb._convert_relative_to_absolute(
                        [x, y],
                        self.adb._screen_width,
                        self.adb._screen_height
                    )

                res = self.adb.long_press(x, y, duration_ms)

            elif cmd == "swipe":
                # 滑动
                x1 = params.get("x1", params.get("start_x", 0))
                y1 = params.get("y1", params.get("start_y", 0))
                x2 = params.get("x2", params.get("end_x", 0))
                y2 = params.get("y2", params.get("end_y", 0))
                duration_ms = params.get("duration_ms")
                relative = params.get("relative", False)

                if relative:
                    x1, y1 = self.adb._convert_relative_to_absolute(
                        [x1, y1],
                        self.adb._screen_width,
                        self.adb._screen_height
                    )
                    x2, y2 = self.adb._convert_relative_to_absolute(
                        [x2, y2],
                        self.adb._screen_width,
                        self.adb._screen_height
                    )

                res = self.adb.swipe(x1, y1, x2, y2, duration_ms)

            # ==================== 系统按键指令 ====================
            elif cmd == "back":
                res = self.adb.back()
            elif cmd == "home":
                res = self.adb.home()
            elif cmd == "menu":
                res = self.adb.menu()
            elif cmd == "power":
                res = self.adb.power()
            elif cmd == "volume_up":
                res = self.adb.volume_up()
            elif cmd == "volume_down":
                res = self.adb.volume_down()
            elif cmd == "enter":
                res = self.adb.enter()
            elif cmd == "delete":
                count = params.get("count", 1)
                res = self.adb.delete(count)
            elif cmd == "keyevent":
                keycode = params.get("keycode", "")
                res = self.adb.keyevent(keycode)

            # ==================== 应用管理指令 ====================
            elif cmd == "launch_app":
                package = params.get("package") or params.get("app")
                activity = params.get("activity")
                if package:
                    res = self.adb.launch_app(package, activity)
                else:
                    res = {"success": False, "error": "Missing package"}

            elif cmd == "stop_app":
                package = params.get("package") or params.get("app")
                if package:
                    res = self.adb.stop_app(package)
                else:
                    res = {"success": False, "error": "Missing package"}

            elif cmd == "clear_app":
                package = params.get("package") or params.get("app")
                if package:
                    res = self.adb.clear_app(package)
                else:
                    res = {"success": False, "error": "Missing package"}

            elif cmd == "get_current_app":
                res = self.adb.get_current_app()

            # ==================== 文本输入指令 (对标 input.py) ====================
            elif cmd == "type_text":
                text = params.get("text", "")
                clear_first = params.get("clear_first", True)
                res = self.adb.input_with_keyboard(text, clear_first)

            elif cmd == "clear_text":
                res = self.adb.clear_text()

            elif cmd == "set_keyboard":
                # 手动设置输入法
                ime = params.get("ime", ADB_KEYBOARD_IME)
                adb_prefix = self.adb._get_adb_prefix()
                result = subprocess.run(
                    adb_prefix + ["shell", "ime", "set", ime],
                    capture_output=True, text=True, timeout=10
                )
                res = {"success": result.returncode == 0, "error": result.stderr}

            # ==================== 屏幕状态指令 ====================
            elif cmd == "is_screen_on":
                res = self.adb.is_screen_on()
            elif cmd == "unlock":
                res = self.adb.unlock()

            # ==================== 截图指令 ====================
            elif cmd == "screenshot":
                res = self.adb.screenshot()

            # ==================== 未知指令 ====================
            else:
                res = {"success": False, "error": f"Unknown command: {cmd}"}

        except Exception as e:
            logger.error(f"指令执行异常: {e}")
            res = {"success": False, "error": str(e)}

        # 发送结果回服务器
        await self.websocket.send(json.dumps({
            "msg_type": "result",
            "msg_id": msg_id,
            "success": res.get("success", False),
            "result": res
        }))

    async def run(self):
        """主运行循环"""
        while True:
            if not self.websocket or self.websocket.closed:
                if not await self.connect():
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue

            try:
                async for message_json in self.websocket:
                    data = json.loads(message_json)
                    if data.get("msg_type") == "command":
                        await self.handle_command(data)
            except Exception as e:
                logger.error(f"📡 连接断开: {e}")
                self.websocket = None
                await asyncio.sleep(2)


# ==================== 主入口 ====================
if __name__ == "__main__":
    agent = MobileAgent(SERVER_URL, DEVICE_ID)
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger.info("停止运行")
