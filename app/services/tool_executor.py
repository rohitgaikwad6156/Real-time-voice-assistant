"""Tool Executor for Gemini Live Function Calling."""

import asyncio
import inspect
import logging
from typing import Any, Dict, List, Optional, Sequence
from google.genai import types
from app.tools.registry import ToolRegistry, get_default_registry

logger = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(self, registry: Optional[ToolRegistry] = None, user_id: Optional[str] = None) -> None:
        self.registry = registry or get_default_registry()
        self.user_id = user_id

    async def execute(self, name: str, call_id: str, args: Optional[Dict[str, Any]] = None) -> types.FunctionResponse:
        logger.info("Executing tool '%s' (call_id: %s) with args: %s", name, call_id, args)
        tool_def = self.registry.get(name)
        if tool_def is None:
            err_msg = f"Unknown tool: '{name}'. Available tools: {[t['name'] for t in self.registry.list_tools()]}"
            return types.FunctionResponse(name=name, id=call_id, response={"result": {"status": "error", "error": err_msg}})

        safe_args = dict(args) if isinstance(args, dict) else {}
        if name == "create_reminder" and self.user_id:
            safe_args["user_id"] = self.user_id

        try:
            func = tool_def.func
            if inspect.iscoroutinefunction(func):
                result = await func(**safe_args)
            else:
                result = await asyncio.to_thread(func, **safe_args)
            logger.info("Tool '%s' returned: %s", name, result)
        except TypeError as type_err:
            result = {"status": "error", "error": f"Invalid arguments for tool '{name}': {type_err}"}
        except Exception as exc:
            logger.exception("Unexpected error executing tool '%s': %s", name, exc)
            result = {"status": "error", "error": f"Tool execution failed for '{name}': {exc}"}

        return types.FunctionResponse(name=name, id=call_id, response={"result": result})

    async def execute_calls(self, function_calls: Sequence[Any]) -> List[types.FunctionResponse]:
        tasks = []
        seen_call_ids = set()
        for call in function_calls:
            c_name = getattr(call, "name", "")
            c_id = getattr(call, "id", "")
            c_args = getattr(call, "args", {}) or {}
            if c_id:
                if c_id in seen_call_ids:
                    continue
                seen_call_ids.add(c_id)
            tasks.append(self.execute(c_name, c_id, c_args))
        if not tasks:
            return []
        return await asyncio.gather(*tasks)


def get_default_tool_executor(user_id: Optional[str] = None) -> ToolExecutor:
    return ToolExecutor(user_id=user_id)
