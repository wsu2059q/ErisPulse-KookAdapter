import aiohttp
import uuid

class CallApi:
    def __init__(self, token: str):
        self.token = token
        self.session = aiohttp.ClientSession()
    
    async def close(self):
        if self.session.closed:
            return
        await self.session.close()

    async def start(self):
        if not self.session.closed:
            return
        await self.session.start()

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
                "code": -1,
                "msg": "token未刷新, 请刷新后重试",
                "data": {}
            }
        
        # DEBUG: 打印接收到的参数
        print(f"[DEBUG send_message] target_id={target_id}, type={type}, content={content[:50] if content else 'None'}...")
        print(f"[DEBUG send_message] quote={quote}, template_id={template_id}")
        print(f"[DEBUG send_message] kwargs={kwargs}")
        
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
        
        # DEBUG: 打印最终 payload
        print(f"[DEBUG send_message] final payload={payload}")
        
        async with self.session.post(
            "https://www.kookapp.cn/api/v3/message/create",
            json=payload,
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json"
            }
        ) as resp:
            data = await resp.json()
            return data

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
                "code": -1,
                "msg": "token未刷新, 请刷新后重试",
                "data": {}
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
            return data

    async def update_direct_message(
        self,
        msg_id: str,
        content: str,
        quote: str = None,
        template_id: str = None,
        **kwargs
    ) -> dict:
        """更新私信消息（仅支持 KMarkdown type=9 和 CardMessage type=10）"""
        if not self.token:
            return {
                "code": -1,
                "msg": "token未刷新, 请刷新后重试",
                "data": {}
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
            return data

    async def delete_direct_message(
        self,
        msg_id: str,
        **kwargs
    ) -> dict:
        """删除私信消息"""
        if not self.token:
            return {
                "code": -1,
                "msg": "token未刷新, 请刷新后重试",
                "data": {}
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
            return data

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
                "code": -1,
                "msg": "token未刷新, 请刷新后重试",
                "data": {}
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
            return data

    async def delete_channel_message(
        self,
        msg_id: str,
        **kwargs
    ) -> dict:
        """删除频道消息"""
        if not self.token:
            return {
                "code": -1,
                "msg": "token未刷新, 请刷新后重试",
                "data": {}
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
            return data

    async def upload_file(self, file_path: str) -> dict:
        if not self.token:
            return {
                "code": -1,
                "msg": "token未刷新, 请刷新后重试",
                "data": {}
            }
        
        async with self.session.post(
            "https://www.kookapp.cn/api/v3/asset/create",
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "multipart/form-data"
            },
            data={
                "file": open(file_path, "rb")
            }
        ) as resp:
            data = await resp.json()
            return data
        
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