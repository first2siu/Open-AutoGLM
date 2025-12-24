"""WebSocket-based ADB device implementation for remote command execution."""

import base64
import logging
from dataclasses import dataclass
from typing import Optional, List

from phone_agent.websocket_protocol import CommandType, create_command, create_result
from phone_agent.websocket_server import get_server

logger = logging.getLogger(__name__)


@dataclass
class Screenshot:
    """Represents a captured screenshot."""

    base64_data: str
    width: int
    height: int
    is_sensitive: bool = False


def _execute_ws_command(device_id: str, command: CommandType, params: dict, timeout: float = 30.0) -> any:
    """
    Execute a command via WebSocket and return the result.

    Args:
        device_id: Target device ID
        command: Command type to execute
        params: Command parameters
        timeout: Timeout in seconds

    Returns:
        Command result data

    Raises:
        ConnectionError: If device is not connected or command fails
        TimeoutError: If command execution times out
    """
    server = get_server()

    if not server.is_device_connected(device_id):
        raise ConnectionError(f"Device {device_id} is not connected via WebSocket")

    cmd_msg = create_command(command, params)

    try:
        import asyncio

        # Get or create event loop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Execute command and wait for result
        result_msg = loop.run_until_complete(
            server.execute_command(device_id, cmd_msg, timeout=timeout)
        )

        if not result_msg.success:
            error = result_msg.error or "Unknown error"
            logger.error(f"Command {command.value} failed: {error}")
            raise ConnectionError(f"Command failed: {error}")

        # Validate result data
        if result_msg.result is None:
            error_msg = f"Mobile device returned None for command {command.value}"
            logger.error(error_msg)
            raise ConnectionError(error_msg)

        return result_msg.result

    except Exception as e:
        logger.error(f"Error executing WebSocket command {command.value}: {e}")
        raise


def get_current_app(device_id: str | None = None) -> str:
    """
    Get the currently focused app name via WebSocket.

    Args:
        device_id: Device ID (uses 'default' if None)

    Returns:
        The app name if recognized, otherwise "System Home".
    """
    if device_id is None:
        device_id = "default"

    try:
        result = _execute_ws_command(device_id, CommandType.GET_CURRENT_APP, {})

        # Validate result
        if not isinstance(result, dict):
            logger.error(f"get_current_app result is not a dict: {type(result)}")
            return "System Home"

        # 优先使用 app_name
        app_name = result.get("app_name")
        package = result.get("package")

        # 如果 app_name 存在且不是 Unknown，直接返回
        if app_name and app_name != "Unknown":
            logger.debug(f"Current app: {app_name}")
            return app_name

        # 如果 app_name 是 Unknown 或不存在，但 package 存在
        # 尝试从本地 APP_PACKAGES 映射包名到应用名
        if package and package != "Unknown":
            from phone_agent.config.apps import APP_PACKAGES
            for name, pkg in APP_PACKAGES.items():
                if pkg == package:
                    logger.info(f"Mapped package {package} to app name {name}")
                    return name

            # 即使没有映射到已知应用，如果包名有效，记录日志
            logger.debug(f"Unknown package {package}, returning System Home")

        logger.warning(f"Got unknown app, returning System Home")
        return "System Home"

    except Exception as e:
        logger.error(f"Failed to get current app: {e}")
        return "System Home"


def tap(x: int, y: int, device_id: str | None = None, delay: float | None = None) -> None:
    """
    Tap at the specified coordinates via WebSocket.

    Args:
        x: X coordinate.
        y: Y coordinate.
        device_id: Device ID (uses 'default' if None).
        delay: Delay in seconds after tap (server-side).
    """
    if device_id is None:
        device_id = "default"

    params = {"x": x, "y": y}
    if delay is not None:
        params["delay"] = delay

    _execute_ws_command(device_id, CommandType.TAP, params)


def double_tap(x: int, y: int, device_id: str | None = None, delay: float | None = None) -> None:
    """
    Double tap at the specified coordinates via WebSocket.

    Args:
        x: X coordinate.
        y: Y coordinate.
        device_id: Device ID (uses 'default' if None).
        delay: Delay in seconds after double tap (server-side).
    """
    if device_id is None:
        device_id = "default"

    params = {"x": x, "y": y}
    if delay is not None:
        params["delay"] = delay

    _execute_ws_command(device_id, CommandType.DOUBLE_TAP, params)


def long_press(
    x: int,
    y: int,
    duration_ms: int = 3000,
    device_id: str | None = None,
    delay: float | None = None,
) -> None:
    """
    Long press at the specified coordinates via WebSocket.

    Args:
        x: X coordinate.
        y: Y coordinate.
        duration_ms: Duration of press in milliseconds.
        device_id: Device ID (uses 'default' if None).
        delay: Delay in seconds after long press (server-side).
    """
    if device_id is None:
        device_id = "default"

    params = {"x": x, "y": y, "duration_ms": duration_ms}
    if delay is not None:
        params["delay"] = delay

    _execute_ws_command(device_id, CommandType.LONG_PRESS, params)


def swipe(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration_ms: int | None = None,
    device_id: str | None = None,
    delay: float | None = None,
) -> None:
    """
    Swipe from start to end coordinates via WebSocket.

    Args:
        start_x: Starting X coordinate.
        start_y: Starting Y coordinate.
        end_x: Ending X coordinate.
        end_y: Ending Y coordinate.
        duration_ms: Duration of swipe in milliseconds.
        device_id: Device ID (uses 'default' if None).
        delay: Delay in seconds after swipe (server-side).
    """
    if device_id is None:
        device_id = "default"

    params = {
        "start_x": start_x,
        "start_y": start_y,
        "end_x": end_x,
        "end_y": end_y,
    }
    if duration_ms is not None:
        params["duration_ms"] = duration_ms
    if delay is not None:
        params["delay"] = delay

    _execute_ws_command(device_id, CommandType.SWIPE, params)


def back(device_id: str | None = None, delay: float | None = None) -> None:
    """
    Press the back button via WebSocket.

    Args:
        device_id: Device ID (uses 'default' if None).
        delay: Delay in seconds after pressing back (server-side).
    """
    if device_id is None:
        device_id = "default"

    params = {}
    if delay is not None:
        params["delay"] = delay

    _execute_ws_command(device_id, CommandType.BACK, params)


def home(device_id: str | None = None, delay: float | None = None) -> None:
    """
    Press the home button via WebSocket.

    Args:
        device_id: Device ID (uses 'default' if None).
        delay: Delay in seconds after pressing home (server-side).
    """
    if device_id is None:
        device_id = "default"

    params = {}
    if delay is not None:
        params["delay"] = delay

    _execute_ws_command(device_id, CommandType.HOME, params)


def launch_app(app_name: str, device_id: str | None = None, delay: float | None = None) -> bool:
    """
    Launch an app by name via WebSocket.

    Args:
        app_name: The app name (must be in APP_PACKAGES).
        device_id: Device ID (uses 'default' if None).
        delay: Delay in seconds after launching (server-side).

    Returns:
        True if app was launched, False if app not found.
    """
    if device_id is None:
        device_id = "default"

    from phone_agent.config.apps import APP_PACKAGES

    if app_name not in APP_PACKAGES:
        return False

    params = {"app_name": app_name, "package": APP_PACKAGES[app_name]}
    if delay is not None:
        params["delay"] = delay

    try:
        _execute_ws_command(device_id, CommandType.LAUNCH_APP, params)
        return True
    except Exception as e:
        logger.error(f"Failed to launch app {app_name}: {e}")
        return False


def get_screenshot(device_id: str | None = None, timeout: int = 10) -> Screenshot:
    """
    Capture a screenshot from the connected device via WebSocket.

    Args:
        device_id: Device ID (uses 'default' if None).
        timeout: Timeout in seconds for screenshot operations.

    Returns:
        Screenshot object containing base64 data and dimensions.
    """
    if device_id is None:
        device_id = "default"

    try:
        result = _execute_ws_command(
            device_id, CommandType.SCREENSHOT, {}, timeout=timeout
        )

        # Validate result structure
        if not isinstance(result, dict):
            logger.error(f"Screenshot result is not a dict: {type(result)}")
            raise ValueError(f"Invalid screenshot result type: {type(result)}")

        # Extract required fields
        width = result.get("width", 1080)
        height = result.get("height", 2400)
        is_sensitive = result.get("is_sensitive", False)
        data_size = result.get("data_size", 0)
        img_format = result.get("format", "jpeg")

        # Handle binary data or base64 data
        binary_data = result.get("binary_data")
        base64_data = result.get("base64_data")

        if binary_data:
            # Convert binary data to base64 for compatibility
            base64_data = base64.b64encode(binary_data).decode("utf-8")
            logger.info(
                f"📸 Screenshot [WebP二进制]: {width}x{height}, "
                f"原始: {result.get('original_width', '?')}x{result.get('original_height', '?')}, "
                f"大小: {data_size/1024:.1f}KB"
            )
        elif base64_data:
            logger.info(
                f"📸 Screenshot [{img_format}+Base64]: {width}x{height}, "
                f"大小: {data_size/1024:.1f}KB"
            )
        else:
            logger.error("Screenshot data is empty (no binary_data or base64_data)")
            raise ValueError("Screenshot data is empty")

        return Screenshot(
            base64_data=base64_data,
            width=width,
            height=height,
            is_sensitive=is_sensitive,
        )

    except Exception as e:
        logger.error(f"Failed to get screenshot: {e}")
        # Return fallback screenshot
        from io import BytesIO
        from PIL import Image

        default_width, default_height = 1080, 2400
        black_img = Image.new("RGB", (default_width, default_height), color="black")
        buffered = BytesIO()
        black_img.save(buffered, format="PNG")
        base64_data = base64.b64encode(buffered.getvalue()).decode("utf-8")

        logger.warning(f"Returning fallback black screenshot: {default_width}x{default_height}")

        return Screenshot(
            base64_data=base64_data,
            width=default_width,
            height=default_height,
            is_sensitive=False,
        )


def type_text(text: str, device_id: str | None = None) -> None:
    """
    Type text into the currently focused input field via WebSocket.

    Args:
        text: The text to type.
        device_id: Device ID (uses 'default' if None).
    """
    if device_id is None:
        device_id = "default"

    params = {"text": text}
    _execute_ws_command(device_id, CommandType.TYPE_TEXT, params)


def clear_text(device_id: str | None = None) -> None:
    """
    Clear text in the currently focused input field via WebSocket.

    Args:
        device_id: Device ID (uses 'default' if None).
    """
    if device_id is None:
        device_id = "default"

    _execute_ws_command(device_id, CommandType.CLEAR_TEXT, {})


def detect_and_set_adb_keyboard(device_id: str | None = None) -> str:
    """
    Note: This is handled automatically by the mobile agent.
    Kept for API compatibility.

    Args:
        device_id: Device ID (uses 'default' if None).

    Returns:
        Placeholder IME string.
    """
    if device_id is None:
        device_id = "default"

    # The mobile agent handles keyboard switching automatically
    return "com.android.adbkeyboard/.AdbIME"


def restore_keyboard(ime: str, device_id: str | None = None) -> None:
    """
    Note: This is handled automatically by the mobile agent.
    Kept for API compatibility.

    Args:
        ime: The IME identifier to restore (ignored).
        device_id: Device ID (uses 'default' if None).
    """
    # The mobile agent handles keyboard restoration automatically
    pass


@dataclass
class DeviceInfo:
    """Simple device info for WebSocket connected devices."""

    device_id: str
    connection_type: str = "websocket"
    model: Optional[str] = None
    status: str = "device"


def list_devices() -> List[DeviceInfo]:
    """
    List connected WebSocket devices.

    Returns:
        List of DeviceInfo objects for connected devices.
    """
    server = get_server()
    connected_ids = server.list_connected_devices()

    return [
        DeviceInfo(
            device_id=device_id,
            connection_type="websocket",
            status="device"
        )
        for device_id in connected_ids
    ]
