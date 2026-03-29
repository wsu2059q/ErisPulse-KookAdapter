"""
Kook Event -> OneBot V12
标准协议转换器
"""

import json
import uuid
import time


class KookAdapterConverter:
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
                
            mentions = extra.get("mention", [])
            if mentions:
                onebot_data["mentions"] = mentions
                
        elif onebot_data["type"] == "notice":
            onebot_data["user_id"] = author.get("id", "")
            onebot_data["guild_id"] = extra.get("guild_id", "")
            onebot_data.update(self._convert_notice_data(data))
            
        return onebot_data
    
    def _get_message_type(self, data):
        kook_type = data.get("d", {}).get("type", 0)
        return {
            255: "notice"
        }.get(kook_type, "message")
    
    def _get_detail_type(self, data):
        d = data.get("d", {})
        kook_type = d.get("type", 0)
        if kook_type == 255:
            return data.get("d", {}).get("extra", {}).get("type", "")
        
        return {
            1: "text",
            2: "image",
            3: "video",
            4: "file",
            8: "record",
            9: "kmarkdown",
            10: "json",
        }.get(kook_type, "unknown")
    
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
            notice_data["notice_type"] = "reaction_added"
            notice_data["sub_type"] = "added_reaction"
            notice_data["channel_id"] = body.get("channel_id", "")
            notice_data["message_id"] = body.get("msg_id", "")
            notice_data["user_id"] = body.get("user_id", "")
            notice_data["emoji"] = body.get("emoji", {})
        elif event_type == "deleted_reaction":
            notice_data["notice_type"] = "reaction_removed"
            notice_data["sub_type"] = "deleted_reaction"
            notice_data["channel_id"] = body.get("channel_id", "")
            notice_data["message_id"] = body.get("msg_id", "")
            notice_data["user_id"] = body.get("user_id", "")
            notice_data["emoji"] = body.get("emoji", {})
        elif event_type == "private_added_reaction":
            notice_data["notice_type"] = "reaction_added"
            notice_data["sub_type"] = "private_added_reaction"
            notice_data["message_id"] = body.get("msg_id", "")
            notice_data["user_id"] = body.get("user_id", "")
            notice_data["emoji"] = body.get("emoji", {})
        elif event_type == "private_deleted_reaction":
            notice_data["notice_type"] = "reaction_removed"
            notice_data["sub_type"] = "private_deleted_reaction"
            notice_data["message_id"] = body.get("msg_id", "")
            notice_data["user_id"] = body.get("user_id", "")
            notice_data["emoji"] = body.get("emoji", {})
        elif event_type == "updated_private_message":
            notice_data["notice_type"] = "message_updated"
            notice_data["sub_type"] = "updated_private_message"
            notice_data["message_id"] = body.get("msg_id", "")
            notice_data["user_id"] = body.get("author_id", "")
            notice_data["content"] = body.get("content", "")
        elif event_type == "deleted_private_message":
            notice_data["notice_type"] = "message_deleted"
            notice_data["sub_type"] = "deleted_private_message"
            notice_data["message_id"] = body.get("msg_id", "")
            notice_data["user_id"] = body.get("author_id", "")
        else:
            notice_data["notice_type"] = "system"
            notice_data["sub_type"] = event_type if event_type else "system"
            notice_data["raw_event"] = extra

        return notice_data
