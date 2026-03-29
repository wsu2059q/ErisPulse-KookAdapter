"""
Kook Event -> OneBot V12
标准协议转换器
"""

import json
import uuid
import time


class KookAdapterConverter:
    def __init__(self):
        from ErisPulse.Core import logger
        self.logger = logger.get_child("KookAdapterConverter")
    
    def convert(self, data):
        d = data.get("d", {})
        extra = d.get("extra", {})
        author = extra.get("author", {})
        kook_type = d.get("type", 0)
        channel_type = d.get("channel_type", "")
        
        onebot_data = {
            "id": str(uuid.uuid4()).replace("-", ""),
            "time": time.time(),
            "type": self._get_message_type(data),
            "detail_type": self._get_detail_type(data),
            "platform": "Kook",
            "self": {
                "platform": "Kook",
                "user_id": ""
            },
            "Kook_raw": data,
            "Kook_raw_type": kook_type,
        }
        
        if onebot_data["type"] == "message":
            onebot_data.update({
                "message_id": d.get("msg_id", ""),
                "user_id": author.get("id", ""),
                "message": self._convert_message_content(data),
                "alt_message": d.get("content", ""),
            })
            
            if channel_type == "PERSON":
                onebot_data["detail_type"] = "private"
            elif channel_type == "GROUP":
                onebot_data["group_id"] = d.get("target_id", "")
                # Kook 平台频道消息需要使用频道ID作为发送目标
                onebot_data["channel_id"] = d.get("target_id", "")
                
            mentions = extra.get("mention", [])
            if mentions:
                onebot_data["mentions"] = mentions
                
        elif onebot_data["type"] == "notice":
            onebot_data["user_id"] = author.get("id", "")
            onebot_data["group_id"] = extra.get("body", {}).get("channel_id", "")
            onebot_data.update(self._convert_notice_data(data))
        
        self.logger.debug(f"Convert Output: {onebot_data}")
        return onebot_data
    
    def _get_message_type(self, data):
        kook_type = data.get("d", {}).get("type", 0)
        return {
            255: "notice"
        }.get(kook_type, "message")
    
    def _get_detail_type(self, data):
        """获取 OneBot12 标准 detail_type
        
        根据 OneBot12 标准：
        - detail_type 表示消息场景：private(私聊) 或 group(群组)
        - 不是消息内容的格式(text/image/kmarkdown等)
        """
        d = data.get("d", {})
        kook_type = d.get("type", 0)
        channel_type = d.get("channel_type", "")
        
        # Notice 事件使用 Kook 的原始事件类型
        if kook_type == 255:
            return data.get("d", {}).get("extra", {}).get("type", "")
        
        # 根据 channel_type 判断是群组消息还是私聊消息
        # OneBot12 标准：group = 群组/频道消息, private = 私聊消息
        if channel_type == "PERSON":
            return "private"
        elif channel_type == "GROUP":
            return "group"
        else:
            return "unknown"
    
    def _convert_message_content(self, data):
        d = data.get("d", {})
        extra = d.get("extra", {})
        kook_type = d.get("type", 0)
        content = d.get("content", "")
        
        message_segments = []
        
        if kook_type == 1:
            message_segments.append({"type": "text", "data": {"text": content}})
        elif kook_type == 2:
            attachments = extra.get("attachments", {})
            url = attachments.get("url", content)
            message_segments.append({"type": "image", "data": {"file": url, "url": url}})
        elif kook_type == 3:
            attachments = extra.get("attachments", {})
            url = attachments.get("url", content)
            message_segments.append({"type": "video", "data": {"file": url, "url": url}})
        elif kook_type == 4:
            attachments = extra.get("attachments", {})
            url = attachments.get("url", content)
            message_segments.append({"type": "file", "data": {"file": url, "url": url}})
        elif kook_type == 8:
            attachments = extra.get("attachments", {})
            url = attachments.get("url", content)
            message_segments.append({"type": "record", "data": {"file": url, "url": url}})
        elif kook_type == 9:
            kmarkdown = extra.get("kmarkdown", {})
            raw_content = kmarkdown.get("raw_content", content)
            message_segments.append({"type": "text", "data": {"text": raw_content}})
        elif kook_type == 10:
            message_segments.append({"type": "json", "data": {"data": content}})
        else:
            message_segments.append({"type": "text", "data": {"text": content}})
        
        mention = extra.get("mention", [])
        if mention:
            for user_id in mention:
                message_segments.insert(0, {"type": "mention", "data": {"user_id": user_id}})
        
        if extra.get("mention_all", False):
            message_segments.insert(0, {"type": "mention_all", "data": {}})
        
        return message_segments
    
    def _convert_notice_data(self, data):
        d = data.get("d", {})
        extra = d.get("extra", {})
        event_type = extra.get("type", "")
        body = extra.get("body", {})
        
        notice_data = {}
        
        # 映射 Kook 事件类型到标准 notice_type
        if event_type == "added_reaction":
            notice_data["detail_type"] = "group"
            notice_data["sub_type"] = "added_reaction"
            notice_data["group_id"] = body.get("channel_id", "")
            notice_data["message_id"] = body.get("msg_id", "")
            notice_data["user_id"] = body.get("user_id", "")
            notice_data["emoji"] = body.get("emoji", {})
        elif event_type == "deleted_reaction":
            notice_data["detail_type"] = "group"
            notice_data["sub_type"] = "deleted_reaction"
            notice_data["group_id"] = body.get("channel_id", "")
            notice_data["message_id"] = body.get("msg_id", "")
            notice_data["user_id"] = body.get("user_id", "")
            notice_data["emoji"] = body.get("emoji", {})
        elif event_type == "private_added_reaction":
            notice_data["detail_type"] = "private"
            notice_data["sub_type"] = "private_added_reaction"
            notice_data["message_id"] = body.get("msg_id", "")
            notice_data["user_id"] = body.get("user_id", "")
            notice_data["emoji"] = body.get("emoji", {})
        elif event_type == "private_deleted_reaction":
            notice_data["detail_type"] = "private"
            notice_data["sub_type"] = "private_deleted_reaction"
            notice_data["message_id"] = body.get("msg_id", "")
            notice_data["user_id"] = body.get("user_id", "")
            notice_data["emoji"] = body.get("emoji", {})
        elif event_type == "updated_private_message":
            notice_data["detail_type"] = "private"
            notice_data["sub_type"] = "updated_private_message"
            notice_data["message_id"] = body.get("msg_id", "")
            notice_data["user_id"] = body.get("author_id", "")
            notice_data["content"] = body.get("content", "")
        elif event_type == "deleted_private_message":
            notice_data["detail_type"] = "private"
            notice_data["sub_type"] = "deleted_private_message"
            notice_data["message_id"] = body.get("msg_id", "")
            notice_data["user_id"] = body.get("author_id", "")
        else:
            notice_data["notice_type"] = "system"
            notice_data["sub_type"] = event_type if event_type else "system"
            notice_data["raw_event"] = extra

        return notice_data
