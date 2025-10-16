import re
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import Reply
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)


@register(
    "astrbot_plugin_human_service",
    "Zhalslar&dongyue",
    "人工客服插件 - 支持智能排队",
    "1.4.0",
    "https://github.com/Zhalslar/astrbot_plugin_human_service",
)
class HumanServicePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # 客服QQ号列表
        self.servicers_id: list[str] = config.get("servicers_id", [])
        if not self.servicers_id:
            # 默认使用管理员作为客服
            for admin_id in context.get_config()["admins_id"]:
                if admin_id.isdigit():
                    self.servicers_id.append(admin_id)
        
        self.enable_servicer_selection = config.get("enable_servicer_selection", True)
        self.enable_chat_history = config.get("enable_chat_history", False)
        self.session_map = {}
        # 用户选择客服的临时状态
        self.selection_map = {}
        # 聊天记录：{user_id: [{"sender": "user/servicer", "name": "xxx", "message": "xxx", "time": "xxx"}]}
        self.chat_history = {}
        # 客服队列：{servicer_id: [{"user_id": "xxx", "name": "xxx", "group_id": "xxx", "time": timestamp}]}
        self.servicer_queue = {sid: [] for sid in self.servicers_id}
    
    def is_servicer_busy(self, servicer_id: str) -> bool:
        """检查客服是否正在服务中"""
        for session in self.session_map.values():
            if session.get("servicer_id") == servicer_id and session.get("status") == "connected":
                return True
        return False
    
    def add_to_queue(self, servicer_id: str, user_id: str, user_name: str, group_id: str):
        """将用户添加到客服队列"""
        import time
        if servicer_id not in self.servicer_queue:
            self.servicer_queue[servicer_id] = []
        
        # 检查用户是否已在队列中
        for item in self.servicer_queue[servicer_id]:
            if item["user_id"] == user_id:
                return False
        
        self.servicer_queue[servicer_id].append({
            "user_id": user_id,
            "name": user_name,
            "group_id": group_id,
            "time": time.time()
        })
        return True
    
    def get_queue_position(self, servicer_id: str, user_id: str) -> int:
        """获取用户在队列中的位置（从1开始）"""
        if servicer_id not in self.servicer_queue:
            return -1
        for i, item in enumerate(self.servicer_queue[servicer_id]):
            if item["user_id"] == user_id:
                return i + 1
        return -1
    
    def remove_from_queue(self, user_id: str) -> bool:
        """从所有队列中移除用户"""
        removed = False
        for servicer_id in self.servicer_queue:
            self.servicer_queue[servicer_id] = [
                item for item in self.servicer_queue[servicer_id] 
                if item["user_id"] != user_id
            ]
            if len(self.servicer_queue[servicer_id]) != len([item for item in self.servicer_queue[servicer_id] if item["user_id"] != user_id]):
                removed = True
        return removed

    @filter.command("转人工", priority=1)
    async def transfer_to_human(self, event: AiocqhttpMessageEvent):
        sender_id = event.get_sender_id()
        send_name = event.get_sender_name()
        group_id = event.get_group_id() or "0"

        if sender_id in self.session_map:
            yield event.plain_result("⚠ 您已在等待接入或正在对话")
            return
        
        if sender_id in self.selection_map:
            yield event.plain_result("⚠ 您正在选择客服，请先完成选择")
            return
        
        # 检查用户是否已在某个队列中
        for servicer_id in self.servicer_queue:
            if any(item["user_id"] == sender_id for item in self.servicer_queue[servicer_id]):
                position = self.get_queue_position(servicer_id, sender_id)
                yield event.plain_result(f"⚠ 您已在排队中，当前队列位置：第 {position} 位")
                return

        # 如果启用了客服选择且有多个客服
        if self.enable_servicer_selection and len(self.servicers_id) > 1:
            self.selection_map[sender_id] = {
                "status": "selecting",
                "group_id": group_id,
                "name": send_name
            }
            
            # 生成客服列表，显示客服状态
            servicer_list_items = []
            for idx, sid in enumerate(self.servicers_id):
                status = "🔴 忙碌中" if self.is_servicer_busy(sid) else "🟢 空闲"
                queue_count = len(self.servicer_queue.get(sid, []))
                queue_info = f"（排队 {queue_count} 人）" if queue_count > 0 else ""
                servicer_list_items.append(f"{idx + 1}. 客服{idx + 1} {status}{queue_info}")
            
            servicer_list = "\n".join(servicer_list_items)
            
            yield event.plain_result(
                f"请选择要对接的客服（回复序号）：\n{servicer_list}\n\n回复 0 取消请求"
            )
        else:
            # 只有一个客服或未启用选择功能
            target_servicer = self.servicers_id[0] if len(self.servicers_id) == 1 else None
            
            # 检查客服是否忙碌
            if target_servicer and self.is_servicer_busy(target_servicer):
                # 客服忙碌，加入队列
                self.add_to_queue(target_servicer, sender_id, send_name, group_id)
                position = self.get_queue_position(target_servicer, sender_id)
                queue_count = len(self.servicer_queue[target_servicer])
                
                yield event.plain_result(
                    f"客服正在服务中🔴\n"
                    f"您已加入等待队列，当前排队人数：{queue_count}\n"
                    f"您的位置：第 {position} 位\n\n"
                    f"💡 使用 /取消排队 可退出队列"
                )
                
                # 通知客服有人排队
                await self.send(
                    event,
                    message=f"📋 {send_name}({sender_id}) 已加入排队，当前队列：{queue_count} 人",
                    user_id=target_servicer,
                )
            else:
                # 客服空闲，直接等待接入
                self.session_map[sender_id] = {
                    "servicer_id": "",
                    "status": "waiting",
                    "group_id": group_id,
                }
                yield event.plain_result("正在等待客服👤接入...")
                for servicer_id in self.servicers_id:
                    await self.send(
                        event,
                        message=f"{send_name}({sender_id}) 请求转人工",
                        user_id=servicer_id,
                    )

    @filter.command("转人机", priority=1)
    async def transfer_to_bot(self, event: AiocqhttpMessageEvent):
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
        
        # 检查是否在选择客服状态
        if sender_id in self.selection_map:
            del self.selection_map[sender_id]
            yield event.plain_result("已取消客服选择")
            return
        
        # 检查是否在排队中
        removed = self.remove_from_queue(sender_id)
        if removed:
            yield event.plain_result("已退出排队，我现在是人机啦！")
            return
        
        session = self.session_map.get(sender_id)

        if not session:
            yield event.plain_result("⚠ 您当前没有人工服务请求")
            return

        if session["status"] == "waiting":
            # 用户在等待状态取消请求
            del self.session_map[sender_id]
            yield event.plain_result("已取消人工客服请求，我现在是人机啦！")
            # 通知所有客服人员该用户已取消请求
            for servicer_id in self.servicers_id:
                await self.send(
                    event,
                    message=f"❗{sender_name}({sender_id}) 已取消人工请求",
                    user_id=servicer_id,
                )
        elif session["status"] == "connected":
            # 用户在对话中结束会话
            await self.send(
                event,
                message=f"❗{sender_name} 已结束对话",
                user_id=session["servicer_id"],
            )
            del self.session_map[sender_id]
            yield event.plain_result("好的，我现在是人机啦！")
    
    @filter.command("取消排队", priority=1)
    async def cancel_queue(self, event: AiocqhttpMessageEvent):
        sender_id = event.get_sender_id()
        
        removed = self.remove_from_queue(sender_id)
        if removed:
            yield event.plain_result("✅ 已退出排队")
        else:
            yield event.plain_result("⚠ 您当前不在排队中")
    
    @filter.command("排队状态", priority=1)
    async def check_queue_status(self, event: AiocqhttpMessageEvent):
        sender_id = event.get_sender_id()
        
        # 检查用户是否在队列中
        for servicer_id in self.servicer_queue:
            position = self.get_queue_position(servicer_id, sender_id)
            if position > 0:
                queue_count = len(self.servicer_queue[servicer_id])
                yield event.plain_result(
                    f"📋 您的排队信息：\n"
                    f"当前位置：第 {position} 位\n"
                    f"前面还有：{position - 1} 人\n"
                    f"总排队人数：{queue_count} 人"
                )
                return
        
        yield event.plain_result("⚠ 您当前不在排队中")

    @filter.command("接入对话", priority=1)
    async def accept_conversation(
        self, event: AiocqhttpMessageEvent, target_id: str | int | None = None
    ):
        sender_id = event.get_sender_id()
        if sender_id not in self.servicers_id:
            return

        if reply_seg := next(
            (seg for seg in event.get_messages() if isinstance(seg, Reply)), None
        ):
            if text := reply_seg.message_str:
                if match := re.search(r"\((\d+)\)", text):
                    target_id = match.group(1)

        session = self.session_map.get(target_id)

        if not session or session["status"] != "waiting":
            yield event.plain_result(f"用户({target_id})未请求人工")
            return

        if session["status"] == "connected":
            yield event.plain_result("您正在与该用户对话")

        session["status"] = "connected"
        session["servicer_id"] = sender_id
        
        # 初始化聊天记录
        if self.enable_chat_history:
            self.chat_history[target_id] = []

        await self.send(
            event,
            message="客服👤已接入",
            group_id=session["group_id"],
            user_id=target_id,
        )
        
        tips = "好的，接下来我将转发你的消息给对方，请开始对话："
        if self.enable_chat_history:
            tips += "\n💡 提示：可使用 /导出记录 命令导出聊天记录"
        yield event.plain_result(tips)
        event.stop_event()

    @filter.command("拒绝接入", priority=1)
    async def reject_conversation(self, event: AiocqhttpMessageEvent, target_id: str | int | None = None):
        sender_id = event.get_sender_id()
        if sender_id not in self.servicers_id:
            return

        if reply_seg := next(
            (seg for seg in event.get_messages() if isinstance(seg, Reply)), None
        ):
            if text := reply_seg.message_str:
                if match := re.search(r"\((\d+)\)", text):
                    target_id = match.group(1)

        session = self.session_map.get(target_id)

        if not session or session["status"] != "waiting":
            yield event.plain_result(f"用户({target_id})未请求人工或已被接入")
            return

        # 删除会话
        del self.session_map[target_id]
        
        # 通知用户
        await self.send(
            event,
            message="抱歉，客服暂时无法接入，请稍后再试或联系其他客服",
            group_id=session["group_id"],
            user_id=target_id,
        )
        
        yield event.plain_result(f"已拒绝用户 {target_id} 的接入请求")

    @filter.command("导出记录", priority=1)
    async def export_chat_history(self, event: AiocqhttpMessageEvent):
        sender_id = event.get_sender_id()
        if sender_id not in self.servicers_id:
            return
        
        if not self.enable_chat_history:
            yield event.plain_result("⚠ 聊天记录功能未启用")
            return
        
        # 查找当前客服正在服务的用户
        target_user_id = None
        for uid, session in self.session_map.items():
            if session.get("servicer_id") == sender_id and session.get("status") == "connected":
                target_user_id = uid
                break
        
        if not target_user_id:
            yield event.plain_result("⚠ 当前没有正在进行的对话")
            return
        
        history = self.chat_history.get(target_user_id, [])
        if not history:
            yield event.plain_result("⚠ 暂无聊天记录")
            return
        
        # 生成QQ聊天记录格式的转发消息
        from datetime import datetime
        
        forward_messages = []
        for record in history:
            # 构造转发消息节点
            forward_messages.append({
                "type": "node",
                "data": {
                    "name": record["name"],
                    "uin": record["sender_id"],
                    "content": record["message"]
                }
            })
        
        # 发送合并转发消息
        try:
            await event.bot.send_private_forward_msg(
                user_id=int(sender_id),
                messages=forward_messages
            )
            yield event.plain_result(f"✅ 已导出聊天记录（共 {len(history)} 条消息）")
        except Exception as e:
            # 如果合并转发失败，使用文本格式
            text_history = f"📝 聊天记录（共 {len(history)} 条）\n" + "="*30 + "\n\n"
            for record in history:
                text_history += f"[{record['time']}] {record['name']}:\n{record['message']}\n\n"
            
            yield event.plain_result(text_history)

    @filter.command("结束对话")
    async def end_conversation(self, event: AiocqhttpMessageEvent):
        sender_id = event.get_sender_id()
        if sender_id not in self.servicers_id:
            return

        for uid, session in self.session_map.items():
            if session["servicer_id"] == sender_id:
                await self.send(
                    event,
                    message="客服👤已结束对话",
                    group_id=session["group_id"],
                    user_id=uid,
                )
                del self.session_map[uid]
                # 清理聊天记录
                if uid in self.chat_history:
                    del self.chat_history[uid]
                
                # 检查队列中是否有等待的用户
                if sender_id in self.servicer_queue and len(self.servicer_queue[sender_id]) > 0:
                    # 从队列中取出第一个用户
                    next_user = self.servicer_queue[sender_id].pop(0)
                    next_user_id = next_user["user_id"]
                    next_user_name = next_user["name"]
                    next_group_id = next_user["group_id"]
                    
                    # 创建新的会话（等待接入状态）
                    self.session_map[next_user_id] = {
                        "servicer_id": "",
                        "status": "waiting",
                        "group_id": next_group_id,
                        "selected_servicer": sender_id
                    }
                    
                    # 通知用户
                    await self.send(
                        event,
                        message=f"⏰ 轮到您了！客服正在准备接入您的对话...\n客服可以使用 /接入对话 命令开始服务",
                        group_id=next_group_id,
                        user_id=next_user_id,
                    )
                    
                    # 通知客服
                    remaining_queue = len(self.servicer_queue[sender_id])
                    queue_info = f"（队列剩余 {remaining_queue} 人）" if remaining_queue > 0 else "（队列已清空）"
                    
                    yield event.plain_result(
                        f"✅ 已结束与用户 {uid} 的对话\n"
                        f"📋 队列中的下一位用户已准备就绪：\n"
                        f"用户：{next_user_name}({next_user_id})\n"
                        f"请使用 /接入对话 命令（回复用户消息）开始服务\n"
                        f"{queue_info}"
                    )
                else:
                    yield event.plain_result(f"✅ 已结束与用户 {uid} 的对话\n📋 当前队列为空")
                
                return

        yield event.plain_result("当前无对话需要结束")

    async def send(
        self,
        event: AiocqhttpMessageEvent,
        message,
        group_id: int | str | None = None,
        user_id: int | str | None = None,
    ):
        """向用户发消息，兼容群聊或私聊"""
        if group_id and str(group_id) != "0":
            await event.bot.send_group_msg(group_id=int(group_id), message=message)
        elif user_id:
            await event.bot.send_private_msg(user_id=int(user_id), message=message)

    async def send_ob(
        self,
        event: AiocqhttpMessageEvent,
        group_id: int | str | None = None,
        user_id: int | str | None = None,
    ):
        """向用户发onebot格式的消息，兼容群聊或私聊"""
        ob_message = await event._parse_onebot_json(
            MessageChain(chain=event.message_obj.message)
        )
        if group_id and str(group_id) != "0":
            await event.bot.send_group_msg(group_id=int(group_id), message=ob_message)
        elif user_id:
            await event.bot.send_private_msg(user_id=int(user_id), message=ob_message)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_match(self, event: AiocqhttpMessageEvent):
        """监听对话消息转发和客服选择"""
        chain = event.get_messages()
        if not chain or any(isinstance(seg, (Reply)) for seg in chain):
            return
        sender_id = event.get_sender_id()
        message_text = event.message_str.strip()
        
        # 处理用户选择客服
        if sender_id in self.selection_map:
            selection = self.selection_map[sender_id]
            
            # 检查是否是数字选择
            if message_text.isdigit():
                choice = int(message_text)
                
                if choice == 0:
                    # 取消选择
                    del self.selection_map[sender_id]
                    yield event.plain_result("已取消客服选择")
                    event.stop_event()
                    return
                elif 1 <= choice <= len(self.servicers_id):
                    # 选择了有效的客服
                    selected_servicer_id = self.servicers_id[choice - 1]
                    
                    # 删除选择状态
                    del self.selection_map[sender_id]
                    
                    # 检查客服是否忙碌
                    if self.is_servicer_busy(selected_servicer_id):
                        # 客服忙碌，加入队列
                        self.add_to_queue(selected_servicer_id, sender_id, selection['name'], selection["group_id"])
                        position = self.get_queue_position(selected_servicer_id, sender_id)
                        queue_count = len(self.servicer_queue[selected_servicer_id])
                        
                        yield event.plain_result(
                            f"客服{choice}正在服务中🔴\n"
                            f"您已加入等待队列，当前排队人数：{queue_count}\n"
                            f"您的位置：第 {position} 位\n\n"
                            f"💡 使用 /取消排队 可退出队列"
                        )
                        
                        # 通知客服有人排队
                        await self.send(
                            event,
                            message=f"📋 {selection['name']}({sender_id}) 已加入排队（指定客服{choice}），当前队列：{queue_count} 人",
                            user_id=selected_servicer_id,
                        )
                    else:
                        # 客服空闲，创建会话
                        self.session_map[sender_id] = {
                            "servicer_id": "",
                            "status": "waiting",
                            "group_id": selection["group_id"],
                            "selected_servicer": selected_servicer_id
                        }
                        
                        # 通知用户和客服
                        yield event.plain_result(f"正在等待客服{choice}接入...")
                        await self.send(
                            event,
                            message=f"{selection['name']}({sender_id}) 请求转人工（指定客服{choice}）",
                            user_id=selected_servicer_id,
                        )
                    event.stop_event()
                    return
                else:
                    yield event.plain_result(f"⚠ 无效的选择，请输入 1-{len(self.servicers_id)} 或 0 取消")
                    event.stop_event()
                    return
            else:
                yield event.plain_result("⚠ 请输入数字进行选择")
                event.stop_event()
                return
        
        # 客服 → 用户 (仅私聊生效)
        if (
            sender_id in self.servicers_id
            and event.is_private_chat()
            and event.message_str not in ("接入对话", "结束对话", "拒绝接入", "导出记录")
        ):
            for user_id, session in self.session_map.items():
                if (
                    session["servicer_id"] == sender_id
                    and session["status"] == "connected"
                ):
                    # 记录聊天内容
                    if self.enable_chat_history and user_id in self.chat_history:
                        from datetime import datetime
                        self.chat_history[user_id].append({
                            "sender_id": sender_id,
                            "name": f"客服({sender_id})",
                            "message": event.message_str,
                            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        })
                    
                    await self.send_ob(
                        event,
                        group_id=session["group_id"],
                        user_id=user_id,
                    )
                    event.stop_event()
                    break

        # 用户 → 客服
        elif session := self.session_map.get(sender_id):
            if session["status"] == "connected":
                # 记录聊天内容
                if self.enable_chat_history and sender_id in self.chat_history:
                    from datetime import datetime
                    self.chat_history[sender_id].append({
                        "sender_id": sender_id,
                        "name": event.get_sender_name(),
                        "message": event.message_str,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    })
                
                await self.send_ob(
                    event,
                    user_id=session["servicer_id"],
                )
                event.stop_event()
