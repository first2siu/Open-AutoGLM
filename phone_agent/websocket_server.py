"""WebSocket server for AutoGLM command forwarding."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set
import uuid

import websockets
from websockets.server import WebSocketServerProtocol

from phone_agent.websocket_protocol import (
    AckMessage,
    CommandMessage,
    MessageType,
    RegisterMessage,
    ResultMessage,
    WSMessage,
    create_ack,
)

logger = logging.getLogger(__name__)


@dataclass
class PerformanceStats:
    """Performance statistics for a command type."""
    command: str
    count: int = 0
    total_time: float = 0.0  # Total round trip time
    server_to_client_time: float = 0.0
    execution_time: float = 0.0
    client_to_server_time: float = 0.0
    min_time: float = float('inf')
    max_time: float = 0.0

    def add_sample(self, total: float, s2c: float, exec_time: float, c2s: float):
        """Add a performance sample."""
        self.count += 1
        self.total_time += total
        self.server_to_client_time += s2c
        self.execution_time += exec_time
        self.client_to_server_time += c2s
        self.min_time = min(self.min_time, total)
        self.max_time = max(self.max_time, total)

    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics."""
        if self.count == 0:
            return {"command": self.command, "count": 0}

        return {
            "command": self.command,
            "count": self.count,
            "avg_total": f"{self.total_time / self.count * 1000:.1f}ms",
            "avg_s2c": f"{self.server_to_client_time / self.count * 1000:.1f}ms",
            "avg_exec": f"{self.execution_time / self.count * 1000:.1f}ms",
            "avg_c2s": f"{self.client_to_server_time / self.count * 1000:.1f}ms",
            "min": f"{self.min_time * 1000:.1f}ms",
            "max": f"{self.max_time * 1000:.1f}ms",
        }


@dataclass
class PendingCommand:
    """A command waiting for execution result."""

    msg_id: str
    command: CommandMessage
    future: asyncio.Future
    timeout: float = 30.0  # seconds


# 差异化超时配置
COMMAND_TIMEOUTS = {
    "screenshot": 60.0,  # 截图可能较慢
    "tap": 5.0,
    "double_tap": 5.0,
    "long_press": 8.0,
    "swipe": 8.0,
    "back": 3.0,
    "home": 3.0,
    "type_text": 10.0,
    "clear_text": 5.0,
    "launch_app": 15.0,
    "get_current_app": 5.0,
    "default": 10.0,
}


@dataclass
class ConnectedDevice:
    """A connected mobile device."""

    device_id: str
    websocket: WebSocketServerProtocol
    device_info: Dict = field(default_factory=dict)
    pending_commands: Dict[str, PendingCommand] = field(default_factory=dict)


class WebSocketCommandServer:
    """
    WebSocket server that manages connected mobile devices and command execution.

    This server:
    1. Accepts connections from mobile agents running on phones
    2. Maintains a command queue for each device
    3. Sends commands to devices and waits for ACK/results
    4. Handles timeouts and errors
    5. Tracks performance statistics
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8765):
        self.host = host
        self.port = port
        self.devices: Dict[str, ConnectedDevice] = {}
        self.server = None
        self._lock = asyncio.Lock()
        self._running = False
        # Performance tracking
        self._performance_stats: Dict[str, PerformanceStats] = {}

    async def start(self):
        """Start the WebSocket server."""
        logger.info(f"Starting WebSocket server on {self.host}:{self.port}")
        self.server = await websockets.serve(
            self._handle_connection, self.host, self.port
        )
        self._running = True
        logger.info("WebSocket server started successfully")

    async def stop(self):
        """Stop the WebSocket server."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self._running = False
            logger.info("WebSocket server stopped")

    async def _handle_connection(self, websocket: WebSocketServerProtocol):
        """Handle a new WebSocket connection.

        Note: Compatible with websockets >= 14.0 where path parameter is removed.
        """
        device_id = None
        try:
            logger.info(f"New connection from {websocket.remote_address}")

            # Wait for registration message
            message_json = await websocket.recv()
            msg_data = json.loads(message_json)

            logger.debug(f"Registration message: {msg_data}")

            # Validate it's a registration message
            if msg_data.get("msg_type") != "register":
                logger.warning(f"Expected 'register' message type, got: {msg_data.get('msg_type')}")
                await websocket.close()
                return

            # Parse registration
            register_data = RegisterMessage.from_dict(msg_data)
            device_id = register_data.device_id or str(uuid.uuid4())

            # Register device
            async with self._lock:
                if device_id in self.devices:
                    logger.warning(f"Device {device_id} already connected, replacing")
                    await self._disconnect_device(device_id)

                self.devices[device_id] = ConnectedDevice(
                    device_id=device_id,
                    websocket=websocket,
                    device_info=register_data.device_info,
                )

            logger.info(
                f"Device {device_id} registered successfully. "
                f"Info: {register_data.device_info}"
            )

            # Send ACK
            await self._send_message(
                websocket, create_ack(str(uuid.uuid4())).to_dict()
            )

            # Handle messages from this device
            await self._handle_device_messages(device_id, websocket)

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Connection closed for device {device_id}")
        except Exception as e:
            logger.error(f"Error handling connection: {e}", exc_info=True)
        finally:
            if device_id:
                await self._disconnect_device(device_id)

    async def _handle_device_messages(
        self, device_id: str, websocket: WebSocketServerProtocol
    ):
        """Handle incoming messages from a connected device."""
        try:
            async for message in websocket:
                try:
                    # Check if message is binary or text
                    if isinstance(message, bytes):
                        # Binary data - associate with pending command
                        await self._handle_binary_data(device_id, message)
                        continue

                    # Text message - JSON
                    msg_data = json.loads(message)

                    # Log raw message for debugging
                    msg_type_str = msg_data.get("msg_type", "unknown")
                    logger.debug(f"Received from {device_id}: msg_type={msg_type_str}, data keys={list(msg_data.keys())}")

                    # Parse message based on type
                    if msg_type_str == "command":
                        msg = CommandMessage.from_dict(msg_data)
                        if msg is None:
                            logger.error(f"Failed to parse command message: {json.dumps(msg_data)[:200]}")
                            continue
                        await self._execute_command(device_id, msg)
                    elif msg_type_str == "ack":
                        msg = AckMessage.from_dict(msg_data)
                        await self._handle_ack(device_id, msg)
                    elif msg_type_str == "result":
                        result_msg = ResultMessage.from_dict(msg_data)
                        if result_msg is None:
                            logger.error(f"Failed to parse result message: {json.dumps(msg_data)[:200]}")
                            continue
                        await self._handle_result(device_id, result_msg)
                    elif msg_type_str == "error":
                        msg = WSMessage.from_dict(msg_data)
                        if msg is None:
                            logger.error(f"Failed to parse error message: {json.dumps(msg_data)[:200]}")
                            continue
                        await self._handle_error(device_id, msg)
                    elif msg_type_str == "pong":
                        logger.debug(f"PONG received from {device_id}")
                    elif msg_type_str == "register":
                        # Already handled during connection
                        logger.debug(f"Late registration message from {device_id}")
                    else:
                        logger.warning(f"Unknown message type: {msg_type_str}")

                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON from {device_id}: {e}")
                    logger.error(f"Raw message: {message[:200] if isinstance(message, (str, bytes)) else 'unknown'}")
                except Exception as e:
                    logger.error(f"Error handling message from {device_id}: {e}")
                    import traceback
                    logger.error(traceback.format_exc())

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Device {device_id} disconnected")

    async def _handle_binary_data(self, device_id: str, binary_data: bytes):
        """
        Handle binary data from device using Header+Body single-packet format.

        Format: [4 bytes JSON length] + [JSON bytes] + [Image binary data]
        """
        async with self._lock:
            device = self.devices.get(device_id)
            if not device:
                logger.warning(f"Received binary data for unknown device {device_id}")
                return

            # 检查数据长度是否足够（至少需要4字节头）
            if len(binary_data) < 4:
                logger.error(f"⚠️ Invalid binary packet: too short ({len(binary_data)} bytes)")
                return

            try:
                # 1. 读取前 4 字节获取 JSON 长度（大端序）
                json_length = int.from_bytes(binary_data[:4], byteorder='big')

                # 2. 验证长度合理性
                if json_length <= 0 or json_length > len(binary_data) - 4:
                    logger.error(f"⚠️ Invalid JSON length: {json_length}, packet size: {len(binary_data)}")
                    return

                # 3. 截取 JSON 字节并解析
                json_bytes = binary_data[4:4 + json_length]
                json_str = json_bytes.decode('utf-8')
                metadata = json.loads(json_str)

                # 4. 剩余部分为图片二进制数据
                image_data = binary_data[4 + json_length:]

                logger.debug(
                    f"📦 单包解析: JSON={json_length}B, Image={len(image_data)/1024:.1f}KB, "
                    f"msg_id={metadata.get('msg_id', 'unknown')[:8]}"
                )

                # 5. 获取 msg_id 并查找对应的 pending command
                msg_id = metadata.get("msg_id")
                if not msg_id:
                    logger.error("⚠️ No msg_id in metadata")
                    return

                pending = device.pending_commands.get(msg_id)
                if not pending:
                    logger.warning(
                        f"⚠️ No pending command found for {msg_id[:8]}. "
                        f"Device: {device_id}"
                    )
                    return

                # 6. 构造 ResultMessage 并完成 Future
                # 从 metadata 提取字段构造 ResultMessage
                result_msg = ResultMessage.from_dict(metadata)
                if result_msg is None:
                    logger.error(f"⚠️ Failed to parse ResultMessage from metadata")
                    return

                # 将图片二进制数据附加到结果中
                result_msg.result["binary_data"] = image_data
                result_msg.result["data_size"] = len(image_data)

                # 完成命令
                if not pending.future.done():
                    pending.future.set_result(result_msg)
                    logger.info(
                        f"✅ 单包命令完成: {msg_id[:8]}, "
                        f"image={len(image_data)/1024:.1f}KB"
                    )
                else:
                    logger.warning(f"⚠️ Future already done for {msg_id[:8]}")

                # 从 pending 列表中移除
                device.pending_commands.pop(msg_id)

            except json.JSONDecodeError as e:
                logger.error(f"⚠️ JSON decode error: {e}")
                logger.debug(f"JSON bytes preview: {binary_data[4:200].hex()}")
            except UnicodeDecodeError as e:
                logger.error(f"⚠️ Unicode decode error: {e}")
            except Exception as e:
                logger.error(f"⚠️ Error parsing binary packet: {e}")
                import traceback
                logger.error(traceback.format_exc())

    async def _handle_ack(self, device_id: str, msg: WSMessage):
        """Handle ACK message from device."""
        logger.debug(f"ACK received from {device_id} for msg {msg.msg_id}")
        # ACK confirms command was received, result will follow

    async def _handle_result(self, device_id: str, result_msg: ResultMessage):
        """
        Handle result message from device (JSON only, no binary data).

        Note: Binary data (screenshots) are now sent via single-packet format
        and handled by _handle_binary_data(). This method only handles
        non-binary results like tap, swipe, etc.
        """
        async with self._lock:
            device = self.devices.get(device_id)
            if not device:
                logger.warning(f"Result for unknown device {device_id}")
                return

            msg_id = result_msg.msg_id
            pending = device.pending_commands.get(msg_id)

            if not pending:
                # Check if this might be a result for an already completed command
                logger.warning(
                    f"⚠️ Received result for unknown/already-completed command {msg_id[:8]} from {device_id}. "
                    f"This might indicate a timing issue or duplicate response."
                )
                return

            # 在单包传输模式下，JSON result 消息不应该包含 has_binary=True
            # (二进制数据已经通过单包发送并处理了)
            has_binary = result_msg.result.get("has_binary", False)
            if has_binary:
                logger.warning(
                    f"⚠️ Unexpected has_binary=True in JSON result for {msg_id[:8]}. "
                    f"This should not happen with single-packet transmission."
                )

            # 直接完成命令（无二进制数据）
            device.pending_commands.pop(msg_id)
            if not pending.future.done():
                pending.future.set_result(result_msg)
                logger.debug(f"✅ Completed {msg_id[:8]} (JSON only)")
            else:
                logger.warning(f"⚠️ Future already done for {msg_id[:8]}")

    async def _handle_error(self, device_id: str, msg: WSMessage):
        """Handle error message from device."""
        async with self._lock:
            device = self.devices.get(device_id)
            if not device:
                return

            pending = device.pending_commands.pop(msg.msg_id, None)
            if pending and not pending.future.done():
                # Set exception for the waiting future
                pending.future.set_exception(
                    Exception(msg.data.get("error", "Unknown error"))
                )

    async def _disconnect_device(self, device_id: str):
        """Disconnect a device and clean up."""
        async with self._lock:
            device = self.devices.pop(device_id, None)
            if device:
                # Cancel all pending commands
                for pending in device.pending_commands.values():
                    if not pending.future.done():
                        pending.future.cancel()
                device.pending_commands.clear()

                try:
                    await device.websocket.close()
                except Exception:
                    pass

                logger.info(f"Device {device_id} disconnected and cleaned up")

    async def _send_message(self, websocket: WebSocketServerProtocol, message: Dict):
        """Send a message through websocket."""
        try:
            await websocket.send(json.dumps(message))
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            raise

    async def _receive_message(
        self, websocket: WebSocketServerProtocol
    ) -> Optional[WSMessage]:
        """Receive a message from websocket."""
        try:
            message_json = await websocket.recv()
            msg_data = json.loads(message_json)
            return WSMessage.from_dict(msg_data)
        except Exception as e:
            logger.error(f"Error receiving message: {e}")
            return None

    async def execute_command(
        self, device_id: str, command: CommandMessage, timeout: Optional[float] = None
    ) -> ResultMessage:
        """
        Execute a command on a device and wait for result.

        Args:
            device_id: Target device ID
            command: Command to execute
            timeout: Maximum time to wait for result (seconds). If None, uses command-specific default

        Returns:
            Result message from device

        Raises:
            TimeoutError: If command execution times out
            ConnectionError: If device is not connected
        """
        # 获取命令特定的超时时间
        if timeout is None:
            cmd_name = command.command.value
            timeout = COMMAND_TIMEOUTS.get(cmd_name, COMMAND_TIMEOUTS["default"])

        async with self._lock:
            device = self.devices.get(device_id)
            if not device:
                raise ConnectionError(f"Device {device_id} not connected")

            # Create future for this command
            future = asyncio.Future()

            # Add to pending commands
            pending = PendingCommand(
                msg_id=command.msg_id, command=command, future=future, timeout=timeout
            )
            device.pending_commands[command.msg_id] = pending

        # Update server send time (just before sending)
        command.server_send_time = time.time()

        # Send command
        try:
            await self._send_message(device.websocket, command.to_dict())
            logger.info(f"Command {command.msg_id} sent to {device_id}: {command.command.value} (timeout: {timeout}s)")
        except Exception as e:
            # Clean up on send failure
            async with self._lock:
                device.pending_commands.pop(command.msg_id, None)
            raise ConnectionError(f"Failed to send command: {e}")

        # Wait for result
        try:
            result = await asyncio.wait_for(future, timeout=timeout)

            # Log performance metrics
            self._log_performance_metrics(command, result)

            return result
        except asyncio.TimeoutError:
            # Clean up on timeout
            async with self._lock:
                device.pending_commands.pop(command.msg_id, None)
            logger.error(f"Command {command.msg_id} ({command.command.value}) timed out after {timeout}s")
            raise TimeoutError(f"Command {command.command.value} timed out after {timeout}s")

    def _log_performance_metrics(self, command: CommandMessage, result: ResultMessage):
        """Log detailed performance metrics for command execution."""
        server_recv_time = time.time()

        metrics = {
            "command": command.command.value,
            "msg_id": command.msg_id[:8],
        }

        # Calculate breakdown if timestamps are available
        if result.server_send_time and result.client_send_time:
            # Full cycle breakdown
            total_time = server_recv_time - command.server_send_time
            s2c_time = result.client_recv_time - command.server_send_time if result.client_recv_time else 0
            exec_time = result.client_execute_end_time - result.client_execute_start_time if result.client_execute_start_time and result.client_execute_end_time else 0
            c2s_time = server_recv_time - result.client_send_time if result.client_send_time else 0

            metrics["total_round_trip"] = f"{total_time * 1000:.1f}ms"
            metrics["server_to_client"] = f"{s2c_time * 1000:.1f}ms" if result.client_recv_time else "N/A"
            metrics["client_execution"] = f"{exec_time * 1000:.1f}ms" if result.client_execute_start_time and result.client_execute_end_time else "N/A"
            metrics["client_to_server"] = f"{c2s_time * 1000:.1f}ms" if result.client_send_time else "N/A"

            logger.info(
                f"⏱️  Performance [{metrics['command']}]: "
                f"total={metrics['total_round_trip']}, "
                f"s→c={metrics['server_to_client']}, "
                f"exec={metrics['client_execution']}, "
                f"c→s={metrics['client_to_server']}"
            )

            # Update statistics
            cmd_name = command.command.value
            if cmd_name not in self._performance_stats:
                self._performance_stats[cmd_name] = PerformanceStats(command=cmd_name)
            self._performance_stats[cmd_name].add_sample(total_time, s2c_time, exec_time, c2s_time)
        else:
            total_time = server_recv_time - command.server_send_time
            metrics["total_round_trip"] = f"{total_time * 1000:.1f}ms"
            logger.info(f"⏱️  Total round trip: {metrics['total_round_trip']}")

            # Update statistics (partial data)
            cmd_name = command.command.value
            if cmd_name not in self._performance_stats:
                self._performance_stats[cmd_name] = PerformanceStats(command=cmd_name)
            self._performance_stats[cmd_name].add_sample(total_time, 0, 0, 0)

    def get_performance_summary(self) -> Dict[str, Dict[str, Any]]:
        """Get performance summary for all command types."""
        return {
            cmd: stats.get_summary()
            for cmd, stats in self._performance_stats.items()
        }

    def log_performance_summary(self):
        """Log performance summary to logger."""
        if not self._performance_stats:
            logger.info("📊 No performance data collected yet")
            return

        logger.info("📊 Performance Summary:")
        for cmd_name, stats in sorted(self._performance_stats.items()):
            summary = stats.get_summary()
            if summary["count"] > 0:
                logger.info(
                    f"  {summary['command']}: "
                    f"count={summary['count']}, "
                    f"avg_total={summary['avg_total']}, "
                    f"avg_s2c={summary['avg_s2c']}, "
                    f"avg_exec={summary['avg_exec']}, "
                    f"avg_c2s={summary['avg_c2s']}, "
                    f"min={summary['min']}, "
                    f"max={summary['max']}"
                )

    def is_device_connected(self, device_id: str) -> bool:
        """Check if a device is connected."""
        return device_id in self.devices

    def list_connected_devices(self) -> list[str]:
        """List all connected device IDs."""
        return list(self.devices.keys())


# Global server instance
_server_instance: Optional[WebSocketCommandServer] = None


def get_server(host: str = "0.0.0.0", port: int = 8765) -> WebSocketCommandServer:
    """Get or create the global WebSocket server instance."""
    global _server_instance
    if _server_instance is None:
        _server_instance = WebSocketCommandServer(host=host, port=port)
    return _server_instance
