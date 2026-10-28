"""AstrBot v4.29.1 / aiocqhttp(OneBot v11) 的投递发送适配层。

只把已保存 DeliveryMessage 路由到实际存在的 aiocqhttp 平台实例；不注册后台
入口、不调度、不启用任务，也不在插件启动时自动装配。其他适配器明确拒绝。
"""

from __future__ import annotations

import asyncio
from typing import Any

from .delivery import DeliveryMessage, DeliverySendResult, SendStatus

MAX_TEXT_BYTES = 1 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class AstrBotAiocqhttpSender:
    """通过真实 Context / MessageSession / aiocqhttp 路由发送一条受控消息。"""

    def __init__(self, context: Any) -> None:
        self._context = context

    async def send(self, message: DeliveryMessage) -> DeliverySendResult:
        try:
            session, platform_id = self._parse_session(message.umo)
        except ValueError:
            return DeliverySendResult(
                SendStatus.FAILED, "sender_invalid_payload",
            )
        try:
            platform = self._context.get_platform_inst(platform_id)
        except Exception:
            return DeliverySendResult(
                SendStatus.FAILED, "sender_route_rejected",
            )
        if platform is None:
            return DeliverySendResult(
                SendStatus.FAILED, "sender_not_found",
            )
        if not self._is_supported(platform):
            return DeliverySendResult(
                SendStatus.FAILED, "sender_not_supported",
            )
        try:
            chain = self._build_chain(message, session)
        except ValueError:
            return DeliverySendResult(
                SendStatus.FAILED, "sender_invalid_payload",
            )
        try:
            found = await self._context.send_message(session, chain)
        except asyncio.CancelledError:
            raise
        except Exception:
            return DeliverySendResult(
                SendStatus.UNKNOWN, "sender_exception",
            )
        if found is True:
            return DeliverySendResult(SendStatus.SUCCESS)
        if found is False:
            return DeliverySendResult(
                SendStatus.FAILED, "sender_route_rejected",
            )
        return DeliverySendResult(SendStatus.UNKNOWN, "sender_invalid_result")

    @staticmethod
    def _parse_session(umo: object):
        from astrbot.core.platform.message_session import MessageSession
        from astrbot.core.platform.message_type import MessageType

        if not isinstance(umo, str):
            raise ValueError("invalid umo")
        try:
            session = MessageSession.from_str(umo)
        except Exception as exc:
            raise ValueError("invalid umo") from exc
        if session.message_type not in (
            MessageType.GROUP_MESSAGE,
            MessageType.FRIEND_MESSAGE,
        ):
            raise ValueError("unsupported message type")
        return session, session.platform_id

    @staticmethod
    def _is_supported(platform: object) -> bool:
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
            AiocqhttpAdapter,
        )

        return isinstance(platform, AiocqhttpAdapter)

    @staticmethod
    def _build_chain(message: DeliveryMessage, session: object):
        from astrbot.core.message.components import Image, Plain
        from astrbot.core.message.message_event_result import MessageChain

        if session.message_type.value == "GroupMessage":
            target = session.session_id.rsplit("_", 1)[-1]
        else:
            target = session.session_id
        if not target or not all("0" <= char <= "9" for char in target):
            raise ValueError("invalid numeric target")
        if message.kind in ("text", "markdown"):
            if (
                not isinstance(message.text, str)
                or not message.text.strip()
                or len(message.text.encode("utf-8")) > MAX_TEXT_BYTES
            ):
                raise ValueError("invalid text")
            return MessageChain([Plain(message.text)])
        if message.kind == "image":
            png = message.image_png
            if (
                not isinstance(png, bytes)
                or not png
                or len(png) > MAX_IMAGE_BYTES
                or not png.startswith(PNG_SIGNATURE)
            ):
                raise ValueError("invalid image")
            from io import BytesIO

            from PIL import Image as PILImage

            try:
                with PILImage.open(BytesIO(png)) as image:
                    image.verify()
            except Exception as exc:
                raise ValueError("invalid image") from exc
            return MessageChain([Image.fromBytes(png)])
        raise ValueError("unsupported kind")
