from ErisPulse import sdk
from ErisPulse.Core import BaseAdapter
from ErisPulse.Core import logger, config as config_manager, adapter
import websockets
import asyncio
import json
import zlib

from .CallApi import CallApi
from .Converter import KookAdapterConverter

class KookAdapter(BaseAdapter):
    def __init__(self, sdk):
        super().__init__()

        self.sdk = sdk
        self.config_manager = config_manager
        self.logger = logger.get_child("KookAdapter")
        self.websocket = None
        self.converter = KookAdapterConverter()
        self.adapter = adapter
        
        self.sn = 0
        self.buffer = []
        self.need_buffer = False
        self._running = False
        self._heartbeat_task = None
        self._receive_task = None

        self.config = self._get_config()
        is_vaild, msg = self._check_valid_config()
        if not is_vaild:
            self.logger.error(msg)
            raise Exception(msg)
        
        self.api = CallApi(self.config.get("token"))
    
    async def start(self):
        """启动适配器"""
        self._running = True
        while self._running:
            try:
                url = await self.api.get_ws_gateway(self.config.get("compress", True))
                async with websockets.connect(
                    url,
                    ping_interval=None
                ) as websocket:
                    result = await self._wait_server_hello(websocket)
                    if not result:
                        self.logger.error("KookAdapter 启动失败, 连接关闭, 5秒后重试")
                        await asyncio.sleep(5)
                        continue
                    
                    self.websocket = websocket
                    self.logger.info("KookAdapter 连接成功，开始处理消息")
                    
                    # 启动心跳和消息接收任务
                    self._heartbeat_task = asyncio.create_task(self._send_heartbeat())
                    self._receive_task = asyncio.create_task(self._receive_messages())
                    
                    # 等待任务完成（连接断开时会返回）
                    await asyncio.gather(
                        self._heartbeat_task,
                        self._receive_task,
                        return_exceptions=True
                    )
                    
            except Exception as e:
                self.logger.error(f"KookAdapter 连接异常: {e}, 5秒后重试")
                await asyncio.sleep(5)
    
    async def stop(self):
        """停止适配器"""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._receive_task:
            self._receive_task.cancel()
        if self.websocket:
            await self.websocket.close()
        self.logger.info("KookAdapter 已停止")
    
    async def _wait_server_hello(self, websocket):
        """等待服务器发送hello消息"""
        try:
            message = await asyncio.wait_for(
                websocket.recv(),
                timeout=6
            )
            if self.config.get("compress", True):
                message = zlib.decompress(message)
            if isinstance(message, bytes):
                message = message.decode("utf-8")
            data = json.loads(message)
            if data["s"] == 1:
                self.logger.info("成功接收到服务器信令: HELLO[1], 开始处理消息")
                if data.get("d", {}).get("code", -1) == 0:
                    self.logger.info("KookAdapter 连接成功")
                    return True
                return False
            return False
        except asyncio.TimeoutError:
            self.logger.error("等待服务器发送HELLO[1]信令超时, 请重试")
            await websocket.close()
            return False
    
    async def _receive_messages(self):
        """持续接收消息"""
        while self._running and self.websocket:
            try:
                message = await self.websocket.recv()
                
                if self.config.get("compress", True):
                    message = zlib.decompress(message)
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                
                data = json.loads(message)
                signal_type = data.get("s")
                
                if signal_type == 0:
                    # 正常消息事件
                    await self._handle_message_signal(data)
                elif signal_type == 3:
                    # 心跳响应（PONG）
                    self.logger.debug("收到心跳响应 PONG[3]")
                elif signal_type == 5:
                    # 需要重连
                    code = data.get("d", {}).get("code", "")
                    err = data.get("d", {}).get("err", "")
                    self.logger.error(f"收到RECONNECT[5]信令，连接已失效，code={code}, err={err}")
                    await self._handle_reconnect_signal()
                    return
                elif signal_type == 6:
                    # RESUME 成功
                    session_id = data.get("d", {}).get("session_id", "")
                    self.logger.info(f"RESUME成功，session_id: {session_id}")
                else:
                    self.logger.warning(f"收到未知信令类型: {signal_type}")
                    
            except websockets.exceptions.ConnectionClosed:
                self.logger.warning("WebSocket连接已关闭")
                return
            except Exception as e:
                self.logger.error(f"消息接收异常: {e}")
                await asyncio.sleep(1)
    
    async def _send_heartbeat(self):
        """发送心跳"""
        while self._running and self.websocket:
            try:
                payload = {
                    "s": 2,
                    "sn": self.sn
                }
                await self.websocket.send(json.dumps(payload).encode("utf-8"))
                self.logger.debug(f"发送心跳 PING[2]，sn={self.sn}")
                await asyncio.sleep(30)
            except websockets.exceptions.ConnectionClosed:
                self.logger.warning("心跳发送失败，连接已关闭")
                return
            except Exception as e:
                self.logger.error(f"心跳发送异常: {e}")
                return
    
    async def _reconnect(self):
        """重新连接"""
        self.logger.info("开始执行重连...")
        if self.websocket:
            try:
                await self.websocket.close()
            except:
                pass
        self.websocket = None
        
        max_retries = 5
        retry_count = 0
        while retry_count < max_retries and self._running:
            try:
                async with websockets.connect(self.api.get_ws_gateway(self.config.get("compress", True))) as websocket:
                    resume_payload = {
                        "s": 4,
                        "sn": self.sn
                    }
                    await websocket.send(json.dumps(resume_payload).encode("utf-8"))
                    self.logger.info(f"发送RESUME[4]信令，sn={self.sn}")
                    
                    message = await asyncio.wait_for(
                        websocket.recv(),
                        timeout=6
                    )
                    if self.config.get("compress", True):
                        message = zlib.decompress(message)
                    if isinstance(message, bytes):
                        message = message.decode("utf-8")
                    data = json.loads(message)
                    
                    if data.get("s") == 6:
                        session_id = data.get("d", {}).get("session_id", "")
                        self.logger.info(f"RESUME成功，连接已恢复，session_id: {session_id}")
                        self.websocket = websocket
                        return True
                    elif data.get("s") == 5:
                        code = data.get("d", {}).get("code", "")
                        err = data.get("d", {}).get("err", "")
                        self.logger.error(f"收到RECONNECT[5]信令，resume失败，code={code}, err={err}")
                        return False
                    else:
                        self.logger.warning(f"RESUME响应异常，预期 s=6，实际: {data}")
                        
            except Exception as e:
                self.logger.error(f"重连尝试 {retry_count + 1}/{max_retries} 失败: {e}")
            
            retry_count += 1
            await asyncio.sleep(5)
        
        self.logger.error("重连失败，已达到最大重试次数")
        return False

    async def _handle_reconnect_signal(self):
        """处理重连信号"""
        self.logger.warning("处理RECONNECT[5]信令：重新获取gateway，清空sn计数和消息队列...")
        if self.websocket:
            try:
                await self.websocket.close()
            except:
                pass
        self.websocket = None
        self.sn = 0
        self.buffer.clear()
        self.need_buffer = False

    async def _handle_message_signal(self, data: dict):
        """
        处理信令[0] - 正常消息接收
        
        Args:
            data: 消息数据，包含事件类型和事件数据
                  格式: {"s": 0, "d": {...}, "sn": 123}
        """
        msg_sn = data.get("sn", 0)
        expected_sn = self.sn + 1
        
        if msg_sn != expected_sn:
            if not self.need_buffer:
                self.need_buffer = True
                self.logger.warning(f"消息序号不连续，进入暂存模式。期望sn={expected_sn}，实际sn={msg_sn}")
            self.buffer.append(data)
            self.buffer.sort(key=lambda x: x.get("sn", 0))
            return
        
        await self._process_message(data)
        
        # 处理暂存区中的消息
        while self.buffer:
            next_expected = self.sn + 1
            found = False
            for i, buffered_msg in enumerate(self.buffer):
                if buffered_msg.get("sn", 0) == next_expected:
                    self.logger.debug(f"从暂存区处理消息，sn={next_expected}")
                    await self._process_message(buffered_msg)
                    self.buffer.pop(i)
                    found = True
                    break
            if not found:
                break
        
        if not self.buffer:
            self.need_buffer = False
            self.logger.debug("暂存区已清空，退出暂存模式")

    async def _process_message(self, data: dict):
        """
        处理单条消息，转换为OneBot12格式并分发到事件系统
        
        Args:
            data: 消息数据
        """
        self.sn = data.get("sn", self.sn)
        
        # 检查是否为机器人发送的消息（仅针对消息事件）
        d = data.get("d", {})
        kook_type = d.get("type", 0)
        if kook_type != 255:  # 非 notice 事件才过滤机器人消息
            author = d.get("extra", {}).get("author", {})
            if author.get("bot", False):
                return
        
        try:
            # 使用Converter将Kook事件转换为OneBot12格式
            onebot_event = self.converter.convert(data)
            
            # 通过adapter.emit分发事件到ErisPulse事件系统
            await self.adapter.emit(onebot_event)
            
            self.logger.debug(f"事件已分发: {onebot_event.get('type')} - {onebot_event.get('detail_type')}")
            
        except Exception as e:
            self.logger.error(f"事件处理异常: {e}, 原始数据: {data}")

    def _get_config(self):
        return self.config_manager.getConfig("KookAdapter")

    def _check_valid_config(self) -> bool|str:
        if self.config is None:
            return False, "KookAdapter 无配置项, 请添加后重试"
        if self.config.get("token") is None:
            return False, "KookAdapter 无token配置项, 请添加后重试"
        return True, ""

    async def shutdown(self):
        """关闭适配器（必须实现）"""
        await self.stop()

    async def call_api(self, endpoint: str, **params):
        """调用平台 API（必须实现）
        
        根据 endpoint 和 target_type 映射到具体的 API 方法：
        - /message/create + target_type=group -> send_message (频道消息)
        - /message/create + target_type=user -> send_direct_message (私信消息)
        - /message/update + target_type=user -> update_direct_message (更新私信消息)
        - /message/delete + target_type=user -> delete_direct_message (删除私信消息)
        - /asset/create -> upload_file
        """
        target_type = params.get("target_type", "group")
        
        # 移除内部使用的 target_type 参数，避免传递给 API
        api_params = {k: v for k, v in params.items() if k != "target_type"}
        
        if endpoint == "/message/create":
            if target_type in ("private", "user"):
                # 私信消息
                return await self.api.send_direct_message(**api_params)
            else:
                # 频道消息（默认）
                return await self.api.send_message(**api_params)
        elif endpoint == "/message/update":
            if target_type in ("private", "user"):
                # 更新私信消息
                return await self.api.update_direct_message(**api_params)
            else:
                # 更新频道消息
                return await self.api.update_channel_message(**api_params)
        elif endpoint == "/message/delete":
            if target_type in ("private", "user"):
                # 删除私信消息
                return await self.api.delete_direct_message(**api_params)
            else:
                # 删除频道消息
                return await self.api.delete_channel_message(**api_params)
        elif endpoint == "/asset/create":
            # 上传文件接口，需要 file_path 参数
            return await self.api.upload_file(**params)
        else:
            raise ValueError(f"未知的 API endpoint: {endpoint}")

    class Send(BaseAdapter.Send):
        """Send 嵌套类，继承自 BaseAdapter.Send"""

        _METHOD_MAP = {
            "text": "Text",
            "image": "Image",
            "video": "Video",
            "file": "File",
            "voice": "Voice",
            "markdown": "Markdown",
            "card": "Card",
        }

        def __init__(self, adapter, target_type=None, target_id=None, account_id=None):
            super().__init__(adapter, target_type, target_id, account_id)
            self._at_user_ids = []
            self._reply_message_id = None
            self._at_all = False

        def At(self, user_id: str) -> 'KookAdapter.Send':
            """@用户（可多次调用）"""
            self._at_user_ids.append(user_id)
            return self

        def AtAll(self) -> 'KookAdapter.Send':
            """@全体"""
            self._at_all = True
            return self

        def Reply(self, message_id: str) -> 'KookAdapter.Send':
            """回复消息"""
            self._reply_message_id = message_id
            return self

        def _build_content(self, text: str) -> dict:
            """构建消息内容"""
            content = {"type": "text", "content": text}

            if self._at_all:
                content["mention_all"] = True

            if self._at_user_ids:
                content["mention"] = self._at_user_ids

            if self._reply_message_id:
                content["quote"] = self._reply_message_id

            return content

        def Text(self, text: str):
            """发送文本消息"""
            import asyncio
            content = self._build_content(text)
            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/message/create",
                    target_type=self._target_type,
                    target_id=self._target_id,
                    content=text,
                    type=1,
                    **{k: v for k, v in content.items() if k != "content" and k != "type"}
                )
            )

        def Image(self, url: str):
            """发送图片消息"""
            import asyncio
            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/message/create",
                    target_type=self._target_type,
                    target_id=self._target_id,
                    content=url,
                    type=2
                )
            )

        def Video(self, url: str):
            """发送视频消息"""
            import asyncio
            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/message/create",
                    target_type=self._target_type,
                    target_id=self._target_id,
                    content=url,
                    type=3
                )
            )

        def File(self, url: str):
            """发送文件消息"""
            import asyncio
            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/message/create",
                    target_type=self._target_type,
                    target_id=self._target_id,
                    content=url,
                    type=4
                )
            )

        def Voice(self, url: str):
            """发送语音消息"""
            import asyncio
            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/message/create",
                    target_type=self._target_type,
                    target_id=self._target_id,
                    content=url,
                    type=8
                )
            )

        def Markdown(self, text: str):
            """发送 KMarkdown 消息"""
            import asyncio
            content = self._build_content(text)
            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/message/create",
                    target_type=self._target_type,
                    target_id=self._target_id,
                    content=text,
                    type=9,
                    **{k: v for k, v in content.items() if k != "content" and k != "type"}
                )
            )

        def Card(self, card_data: dict):
            """发送卡片消息"""
            import asyncio
            import json
            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/message/create",
                    target_type=self._target_type,
                    target_id=self._target_id,
                    content=json.dumps(card_data),
                    type=10
                )
            )

        def Edit(self, msg_id: str, content: str):
            """编辑消息（仅支持 KMarkdown 和 CardMessage）"""
            import asyncio
            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/message/update",
                    target_type=self._target_type,
                    msg_id=msg_id,
                    content=content
                )
            )

        def Recall(self, msg_id: str):
            """撤回消息"""
            import asyncio
            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/message/delete",
                    target_type=self._target_type,
                    msg_id=msg_id
                )
            )

        def Upload(self, file_path: str):
            """上传本地文件
            
            Args:
                file_path: 本地文件路径
            
            Returns:
                上传结果，包含文件的 URL
            """
            import asyncio
            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/asset/create",
                    file_path=file_path
                )
            )
