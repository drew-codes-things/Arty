import os

from pydantic import BaseModel

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

server = MCPServer("fake")


class Item(BaseModel):
    name: str
    count: int = 1


@server.tool(description="Says the text back.")
def echo(text: str) -> str:
    return text


@server.tool(description="Adds two numbers.")
def add(first: int, second: int) -> int:
    return first + second


@server.tool(description="Counts an item.")
def stock(item: Item) -> str:
    return f"{item.count} x {item.name}"


@server.tool(description="Always fails.")
def fail() -> str:
    raise ToolError("the fake tool broke")


@server.tool(description="Ends the server process.")
def crash() -> str:
    os._exit(3)


@server.tool(description="What the server was started with.")
def environment() -> dict:
    return {"has_hub_key": "MCPO_API_KEY" in os.environ, "setting": os.environ.get("FAKE_SETTING", "")}


if __name__ == "__main__":
    server.run()
