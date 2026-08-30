from __future__ import annotations

import asyncio
import random
import re
from typing import Any

from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder, ToolParameterInfo, ToolParamType


class TypoConfig(PluginConfigBase):
    config_version: str = Field(default="0.1.0", description="配置版本")
    enabled: bool = Field(default=True, description="是否启用概率性自纠错")
    probability: float = Field(default=0.10, description="触发概率", ge=0.0, le=1.0)
    recall_delay_ms: int = Field(default=800, description="错误消息停留时间（毫秒）", ge=200, le=5000)
    max_chars: int = Field(default=120, description="参与自纠错的最大文本长度", ge=1, le=500)


class TypoPluginConfig(PluginConfigBase):
    plugin: TypoConfig = Field(default_factory=TypoConfig)


class TypoCorrectionPlugin(MaiBotPlugin):
    config_model = TypoPluginConfig

    def __init__(self) -> None:
        super().__init__()
        self._busy: set[str] = set()

    async def on_load(self) -> None:
        self.ctx.logger.info("[typo-correction] loaded: enabled=%s probability=%.2f", self.config.plugin.enabled, self.config.plugin.probability)

    async def on_unload(self) -> None:
        self._busy.clear()

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        del scope, config_data, version
        self.ctx.logger.info("[typo-correction] config updated")

    @staticmethod
    def _plain_text(message: dict[str, Any]) -> str:
        return str(message.get("processed_plain_text") or message.get("plain_text") or "").strip()

    @staticmethod
    def _target(message: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        info = message.get("message_info") if isinstance(message.get("message_info"), dict) else {}
        group = info.get("group_info") if isinstance(info.get("group_info"), dict) else {}
        extra = info.get("additional_config") if isinstance(info.get("additional_config"), dict) else {}
        group_id = str(message.get("group_id") or group.get("group_id") or extra.get("platform_io_target_group_id") or "").strip()
        user_id = str(message.get("user_id") or message.get("target_user_id") or extra.get("platform_io_target_user_id") or extra.get("target_user_id") or "").strip()
        if group_id.isdigit():
            return "send_group_msg", {"group_id": int(group_id)}
        if user_id.isdigit():
            return "send_private_msg", {"user_id": int(user_id)}
        return None

    @staticmethod
    def _wrong_text(text: str) -> str:
        replacements = (("已经", "己经"), ("可以", "可依"), ("现在", "再见"), ("知道", "知到"), ("这个", "这 个"), ("的", "得"))
        for old, new in replacements:
            if old in text:
                return text.replace(old, new, 1)
        if text.endswith(("。", "！", "？")):
            return text[:-1] + text[-1] * 2
        return text + "。"

    def _eligible(self, message: dict[str, Any], text: str) -> bool:
        if not self.config.plugin.enabled or not text:
            return False
        if len(text) > int(self.config.plugin.max_chars):
            return False
        if text.startswith(("/", "#")) or text.startswith(("[", "{")):
            return False
        if any(token in text for token in ("http://", "https://", "base64://", "CQ:", "[图片", "[文件")):
            return False
        raw = message.get("raw_message")
        if isinstance(raw, list) and any(isinstance(item, dict) and item.get("type") not in {"text"} for item in raw):
            return False
        return self._target(message) is not None and random.random() < float(self.config.plugin.probability)

    async def _call(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        result = await self.ctx.api.call("adapter.napcat.action.call", action_name=action, params=params)
        return result if isinstance(result, dict) else {"success": bool(result)}

    @staticmethod
    def _extract_message_id(payload: dict[str, Any]) -> Any:
        candidates = [payload]
        for key in ("data", "result"):
            value = payload.get(key)
            if isinstance(value, dict):
                candidates.append(value)
                nested = value.get("data")
                if isinstance(nested, dict):
                    candidates.append(nested)
        for item in candidates:
            value = item.get("message_id")
            if value not in (None, ""):
                return value
        return None

    @Tool(
        "probabilistic_typo_correction",
        description="发送一条文本：先短暂发送轻微错字版本，撤回后再发送正确版本。只处理纯文本，不执行任何系统命令。",
        parameters=[
            ToolParameterInfo(
                name="text",
                param_type=ToolParamType.STRING,
                description="最终要发送的正确文本，1 到 500 字。不要传命令、链接、图片或文件内容。",
                required=True,
            )
        ],
    )
    async def tool_typo_correction(self, text: str = "", **kwargs: Any) -> dict[str, Any]:
        text = str(text or "").strip()
        stream_id = str(kwargs.get("stream_id") or kwargs.get("session_id") or "")
        if not text or len(text) > 500:
            return {"name": "probabilistic_typo_correction", "content": "文本为空或超过 500 字，未发送。"}
        if text.startswith(("/", "#")) or any(token in text for token in ("http://", "https://", "base64://", "CQ:")):
            return {"name": "probabilistic_typo_correction", "content": "只允许发送普通纯文本，命令、链接和消息组件不会通过此工具发送。"}
        if not stream_id:
            return {"name": "probabilistic_typo_correction", "content": "无法确定当前会话，未发送。"}
        target = self._target(kwargs)
        if target is None:
            return {"name": "probabilistic_typo_correction", "content": "无法确定当前会话目标，未发送。"}
        action, target_params = target
        wrong = self._wrong_text(text)
        try:
            sent = await self._call(action, {**target_params, "message": [{"type": "text", "data": {"text": wrong}}]})
            message_id = self._extract_message_id(sent)
            if not message_id:
                return {"name": "probabilistic_typo_correction", "content": "错字版本发送后没有拿到消息 ID，未继续撤回。"}
            await asyncio.sleep(int(self.config.plugin.recall_delay_ms) / 1000)
            await self._call("delete_msg", {"message_id": int(message_id) if str(message_id).isdigit() else message_id})
            await self._call(action, {**target_params, "message": [{"type": "text", "data": {"text": text}}]})
            self.ctx.logger.info("[typo-correction] tool flow completed, session=%s", stream_id)
            return {"name": "probabilistic_typo_correction", "content": "已完成：错字版本已撤回并发送正确文本。"}
        except Exception as exc:
            self.ctx.logger.warning("[typo-correction] tool flow failed: %s", exc)
            return {"name": "probabilistic_typo_correction", "content": "自纠错发送失败。"}

    @HookHandler(
        "send_service.before_send",
        name="probabilistic_typo_correction",
        description="低概率发送短暂错字后撤回并发送原文",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        timeout_ms=5000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def handle_before_send(self, message: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any] | None:
        del kwargs
        if not isinstance(message, dict):
            return None
        text = self._plain_text(message)
        session_id = str(message.get("session_id") or message.get("stream_id") or "")
        if session_id in self._busy or not self._eligible(message, text):
            return None
        target = self._target(message)
        if target is None:
            return None
        action, target_params = target
        wrong = self._wrong_text(text)
        if wrong == text:
            return None
        self._busy.add(session_id)
        try:
            sent = await self._call(action, {**target_params, "message": [{"type": "text", "data": {"text": wrong}}]})
            message_id = self._extract_message_id(sent)
            if not message_id:
                self.ctx.logger.warning("[typo-correction] wrong message sent without message_id; original aborted")
                return {"action": "abort"}
            await asyncio.sleep(int(self.config.plugin.recall_delay_ms) / 1000)
            await self._call("delete_msg", {"message_id": int(message_id) if str(message_id).isdigit() else message_id})
            await self._call(action, {**target_params, "message": [{"type": "text", "data": {"text": text}}]})
            self.ctx.logger.info("[typo-correction] sent wrong -> recalled -> corrected, session=%s", session_id)
            return {"action": "abort"}
        except Exception as exc:
            self.ctx.logger.warning("[typo-correction] flow failed: %s", exc)
            return None
        finally:
            self._busy.discard(session_id)


def create_plugin() -> TypoCorrectionPlugin:
    return TypoCorrectionPlugin()
