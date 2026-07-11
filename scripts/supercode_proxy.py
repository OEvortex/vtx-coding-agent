import os
import json
import asyncio
from typing import AsyncGenerator, Dict, Any, List, Callable, Optional
import httpx


class Supercode:
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        self.base_url = base_url or os.environ.get(
            "SUPERCODE_URL", "https://supercode-8w7e.onrender.com"
        )
        self.token = token or self._load_token()
        self.tools: Dict[str, Callable] = {}

    def _load_token(self) -> str:
        try:
            with open(os.path.expanduser("~/.better-auth/token.json")) as f:
                return json.load(f)["access_token"]
        except Exception:
            raise RuntimeError("No token provided and ~/.better-auth/token.json not found.")

    def register_tool(self, name: str, func: Callable):
        """Register a local Python function that the agent can execute."""
        self.tools[name] = func

    async def chat(
        self, model: str, messages: List[Dict[str, Any]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Runs the main agentic loop, executing tool calls internally and yielding events."""
        provider, model_name = model.split("/", 1) if "/" in model else ("concentrateai", model)

        async with httpx.AsyncClient(timeout=120) as client:
            while True:
                payload = {"provider": provider, "model": model_name, "messages": messages}
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.token}",
                }

                assistant_response = ""
                tool_calls_to_run = []

                async with client.stream(
                    "POST", f"{self.base_url}/api/ai/chat", json=payload, headers=headers
                ) as resp:
                    if resp.status_code >= 400:
                        yield {"type": "error", "content": f"API Error {resp.status_code}"}
                        return

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        ev_type = event.get("type")

                        if ev_type == "text":
                            content = event.get("content", "")
                            assistant_response += content
                            yield {"type": "text", "content": content}
                        elif ev_type == "reasoning" and event.get("content"):
                            yield {"type": "reasoning", "content": event["content"]}
                        elif ev_type == "tool-call":
                            tool_calls_to_run.append(event)
                            yield {
                                "type": "tool_call_start",
                                "name": event.get("toolName"),
                                "args": event.get("args"),
                            }

                # Update state with assistant's turn details
                if assistant_response or tool_calls_to_run:
                    msg = {"role": "assistant"}
                    if assistant_response:
                        msg["content"] = assistant_response
                    if tool_calls_to_run:
                        msg["tool_calls"] = [
                            {
                                "id": tc.get("toolCallId"),
                                "type": "function",
                                "function": {
                                    "name": tc.get("toolName"),
                                    "arguments": json.dumps(tc.get("args", {})),
                                },
                            }
                            for tc in tool_calls_to_run
                        ]
                    messages.append(msg)

                # Base case: Agent finished thinking/speaking and called zero tools
                if not tool_calls_to_run:
                    break

                # Turn Execution: Process tools locally and insert back into conversation block
                for tc in tool_calls_to_run:
                    tc_id = tc.get("toolCallId")
                    tc_name = tc.get("toolName")
                    tc_args = tc.get("args", {})

                    if tc_name in self.tools:
                        try:
                            result = self.tools[tc_name](**tc_args)
                        except Exception as e:
                            result = f"Error executing tool: {str(e)}"
                    else:
                        result = f"Tool '{tc_name}' is not registered on this client instance."

                    yield {"type": "tool_call_result", "name": tc_name, "result": result}

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": tc_name,
                            "content": str(result),
                        }
                    )


# Dummy local function to bind
def get_weather(location: str) -> str:
    return f"The weather in {location} is 72°F and clear."


async def main():
    # Initialize SDK
    ai = Supercode()
    ai.register_tool("get_weather", get_weather)

    # Conversation history tracking
    messages = [{"role": "system", "content": "You are a helpful companion engine."}]

    print("Supercode Agent Session Initialized. Type 'exit' to quit.\n")

    while True:
        user_input = input("\nYou: ").strip()
        if user_input.lower() in ("exit", "quit"):
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})
        print("Agent: ", end="", flush=True)

        # Stream out tokens/steps just like OpenAI's stream chunks
        async for delta in ai.chat("concentrateai/deepseek-v4-flash", messages):
            if delta["type"] == "text":
                print(delta["content"], end="", flush=True)
            elif delta["type"] == "tool_call_start":
                print(
                    f"\n[Running tool {delta['name']} with {delta['args']}...]", end="", flush=True
                )
            elif delta["type"] == "tool_call_result":
                print(f"\n[Tool Result: {delta['result']}]", end="", flush=True)
        print()


if __name__ == "__main__":
    asyncio.run(main())
