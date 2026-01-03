"""MCP HTTP Client for VkusVill"""
import httpx
import logging

log = logging.getLogger(__name__)


class MCPClient:
    """HTTP client for MCP server"""
    
    def __init__(self, url: str):
        self.url = url
        self.session_id = None
    
    async def call(self, method: str, params: dict) -> dict:
        """Call MCP method"""
        async with httpx.AsyncClient(verify=False, timeout=60) as client:
            headers = {
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            }
            if self.session_id:
                headers["mcp-session-id"] = self.session_id
            
            # Initialize session if needed
            if not self.session_id:
                init_resp = await client.post(
                    self.url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 0,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "vkusvill-bot", "version": "2.0"}
                        }
                    },
                    headers=headers
                )
                if "mcp-session-id" in init_resp.headers:
                    self.session_id = init_resp.headers["mcp-session-id"]
                    headers["mcp-session-id"] = self.session_id
                    # Send initialized notification
                    await client.post(
                        self.url,
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                        headers=headers
                    )
            
            # Call method
            response = await client.post(
                self.url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": method, "arguments": params}
                },
                headers=headers
            )
            
            if "mcp-session-id" in response.headers:
                self.session_id = response.headers["mcp-session-id"]
            
            data = response.json()
            if "error" in data:
                # Reset session and retry
                self.session_id = None
                headers.pop("mcp-session-id", None)
                
                init_resp = await client.post(
                    self.url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 0,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "vkusvill-bot", "version": "2.0"}
                        }
                    },
                    headers=headers
                )
                if "mcp-session-id" in init_resp.headers:
                    self.session_id = init_resp.headers["mcp-session-id"]
                    headers["mcp-session-id"] = self.session_id
                    await client.post(
                        self.url,
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                        headers=headers
                    )
                
                response = await client.post(
                    self.url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": method, "arguments": params}
                    },
                    headers=headers
                )
                if "mcp-session-id" in response.headers:
                    self.session_id = response.headers["mcp-session-id"]
                data = response.json()
            
            return data.get("result", {})

