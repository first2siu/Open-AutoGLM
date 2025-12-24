"""WebSocket protocol definition for AutoGLM command forwarding."""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MessageType(str, Enum):
    """Message types for WebSocket communication."""

    # Server -> Mobile commands
    COMMAND = "command"
    PING = "ping"

    # Mobile -> Server responses
    ACK = "ack"
    RESULT = "result"
    ERROR = "error"
    PONG = "pong"

    # Connection management
    REGISTER = "register"
    DEREGISTER = "deregister"


class CommandType(str, Enum):
    """ADB command types that can be sent to mobile agent."""

    TAP = "tap"
    DOUBLE_TAP = "double_tap"
    LONG_PRESS = "long_press"
    SWIPE = "swipe"
    BACK = "back"
    HOME = "home"
    TYPE_TEXT = "type_text"
    CLEAR_TEXT = "clear_text"
    LAUNCH_APP = "launch_app"
    GET_CURRENT_APP = "get_current_app"
    SCREENSHOT = "screenshot"


@dataclass
class WSMessage:
    """Base WebSocket message structure."""

    msg_type: MessageType
    msg_id: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "msg_type": self.msg_type.value,
            "msg_id": self.msg_id,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["WSMessage"]:
        """
        Create from dictionary.

        Ignores extra fields that may be added by mobile implementations.
        Returns None if required fields are missing.
        """
        try:
            return cls(
                msg_type=MessageType(data["msg_type"]),
                msg_id=data.get("msg_id", ""),
                data=data.get("data", {}),
            )
        except (KeyError, ValueError) as e:
            # Missing required fields or invalid enum value
            return None


@dataclass
class CommandMessage(WSMessage):
    """Command message from server to mobile."""

    command: CommandType = CommandType.SCREENSHOT
    params: Dict[str, Any] = field(default_factory=dict)
    # Performance monitoring timestamps
    server_send_time: float = field(default_factory=time.time)

    def __post_init__(self):
        self.msg_type = MessageType.COMMAND

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with command-specific fields."""
        return {
            "msg_type": self.msg_type.value,
            "msg_id": self.msg_id,
            "command": self.command.value,
            "params": self.params,
            "server_send_time": self.server_send_time,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["CommandMessage"]:
        """
        Create CommandMessage from dictionary.

        Ignores extra fields that may be added by mobile implementations.
        Returns None if required fields are missing.
        """
        try:
            msg_id = data.get("msg_id", "")
            command_str = data.get("command", "screenshot")
            command = CommandType(command_str)
            params = data.get("params", {})

            return cls(
                msg_type=MessageType.COMMAND,
                msg_id=msg_id,
                command=command,
                params=params,
                data={},  # Command doesn't use data field
                server_send_time=data.get("server_send_time", time.time()),
            )
        except (KeyError, ValueError) as e:
            # Invalid command type or other error
            return None


@dataclass
class ResultMessage(WSMessage):
    """Result message from mobile to server."""

    success: bool = True
    result: Optional[Any] = None
    error: Optional[str] = None
    # Performance monitoring timestamps
    server_send_time: Optional[float] = None  # Copied from command
    client_recv_time: Optional[float] = None
    client_execute_start_time: Optional[float] = None
    client_execute_end_time: Optional[float] = None
    client_send_time: Optional[float] = None

    def __post_init__(self):
        self.msg_type = MessageType.RESULT

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with result-specific fields."""
        return {
            "msg_type": self.msg_type.value,
            "msg_id": self.msg_id,
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "server_send_time": self.server_send_time,
            "client_recv_time": self.client_recv_time,
            "client_execute_start_time": self.client_execute_start_time,
            "client_execute_end_time": self.client_execute_end_time,
            "client_send_time": self.client_send_time,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResultMessage":
        """
        Create ResultMessage from dictionary.

        Handles both nested and flat structures from mobile devices.
        Ignores extra fields that may be added by mobile implementations.
        """
        msg_id = data.get("msg_id", "")
        success = data.get("success", True)
        result = data.get("result")
        error = data.get("error")

        return cls(
            msg_type=MessageType.RESULT,
            msg_id=msg_id,
            success=success,
            result=result,
            error=error,
            server_send_time=data.get("server_send_time"),
            client_recv_time=data.get("client_recv_time"),
            client_execute_start_time=data.get("client_execute_start_time"),
            client_execute_end_time=data.get("client_execute_end_time"),
            client_send_time=data.get("client_send_time"),
        )


@dataclass
class AckMessage(WSMessage):
    """Acknowledgment message."""

    def __post_init__(self):
        self.msg_type = MessageType.ACK

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AckMessage":
        """
        Create AckMessage from dictionary.

        Ignores extra fields that may be added by mobile implementations.
        """
        msg_id = data.get("msg_id", "")

        return cls(
            msg_type=MessageType.ACK,
            msg_id=msg_id,
            data={},  # ACK doesn't need additional data
        )


@dataclass
class RegisterMessage(WSMessage):
    """Registration message from mobile to server."""

    device_id: str = ""
    device_info: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.msg_type = MessageType.REGISTER

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with registration-specific fields."""
        return {
            "msg_type": self.msg_type.value,
            "msg_id": self.msg_id,
            "device_id": self.device_id,
            "device_info": self.device_info,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RegisterMessage":
        """
        Create RegisterMessage from dictionary.

        Ignores extra fields that may be added by mobile implementations.
        """
        msg_id = data.get("msg_id", "")
        device_id = data.get("device_id", "")
        device_info = data.get("device_info", {})

        return cls(
            msg_type=MessageType.REGISTER,
            msg_id=msg_id,
            device_id=device_id,
            device_info=device_info,
        )


def create_command(
    command: CommandType, params: Dict[str, Any], msg_id: Optional[str] = None
) -> CommandMessage:
    """Helper to create a command message."""
    import uuid

    return CommandMessage(
        msg_type=MessageType.COMMAND,
        msg_id=msg_id or str(uuid.uuid4()),
        command=command,
        params=params,
    )


def create_result(
    msg_id: str, success: bool, result: Any = None, error: Optional[str] = None
) -> ResultMessage:
    """Helper to create a result message."""
    return ResultMessage(
        msg_type=MessageType.RESULT,
        msg_id=msg_id,
        success=success,
        result=result,
        error=error,
    )


def create_ack(msg_id: str) -> AckMessage:
    """Helper to create an ACK message."""
    return AckMessage(msg_type=MessageType.ACK, msg_id=msg_id)
