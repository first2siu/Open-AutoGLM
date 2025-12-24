#!/usr/bin/env python3
"""
MobileAgent (Async Optimized - No U2) - AutoGLM Mobile Device Agent
性能优化版本（轻量化）：
- 移除 uiautomator2 依赖，降低资源占用
- 优先通过环境变量 ADB_PORT 连接
- 完全异步化的 ADB 调用
- 粘性输入法策略 & 直接 Stdout 截图
"""

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Dict, Optional, Tuple

import websockets
from PIL import Image

# ==================== 配置区 ====================
SERVER_URL = os.getenv("AUTOGLM_SERVER_URL", "ws://10.25.144.51:8765")
DEVICE_ID = os.getenv("AUTOGLM_DEVICE_ID", f"termux_{uuid.uuid4().hex[:8]}")
LOG_LEVEL = os.getenv("AUTOGLM_LOG_LEVEL", "INFO")

# 强制要求的 ADB 端口环境变量
ENV_ADB_PORT = os.getenv("ADB_PORT")

# 截图配置
SCREENSHOT_QUALITY = int(os.getenv("AUTOGLM_SCREENSHOT_QUALITY", "45"))
SCREENSHOT_MAX_WIDTH = int(os.getenv("AUTOGLM_SCREENSHOT_MAX_WIDTH", "1080"))
# 优化配置
SCREENSHOT_TARGET_SHORT_EDGE = int(os.getenv("AUTOGLM_SCREENSHOT_TARGET_SHORT_EDGE", "1080"))
SCREENSHOT_WEBP_QUALITY = int(os.getenv("AUTOGLM_SCREENSHOT_WEBP_QUALITY", "50"))
USE_BINARY_TRANSMISSION = os.getenv("AUTOGLM_USE_BINARY_TRANSMISSION", "true").lower() == "true"

# 连接配置
RECONNECT_DELAY = float(os.getenv("AUTOGLM_RECONNECT_DELAY", "5"))
ADB_CMD_TIMEOUT = int(os.getenv("AUTOGLM_ADB_CMD_TIMEOUT", "30"))


# ==================== 动态时序配置 ====================
@dataclass
class DynamicTimingConfig:
    """动态时序配置类 - 根据执行情况自适应调整延迟"""
    # 基础延迟
    base_tap_delay: float = 0.2
    base_double_tap_delay: float = 0.3
    base_long_press_delay: float = 0.3
    base_swipe_delay: float = 0.3
    base_back_delay: float = 0.2
    base_home_delay: float = 0.2
    base_launch_delay: float = 0.5

    # 双击间隔
    double_tap_interval: float = 0.1

    # 文本输入延迟
    base_text_input_delay: float = 0.3
    base_keyboard_switch_delay: float = 0.5

    # 补偿因子
    compensation_factor: float = 1.5
    max_compensation_count: int = 3

    # 实时统计
    success_count: int = field(default_factory=int)
    failure_count: int = field(default_factory=int)
    consecutive_failures: int = field(default_factory=int)

    def get_tap_delay(self) -> float:
        if self.consecutive_failures > 0:
            return self.base_tap_delay * (self.compensation_factor ** min(self.consecutive_failures, self.max_compensation_count))
        return self.base_tap_delay

    def record_success(self):
        self.success_count += 1
        self.consecutive_failures = 0

    def record_failure(self):
        self.failure_count += 1
        self.consecutive_failures += 1

    def get_stats(self) -> Dict[str, Any]:
        total = self.success_count + self.failure_count
        success_rate = self.success_count / total if total > 0 else 0
        return {
            "total": total,
            "success": self.success_count,
            "failure": self.failure_count,
            "success_rate": f"{success_rate:.2%}",
            "consecutive_failures": self.consecutive_failures,
        }

TIMING_CONFIG = DynamicTimingConfig()


# ==================== 应用包名映射 ====================
APP_PACKAGES = {
    "微信": "com.tencent.mm",
    "QQ": "com.tencent.mobileqq",
    "新浪微博": "com.sina.weibo",
    "小红书": "com.xingin.xhs",
    "钉钉": "com.alibaba.android.rimet",
    "抖音": "com.ss.android.ugc.aweme",
    "快手": "com.smile.gifmaker",
    "淘宝": "com.taobao.taobao",
    "天猫": "com.tmall.wireless",
    "京东": "com.jingdong.app.mall",
    "拼多多": "com.xunmeng.pinduoduo",
    "美团": "com.sankuai.meituan",
    "支付宝": "com.eg.android.AlipayGphone",
    "高德地图": "com.autonavi.minimap",
    "百度地图": "com.baidu.BaiduMap",
    "滴滴出行": "com.sdu.didi.psnger",
    "腾讯视频": "com.tencent.qqlive",
    "爱奇艺": "com.qiyi.video",
    "哔哩哔哩": "tv.danmaku.bili",
    "网易云音乐": "com.netease.cloudmusic",
    "QQ音乐": "com.tencent.qqmusic",
    "百度": "com.baidu.searchbox",
    "Chrome": "com.android.chrome",
    "QQ浏览器": "com.tencent.mtt",
    "设置": "com.android.settings",
}


# ==================== 异步 ADB 管理器 ====================
class AsyncADBManager:
    """
    异步 ADB 设备管理器 - 智能检测版
    功能：
    1. 自动识别连接策略：环境变量 -> 已连接设备 -> 端口扫描
    2. 完全异步的 ADB Shell 执行
    3. 高性能截图 (Stdout 直读)
    4. 粘性输入法支持 (ADB Keyboard)
    """

    def __init__(self):
        self.adb_port = None
        self.device_id = None
        self.adb_available = False
        
        # 屏幕参数缓存
        self._screen_width = 1080
        self._screen_height = 2400
        self._cached = False

        # 输入法状态缓存
        self._current_ime: Optional[str] = None
        self._adb_keyboard_active = False

        # ADB Keyboard 常量
        self.ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"
        self.ADB_INPUT_B64_ACTION = "ADB_INPUT_B64"
        self.ADB_CLEAR_TEXT_ACTION = "ADB_CLEAR_TEXT"

    async def _initialize_adb(self):
        """
        异步初始化 ADB 连接
        优先级：
        1. 环境变量 (ADB_PORT)
        2. 当前已通过 adb connect 连接的设备
        3. 扫描常见端口
        """
        logger.info("🔄 正在初始化 ADB 连接...")

        # --- 策略 1: 环境变量 (强制指定) ---
        if ENV_ADB_PORT:
            self.adb_port = ENV_ADB_PORT
            self.device_id = f"127.0.0.1:{self.adb_port}"
            logger.info(f"📍 [策略1] 使用环境变量端口: {self.adb_port}")
        
        else:
            # --- 策略 2: 检测已连接设备 (智能识别) ---
            connected_device = await self._detect_connected_device()
            if connected_device:
                self.device_id = connected_device
                # 尝试从 IP:Port 中提取端口用于记录，非必须
                if ":" in connected_device:
                    self.adb_port = connected_device.split(":")[-1]
                logger.info(f"🔗 [策略2] 发现已连接设备: {self.device_id}")
            
            # --- 策略 3: 扫描常见端口 (最后尝试) ---
            else:
                logger.warning("⚠️ 未发现已连接设备，尝试扫描常见端口...")
                self.adb_port = await self._scan_common_ports()
                if self.adb_port:
                    self.device_id = f"127.0.0.1:{self.adb_port}"
                    logger.info(f"🎯 [策略3] 扫描发现端口: {self.adb_port}")
                else:
                    logger.error("❌ 初始化失败：无环境变量、无已连接设备、扫描失败。")
                    self.adb_available = False
                    return

        # 最终连接测试
        if await self._connect_local_adb():
            logger.info(f"✅ ADB 服务就绪: {self.device_id}")
            self.adb_available = True
            await self._update_screen_size()
        else:
            logger.error(f"❌ ADB 连接测试失败: {self.device_id}")
            self.adb_available = False

    async def _detect_connected_device(self) -> Optional[str]:
        """
        检测 `adb devices` 列表
        返回: device_id (如 "127.0.0.1:5555") 或 None
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "adb", "devices",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
            output = stdout.decode()

            # 解析输出，寻找状态为 'device' 且包含端口号的设备
            lines = output.strip().split('\n')
            for line in lines:
                if "List of devices" in line:
                    continue
                
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "device":
                    device_name = parts[0]
                    # 简单判断：包含冒号的通常是网络调试设备
                    if ":" in device_name:
                        return device_name
            return None
        except Exception as e:
            logger.debug(f"检测已连接设备失败: {e}")
            return None

    async def _scan_common_ports(self) -> Optional[str]:
        """扫描常见的 ADB 端口 (Termux/Android默认)"""
        common_ports = ["5555", "37138", "42138", "47138", "39139", "40139"]
        for port in common_ports:
            try:
                device_id = f"127.0.0.1:{port}"
                proc = await asyncio.create_subprocess_exec(
                    "adb", "connect", device_id,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.5)
                if "connected" in stdout.decode().lower():
                    # 简单验证
                    return port
            except Exception:
                continue
        return None

    async def _connect_local_adb(self) -> bool:
        """
        验证连接有效性
        如果已有连接，直接测试；如果测试失败，尝试重连。
        """
        try:
            # 1. 直接尝试执行指令，不盲目断开
            result = await self._run_adb_cmd(["shell", "echo", "ok"], timeout=3)
            if result.get("success", False):
                return True
            
            # 2. 如果失败，尝试 强制断开 + 重连
            logger.info("⚠️ 连接测试未通过，尝试重连...")
            await asyncio.create_subprocess_exec("adb", "disconnect", self.device_id)
            await asyncio.sleep(0.5)
            
            proc = await asyncio.create_subprocess_exec(
                "adb", "connect", self.device_id,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            
            # 3. 再次测试
            result = await self._run_adb_cmd(["shell", "echo", "ok"], timeout=3)
            return result.get("success", False)
        except Exception:
            return False

    async def _update_screen_size(self):
        """异步更新屏幕尺寸（带缓存）"""
        if self._cached: return

        try:
            result = await self._run_adb_cmd(["shell", "wm", "size"], timeout=5)
            if result["success"] and result["stdout"]:
                match = re.search(r'Physical size: (\d+)x(\d+)', result["stdout"])
                if match:
                    self._screen_width = int(match.group(1))
                    self._screen_height = int(match.group(2))
                    logger.info(f"📱 屏幕尺寸: {self._screen_width}x{self._screen_height}")
                    self._cached = True
        except Exception as e:
            logger.warning(f"⚠️ 获取屏幕尺寸失败: {e}")

    async def _run_adb_cmd(self, args: list, timeout: int = 30) -> Dict[str, Any]:
        """异步执行 ADB 命令的核心方法"""
        # 防止递归调用初始化
        if not self.adb_available and args[0] != "connect" and args[0] != "disconnect":
            # 如果不是在尝试连接，且标记为不可用，则尝试一次初始化
            # 注意：在主循环中通常由 initialize 处理，这里是防守性编程
            pass 

        cmd = ["adb", "-s", self.device_id] + args
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            
            stdout_str = stdout.decode('utf-8', errors='ignore')
            stderr_str = stderr.decode('utf-8', errors='ignore')

            if proc.returncode != 0:
                if "offline" in stderr_str.lower() or "not found" in stderr_str.lower():
                    self.adb_available = False

            return {
                "success": proc.returncode == 0,
                "stdout": stdout_str,
                "stderr": stderr_str
            }
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception: pass
            return {"success": False, "stdout": "", "stderr": "Timeout"}
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": str(e)}

    # ==================== 功能实现区 ====================

    async def screenshot(self) -> Dict[str, Any]:
        """
        异步截图 - 优化版 (WebP + 二进制传输 + 动态分辨率同步)

        关键改进：
        1. 从原始截图获取物理分辨率，动态更新 _screen_width/_screen_height
        2. 确保坐标转换使用最新的物理分辨率
        3. 返回完整的元数据供服务端校验
        """
        try:
            # 执行 screencap 命令获取二进制流
            proc = await asyncio.create_subprocess_exec(
                "adb", "-s", self.device_id, "shell", "screencap", "-p",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=ADB_CMD_TIMEOUT)

            if proc.returncode != 0:
                return {"success": False, "error": f"screencap failed: {stderr.decode()}"}

            # --- PNG 数据提取 ---
            png_header = b'\x89\x50\x4E\x47\x0D\x0A\x1A\x0A'
            start_index = stdout.find(png_header)

            if start_index != -1:
                png_data = stdout[start_index:]
            else:
                png_data = stdout.replace(b'\r\n', b'\n')

            # 打开图片
            try:
                img = Image.open(BytesIO(png_data))
                img.verify()
                img = Image.open(BytesIO(png_data))
            except Exception as e:
                logger.warning(f"⚠️ 标准截图失败，尝试二进制修复: {e}")
                png_data_fixed = stdout.replace(b'\r\n', b'\n')
                img = Image.open(BytesIO(png_data_fixed))

            # 获取物理分辨率
            original_width, original_height = img.size

            # 🔥 关键修复：动态更新物理分辨率缓存，确保坐标转换准确
            if (self._screen_width != original_width or self._screen_height != original_height):
                old_width, old_height = self._screen_width, self._screen_height
                self._screen_width = original_width
                self._screen_height = original_height
                logger.info(
                    f"🔄 屏幕分辨率已更新: {old_width}x{old_height} -> {original_width}x{original_height}"
                )

            # --- 尺寸缩放：保持长宽比，短边缩放到768px ---
            short_edge = min(original_width, original_height)
            scale_factor = 1.0  # 默认不缩放

            if short_edge > SCREENSHOT_TARGET_SHORT_EDGE:
                # 计算缩放比例
                scale_factor = SCREENSHOT_TARGET_SHORT_EDGE / short_edge
                new_width = int(original_width * scale_factor)
                new_height = int(original_height * scale_factor)
                img = img.resize((new_width, new_height), Image.LANCZOS)
                logger.debug(f"🔄 缩放: {original_width}x{original_height} -> {new_width}x{new_height} (scale={scale_factor:.3f})")
            else:
                logger.debug(f"✓ 无需缩放 (短边 {short_edge}px <= {SCREENSHOT_TARGET_SHORT_EDGE}px)")

            # 转换为RGB模式（WebP需要）
            if img.mode != "RGB":
                img = img.convert("RGB")

            # --- 格式优化：WebP压缩 ---
            buffered = BytesIO()
            img.save(buffered, format="WebP", quality=SCREENSHOT_WEBP_QUALITY)
            webp_data = buffered.getvalue()

            # 构建返回结果（包含完整的坐标映射信息）
            result = {
                "success": True,
                "format": "webp",
                # 缩放后的图片尺寸
                "width": img.width,
                "height": img.height,
                # 物理屏幕分辨率（用于坐标转换）
                "screen_width": original_width,
                "screen_height": original_height,
                # 原始截图分辨率
                "original_width": original_width,
                "original_height": original_height,
                # 缩放比例（供服务端参考）
                "scale_factor": scale_factor,
                "data_size": len(webp_data),
                "error": ""
            }

            # 如果启用二进制传输，返回二进制数据
            if USE_BINARY_TRANSMISSION:
                result["binary_data"] = webp_data
                logger.info(
                    f"📸 截图完成 [WebP二进制]: 物理={original_width}x{original_height}, "
                    f"缩放={img.width}x{img.height}, 大小={len(webp_data)/1024:.1f}KB"
                )
                return result
            else:
                # 降级到Base64（兼容旧版本）
                base64_data = base64.b64encode(webp_data).decode("utf-8")
                result["base64_data"] = base64_data
                logger.info(
                    f"📸 截图完成 [WebP+Base64]: 物理={original_width}x{original_height}, "
                    f"缩放={img.width}x{img.height}, 大小={len(webp_data)/1024:.1f}KB"
                )
                return result

        except Exception as e:
            logger.error(f"截图异常: {e}")
            if 'stdout' in locals():
                logger.error(f"数据头预览(Hex): {stdout[:20].hex()}")
            return {"success": False, "error": str(e)}

    def _convert_relative_to_absolute(self, element: list) -> Tuple[int, int]:
        """
        相对坐标 (0-1000) 转绝对坐标（物理屏幕像素）

        Args:
            element: [相对x, 相对y]，范围 0-1000

        Returns:
            (绝对x, 绝对y)：基于物理屏幕分辨率的像素坐标

        坐标系说明：
        - 输入：归一化坐标 (0-1000)，由 VLM 模型基于缩放后的图片生成
        - 输出：物理像素坐标，映射到真实的屏幕分辨率
        - 映射公式：absolute = relative / 1000 * screen_size
        """
        # 输入验证
        if not isinstance(element, (list, tuple)) or len(element) < 2:
            logger.warning(f"⚠️ 无效的坐标格式: {element}")
            return (0, 0)

        rel_x, rel_y = element[0], element[1]

        # 边界检查：限制在 0-1000 范围内
        rel_x = max(0, min(1000, rel_x))
        rel_y = max(0, min(1000, rel_y))

        # 坐标转换：归一化 -> 物理像素
        abs_x = int(rel_x / 1000 * self._screen_width)
        abs_y = int(rel_y / 1000 * self._screen_height)

        # 边界修正：确保坐标在屏幕范围内
        abs_x = max(0, min(self._screen_width - 1, abs_x))
        abs_y = max(0, min(self._screen_height - 1, abs_y))

        # 详细日志（便于调试）
        logger.debug(
            f"📍 坐标转换: ({rel_x:.1f}, {rel_y:.1f}) [0-1000] -> "
            f"({abs_x}, {abs_y}) [物理像素], "
            f"屏幕: {self._screen_width}x{self._screen_height}"
        )

        return abs_x, abs_y

    async def tap(self, x: int, y: int) -> Dict[str, Any]:
        delay = TIMING_CONFIG.get_tap_delay()
        result = await self._run_adb_cmd(["shell", "input", "tap", str(x), str(y)])
        if result["success"]:
            await asyncio.sleep(delay)
            TIMING_CONFIG.record_success()
        else:
            TIMING_CONFIG.record_failure()
        return {"success": result["success"], "error": result["stderr"]}

    async def double_tap(self, x: int, y: int) -> Dict[str, Any]:
        delay = TIMING_CONFIG.get_tap_delay()
        # 第一次点击
        res1 = await self._run_adb_cmd(["shell", "input", "tap", str(x), str(y)])
        await asyncio.sleep(TIMING_CONFIG.double_tap_interval)
        # 第二次点击
        res2 = await self._run_adb_cmd(["shell", "input", "tap", str(x), str(y)])
        
        success = res1["success"] and res2["success"]
        if success:
            await asyncio.sleep(delay)
            TIMING_CONFIG.record_success()
        else:
            TIMING_CONFIG.record_failure()
        return {"success": success, "error": res1["stderr"] if not res1["success"] else res2["stderr"]}

    async def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: Optional[int] = None) -> Dict[str, Any]:
        delay = TIMING_CONFIG.get_tap_delay()
        if duration_ms is None:
            dist_sq = (start_x - end_x) ** 2 + (start_y - end_y) ** 2
            duration_ms = max(1000, min(int(dist_sq / 1000), 2000))

        result = await self._run_adb_cmd([
            "shell", "input", "swipe",
            str(start_x), str(start_y), str(end_x), str(end_y), str(duration_ms)
        ])
        if result["success"]:
            await asyncio.sleep(delay)
            TIMING_CONFIG.record_success()
        else:
            TIMING_CONFIG.record_failure()
        return {"success": result["success"], "error": result["stderr"]}

    async def back(self) -> Dict[str, Any]:
        delay = TIMING_CONFIG.get_tap_delay()
        result = await self._run_adb_cmd(["shell", "input", "keyevent", "4"])
        if result["success"]:
            await asyncio.sleep(delay)
            TIMING_CONFIG.record_success()
        else:
            TIMING_CONFIG.record_failure()
        return {"success": result["success"], "error": result["stderr"]}

    async def home(self) -> Dict[str, Any]:
        delay = TIMING_CONFIG.get_tap_delay()
        result = await self._run_adb_cmd(["shell", "input", "keyevent", "KEYCODE_HOME"])
        if result["success"]:
            await asyncio.sleep(delay)
            TIMING_CONFIG.record_success()
        else:
            TIMING_CONFIG.record_failure()
        return {"success": result["success"], "error": result["stderr"]}

    async def launch_app(self, package: str) -> Dict[str, Any]:
        delay = TIMING_CONFIG.base_launch_delay
        result = await self._run_adb_cmd([
            "shell", "monkey", "-p", package,
            "-c", "android.intent.category.LAUNCHER", "1"
        ])
        if result["success"]:
            await asyncio.sleep(delay)
            TIMING_CONFIG.record_success()
        else:
            TIMING_CONFIG.record_failure()
        return {"success": result["success"], "error": result["stderr"]}

    async def get_current_app(self) -> Dict[str, Any]:
        result = await self._run_adb_cmd(["shell", "dumpsys", "window"])
        if result["success"] and result["stdout"]:
            for line in result["stdout"].split("\n"):
                if "mCurrentFocus" in line or "mFocusedApp" in line:
                    for app_name, package in APP_PACKAGES.items():
                        if package in line:
                            return {"success": True, "package": package, "app_name": app_name}
            # 备选解析
            match = re.search(r'mCurrentFocus.*\{.*\s+(\S+)/', result["stdout"])
            if match:
                return {"success": True, "package": match.group(1), "app_name": "Unknown"}
        return {"success": False, "package": "Unknown", "app_name": "Unknown"}

    async def ensure_adb_keyboard(self) -> bool:
        """
        粘性策略：确保 ADB Keyboard 已激活
        如果已经是激活状态，则跳过切换，节省时间
        """
        if self._adb_keyboard_active: return True
        
        result = await self._run_adb_cmd(["shell", "settings", "get", "secure", "default_input_method"], timeout=5)
        if result["success"]:
            current_ime = result["stdout"].strip()
            self._current_ime = current_ime
            if self.ADB_KEYBOARD_IME not in current_ime:
                logger.info(f"🔄 切换到 ADB Keyboard...")
                switch = await self._run_adb_cmd(["shell", "ime", "set", self.ADB_KEYBOARD_IME], timeout=5)
                if switch["success"]:
                    self._adb_keyboard_active = True
                    await asyncio.sleep(TIMING_CONFIG.base_keyboard_switch_delay)
                    return True
            else:
                self._adb_keyboard_active = True
                return True
        return False

    async def type_text(self, text: str) -> Dict[str, Any]:
        """使用 ADB Keyboard 输入文本（支持中文）"""
        if not await self.ensure_adb_keyboard():
            return {"success": False, "error": "Failed to activate ADB Keyboard"}

        encoded_text = base64.b64encode(text.encode("utf-8")).decode("utf-8")
        result = await self._run_adb_cmd([
            "shell", "am", "broadcast", "-a", self.ADB_INPUT_B64_ACTION, "--es", "msg", encoded_text
        ], timeout=10)
        
        if result["success"]:
            logger.info(f"⌨️  输入文本: {text[:20]}...")
            await asyncio.sleep(TIMING_CONFIG.base_text_input_delay)
            TIMING_CONFIG.record_success()
            return {"success": True, "error": ""}
        else:
            TIMING_CONFIG.record_failure()
            return {"success": False, "error": result["stderr"]}

    async def clear_text(self) -> Dict[str, Any]:
        """使用 ADB Keyboard 清空文本框"""
        if not await self.ensure_adb_keyboard():
            return {"success": False, "error": "Failed to activate ADB Keyboard"}
            
        result = await self._run_adb_cmd(["shell", "am", "broadcast", "-a", self.ADB_CLEAR_TEXT_ACTION], timeout=10)
        if result["success"]:
            logger.info("🗑️  清除文本")
            await asyncio.sleep(TIMING_CONFIG.base_text_input_delay)
            return {"success": True, "error": ""}
        return {"success": False, "error": result["stderr"]}

    async def restore_keyboard(self) -> None:
        """恢复原来的输入法"""
        if self._current_ime and self._adb_keyboard_active:
            if self.ADB_KEYBOARD_IME not in self._current_ime:
                logger.info(f"🔄 恢复输入法: {self._current_ime}")
                await self._run_adb_cmd(["shell", "ime", "set", self._current_ime])
                self._adb_keyboard_active = False


# ==================== WebSocket 客户端 ====================
class AsyncMobileAgent:
    def __init__(self, server_url: str, device_id: str):
        self.server_url = server_url
        self.device_id = device_id
        self.adb: AsyncADBManager = None
        self.websocket = None

    async def initialize(self):
        """显式初始化：创建 ADB 管理器"""
        self.adb = AsyncADBManager()
        await self.adb._initialize_adb()

    async def connect(self) -> bool:
        try:
            self.websocket = await websockets.connect(self.server_url)
            await self.websocket.send(json.dumps({
                "msg_type": "register",
                "msg_id": str(uuid.uuid4()),
                "device_id": self.device_id,
                "device_info": {
                    "type": "termux_async_nou2",
                    "adb_status": self.adb.adb_available if self.adb else False,
                    "screen_width": self.adb._screen_width if self.adb else 1080,
                    "screen_height": self.adb._screen_height if self.adb else 2400,
                }
            }))
            logger.info(f"🚀 设备已注册: {self.device_id}")
            return True
        except Exception as e:
            logger.error(f"❌ 连接服务器失败: {e}")
            return False

    async def handle_command(self, data: Dict[str, Any]):
        # Record receive time
        client_recv_time = time.time()

        msg_id = data.get("msg_id")
        cmd = data.get("command")
        params = data.get("params", {})
        server_send_time = data.get("server_send_time")

        res = {"success": False}

        # 检查 adb 是否已初始化
        if self.adb is None:
             res = {"success": False, "error": "ADB Manager not initialized"}
        else:
            try:
                # Record execution start time
                client_execute_start_time = time.time()

                if cmd == "screenshot":
                    res = await self.adb.screenshot()
                elif cmd == "tap":
                    x, y = params.get("x", 0), params.get("y", 0)
                    if params.get("relative", False):
                        x, y = self.adb._convert_relative_to_absolute([x, y])
                    res = await self.adb.tap(x, y)
                elif cmd == "double_tap":
                    x, y = params.get("x", 0), params.get("y", 0)
                    if params.get("relative", False):
                        x, y = self.adb._convert_relative_to_absolute([x, y])
                    res = await self.adb.double_tap(x, y)
                elif cmd == "swipe":
                    x1, y1 = params.get("start_x", 0), params.get("start_y", 0)
                    x2, y2 = params.get("end_x", 0), params.get("end_y", 0)
                    if params.get("relative", False):
                        x1, y1 = self.adb._convert_relative_to_absolute([x1, y1])
                        x2, y2 = self.adb._convert_relative_to_absolute([x2, y2])
                    res = await self.adb.swipe(x1, y1, x2, y2, params.get("duration_ms"))
                elif cmd == "back":
                    res = await self.adb.back()
                elif cmd == "home":
                    res = await self.adb.home()
                elif cmd == "type_text":
                    res = await self.adb.type_text(params.get("text", ""))
                elif cmd == "clear_text":
                    res = await self.adb.clear_text()
                elif cmd == "get_current_app":
                    res = await self.adb.get_current_app()
                elif cmd == "launch_app":
                    res = await self.adb.launch_app(params.get("package") or params.get("app"))
                elif cmd == "restore_keyboard":
                    await self.adb.restore_keyboard()
                    res = {"success": True}
                else:
                    res = {"success": False, "error": f"Unknown command: {cmd}"}

                # Record execution end time
                client_execute_end_time = time.time()

            except Exception as e:
                logger.error(f"执行异常: {e}")
                res = {"success": False, "error": str(e)}
                client_execute_end_time = time.time()

        # Record send time and send result
        client_send_time = time.time()

        if self.websocket:
            # 检查是否包含二进制数据（截图）
            binary_data = res.pop("binary_data", None)

            # 构建结果元数据
            result_metadata = {
                "msg_type": "result",
                "msg_id": msg_id,
                "success": res.get("success", False),
                "result": res,
                "server_send_time": server_send_time,
                "client_recv_time": client_recv_time,
                "client_execute_start_time": client_execute_start_time if 'client_execute_start_time' in locals() else None,
                "client_execute_end_time": client_execute_end_time if 'client_execute_end_time' in locals() else None,
                "client_send_time": client_send_time,
                "has_binary": binary_data is not None,
            }

            # 如果有二进制数据，使用 Header+Body 单包传输
            if binary_data is not None:
                # 1. 将元数据转换为 JSON 字节串
                json_bytes = json.dumps(result_metadata).encode('utf-8')

                # 2. 构建 4 字节长度头（大端序）
                header = len(json_bytes).to_bytes(4, byteorder='big')

                # 3. 构建单一二进制流：[4字节长度] + [JSON字节] + [图片二进制数据]
                combined_data = header + json_bytes + binary_data

                logger.debug(f"📤 发送单包: JSON={len(json_bytes)}B, Image={len(binary_data)/1024:.1f}KB, Total={len(combined_data)/1024:.1f}KB for {msg_id}")
                # 一次性发送完整的 Buffer
                await self.websocket.send(combined_data)
            else:
                # 普通JSON响应（无二进制数据）
                await self.websocket.send(json.dumps(result_metadata))

            # Log client-side performance
            if server_send_time and 'client_execute_end_time' in locals():
                network_latency = (client_recv_time - server_send_time) * 1000
                execution_time = (client_execute_end_time - client_execute_start_time) * 1000
                logger.info(
                    f"⏱️  [{cmd}] network: {network_latency:.1f}ms, execution: {execution_time:.1f}ms"
                )

    async def run(self):
        while True:
            if not self.websocket or self.websocket.closed:
                if not await self.connect():
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue
            try:
                async for message in self.websocket:
                    data = json.loads(message)
                    if data.get("msg_type") == "command":
                        asyncio.create_task(self.handle_command(data))
            except Exception as e:
                logger.error(f"📡 连接断开: {e}")
                self.websocket = None
                await asyncio.sleep(2)

# ==================== 主入口（关键修正） ====================
async def main():
    agent = AsyncMobileAgent(SERVER_URL, DEVICE_ID)
    # 关键修正：必须显式调用 initialize 来创建 self.adb
    await agent.initialize() 
    await agent.run()

if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging, LOG_LEVEL.upper()), format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    global logger
    logger = logging.getLogger(__name__)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 停止运行")