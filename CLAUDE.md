# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Open-AutoGLM is an AI-powered phone automation framework that uses vision-language models to understand and interact with mobile device screens. The agent can:
- Take screenshots of connected devices
- Send them to a vision-language model (AutoGLM-Phone-9B)
- Parse the model's response into actions (tap, swipe, type, launch apps, etc.)
- Execute those actions via ADB (Android), HDC (HarmonyOS), or XCTest (iOS)

The system supports three device types: Android (ADB), HarmonyOS (HDC), and iOS (XCTest/WebDriverAgent).

## Essential Commands

### Installation and Setup
```bash
# Install dependencies
pip install -r requirements.txt
pip install -e .

# Install with development dependencies
pip install -e ".[dev]"
```

### Device Connection and Management

**Android (ADB):**
```bash
# Check connected devices
adb devices

# Connect to remote device via WiFi
adb connect 192.168.1.100:5555

# Enable TCP/IP mode on USB device
adb tcpip 5555
```

**HarmonyOS (HDC):**
```bash
# Check connected devices
hdc list targets

# Connect to remote device
hdc tconn 192.168.1.100:5555
```

**iOS:**
```bash
# Check connected devices
idevice_id -l

# Pair with device (if needed)
idevicepair pair

# Set up port forwarding for WebDriverAgent
iproxy 8100 8100
```

### Running the Agent

```bash
# Interactive mode (Android)
python main.py --base-url http://localhost:8000/v1 --model "autoglm-phone-9b"

# Single task (Android)
python main.py --base-url http://localhost:8000/v1 --model "autoglm-phone-9b" "打开微信发消息"

# Interactive mode (HarmonyOS)
python main.py --device-type hdc --base-url http://localhost:8000/v1 --model "autoglm-phone-9b"

# Interactive mode (iOS)
python main.py --device-type ios --wda-url http://localhost:8100 --base-url http://localhost:8000/v1

# List supported apps
python main.py --list-apps

# List connected devices
python main.py --list-devices

# Check WebDriverAgent status (iOS)
python main.py --device-type ios --wda-status
```

### Model Deployment

The project does not include the model itself. You need to either:
1. Use a third-party API service (BigModel, ModelScope)
2. Deploy the model yourself using vLLM or SGLang

**vLLM deployment example:**
```bash
python3 -m vllm.entrypoints.openai.api_server \
  --served-model-name autoglm-phone-9b \
  --allowed-local-media-path / \
  --mm-encoder-tp-mode data \
  --mm_processor_cache_type shm \
  --mm_processor_kwargs '{"max_pixels":5000000}' \
  --max-model-len 25480 \
  --chat-template-content-format string \
  --limit-mm-per-prompt '{"image":10}' \
  --model zai-org/AutoGLM-Phone-9B \
  --port 8000
```

### Testing

```bash
# Run tests
pytest tests/

# Run specific test file
pytest tests/test_something.py
```

### Development

```bash
# Format code
black phone_agent/

# Type check
mypy phone_agent/

# Run linter
ruff check phone_agent/
```

## Architecture

### Core Components

**1. Main Entry Point (`main.py`)**
- Parses command-line arguments
- Handles device connection commands
- Creates and runs the agent
- Performs system requirement checks

**2. Agent (`phone_agent/agent.py` and `phone_agent/agent_ios.py`)**
- `PhoneAgent`: Main agent for Android/HarmonyOS devices
- `IOSPhoneAgent`: Specialized agent for iOS devices
- Implements the main loop: capture screenshot → request model → execute action → repeat
- Manages conversation context with the model

**3. Device Factory Pattern (`phone_agent/device_factory.py`)**
- Abstract interface for device operations across ADB, HDC, and iOS
- `DeviceType` enum: `ADB`, `HDC`, `IOS`
- `DeviceFactory` provides unified API for:
  - Screenshots (`get_screenshot`)
  - App launching (`launch_app`)
  - Input operations (`tap`, `swipe`, `type_text`)
  - Navigation (`back`, `home`)

**4. Model Client (`phone_agent/model/client.py`)**
- `ModelClient`: OpenAI-compatible API client
- Streams responses and parses thinking/action phases
- `MessageBuilder`: Constructs messages with images and text
- Model response format: `<thinking>do(action=...)` or `<thinking>finish(message=...)`

**5. Action Handler (`phone_agent/actions/handler.py`)**
- Parses and executes actions from model output
- Supported actions:
  - `Launch`: Start an app by name
  - `Tap`: Click at coordinates (relative 0-1000)
  - `Type`: Input text (switches to ADB Keyboard for Android)
  - `Swipe`: Drag from start to end coordinates
  - `Back/Home`: System navigation
  - `Double Tap`, `Long Press`: Special gestures
  - `Wait`: Delay for specified duration
  - `Take_over`: Request human intervention
  - `Note`, `Call_API`, `Interact`: Placeholder actions

**6. Device Modules**
- `phone_agent/adb/`: Android device control via ADB
- `phone_agent/hdc/`: HarmonyOS device control via HDC
- `phone_agent/xctest/`: iOS device control via XCTest/WebDriverAgent

**7. Configuration (`phone_agent/config/`)**
- `apps.py`: App name to package name mapping (Android)
- `apps_harmonyos.py`: App bundle ID mapping (HarmonyOS)
- `apps_ios.py`: App bundle ID mapping (iOS)
- `prompts_zh.py`: Chinese system prompts
- `prompts_en.py`: English system prompts
- `timing.py`: Delay configurations for device operations

### Agent Execution Flow

1. User provides natural language task (e.g., "Open WeChat and send a message")
2. Agent captures screenshot via device factory
3. Screenshot + task + system prompt → model API
4. Model responds with thinking process and action (e.g., `do(action="Tap", element=[500, 500])`)
5. Action handler parses and executes the action
6. Repeat from step 2 until:
   - Model outputs `finish(message="...")`, or
   - Max steps reached (default: 100)

### Coordinate System

The model uses relative coordinates (0-1000) for screen positions:
- Top-left: `[0, 0]`
- Bottom-right: `[1000, 1000]`
- Center: `[500, 500]`

Action handler converts these to absolute pixels based on screen resolution.

### Model Response Format

The model returns responses in two parts:

**Thinking phase**: Model's reasoning about current state
```
当前在系统桌面，需要先启动微信应用
```

**Action phase**: Structured action to execute
```
do(action="Launch", app="微信")
```

Alternative action to finish task:
```
finish(message="已成功发送消息")
```

The parser in `client.py` splits on `do(action=` or `finish(message=` markers.

## Environment Variables

- `PHONE_AGENT_BASE_URL`: Model API base URL (default: `http://localhost:8000/v1`)
- `PHONE_AGENT_MODEL`: Model name (default: `autoglm-phone-9b`)
- `PHONE_AGENT_API_KEY`: API key for model authentication (default: `EMPTY`)
- `PHONE_AGENT_MAX_STEPS`: Maximum steps per task (default: `100`)
- `PHONE_AGENT_DEVICE_ID`: Device ID for multi-device setups
- `PHONE_AGENT_DEVICE_TYPE`: Device type - `adb`, `hdc`, or `ios` (default: `adb`)
- `PHONE_AGENT_LANG`: Language for system prompts - `cn` or `en` (default: `cn`)
- `PHONE_AGENT_WDA_URL`: WebDriverAgent URL for iOS (default: `http://localhost:8100`)

## Important Constraints

### ADB Keyboard (Android only)
- Required for text input on Android devices
- Must be installed and enabled on the device
- Agent automatically switches to ADB Keyboard before typing and restores original keyboard after
- Installation: Download ADBKeyboard.apk and enable in system settings

### Screenshot Sensitivity
- Some apps (banking, payment) return black screens when screenshots are captured
- Agent detects this and can request human takeover via `Take_over` action
- iOS: WebViewDriverAgent may have restrictions on certain screens

### Model Requirements
- Requires vision-language model with phone GUI understanding capabilities
- Official model: `AutoGLM-Phone-9B` or `AutoGLM-Phone-9B-Multilingual`
- Model must support multimodal inputs (text + images)
- OpenAI-compatible API format required

### Device-Specific Behaviors

**Android (ADB):**
- Requires USB debugging enabled
- Some devices need "USB debugging (security settings)" enabled for tap/swipe to work
- ADB Keyboard must be installed for text input

**HarmonyOS (HDC):**
- Uses native input method (no ADB Keyboard needed)
- Commands differ: `hdc list targets` instead of `adb devices`
- Some HarmonyOS-specific keycodes (e.g., keyEvent 2054 for Enter)

**iOS (XCTest):**
- Requires WebDriverAgent running and accessible
- USB: Use `iproxy` to forward port 8100 from device
- WiFi: Use device IP directly as `--wda-url http://device-ip:8100`
- Some iOS gestures may differ from Android/HarmonyOS

## Adding Support for New Apps

Edit the appropriate app configuration file:

**Android:** `phone_agent/config/apps.py`
```python
APP_PACKAGES: dict[str, str] = {
    "App Display Name": "com.example.apppackage",
    ...
}
```

**HarmonyOS:** `phone_agent/config/apps_harmonyos.py`
**iOS:** `phone_agent/config/apps_ios.py`

Use `adb shell pm list packages` (Android) or `ideviceinstaller -l` (iOS) to find package/bundle IDs.

## Common Issues

1. **Device not found**: Run `adb kill-server && adb start-server`
2. **Can tap but can't type**: ADB Keyboard not installed or not enabled
3. **Black screenshots**: Sensitive app - agent will request takeover
4. **Model connection failed**: Check `--base-url` and model service status
5. **Windows encoding errors**: Set `PYTHONIOENCODING=utf-8`
6. **iOS WebDriverAgent not ready**: Check `--wda-url` and ensure WDA is running

## Python API Usage

```python
from phone_agent import PhoneAgent
from phone_agent.model import ModelConfig
from phone_agent.agent import AgentConfig

# Configure model
model_config = ModelConfig(
    base_url="http://localhost:8000/v1",
    model_name="autoglm-phone-9b",
)

# Configure agent
agent_config = AgentConfig(
    max_steps=100,
    verbose=True,
    lang="cn",  # or "en"
)

# Create agent
agent = PhoneAgent(
    model_config=model_config,
    agent_config=agent_config,
)

# Run task
result = agent.run("打开微信发消息给张三")
print(result)

# Or step-by-step
result = agent.step("打开微信")
while not result.finished:
    result = agent.step()
```
