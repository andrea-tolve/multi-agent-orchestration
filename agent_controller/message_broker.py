"""
Message broker for inter-agent communication.
Handles synchronous requests, asynchronous messages, and events.
Supports remote forwarding via HTTP to the target agent's controller
found via GlobalRegistry lookup.
"""

import asyncio
import uuid
from typing import Any, Dict, List

from shared.utils import request_json_async


class MessageBroker:
    """Central message broker for agent communication."""

    def __init__(self, global_registry_url: str):
        # Message queues by agent_id
        self.queues: Dict[str, asyncio.Queue] = {}

        # Request-response tracking: request_id -> asyncio.Event + response
        self.pending_requests: Dict[str, Dict] = {}

        # Collaboration routing: task_id -> [allowed_partner_task_ids]
        self.collaboration_graph: Dict[str, List[str]] = {}

        # Task to agent mapping: task_id -> agent_id
        self.task_to_agent_mapping: Dict[str, str] = {}

        # Lock for thread-safe operations
        self.lock = asyncio.Lock()

        # GlobalRegistry URL for controller lookup
        self._global_registry_url = global_registry_url

        self._agent_registry: Dict[str, str] = {}  # agent_id -> controller_url

    async def _http_deliver(
        self, controller_url: str, target_agent_id: str, message: Dict[str, Any]
    ):
        """Deliver a message to a remote controller via HTTP."""
        payload = {
            "target_agent_id": target_agent_id,
            "message": message,
        }
        await request_json_async(
            "POST",
            f"{controller_url}/broker/forward",
            payload=payload,
            timeout=10.0,
        )

    async def _http_request(
        self,
        controller_url: str,
        target_agent_id: str,
        request: Dict[str, Any],
        timeout: float = 60.0,
    ) -> Any:
        """Send a synchronous request through a remote controller."""
        payload = {
            "target_agent_id": target_agent_id,
            "message": request,
        }
        return await request_json_async(
            "POST",
            f"{controller_url}/broker/request",
            payload=payload,
            timeout=timeout,
        )

    async def register_agent(self, agent_id: str, task_id: str):
        """Register an agent with a message queue."""
        async with self.lock:
            if agent_id not in self.queues:
                self.queues[agent_id] = asyncio.Queue()
                self.task_to_agent_mapping[task_id] = agent_id

    async def send(self, target_agent_id: str, message: Dict[str, Any]):
        """Send a message to a specific agent (unicast), locally or remotely."""
        async with self.lock:
            is_local = (
                target_agent_id in self.queues
            )  # just the local agent are in the queues

        if not is_local:
            controller_url = self._agent_registry.get(target_agent_id)
            if not controller_url:
                raise ValueError(
                    f"Agent {target_agent_id} not found locally and "
                    f"no controller URL found for task '{target_agent_id}'"
                )
            await self._http_deliver(controller_url, target_agent_id, message)
            return

        async with self.lock:
            await self.queues[target_agent_id].put(message)

    async def request(
        self,
        sender_id: str,
        target_agent_id: str,
        request: Dict[str, Any],
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> Any:
        """Send a synchronous request to a specific agent and wait for response."""
        request_id = request_id or str(uuid.uuid4())
        request["request_id"] = request_id
        request["sender_id"] = sender_id

        async with self.lock:
            is_local = target_agent_id in self.queues

        if not is_local:
            controller_url = self._agent_registry.get(target_agent_id)
            if not controller_url:
                raise ValueError(
                    f"Agent {target_agent_id} not found locally and "
                    f"no controller URL found for task '{target_agent_id}'"
                )
            return await self._http_request(
                controller_url, target_agent_id, request, timeout=timeout or 60.0
            )

        async with self.lock:
            if target_agent_id not in self.queues:
                raise ValueError(f"Target agent {target_agent_id} not registered")

            self.pending_requests[request_id] = {
                "event": asyncio.Event(),
                "response": None,
                "sender_id": sender_id,
            }

        await self.send(target_agent_id, request)

        try:
            await asyncio.wait_for(
                self.pending_requests[request_id]["event"].wait(),
                timeout=timeout,
            )
            return self.pending_requests[request_id]["response"]
        finally:
            async with self.lock:
                self.pending_requests.pop(request_id, None)

    async def respond(self, request_id: str, response: Any):
        """Send a response to a pending request."""
        async with self.lock:
            if request_id in self.pending_requests:
                self.pending_requests[request_id]["response"] = response
                self.pending_requests[request_id]["event"].set()

                sender_agent_id = self.pending_requests[request_id].get("sender_id")
                if not sender_agent_id:
                    return

                is_local = sender_agent_id in self.queues

                response_msg = {
                    "type": "response",
                    "request_id": request_id,
                    "content": response.get("content", {}),
                    "sender_task_id": response.get("sender_task_id"),
                    "timestamp": response.get("timestamp"),
                }

                if is_local:
                    await self.queues[sender_agent_id].put(response_msg)
                else:
                    controller_url = self._agent_registry.get(sender_agent_id)
                    if controller_url:
                        await self._http_deliver(
                            controller_url, sender_agent_id, response_msg
                        )

    async def get_message(
        self, agent_id: str, timeout: float | None = None
    ) -> Dict[str, Any] | None:
        """Get next message from agent's queue."""
        if agent_id not in self.queues:
            raise ValueError(f"Agent {agent_id} not registered")

        try:
            return await asyncio.wait_for(self.queues[agent_id].get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def put_message_back(self, agent_id: str, message: Dict[str, Any]):
        """Put a consumed message back at the front of the agent's queue."""
        if agent_id not in self.queues:
            raise ValueError(f"Agent {agent_id} not registered")
        await self.queues[agent_id].put(message)

    async def cleanup_agent(self, agent_id: str):
        """Clean up agent resources."""
        async with self.lock:
            if agent_id in self.queues:
                del self.queues[agent_id]

    async def register_collaboration(
        self,
        task_id: str,
        can_collaborate_with: list[str],
    ):
        """Register which tasks an agent can collaborate with."""
        async with self.lock:
            self.collaboration_graph[task_id] = can_collaborate_with

    def validate_messaging_allowed(
        self, sender_task_id: str, target_task_id: str
    ) -> bool:
        """Check if sender is allowed to message target."""
        allowed = self.collaboration_graph.get(sender_task_id, [])
        return target_task_id in allowed

    def get_agent_id_for_task(self, task_id: str) -> str | None:
        """Get agent_id for a given task_id."""
        return self.task_to_agent_mapping.get(task_id)

    async def deliver_forwarded_message(
        self, target_agent_id: str, message: Dict[str, Any]
    ):
        """Deliver a message forwarded from a remote controller to a local agent."""
        async with self.lock:
            if target_agent_id not in self.queues:
                raise ValueError(f"Agent {target_agent_id} not registered locally")
            await self.queues[target_agent_id].put(message)

    async def register_remote_agent(
        self, task_id: str, agent_id: str, controller_url: str
    ):
        """Register a remote agent with its task_id, agent_id, and controller URL."""
        async with self.lock:
            # Map task_id -> agent_id for message resolution
            self.task_to_agent_mapping[task_id] = agent_id
            # Map agent_id -> controller_url for routing
            self._agent_registry[agent_id] = controller_url
            print(
                f"[MessageBroker] Registered remote agent: task_id={task_id}, agent_id={agent_id}, controller={controller_url}"
            )
