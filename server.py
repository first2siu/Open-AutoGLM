# server.py
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import List, Dict, Any
import json
import traceback

# 导入 Open-AutoGLM 的核心组件
# 注意：确保你的 server.py 放在项目根目录，或者将项目路径加入 PYTHONPATH
from phone_agent.model import ModelClient, ModelConfig
from phone_agent.model.client import MessageBuilder
from phone_agent.actions.handler import parse_action
from phone_agent.config import get_system_prompt

app = FastAPI()

# 1. 配置模型 (与 examples/basic_usage.py 保持一致)
# 请修改 base_url 为你实际的模型 API 地址
model_config = ModelConfig(
    base_url="http://localhost:8000/v1",  # 你的 vllm 或其他推理服务地址
    model_name="autoglm-phone-9b",
    temperature=0.1,
)
# 初始化模型客户端
model_client = ModelClient(model_config)

# 系统提示词 (System Prompt)
SYSTEM_PROMPT = get_system_prompt("cn")  # 或 "en"

class AgentSession:
    """
    为每个 WebSocket 连接维护独立的会话状态。
    复刻了 PhoneAgent 类中的 _context 管理逻辑。
    """
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.context: List[Dict[str, Any]] = []
        self.step_count = 0

    def init_session(self, task: str, screen_info: str, screenshot_base64: str):
        """第一步：初始化上下文，加入 System Prompt 和用户任务"""
        self.context = []
        self.step_count = 1
        
        # 1. System Prompt
        self.context.append(MessageBuilder.create_system_message(SYSTEM_PROMPT))
        
        # 2. User Task + First Screenshot
        # screen_info 可以是简单的 App 名称，或者是详细的 UI 树文本
        text_content = f"{task}\n\nScreen Info: {screen_info}"
        self.context.append(
            MessageBuilder.create_user_message(
                text=text_content, 
                image_base64=screenshot_base64
            )
        )

    def step_session(self, screen_info: str, screenshot_base64: str):
        """后续步骤：加入新的屏幕截图"""
        self.step_count += 1
        
        # 1. 压缩历史 Context：移除上一轮的图片数据，防止 Token 爆炸
        # - logic inside _execute_step
        if self.context:
            self.context[-1] = MessageBuilder.remove_images_from_message(self.context[-1])

        # 2. User Update
        text_content = f"** Screen Info **\n\n{screen_info}"
        self.context.append(
            MessageBuilder.create_user_message(
                text=text_content, 
                image_base64=screenshot_base64
            )
        )

    def add_assistant_response(self, thinking: str, action_str: str):
        """记录模型的回答到历史中"""
        self.context.append(
            MessageBuilder.create_assistant_message(
                f"<think>{thinking}</think><answer>{action_str}</answer>"
            )
        )

# 连接管理器
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.sessions: Dict[str, AgentSession] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        self.sessions[client_id] = AgentSession(client_id)
        print(f"Client connected: {client_id}")

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        if client_id in self.sessions:
            del self.sessions[client_id]
        print(f"Client disconnected: {client_id}")

    def get_session(self, client_id: str) -> AgentSession:
        return self.sessions.get(client_id)

manager = ConnectionManager()

@app.websocket("/ws/agent/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(websocket, client_id)
    session = manager.get_session(client_id)
    
    try:
        while True:
            # === 1. 接收 SpringBoot 发来的数据 ===
            data = await websocket.receive_json()
            # 预期数据格式:
            # {
            #   "type": "init" | "step",
            #   "task": "帮我点外卖", (仅 init 需要)
            #   "screenshot": "BASE64_STRING_WITHOUT_HEADER",
            #   "screen_info": "Meituan Home Page" (可选，UI描述)
            # }
            
            req_type = data.get("type")
            screenshot = data.get("screenshot")
            screen_info = data.get("screen_info", "Unknown Page")
            
            if not screenshot:
                await websocket.send_json({"status": "error", "message": "Missing screenshot"})
                continue

            # === 2. 更新 Session 上下文 ===
            if req_type == "init":
                task = data.get("task")
                if not task:
                    await websocket.send_json({"status": "error", "message": "Missing task for init"})
                    continue
                session.init_session(task, screen_info, screenshot)
                print(f"[{client_id}] Task initialized: {task}")
                
            elif req_type == "step":
                session.step_session(screen_info, screenshot)
                print(f"[{client_id}] Processing step {session.step_count}")

            # === 3. 模型推理 (Thinking) ===
            try:
                # 调用模型 API
                # - model_client.request
                response = model_client.request(session.context)
            except Exception as e:
                traceback.print_exc()
                await websocket.send_json({"status": "error", "message": f"Model inference failed: {str(e)}"})
                continue

            # === 4. 解析动作 (Processing) ===
            # - parse_action
            try:
                action_data = parse_action(response.action)
            except ValueError:
                # 解析失败通常意味着模型输出了非标准指令，或者任务结束
                action_data = {"_metadata": "finish", "message": response.action}

            # === 5. 更新上下文 ===
            session.add_assistant_response(response.thinking, response.action)

            # === 6. 返回结果给 SpringBoot (Response) ===
            response_payload = {
                "status": "success",
                "step": session.step_count,
                "thinking": response.thinking, # 思考过程
                "action": action_data,         # 解析后的 JSON 动作
                "raw_response": response.action,
                "finished": action_data.get("_metadata") == "finish"
            }
            
            await websocket.send_json(response_payload)
            
            if response_payload["finished"]:
                print(f"[{client_id}] Task finished.")
                # 任务结束，但不一定要断开 WS，可以等待下一个 init
                # 但根据你的流程，可能需要在 App 端重置
                
    except WebSocketDisconnect:
        manager.disconnect(client_id)
    except Exception as e:
        print(f"Error: {e}")
        try:
            await websocket.send_json({"status": "error", "message": str(e)})
        except:
            pass

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)