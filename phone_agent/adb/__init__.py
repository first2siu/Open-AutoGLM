"""ADB utilities for Android device interaction."""

import os

# Check if WebSocket mode is enabled
WEBSOCKET_MODE = os.getenv("AUTOGLEM_WEBSOCKET_MODE", "false").lower() == "true"

if WEBSOCKET_MODE:
    # Use WebSocket-based command execution
    from phone_agent.adb.websocket_device import (
        DeviceInfo,
        back,
        clear_text,
        detect_and_set_adb_keyboard,
        double_tap,
        get_current_app,
        get_screenshot,
        home,
        launch_app,
        list_devices,
        long_press,
        restore_keyboard,
        swipe,
        tap,
        type_text,
    )
else:
    # Use direct shell ADB commands (original mode)
    from phone_agent.adb.connection import (
        ADBConnection,
        ConnectionType,
        DeviceInfo,
        list_devices,
        quick_connect,
    )
    from phone_agent.adb.device import (
        back,
        double_tap,
        get_current_app,
        home,
        launch_app,
        long_press,
        swipe,
        tap,
    )
    from phone_agent.adb.input import (
        clear_text,
        detect_and_set_adb_keyboard,
        restore_keyboard,
        type_text,
    )
    from phone_agent.adb.screenshot import get_screenshot

__all__ = [
    # Screenshot
    "get_screenshot",
    # Input
    "type_text",
    "clear_text",
    "detect_and_set_adb_keyboard",
    "restore_keyboard",
    # Device control
    "get_current_app",
    "tap",
    "swipe",
    "back",
    "home",
    "double_tap",
    "long_press",
    "launch_app",
    # Device listing
    "list_devices",
    "DeviceInfo",
]

# Only export connection classes in non-WebSocket mode
if not WEBSOCKET_MODE:
    __all__.extend([
        # Connection management
        "ADBConnection",
        "ConnectionType",
        "quick_connect",
    ])

# Export mode flag
__all__.append("WEBSOCKET_MODE")
