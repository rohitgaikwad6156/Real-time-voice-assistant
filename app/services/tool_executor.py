"""Tool Executor for Gemini Live Function Calling.

Receives function calls from Gemini Live API, validates arguments against the Tool Registry,
executes the tool implementation, and formats structured types.FunctionResponse objects.
"""

import asyncio
import inspect
import logging
from typing import Any, Dict, List, Optional, Sequence

from google.genai import types

from app.tools.registry import ToolRegistry, get_default_registry

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes registered tools upon receiving function calls from Gemini."""

    def __init__(self, registry: Optional[ToolRegistry] = None) -> None:
        self.registry = registry or get_default_registry()

    async def execute(
        self,
        name: str,
        call_id: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> types.FunctionResponse:
        """Execute a single function call and return a structured FunctionResponse.

        Args:
            name: Tool / function name.
            call_id: Unique call identifier provided by Gemini.
            args: Dictionary of arguments supplied by Gemini.

        Returns:
            Official Google GenAI types.FunctionResponse ready to return to Gemini.
        """
        logger.info("Executing tool '%s' (call_id: %s) with args: %s", name, call_id, args)

        # 1. Look up tool in registry
        tool_def = self.registry.get(name)
        if tool_def is None:
            err_msg = f"Unknown tool: '{name}'. Available tools: {[t['name'] for t in self.registry.list_tools()]}"
            logger.warning("Tool execution error: %s", err_msg)
            return types.FunctionResponse(
                name=name,
                id=call_id,
                response={"result": {"status": "error", "error": err_msg}},
            )

        # 2. Normalize and validate args
        safe_args = args if isinstance(args, dict) else {}

        # 3. Safely execute the tool
        try:
            func = tool_def.func
            if inspect.iscoroutinefunction(func):
                result = await func(**safe_args)
            else:
                # Run sync functions in a threadpool to avoid blocking the event loop
                result = await asyncio.to_thread(func, **safe_args)

            logger.info("Tool '%s' returned: %s", name, result)

        except TypeError as type_err:
            # Handle invalid argument types or mismatched parameters
            err_msg = f"Invalid arguments for tool '{name}': {type_err}"
            logger.warning("Tool argument mismatch: %s", err_msg)
            result = {"status": "error", "error": err_msg}

        except Exception as exc:
            # Handle runtime errors inside tool execution
            err_msg = f"Tool execution failed for '{name}': {exc}"
            logger.exception("Unexpected error executing tool '%s': %s", name, exc)
            result = {"status": "error", "error": err_msg}

        # 4. Return structured FunctionResponse
        return types.FunctionResponse(
            name=name,
            id=call_id,
            response={"result": result},
        )

    async def execute_calls(
        self,
        function_calls: Sequence[Any],
    ) -> List[types.FunctionResponse]:
        """Execute multiple function calls concurrently with deduplication protection.

        Args:
            function_calls: List of function call objects with .name, .id, and .args.

        Returns:
            List of types.FunctionResponse objects.
        """
        tasks = []
        seen_call_ids = set()

        for call in function_calls:
            c_name = getattr(call, "name", "")
            c_id = getattr(call, "id", "")
            c_args = getattr(call, "args", {}) or {}

            # Prevent duplicate tool execution for identical call IDs
            if c_id:
                if c_id in seen_call_ids:
                    logger.warning("Skipping duplicate tool call '%s' with identical call_id '%s'", c_name, c_id)
                    continue
                seen_call_ids.add(c_id)

            tasks.append(self.execute(c_name, c_id, c_args))

        if not tasks:
            return []

        return await asyncio.gather(*tasks)


# Global default executor instance
default_tool_executor = ToolExecutor()


def get_default_tool_executor() -> ToolExecutor:
    """Access the default tool executor."""
    return default_tool_executor
