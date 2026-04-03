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
            self.logger.warning(f"{msg}，生成默认配置文件")
            default_config = self._get_default_config()
            self.config_manager.setConfig("KookAdapter", default_config, immediate=True)
            self.logger.info("默认配置已生成，请填写 token 后重新运行")
            raise SystemExit(0)
        
        self.api = CallApi(self.config.get("token"))
    
    async def start(self):
        """启动适配器"""
        self._running = True
        while self._running:
            try:
                # 尝试 RESUME（如果 sn > 0）
                if self.sn > 0:
                    self.logger.info("尝试使用 RESUME 恢复连接...")
                    if await self._try_resume():
                        # RESUME 成功，启动消息处理
                        await self._start_message_processing()
                        continue
                    else:
                        # RESUME 失败，重置 sn，进行全新连接
                        self.logger.info("RESUME 失败，重新获取 gateway...")
                        self.sn = 0
                        self.buffer.clear()
                        self.need_buffer = False
                
                # 全新连接（HELLO 流程）
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
                    
                    # 启动消息处理
                    await self._start_message_processing()
                    
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

    async def _send_logout(self):
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                self.logger.error(f"下线失败: {e}")

    async def _try_resume(self):
        """
        尝试使用 RESUME[4] 恢复连接
        
        Returns:
            bool: 是否成功恢复
        """
        try:
            url = await self.api.get_ws_gateway(self.config.get("compress", True))
            async with websockets.connect(url, ping_interval=None) as websocket:
                # 发送 RESUME 信令
                resume_payload = {
                    "s": 4,
                    "sn": self.sn
                }
                await websocket.send(json.dumps(resume_payload).encode("utf-8"))
                self.logger.info(f"发送 RESUME[4] 信令，sn={self.sn}")
                
                # 等待响应
                message = await asyncio.wait_for(websocket.recv(), timeout=6)
                if self.config.get("compress", True):
                    message = zlib.decompress(message)
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                data = json.loads(message)
                
                if data.get("s") == 6:
                    # RESUME 成功
                    session_id = data.get("d", {}).get("session_id", "")
                    self.logger.info(f"RESUME 成功，session_id: {session_id}")
                    self.websocket = websocket
                    return True
                else:
                    self.logger.warning(f"RESUME 失败，收到响应: {data}")
                    return False
        except Exception as e:
            self.logger.error(f"RESUME 尝试失败: {e}")
            return False

    async def _start_message_processing(self):
        """启动消息处理（心跳和接收）"""
        self._heartbeat_task = asyncio.create_task(self._send_heartbeat())
        self._receive_task = asyncio.create_task(self._receive_messages())
        
        # 等待任务完成（连接断开时会返回）
        await asyncio.gather(
            self._heartbeat_task,
            self._receive_task,
            return_exceptions=True
        )
    
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

                self.logger.debug(f"收到消息: {data}")
                
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

    async def _handle_reconnect_signal(self):
        """
        处理 RECONNECT[5] 信令

        Kook 规则:
        1. 收到 RECONNECT 后，必须重新获取 gateway
        2. 清空 sn 计数和消息队列
        3. 重新连接（HELLO 流程）
        """
        self.logger.warning("收到 RECONNECT[5] 信令，开始重新连接...")

        # 取消心跳任务
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # 关闭当前连接
        if self.websocket:
            try:
                await self.websocket.close()
            except:
                pass
        self.websocket = None

        # 清空状态
        self.sn = 0
        self.buffer.clear()
        self.need_buffer = False

        # 重新连接会在 start() 方法的循环中自动进行

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

    def _get_default_config(self):
        return {
            "token": "",
            "compress": True
        }

    def _check_valid_config(self) -> bool|str:
        if self.config is None:
            return False, "KookAdapter 无配置项, 请添加后重试"
        if self.config.get("token") is None or self.config.get("token") == "":
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
            # 上传文件接口，支持 file、file_path、file_url 参数
            return await self.api.upload_asset(**params)
        else:
            raise ValueError(f"未知的 API endpoint: {endpoint}")

    class Send(BaseAdapter.Send):
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

        def _build_modifiers(self) -> dict:
            """构建修饰符参数"""
            modifiers = {}
            if self._at_all:
                modifiers["mention_all"] = True
            if self._at_user_ids:
                modifiers["mention"] = self._at_user_ids
            if self._reply_message_id:
                modifiers["quote"] = self._reply_message_id
            return modifiers

        def _error_response(self, message: str, retcode: int = -1) -> dict:
            """构建错误响应"""
            return {
                "status": "failed",
                "retcode": retcode,
                "data": None,
                "message_id": "",
                "message": message,
                "kook_raw": None
            }

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

        def Text(self, text: str):
            """发送文本消息"""
            import asyncio
            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/message/create",
                    target_type=self._target_type,
                    target_id=self._target_id,
                    content=text,
                    type=1,
                    **self._build_modifiers()
                )
            )

        def Image(self, file):
            import asyncio
            
            async def _send():
                # 判断输入类型
                if isinstance(file, bytes):
                    # 二进制数据，需要上传
                    upload_result = await self._adapter.api.upload_asset(file=file)
                    if upload_result["retcode"] != 0:
                        return upload_result
                    url = upload_result["data"]["url"]
                elif isinstance(file, str):
                    if file.startswith(("http://", "https://")):
                        # URL，需要上传到Kook服务器
                        upload_result = await self._adapter.api.upload_asset(file_url=file)
                        if upload_result["retcode"] != 0:
                            return upload_result
                        url = upload_result["data"]["url"]
                    else:
                        # 本地文件路径，需要上传
                        upload_result = await self._adapter.api.upload_asset(file_path=file)
                        if upload_result["retcode"] != 0:
                            return upload_result
                        url = upload_result["data"]["url"]
                else:
                    return self._error_response("不支持的文件类型")
                
                # 发送图片消息
                return await self._adapter.call_api(
                    endpoint="/message/create",
                    target_type=self._target_type,
                    target_id=self._target_id,
                    content=url,
                    type=2,
                    **self._build_modifiers()
                )
            
            return asyncio.create_task(_send())

        def Video(self, file):
            import asyncio
            
            async def _send():
                # 判断输入类型
                if isinstance(file, bytes):
                    # 二进制数据，需要上传
                    upload_result = await self._adapter.api.upload_asset(file=file)
                    if upload_result["retcode"] != 0:
                        return upload_result
                    url = upload_result["data"]["url"]
                elif isinstance(file, str):
                    if file.startswith(("http://", "https://")):
                        # URL，需要上传到Kook服务器
                        upload_result = await self._adapter.api.upload_asset(file_url=file)
                        if upload_result["retcode"] != 0:
                            return upload_result
                        url = upload_result["data"]["url"]
                    else:
                        # 本地文件路径，需要上传
                        upload_result = await self._adapter.api.upload_asset(file_path=file)
                        if upload_result["retcode"] != 0:
                            return upload_result
                        url = upload_result["data"]["url"]
                else:
                    return self._error_response("不支持的文件类型")
                
                # 发送视频消息
                return await self._adapter.call_api(
                    endpoint="/message/create",
                    target_type=self._target_type,
                    target_id=self._target_id,
                    content=url,
                    type=3,
                    **self._build_modifiers()
                )
            
            return asyncio.create_task(_send())

        def File(self, file, filename=None):
            import asyncio
            
            async def _send():
                # 判断输入类型
                if isinstance(file, bytes):
                    # 二进制数据，需要上传
                    upload_result = await self._adapter.api.upload_asset(file=file, file_path=filename)
                    if upload_result["retcode"] != 0:
                        return upload_result
                    url = upload_result["data"]["url"]
                elif isinstance(file, str):
                    if file.startswith(("http://", "https://")):
                        # URL，需要上传到Kook服务器
                        upload_result = await self._adapter.api.upload_asset(file_url=file, file_path=filename)
                        if upload_result["retcode"] != 0:
                            return upload_result
                        url = upload_result["data"]["url"]
                    else:
                        # 本地文件路径，需要上传
                        upload_result = await self._adapter.api.upload_asset(file_path=file)
                        if upload_result["retcode"] != 0:
                            return upload_result
                        url = upload_result["data"]["url"]
                else:
                    return self._error_response("不支持的文件类型")
                
                # 发送文件消息
                return await self._adapter.call_api(
                    endpoint="/message/create",
                    target_type=self._target_type,
                    target_id=self._target_id,
                    content=url,
                    type=4,
                    **self._build_modifiers()
                )
            
            return asyncio.create_task(_send())

        def Voice(self, file):
            import asyncio
            
            async def _send():
                # 判断输入类型
                if isinstance(file, bytes):
                    # 二进制数据，需要上传
                    upload_result = await self._adapter.api.upload_asset(file=file)
                    if upload_result["retcode"] != 0:
                        return upload_result
                    url = upload_result["data"]["url"]
                elif isinstance(file, str):
                    if file.startswith(("http://", "https://")):
                        # URL，需要上传到Kook服务器
                        upload_result = await self._adapter.api.upload_asset(file_url=file)
                        if upload_result["retcode"] != 0:
                            return upload_result
                        url = upload_result["data"]["url"]
                    else:
                        # 本地文件路径，需要上传
                        upload_result = await self._adapter.api.upload_asset(file_path=file)
                        if upload_result["retcode"] != 0:
                            return upload_result
                        url = upload_result["data"]["url"]
                else:
                    return self._error_response("不支持的文件类型")
                
                # 发送语音消息
                return await self._adapter.call_api(
                    endpoint="/message/create",
                    target_type=self._target_type,
                    target_id=self._target_id,
                    content=url,
                    type=8,
                    **self._build_modifiers()
                )
            
            return asyncio.create_task(_send())

        def Markdown(self, text: str):
            """发送 KMarkdown 消息"""
            import asyncio
            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/message/create",
                    target_type=self._target_type,
                    target_id=self._target_id,
                    content=text,
                    type=9,
                    **self._build_modifiers()
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
                    type=10,
                    **self._build_modifiers()
                )
            )

        def Raw_ob12(self, message):
            import asyncio
            
            async def _send():
                # 统一转换为列表
                if isinstance(message, dict):
                    segments = [message]
                else:
                    segments = message
                
                # 处理每个消息段
                results = []
                for segment in segments:
                    seg_type = segment.get("type")
                    seg_data = segment.get("data", {})
                    
                    if seg_type == "text":
                        result = await self.Text(seg_data.get("text", ""))
                    elif seg_type == "image":
                        result = await self.Image(seg_data.get("file") or seg_data.get("url", ""))
                    elif seg_type == "video":
                        result = await self.Video(seg_data.get("file") or seg_data.get("url", ""))
                    elif seg_type == "file":
                        result = await self.File(seg_data.get("file") or seg_data.get("url", ""))
                    elif seg_type == "record":
                        result = await self.Voice(seg_data.get("file") or seg_data.get("url", ""))
                    elif seg_type == "mention":
                        self._at_user_ids.append(seg_data.get("user_id"))
                        continue  # 不立即发送，等待文本消息
                    elif seg_type == "mention_all":
                        self._at_all = True
                        continue
                    elif seg_type == "reply":
                        self._reply_message_id = seg_data.get("message_id")
                        continue
                    else:
                        self._adapter.logger.warning(f"不支持的消息段类型: {seg_type}")
                        continue
                    
                    results.append(result)
                
                # 返回最后一个结果
                return results[-1] if results else None
            
            return asyncio.create_task(_send())

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
