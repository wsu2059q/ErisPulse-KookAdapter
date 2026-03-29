import aiohttp
import uuid
import os

class CallApi:
    def __init__(self, token: str):
        self.token = token
        self.session = aiohttp.ClientSession()
        
        from ErisPulse.Core import logger
        self.logger = logger.get_child("KookApi")
    
    async def close(self):
        if self.session.closed:
            return
        await self.session.close()

    async def start(self):
        if not self.session.closed:
            return
        await self.session.start()

    def _standardize_response(self, raw_response: dict, message_id: str = "") -> dict:
        code = raw_response.get("code", -1)
        msg = raw_response.get("message", "")
        
        if code == 0:
            # 成功
            raw_data = raw_response.get("data", {})
            kook_msg_id = message_id or raw_data.get("msg_id", "")
            standardized_data = dict(raw_data)
            if "message_id" not in standardized_data and "msg_id" in standardized_data:
                standardized_data["message_id"] = standardized_data["msg_id"]
            
            return {
                "status": "ok",
                "retcode": 0,
                "data": standardized_data,
                "message_id": kook_msg_id,
                "message": msg or "操作成功",
                "kook_raw": raw_response
            }
        else:
            # 失败
            return {
                "status": "failed",
                "retcode": code,
                "data": None,
                "message_id": "",
                "message": msg or f"操作失败 (code={code})",
                "kook_raw": raw_response
            }

    async def send_message(
        self,
        target_id: str,
        type: int,
        content: str,
        quote: str = None,
        template_id: str = None,
        **kwargs
    ) -> dict:
        """发送频道消息"""
        if not self.token:
            return {
                "status": "failed",
                "retcode": -1,
                "data": None,
                "message_id": "",
                "message": "token未刷新, 请刷新后重试",
                "kook_raw": None
            }
        
        self.logger.debug(f"send_message: target_id={target_id}, type={type}, content={content[:50] if content else 'None'}...")
        
        nonce = str(uuid.uuid4())
        payload = {
            "nonce": nonce,
            "target_id": target_id,
            "type": type,
            "content": content,
        }
        if quote:
            payload["quote"] = quote
        if template_id:
            payload["template_id"] = template_id
        
        # 添加其他可选参数（如 mention, mention_all 等）
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value
        
        self.logger.debug(f"send_message payload: {payload}")
        
        async with self.session.post(
            "https://www.kookapp.cn/api/v3/message/create",
            json=payload,
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json"
            }
        ) as resp:
            data = await resp.json()
            message_id = data.get("data", {}).get("msg_id", "")
            return self._standardize_response(data, message_id)

    async def send_direct_message(
        self,
        target_id: str,
        type: int,
        content: str,
        quote: str = None,
        template_id: str = None,
        **kwargs
    ) -> dict:
        """发送私信消息"""
        if not self.token:
            return {
                "status": "failed",
                "retcode": -1,
                "data": None,
                "message_id": "",
                "message": "token未刷新, 请刷新后重试",
                "kook_raw": None
            }
        
        nonce = str(uuid.uuid4())
        payload = {
            "nonce": nonce,
            "target_id": target_id,
            "type": type,
            "content": content,
        }
        if quote:
            payload["quote"] = quote
        if template_id:
            payload["template_id"] = template_id
        
        # 添加其他可选参数（如 reply_msg_id 等）
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value
        
        async with self.session.post(
            "https://www.kookapp.cn/api/v3/direct-message/create",
            json=payload,
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json"
            }
        ) as resp:
            data = await resp.json()
            message_id = data.get("data", {}).get("msg_id", "")
            return self._standardize_response(data, message_id)

    async def update_direct_message(
        self,
        msg_id: str,
        content: str,
        quote: str = None,
        template_id: str = None,
        **kwargs
    ) -> dict:
        if not self.token:
            return {
                "status": "failed",
                "retcode": -1,
                "data": None,
                "message_id": "",
                "message": "token未刷新, 请刷新后重试",
                "kook_raw": None
            }
        
        payload = {
            "msg_id": msg_id,
            "content": content,
        }
        if quote is not None:
            payload["quote"] = quote
        if template_id:
            payload["template_id"] = template_id
        
        # 添加其他可选参数
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value
        
        async with self.session.post(
            "https://www.kookapp.cn/api/v3/direct-message/update",
            json=payload,
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json"
            }
        ) as resp:
            data = await resp.json()
            return self._standardize_response(data, message_id=msg_id)

    async def delete_direct_message(
        self,
        msg_id: str,
        **kwargs
    ) -> dict:
        """删除私信消息"""
        if not self.token:
            return {
                "status": "failed",
                "retcode": -1,
                "data": None,
                "message_id": "",
                "message": "token未刷新, 请刷新后重试",
                "kook_raw": None
            }
        
        payload = {
            "msg_id": msg_id,
        }
        
        # 添加其他可选参数
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value
        
        async with self.session.post(
            "https://www.kookapp.cn/api/v3/direct-message/delete",
            json=payload,
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json"
            }
        ) as resp:
            data = await resp.json()
            return self._standardize_response(data, message_id=msg_id)

    async def update_channel_message(
        self,
        msg_id: str,
        content: str,
        quote: str = None,
        temp_target_id: str = None,
        template_id: str = None,
        **kwargs
    ) -> dict:
        """更新频道消息（仅支持 KMarkdown type=9 和 CardMessage type=10）"""
        if not self.token:
            return {
                "status": "failed",
                "retcode": -1,
                "data": None,
                "message_id": "",
                "message": "token未刷新, 请刷新后重试",
                "kook_raw": None
            }
        
        payload = {
            "msg_id": msg_id,
            "content": content,
        }
        if quote is not None:
            payload["quote"] = quote
        if temp_target_id:
            payload["temp_target_id"] = temp_target_id
        if template_id:
            payload["template_id"] = template_id
        
        # 添加其他可选参数
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value
        
        async with self.session.post(
            "https://www.kookapp.cn/api/v3/message/update",
            json=payload,
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json"
            }
        ) as resp:
            data = await resp.json()
            return self._standardize_response(data, message_id=msg_id)

    async def delete_channel_message(
        self,
        msg_id: str,
        **kwargs
    ) -> dict:
        """删除频道消息"""
        if not self.token:
            return {
                "status": "failed",
                "retcode": -1,
                "data": None,
                "message_id": "",
                "message": "token未刷新, 请刷新后重试",
                "kook_raw": None
            }
        
        payload = {
            "msg_id": msg_id,
        }
        
        # 添加其他可选参数
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value
        
        async with self.session.post(
            "https://www.kookapp.cn/api/v3/message/delete",
            json=payload,
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json"
            }
        ) as resp:
            data = await resp.json()
            return self._standardize_response(data, message_id=msg_id)

    async def upload_asset(self, file=None, file_path=None, file_url=None) -> dict:
        if not self.token:
            return {
                "status": "failed",
                "retcode": -1,
                "data": None,
                "message_id": "",
                "message": "token未刷新, 请刷新后重试",
                "kook_raw": None
            }
        
        # 如果是 URL，先下载文件内容
        if file_url:
            self.logger.debug(f"从URL下载文件: {file_url}")
            try:
                async with self.session.get(file_url) as resp:
                    if resp.status != 200:
                        return {
                            "status": "failed",
                            "retcode": -1,
                            "data": None,
                            "message_id": "",
                            "message": f"下载URL失败: HTTP {resp.status}",
                            "kook_raw": None
                        }
                    file = await resp.read()
            except Exception as e:
                self.logger.error(f"下载URL失败: {e}")
                return {
                    "status": "failed",
                    "retcode": -1,
                    "data": None,
                    "message_id": "",
                    "message": f"下载URL失败: {e}",
                    "kook_raw": None
                }
        
        # 如果是本地文件路径，读取文件
        if file_path:
            if not os.path.exists(file_path):
                return {
                    "status": "failed",
                    "retcode": -1,
                    "data": None,
                    "message_id": "",
                    "message": f"文件不存在: {file_path}",
                    "kook_raw": None
                }
            try:
                with open(file_path, "rb") as f:
                    file_data = f.read()
                file = file_data
            except Exception as e:
                return {
                    "status": "failed",
                    "retcode": -1,
                    "data": None,
                    "message_id": "",
                    "message": f"读取文件失败: {e}",
                    "kook_raw": None
                }
        
        # 如果是二进制数据，直接上传
        if file is None:
            return {
                "status": "failed",
                "retcode": -1,
                "data": None,
                "message_id": "",
                "message": "缺少文件数据",
                "kook_raw": None
            }
        
        try:
            # 使用 aiohttp 上传文件
            from aiohttp import FormData
            import mimetypes
            
            # 根据文件路径或URL推断文件名
            filename = self._get_filename(file_path, file_url)
            self.logger.debug(f"上传文件到Kook服务器: {filename}, 文件大小: {len(file)} bytes")
            
            # 根据文件名推断Content-Type和file_type
            content_type, _ = mimetypes.guess_type(filename)
            file_type = self._get_file_type(filename)
            self.logger.debug(f"文件名: {filename}, Content-Type: {content_type}, file_type: {file_type}")
            
            form = FormData()
            form.add_field('file', file, filename=filename, content_type=content_type)
            # 添加file_type参数
            form.add_field('file_type', file_type)
            
            async with self.session.post(
                "https://www.kookapp.cn/api/v3/asset/create",
                headers={
                    "Authorization": f"Bot {self.token}"
                },
                data=form
            ) as resp:
                self.logger.debug(f"上传响应状态码: {resp.status}")
                data = await resp.json()
                self.logger.debug(f"上传响应数据: {data}")
                result = self._standardize_response(data)
                self.logger.debug(f"标准化上传结果: {result}")
                return result
        except Exception as e:
            self.logger.error(f"上传文件失败: {e}")
            return {
                "status": "failed",
                "retcode": -1,
                "data": None,
                "message_id": "",
                "message": f"上传文件失败: {e}",
                "kook_raw": None
            }
    
    def _get_file_type(self, filename):
        """根据文件名推断Kook API的file_type参数"""
        if not filename:
            return "file"
        
        filename_lower = filename.lower()
        if filename_lower.endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')):
            return "image"
        elif filename_lower.endswith(('.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv')):
            return "video"
        elif filename_lower.endswith(('.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac')):
            return "audio"
        else:
            return "file"
    
    def _get_filename(self, file_path=None, file_url=None):
        """根据文件路径或URL推断文件名和扩展名"""
        if file_path:
            return os.path.basename(file_path)
        elif file_url:
            # 从URL中提取文件名
            from urllib.parse import urlparse, unquote
            parsed = urlparse(file_url)
            filename = unquote(os.path.basename(parsed.path))
            if filename:
                return filename
        # 默认文件名
        return "upload.bin"
        
    async def get_ws_gateway(self, need_compress: bool = True) -> str:
        if not self.token:
            return ""
        async with self.session.post(
            "https://www.kookapp.cn/api/v3/gateway/index",
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json"
            },
            json={
                "need_compress": 1 if need_compress else 0
            }
        ) as resp:
            data = await resp.json()
            if data.get("code") == 0:
                return data.get("data", {}).get("url", "")
            return ""