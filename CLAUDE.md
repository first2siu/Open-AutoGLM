# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Open-AutoGLM is an AI-powered phone automation framework that controls Android, HarmonyOS, and iOS devices through ADB, HDC, WebDriverAgent, or WebSocket connections. The system uses a vision-language model (VLM) to understand screen content and generate device actions. Users describe tasks in natural language (e.g., "open WeChat and send a message") and the agent automatically completes them by:

1. Capturing screenshots from the device
2. Sending screenshots + task context to the VLM
3. Parsing the model's response into device actions
4. Executing actions via ADB/HDC/iOS WebDriverAgent/WebSocket
5. Repeating until the task is complete

**Two Operating Modes:**
- **Standard Mode**: Direct ADB/HDC connection via USB or network (requires local ADB/HDC installation)
- **WebSocket Mode** (NEW): Remote control via WebSocket connection. A mobile agent (`mobile_agent.py`) runs on the Android device and connects to the AutoGLM server, enabling remote control without local ADB setup.

## Key Commands

### Installation and Setup
```bash
# Install dependencies
pip install -r requirements.txt
pip install -e .

# Install with development dependencies
pip install -e ".[dev]"

# Install WebSocket mode dependencies
pip install -r requirements-websocket.txt
```

### Running the Agent (Standard Mode)
```bash
# Android device (ADB) - interactive mode
python main.py --base-url http://localhost:8000/v1 --model "autoglm-phone-9b"

# Android device - single task
python main.py --base-url http://localhost:8000/v1 "打开美团搜索附近的火锅店"

# HarmonyOS device (HDC)
python main.py --device-type hdc --base-url http://localhost:8000/v1 "你的任务描述"

# iOS device
python main.py --device-type ios --wda-url http://localhost:8100 "Open Safari and search for tips"

# List supported apps
python main.py --list-apps

# List connected devices
python main.py --list-devices
```

### Running in WebSocket Mode

WebSocket mode allows controlling Android devices remotely without local ADB. The device runs a mobile agent that connects to the AutoGLM server via WebSocket.

```bash
# Option 1: Using the convenience script (recommended)
./run_websocket_mode.sh [base_url] [model] [task]

# Option 2: Manual setup
export AUTOGLEM_WEBSOCKET_MODE=true
export AUTOGLEM_WS_HOST="0.0.0.0"  # WebSocket server host
export AUTOGLEM_WS_PORT="8765"      # WebSocket server port
python main.py --base-url http://localhost:8000/v1 --model "autoglm-phone-9b"

# On the Android device (e.g., in Termux):
# - Set environment variables:
#   export AUTOGLM_SERVER_URL="ws://<your-server-ip>:8765"
#   export ADB_PORT="<your-adb-port>"  # Required for mobile_agent_async.py
# - Run the mobile agent:
python mobile_agent_async.py  # Recommended: lightweight, no uiautomator2
# or
python mobile_agent.py        # Full-featured, requires uiautomator2
```

**WebSocket Mode Architecture:**
1. Server starts WebSocket listener on specified host/port
2. Mobile agent on Android device connects and registers with device ID
3. Server waits for device connection (auto-detects first connected device)
4. Commands are sent via WebSocket protocol defined in `phone_agent/websocket_protocol.py`
5. Mobile agent executes ADB commands locally and returns results
6. Supports all standard operations: tap, swipe, screenshot, text input, app launch, etc.

**Benefits:**
- No local ADB installation required
- Works over WiFi/network
- Device can be controlled from anywhere
- Lower latency for remote devices

### Testing and Development
```bash
# Run tests (when tests are available)
pytest tests/

# Format code with black
black phone_agent/ main.py

# Type checking with mypy
mypy phone_agent/
```

### Device Connection
```bash
# Android - connect remote device via WiFi
adb connect 192.168.1.100:5555

# HarmonyOS - connect remote device
hdc tconn 192.168.1.100:5555

# Enable TCP/IP on USB device (Android)
python main.py --enable-tcpip 5555
```

## Architecture

### Core Components

**Entry Point**: `main.py`
- CLI interface with argument parsing
- System requirements checking (ADB/HDC/iOS tools, device connection, keyboard)
- Device management commands (connect, disconnect, list devices)
- Model API validation
- **WebSocket Mode Support**: Detects `AUTOGLEM_WEBSOCKET_MODE` environment variable and starts WebSocket server automatically

**Agent Core**: `phone_agent/agent.py` (Android/HarmonyOS) and `phone_agent/agent_ios.py` (iOS)
- `PhoneAgent` / `IOSPhoneAgent`: Main orchestration class
- Runs the perception-action loop:
  1. Captures screenshot via device factory
  2. Builds messages with screen info + image
  3. Requests model inference
  4. Parses response into action
  5. Executes action via ActionHandler
- Manages conversation context

**Model Client**: `phone_agent/model/client.py`
- `ModelClient`: OpenAI-compatible API client
- Handles streaming responses with performance metrics (TTFT, thinking time, total time)
- `MessageBuilder`: Constructs messages with text + base64 images
- Parses model responses into thinking + action components

**Action Handler**: `phone_agent/actions/handler.py` (Android/HarmonyOS) and `phone_agent/actions/handler_ios.py` (iOS)
- `ActionHandler`: Executes parsed actions on device
- Action types: Launch, Tap, Type, Swipe, Back, Home, Double Tap, Long Press, Wait, Take_over, Note, Call_API, Interact
- Converts relative coordinates (0-1000) to absolute pixels
- Handles sensitive operation confirmations and takeover requests

**Device Factory**: `phone_agent/device_factory.py`
- Abstraction layer for ADB, HDC, iOS, and WebSocket device operations
- `DeviceFactory` provides unified interface:
  - `get_screenshot()`: Capture screen
  - `get_current_app()`: Detect foreground app
  - `tap()`, `swipe()`, `back()`, `home()`: Device control
  - `type_text()`, `clear_text()`: Text input
  - `launch_app()`: Start application
  - `list_devices()`: List connected devices
- Device type selection via `DeviceType` enum (ADB, HDC, IOS)

**Device Implementations**:

*Standard ADB/HDC/iOS Mode:*
- `phone_agent/adb/`: Android device control via ADB
  - `connection.py`: ADB connection management (remote/local)
  - `screenshot.py`: Screen capture
  - `device.py`: Tap, swipe, back, home, launch_app
  - `input.py`: Text input via ADB Keyboard
- `phone_agent/hdc/`: HarmonyOS device control via HDC
  - Same structure as ADB module but uses HDC commands
  - Uses native input method (no ADB Keyboard needed)
- `phone_agent/xctest/`: iOS device control via WebDriverAgent
  - `connection.py`: XCTest/WebDriverAgent connection
  - `screenshot.py`: Screenshot via WDA
  - `device.py`: Tap, swipe, etc. via WDA endpoints
  - `input.py`: Text input via iOS

*WebSocket Mode:*
- `phone_agent/websocket_server.py`: WebSocket server that manages connected mobile devices
  - Accepts connections from mobile agents
  - Maintains command queues for each device
  - Sends commands and waits for results with timeout handling
  - Supports concurrent connections from multiple devices
- `phone_agent/websocket_protocol.py`: Protocol definition for WebSocket communication
  - Message types: REGISTER, COMMAND, ACK, RESULT, ERROR, PING/PONG
  - Command types: tap, double_tap, long_press, swipe, back, home, type_text, clear_text, launch_app, get_current_app, screenshot
  - Message serialization/deserialization with validation
- `phone_agent/adb/websocket_device.py`: WebSocket-based device implementation
  - Implements same API as standard ADB module but forwards commands via WebSocket
  - Used automatically when `WEBSOCKET_MODE` is enabled (see `phone_agent/adb/__init__.py`)

**Mobile Agents** (run on Android device):
- `mobile_agent_async.py`: Lightweight async mobile agent (recommended)
  - No uiautomator2 dependency, lower resource usage
  - Requires `ADB_PORT` environment variable
  - Fully async ADB command execution
  - Optimized screenshot with compression
- `mobile_agent.py`: Full-featured mobile agent
  - Requires uiautomator2 for device control
  - More feature-rich but higher resource usage
  - Fallback option if mobile_agent_async.py doesn't work

**Configuration**: `phone_agent/config/`
- `prompts_zh.py` / `prompts_en.py`: System prompts defining agent behavior and action formats
- `apps.py` / `apps_harmonyos.py` / `apps_ios.py`: App name to package name/Bundle ID mappings
- `i18n.py`: Internationalization messages
- `timing.py`: Timing configuration for delays between actions

### Data Flow

1. User provides natural language task
2. Agent captures initial screenshot
3. System prompt + task + screenshot sent to VLM
4. VLM returns thinking + action (e.g., `do(action="Tap", element=[500, 500])`)
5. ActionHandler parses and executes action via ADB/HDC/WDA
6. New screenshot captured, cycle repeats until `finish()` action received

### Action Format

The model outputs actions in this format:
```
{thinking explanation}
do(action="ActionName", param1=value1, param2=value2)
```

Or to finish:
```
finish(message="Task completed successfully")
```

## System Prompts

System prompts are defined in:
- `phone_agent/config/prompts_zh.py` (Chinese)
- `phone_agent/config/prompts_en.py` (English)
- `phone_agent/config/prompts.py` (base)

Key aspects:
- Defines all available actions and their parameters
- Specifies rules for app navigation, error handling, user intent following
- Includes current date/time for temporal awareness
- Action format: `do(action="...", ...)` for actions, `finish(message="...")` to complete

## App Mappings

App name to package/Bundle ID mappings:
- Android: `phone_agent/config/apps.py`
- HarmonyOS: `phone_agent/config/apps_harmonyos.py`
- iOS: `phone_agent/config/apps_ios.py`

To add a new app, add an entry to the appropriate `APP_PACKAGES` dictionary.

## Configuration via Environment Variables

**Standard Mode:**
- `PHONE_AGENT_BASE_URL`: Model API URL (default: `http://localhost:8000/v1`)
- `PHONE_AGENT_MODEL`: Model name (default: `autoglm-phone-9b`)
- `PHONE_AGENT_API_KEY`: API key (default: `EMPTY`)
- `PHONE_AGENT_MAX_STEPS`: Max steps per task (default: `100`)
- `PHONE_AGENT_DEVICE_ID`: Device ID for multi-device setups
- `PHONE_AGENT_DEVICE_TYPE`: Device type (`adb`, `hdc`, or `ios`)
- `PHONE_AGENT_LANG`: Language (`cn` or `en`)
- `PHONE_AGENT_WDA_URL`: WebDriverAgent URL for iOS (default: `http://localhost:8100`)

**WebSocket Mode:**
- `AUTOGLEM_WEBSOCKET_MODE`: Enable WebSocket mode (`true` or `false`, default: `false`)
- `AUTOGLEM_WS_HOST`: WebSocket server host (default: `0.0.0.0`)
- `AUTOGLEM_WS_PORT`: WebSocket server port (default: `8765`)

**Mobile Agent (Android device):**
- `AUTOGLM_SERVER_URL`: WebSocket server URL to connect to (default: `ws://10.25.144.51:8765`)
- `AUTOGLM_DEVICE_ID`: Unique device identifier (default: auto-generated UUID)
- `AUTOGLM_LOG_LEVEL`: Logging level (default: `INFO`)
- `AUTOGLM_SCREENSHOT_QUALITY`: JPEG compression quality 1-100 (default: `45`)
- `AUTOGLM_SCREENSHOT_MAX_WIDTH`: Max screenshot width in pixels (default: `1080`)
- `AUTOGLM_RECONNECT_DELAY`: Delay before reconnection in seconds (default: `5`)
- `ADB_PORT`: ADB port for mobile_agent_async.py (required, e.g., `5555`)

## Important Implementation Notes

**Device Connection Methods:**
- **Standard Mode**: Requires ADB/HDC installed locally. Uses USB or network ADB connections.
- **WebSocket Mode**: No local ADB needed. Mobile agent runs on device and connects via WebSocket. Requires:
  - Server and device on same network (or accessible routing)
  - Mobile agent running on Android device (e.g., in Termux)
  - Proper firewall configuration for WebSocket port
  - Device must have ADB server running on accessible port (for mobile_agent_async.py)

**Keyboard Handling:**
- **Standard Android Mode**: Requires ADB Keyboard to be installed and enabled. Agent temporarily switches to ADB Keyboard for text input, then restores original keyboard.
- **HarmonyOS**: Uses native input method, no ADB Keyboard needed.
- **iOS**: Uses WebDriverAgent's input methods.
- **WebSocket Mode**: Keyboard switching is handled automatically by the mobile agent.

**Coordinate Systems:**
- The model uses normalized coordinates (0-1000) which are converted to absolute pixels by the ActionHandler.
- Both standard and WebSocket modes use the same coordinate system.

**Context Management:**
- After each step, images are removed from messages to save context space while preserving conversation text.
- WebSocket mode uses optimized screenshot compression to reduce bandwidth.

**Error Handling:**
- **Sensitive Operations**: Actions with `message` parameter trigger user confirmation via callback.
- **Takeover Requests**: `do(action="Take_over", message="...")` signals need for human intervention (login, captcha).
- **WebSocket Timeouts**: Each command type has specific timeout (e.g., screenshot: 60s, tap: 5s). See `COMMAND_TIMEOUTS` in `websocket_server.py`.

**Remote Access:**
- Standard mode supports WiFi ADB: `adb connect <ip>:5555`
- WebSocket mode works over any network connection with proper routing
- iOS WebDriverAgent requires `--wda-url` (USB requires iproxy port forwarding)

**Model Deployment:**
- The agent code does not include the VLM
- Model must be deployed separately (vLLM, SGLang, or third-party APIs like BigModel/ModelScope)

## Development Notes

**Language and Dependencies:**
- Python 3.10+ required
- WebSocket mode requires `websockets>=12.0` (see `requirements-websocket.txt`)
- Standard requirements: `Pillow>=12.0.0`, `openai>=2.9.0`, `requests>=2.31.0`
- Development dependencies (commented out): pytest, pre-commit, black, mypy

**Testing:**
- No test files currently exist in the repository
- For WebSocket mode testing: start server, run mobile agent on device, verify connection with `python main.py --list-devices`

**Code Organization:**
- `phone_agent/adb/__init__.py` automatically switches between standard ADB and WebSocket implementations based on `WEBSOCKET_MODE` environment variable
- Both implementations expose the same API for compatibility
- WebSocket protocol is defined in `phone_agent/websocket_protocol.py` with strict message validation

**Model Deployment:**
- vLLM or SGLang with specific multimodal configurations required
- See `requirements.txt` "For Model Deployment" section for version requirements
- See README for detailed deployment instructions
