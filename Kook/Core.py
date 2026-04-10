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

        self.bot_id = self.config.get("bot_id", "") or ""
        if not self.bot_id:
            token = self.config.get("token", "")
            parts = token.replace("Bot ", "").split("/")
            self.bot_id = parts[-1] if parts else ""
        self.converter.set_bot_id(self.bot_id)
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
                async with websockets.connect(url, ping_interval=None) as websocket:
                    result = await self._wait_server_hello(websocket)
                    if not result:
                        self.logger.error("KookAdapter 启动失败, 连接关闭, 5秒后重试")
                        await asyncio.sleep(5)
                        continue

                    self.websocket = websocket
                    self.logger.info("KookAdapter 连接成功，开始处理消息")

                    await self.adapter.emit(
                        {
                            "type": "meta",
                            "detail_type": "connect",
                            "platform": "kook",
                            "self": {
                                "platform": "kook",
                                "user_id": self.bot_id,
                            },
                        }
                    )

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
                resume_payload = {"s": 4, "sn": self.sn}
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
            self._heartbeat_task, self._receive_task, return_exceptions=True
        )

        await self.adapter.emit(
            {
                "type": "meta",
                "detail_type": "disconnect",
                "platform": "kook",
                "self": {"platform": "kook", "user_id": self.bot_id},
            }
        )

    async def _wait_server_hello(self, websocket):
        """等待服务器发送hello消息"""
        try:
            message = await asyncio.wait_for(websocket.recv(), timeout=6)
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
                    self.logger.error(
                        f"收到RECONNECT[5]信令，连接已失效，code={code}, err={err}"
                    )
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

    async def _send_heartbeat(self):
        """发送心跳"""
        while self._running and self.websocket:
            try:
                payload = {"s": 2, "sn": self.sn}
                await self.websocket.send(json.dumps(payload).encode("utf-8"))
                self.logger.debug(f"发送心跳 PING[2]，sn={self.sn}")
                await self.adapter.emit(
                    {
                        "type": "meta",
                        "detail_type": "heartbeat",
                        "platform": "kook",
                        "self": {
                            "platform": "kook",
                            "user_id": self.bot_id,
                        },
                    }
                )
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
                self.logger.warning(
                    f"消息序号不连续，进入暂存模式。期望sn={expected_sn}，实际sn={msg_sn}"
                )
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

            self.logger.debug(
                f"事件已分发: {onebot_event.get('type')} - {onebot_event.get('detail_type')}"
            )

        except Exception as e:
            self.logger.error(f"事件处理异常: {e}, 原始数据: {data}")

    def _get_config(self):
        return self.config_manager.getConfig("KookAdapter")

    def _get_default_config(self):
        return {"token": "", "bot_id": "", "compress": True}

    def _check_valid_config(self) -> bool | str:
        if self.config is None:
            return False, "KookAdapter 无配置项, 请添加后重试"
        if self.config.get("token") is None or self.config.get("token") == "":
            return False, "KookAdapter 无token配置项, 请添加后重试"
        if not self.config.get("bot_id"):
            self.logger.warning("KookAdapter 未配置 bot_id，将使用 token 前缀作为标识，建议在配置中设置 bot_id")
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
        _KOOK_MSG_TYPE_MAP = {
            "text": 1,
            "image": 2,
            "video": 3,
            "file": 4,
            "audio": 8,
            "record": 8,
            "markdown": 9,
            "kook_card": 10,
        }

        def __init__(self, adapter, target_type=None, target_id=None, account_id=None):
            super().__init__(adapter, target_type, target_id, account_id)
            self._at_user_ids = []
            self._reply_message_id = None
            self._at_all = False

        def _build_modifiers(self) -> dict:
            modifiers = {}
            if self._at_all:
                modifiers["mention_all"] = True
            if self._at_user_ids:
                modifiers["mention"] = self._at_user_ids
            if self._reply_message_id:
                modifiers["quote"] = self._reply_message_id
            return modifiers

        def _error_response(self, message: str, retcode: int = -1) -> dict:
            return {
                "status": "failed",
                "retcode": retcode,
                "data": None,
                "message_id": "",
                "message": message,
                "kook_raw": None,
            }

        def _reset_modifiers(self):
            self._at_user_ids = []
            self._reply_message_id = None
            self._at_all = False

        def At(self, user_id: str) -> "KookAdapter.Send":
            self._at_user_ids.append(user_id)
            return self

        def AtAll(self) -> "KookAdapter.Send":
            self._at_all = True
            return self

        def Reply(self, message_id: str) -> "KookAdapter.Send":
            self._reply_message_id = message_id
            return self

        def Text(self, text: str):
            return self.Raw_ob12([{"type": "text", "data": {"text": text}}])

        def Image(self, file):
            return self.Raw_ob12([{"type": "image", "data": {"file": file}}])

        def Video(self, file):
            return self.Raw_ob12([{"type": "video", "data": {"file": file}}])

        def File(self, file, filename=None):
            return self.Raw_ob12(
                [{"type": "file", "data": {"file": file, "filename": filename}}]
            )

        def Voice(self, file):
            return self.Raw_ob12([{"type": "audio", "data": {"file": file}}])

        def Markdown(self, text: str):
            return self.Raw_ob12([{"type": "markdown", "data": {"markdown": text}}])

        def Card(self, card_data: dict):
            return self.Raw_ob12([{"type": "kook_card", "data": {"card": card_data}}])

        async def _upload_file(self, file, filename=None):
            if isinstance(file, bytes):
                upload_result = await self._adapter.api.upload_asset(
                    file=file, file_path=filename
                )
            elif isinstance(file, str):
                if file.startswith(("http://", "https://")):
                    upload_result = await self._adapter.api.upload_asset(
                        file_url=file, file_path=filename
                    )
                else:
                    upload_result = await self._adapter.api.upload_asset(file_path=file)
            else:
                return None
            if upload_result["retcode"] != 0:
                return None
            return upload_result["data"]["url"]

        def Raw_ob12(self, message):
            import asyncio

            async def _send():
                if isinstance(message, dict):
                    segments = [message]
                else:
                    segments = message

                modifiers = self._build_modifiers()
                results = []

                for segment in segments:
                    seg_type = segment.get("type")
                    seg_data = segment.get("data", {})

                    if seg_type == "mention":
                        modifiers.setdefault("mention", [])
                        if seg_data.get("user_id") not in modifiers["mention"]:
                            modifiers["mention"].append(seg_data["user_id"])
                        continue
                    elif seg_type == "mention_all":
                        modifiers["mention_all"] = True
                        continue
                    elif seg_type == "reply":
                        modifiers["quote"] = seg_data.get("message_id")
                        continue

                    kook_type = self._KOOK_MSG_TYPE_MAP.get(seg_type)
                    if kook_type is None:
                        self._adapter.logger.warning(f"不支持的消息段类型: {seg_type}")
                        continue

                    if seg_type in ("text", "markdown"):
                        content = seg_data.get("text") or seg_data.get("markdown", "")
                        if kook_type == 10:
                            import json

                            content = json.dumps(seg_data.get("card", {}))
                    elif seg_type == "kook_card":
                        import json

                        content = json.dumps(seg_data.get("card", {}))
                        kook_type = 10
                    elif seg_type in ("image", "video", "file", "audio", "record"):
                        file_source = seg_data.get("file") or seg_data.get("url", "")
                        filename = seg_data.get("filename")
                        url = await self._upload_file(file_source, filename)
                        if url is None:
                            results.append(
                                self._error_response(f"文件上传失败: {seg_type}")
                            )
                            continue
                        content = url
                    else:
                        content = str(seg_data)

                    result = await self._adapter.call_api(
                        endpoint="/message/create",
                        target_type=self._target_type,
                        target_id=self._target_id,
                        content=content,
                        type=kook_type,
                        **modifiers,
                    )
                    results.append(result)

                self._reset_modifiers()
                return (
                    results[-1]
                    if results
                    else self._error_response("没有可发送的消息段")
                )

            return asyncio.create_task(_send())

        def Edit(self, msg_id: str, content: str):
            """编辑消息（仅支持 KMarkdown 和 CardMessage）"""
            import asyncio

            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/message/update",
                    target_type=self._target_type,
                    msg_id=msg_id,
                    content=content,
                )
            )

        def Recall(self, msg_id: str):
            """撤回消息"""
            import asyncio

            return asyncio.create_task(
                self._adapter.call_api(
                    endpoint="/message/delete",
                    target_type=self._target_type,
                    msg_id=msg_id,
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
                self._adapter.call_api(endpoint="/asset/create", file_path=file_path)
            )
