"""
Agent Program: autonomous agent that executes assigned tasks.
"""

import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from shared.utils import (
    load_llm_model,
    parse_llm_json_payload,
    request_json,
)

from .definitions import (
    AgentPlanOutput,
    ExecutionContext,
    TaskDefinition,
    TerminationEvaluationOutput,
    structured_output_to_dict,
)
from .message_broker import MessageBroker

logger = logging.getLogger(__name__)


class AgentProgram:
    """
    Autonomous agent that executes a single task.
    Communicates with other agents via primitives and message broker.
    """

    def __init__(
        self,
        agent_id: str,
        task: TaskDefinition,
        agent_service_manager_url: str,
        message_broker: MessageBroker,
        dependency_graph: Optional[Dict[str, Any]] = None,
    ):
        # Create service for the task
        service = self._create_service(
            task.task_name,
            self._service_description_for_task(task),
            agent_service_manager_url,
            requires_ui=getattr(task, "requires_ui", False),
            ui_description=getattr(task, "ui_description", None),
            api_endpoint=getattr(task, "api_endpoint", None),
            is_delegated=task.is_delegated,
        )

        task.set_service(service)
        executable_service = task.service.get("executable_service", {}) or {}
        service_address = executable_service.get("service_address", "")

        self.agent_id = agent_id
        self.task = task
        self.service_address = service_address
        self.service_manager_url = agent_service_manager_url
        self.message_broker = message_broker
        self.dependency_graph = dependency_graph or {}
        self.task_dependency_ids = list(getattr(task, "depends_on", []))
        self.dependency_node = self.dependency_graph.get("nodes", {}).get(
            task.task_id, {}
        )

        # Collaboration: derive from information_dependencies - task specifies which APs to collaborate with
        self.collaboration_partners = task.information_dependencies or []

        self.dependency_messages: Dict[str, Dict[str, Any]] = {}
        self.service_response: Any = None
        self.execution_history: list[Dict[str, Any]] = []
        self.last_action: Dict[str, Any] = {}
        self.goal_reached = False
        self.max_iterations = 50
        self.llm = self._load_optional_llm_model(
            model_name=os.getenv("SERVICE_MODEL"),
            temperature=0.2,
        )
        self.termination_llm = self._load_optional_llm_model(
            model_name=os.getenv("SERVICE_EVALUTATION_MODEL"),
            temperature=0.2,
        )

        # Execution state
        self.state: Dict[str, Any] = {}
        self.is_running = False
        self.is_completed = False
        self.completion_result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None

        # Message monitoring
        self._monitor_active = False
        self._message_monitor_task: Optional[asyncio.Task[None]] = None

        # Track pending outbound request to prevent simultaneous requests
        self.pending_outbound_request: Optional[str] = None

        # Track sent messages to prevent duplicate sends
        self._sent_message_ids: set = set()

    @classmethod
    def from_service_response(
        cls,
        task: TaskDefinition,
        service_response: dict,
        agent_service_manager_url: str,
        message_broker: MessageBroker,
        dependency_graph: Optional[Dict[str, Any]] = None,
    ) -> "AgentProgram":
        """
        Factory: build AgentProgram from a pre-computed service_response.
        Skips service creation — caller already got the response from SM.
        """
        task.set_service(service_response)
        executable_service = task.service.get("executable_service", {}) or {}
        service_address = executable_service.get("service_address", "")

        ap = cls.__new__(cls)
        ap.agent_id = "agent-" + task.task_name
        ap.task = task
        ap.service_address = service_address
        ap.service_manager_url = agent_service_manager_url
        ap.message_broker = message_broker
        ap.dependency_graph = dependency_graph or {}
        ap.task_dependency_ids = list(getattr(task, "depends_on", []))
        ap.dependency_node = ap.dependency_graph.get("nodes", {}).get(task.task_id, {})
        ap.collaboration_partners = task.information_dependencies or []
        ap.dependency_messages = {}
        ap.service_response = service_response
        ap.execution_history = []
        ap.last_action = {}
        ap.goal_reached = False
        ap.max_iterations = 50
        ap.llm = ap._load_optional_llm_model(
            os.getenv("SERVICE_MODEL"), temperature=0.2
        )
        ap.termination_llm = ap._load_optional_llm_model(
            os.getenv("SERVICE_EVALUTATION_MODEL"), temperature=0.2
        )
        ap.state = {}
        ap.is_running = False
        ap.is_completed = False
        ap.completion_result = None
        ap.error = None
        ap._monitor_active = False
        ap._message_monitor_task = None
        ap.pending_outbound_request = None
        ap._sent_message_ids = set()
        return ap

    def _service_description_for_task(self, task: TaskDefinition) -> str:
        """Build a service description for service creation."""
        # Use explicit service_description if provided, otherwise fall back to task description
        description = (
            task.service_description if task.service_description else task.description
        ).strip()

        if description.lower().startswith("flask web service"):
            return description
        return f"Flask web service that {description}"

    def _create_service(
        self,
        service_name: str,
        service_description: str,
        service_manager_url: str,
        requires_ui: bool = False,
        ui_description: Optional[str] = None,
        api_endpoint: Optional[str] = None,
        is_delegated: bool = False,
    ) -> dict:
        """Create a service via the service manager."""
        payload = {
            "service_name": service_name,
            "service_description": service_description,
            "app_dir": service_name,
            "kind": "kubernetes",
        }

        if requires_ui:
            payload["requires_ui"] = "true"
            if ui_description:
                payload["ui_description"] = ui_description
            if api_endpoint:
                payload["api_endpoint"] = api_endpoint
        if is_delegated:
            payload["is_delegated"] = "true"

        try:
            start = time.time()
            response = request_json(
                "POST",
                service_manager_url + "/create_service",
                payload,
                timeout=500,
            )
            if not response:
                raise Exception("Empty response from service manager")
            end = time.time()
            response["total_time"] = end - start
            return response
        except Exception as e:
            print(f"Failed to create service {service_name}: {str(e)}")
            raise

    def _load_optional_llm_model(self, model_name: str | None, temperature: float):
        """Load an optional Gemini client for a specific Agent Program phase."""
        try:
            return load_llm_model(model_name, temperature=temperature)
        except Exception:
            return None

    async def _wait_for_dependency_messages(self) -> Dict[str, Dict[str, Any]]:
        """Wait for completion messages from upstream dependency agents."""
        dependency_ids = [dep_id for dep_id in self.task_dependency_ids if dep_id]
        if not dependency_ids:
            return {}

        expected = set(dependency_ids)
        received: Dict[str, Dict[str, Any]] = {}
        timeout_sec = 100.0
        print(
            f"Agent {self.agent_id} waiting for dependency tasks: "
            f"{sorted(expected)} (timeout: {timeout_sec}s)\n\n"
        )
        deadline = time.monotonic() + timeout_sec

        while received.keys() != expected and time.monotonic() < deadline:
            remaining = max(0.5, min(2.0, deadline - time.monotonic()))
            message = await self.message_broker.get_message(
                self.agent_id, timeout=remaining
            )
            if not message:
                continue

            if message.get("type") != "task_completed":
                continue

            task_id = message.get("task_id")
            if isinstance(task_id, str) and task_id in expected:
                received[task_id] = message
            else:
                await self.message_broker.put_message_back(
                    self.agent_id, message
                )  # Put back if it's a task_completed message

        if received.keys() != expected:
            missing = sorted(expected - set(received.keys()))
            raise RuntimeError(
                f"Missing dependency messages from tasks: {missing} "
                f"after waiting {timeout_sec}s"
            )

        print(f"Agent {self.agent_id} received all dependency messages\n\n")
        return received

    def _schema_summary(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Reduce the OpenAPI schema to the useful parts for prompting."""
        paths = schema.get("paths") or {}
        return {"openapi": schema.get("openapi"), "paths": paths}

    def _build_planner_prompt(
        self,
        schema: Dict[str, Any],
        last_observation: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Build the prompt that lets the AP choose endpoint and payload."""
        has_ui = getattr(self.task, "requires_ui", False)

        context = {
            "agent_id": self.agent_id,
            "task": {
                "task_id": self.task.task_id,
                "task_name": self.task.task_name,
                "description": self.task.description,
                "termination_condition": getattr(
                    self.task, "termination_condition", None
                ),
                "has_ui": has_ui,
                "api_endpoint": getattr(self.task, "api_endpoint", None),
            },
            "service": {
                "service_address": self.service_address,
                "api_schema_url": f"{self.service_address}/api-schema",
            },
            "last_error": self.state.get("last_error", ""),
            "last_failed_endpoint": self.state.get("last_failed_endpoint", ""),
            "last_failed_method": self.state.get("last_failed_method", ""),
            "dependency_messages": self.dependency_messages,
            "current_state": self.state,
            "last_action": self.last_action,
            "last_observation": last_observation,
            "openapi_schema": self._schema_summary(schema),
            "collaboration": {
                "can_collaborate_with": self.collaboration_partners,
                "incoming_requests": self.state.get("incoming_requests", []),
                "incoming_messages": self.state.get("incoming_messages", []),
                "pending_outbound_request": getattr(
                    self, "pending_outbound_request", None
                ),  # The agent is waiting for a response to this request
            },
            "multiplayer": {
                "last_known_local_move": self.state.get("last_known_local_move"),
            },
        }

        ui_instruction = ""
        if has_ui:
            api_endpoint = getattr(self.task, "api_endpoint", None)
            ui_instruction = f"""
UI TASK: The service has a user interface. The user opens {api_endpoint} directly.
- The user sends POST to {api_endpoint} with their interaction data (this data is stored)
- System polls GET /api/status BEFORE each planning iteration and provides the result as last_observation
- DO NOT call /api/status yourself — use last_observation which is already updated for you
- Use the last_observation data to plan your next actions and send requests to other agents
- If the task is a multiplayer game, /api/status won't immediately reflect the other player's move; rely on inter-agent messages for those updates.
- If you have collaboration partners, use inter-agent messaging to coordinate with them.
"""

        messaging_instruction = ""
        if self.collaboration_partners:
            messaging_instruction = f"""
INTER-AGENT MESSAGING (CRITICAL):
You MUST use inter-agent messaging to coordinate with: {", ".join(self.collaboration_partners)}

SERVICES CANNOT COMMUNICATE WITH AGENTS — ONLY YOU CAN.
If you need data or a response from another agent, send a request message.

MESSAGE RULES (MUST FOLLOW IN ORDER):
1. RULE 1 — RESPOND FIRST: If there are ANY pending incoming requests
   (incoming_requests list is non-empty), you MUST respond to them FIRST
   before sending any new request. Each incoming request needs a response.
   Use message type "response" with the matching request_id.

2. RULE 2 — ONE REQUEST AT A TIME: If you sent a request last iteration
   (check last_message_response or pending_outbound_request is not null),
   you MUST NOT send another request until you receive a response.
   Wait for the response to arrive as last_message_response.

3. RULE 3 — SENDING A REQUEST: Only after all incoming requests are answered
   AND you have no pending outbound request, you may send ONE new request.
   Set wait_for_response: true and the other agent will respond.

4. Rule 4 For MULTI AGENT GAMES: after every user move, send a message to other agents to notify of the move.

Important: You can send multiple messages in a single iteration, like a response and a request.

Send messages using "messages" in your response JSON:
    "messages": {{
        "msg_001": {{
            "type": "request",
            "target_task_id": "task_002",
            "content": {{"value": 1}},
            "wait_for_response": true,
        }},
        "msg_002": {{
            "type": "response",
            "target_task_id": "task_002",
            "request_id": "xxx",  # include request_id when responding to a request
            "content": {{"value": <value>}}
        }}
    }}

The request ID inside a "response" message should have the same value as {self.pending_outbound_request}, it's not a different request from the one who sent it.
Always include "sender_id" in your messages (request, response, task_completed).
Check context["collaboration"]["incoming_requests"] for requests needing responses.
Check context["collaboration"]["incoming_messages"] for prior conversation turns.
Check context["current_state"]["last_message_response"] for the last response received.
Check context["current_state"]["collaborator_completed"] for a list of completed collaboration partners.
"""

        multiplayer_instruction = ""
        if self.collaboration_partners:
            endpoint_path = "/api/opponent-move"
            multiplayer_instruction = f"""
MULTIPLAYER GAME — PROTOCOL:
In this kind of game, you are playing against another player's agent. On your side there's just one player (for example, Player X) and on the other side there's another player (for example, Player O).
YOUR ROLE: Your human uses the UI (which POSTs to your service).
Your job is to relay moves between your human and the other player's agent.

HUMAN PLAYS:
    - Your human clicks in the UI. The UI POSTs to your service.
    - YOU poll GET /api/status to see the new state.
    - After player moves, you need to send information to the other player (collaborator) via a message.
    - So if the game it's not singleplayer (i.e., there are collaboration partners), you need to send a message to the other player for every turn change.

SHARE INFORMATION:
    - Upon board changes, you share the updated state with your collaboration partners, sending a message like this (for a tic tac toe game):
        {{
          "messages": {{
            "msg_001": {{
              "type": "response",
              "target_task_id": "task_XXX",
              "content": {{"board": [...]}},
              "wait_for_response": false
            }}
          }}
        }}
    - Since the game is multiplayer, you can't receive updates of other player via polling; instead, rely on inter-agent messages.

RECEIVE INFORMATION:
    - You receive updates from your collaboration partners via inter-agent messages.
    - You must update the local state with any changes received from your partners.
    - You must update the local UI sending the information to {endpoint_path} (formatted as required by the OpenAPI schema), so the player can see the updated board.
    - Upon receiving an update via inter-agent messages, you must update your game at {endpoint_path} and immediately after send the new game state to your collaboration partners.
    - If the api schema define a different endpoint from {endpoint_path}, you must use the endpoint defined in the schema.
IMPORTANT:
    - Do not poll for remote updates; instead, rely on inter-agent messages.
    - Just send the important information to your partners when you receive an update (e.g., the player's move)
    - When the game is over, send the final game state to your collaboration partners.
"""

        return (
            "You are the AI inside an Agent Program.\n"
            "Your job is to execute the task by choosing actions based on OpenAPI schema and current context.\n\n"
            "Rules:\n"
            "- Return ONLY valid JSON.\n"
            "IMPORTANT: Write property names and values enclose in double quotes"
            "- You can call a service endpoint, send messages to other agents, or both.\n"
            "- If calling a service: use only endpoints and methods that exist in the OpenAPI schema.\n"
            "- If only messaging: omit 'endpoint' and 'method' entirely.\n"
            "- method must be lowercase.\n"
            "- Ignore any endpoints that are not useful for reaching the objective (like health/).\n"
            '- return {"endpoint": ..., "method": ..., "payload": {...}, "reason": ...} OR {"messages": [...], "reason": ...} OR both.\n'
            "- payload must match the requestBody schema as much as possible.\n"
            "- Prefer the smallest action that advances the task toward completion.\n"
            f"{ui_instruction}\n"
            f"{multiplayer_instruction}\n"
            f"{messaging_instruction}\n"
            f"CONTEXT:\n{json.dumps(context, ensure_ascii=False, default=str, indent=2)}\n"
        )

    def _build_request_payload(
        self, operation: Dict[str, Any], planner_output: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Normalize the payload suggested by the AI."""
        payload = planner_output.get("payload")
        if isinstance(payload, dict):
            return payload

        request_body = operation.get("requestBody") or {}
        content = request_body.get("content") or {}
        json_schema = (content.get("application/json") or {}).get("schema") or {}
        if json_schema.get("type") == "object":
            properties = json_schema.get("properties") or {}
            inferred: Dict[str, Any] = {}
            for key in properties:
                if key in self.state:
                    inferred[key] = self.state[key]
                elif key in self.dependency_messages:
                    inferred[key] = self.dependency_messages[key]
            if inferred:
                return inferred

        return None

    async def _plan_next_action(
        self, schema: Dict[str, Any], last_observation: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Ask the AI agent which endpoint and payload to use next."""
        if not self.llm:
            raise RuntimeError("LLM client is not available")

        prompt = self._build_planner_prompt(schema, last_observation)
        try:
            structured_llm = self.llm.with_structured_output(AgentPlanOutput)
            plan = await asyncio.to_thread(
                structured_llm.invoke,
                prompt,
            )
        except Exception as structured_exc:
            # If structured output fails, fall back to standard JSON parsing
            logger.warning(
                "Structured planning failed for %s; falling back to JSON parsing: %s",
                self.task.task_name,
                structured_exc,
            )
            response = await asyncio.to_thread(
                self.llm.invoke,
                prompt,
            )
            if not response or not response.content:
                raise ValueError("LLM response is empty")
            plan = parse_llm_json_payload(
                response.content,
                expected_type=dict,
                error_prefix="Planning LLM response",
            )

        parsed = structured_output_to_dict(plan)
        method = parsed.get("method")
        if method:
            parsed["method"] = str(method).lower()
        return parsed

    def _build_termination_evaluation_prompt(
        self, final_state: Dict[str, Any], termination_description: str
    ) -> str:
        """Build the prompt used by the AP to decide whether the task is complete."""
        context = {
            "agent_id": self.agent_id,
            "task": {
                "task_id": self.task.task_id,
                "task_name": self.task.task_name,
                "description": self.task.description,
                "termination_condition": termination_description,
            },
            "state": final_state,
            "execution_history": self.execution_history,
        }
        return (
            "You are evaluating whether an Agent Program task is complete.\n"
            "Use the task description, the natural-language termination condition, "
            "and the current execution state.\n\n"
            "Return the termination evaluation using the requested structured schema.\n"
            "Schema fields: objective_reached boolean, reason string, final_actions list.\n\n"
            "Rules:\n"
            '- objective_reached must be a JSON boolean, never a string. Use true or false, not "true" or "false".\n'
            "- objective_reached must be true only if the termination condition is satisfied by the current state.\n"
            "- If another API call is needed, objective_reached must be false.\n"
            "- Do not invent state that is not present in the context.\n\n"
            "IMPORTANT: Write property names and values enclose in double quotes"
            f"CONTEXT:\n{json.dumps(context, ensure_ascii=False, default=str, indent=2)}\n"
        )

    async def _evaluate_objective(self, final_state: Dict[str, Any]) -> Dict[str, Any]:
        """Ask the Agent Program LLM whether the natural-language objective is reached."""
        termination_description = (
            getattr(self.task, "termination_condition", "")
            or "The task is complete after receiving a successful API response."
        )

        if not self.termination_llm:
            return {
                "objective_reached": "last_response" in final_state,
                "reason": (
                    "Termination LLM unavailable; using fallback completion after "
                    "successful service API response."
                ),
                "final_actions": [],
            }

        prompt = self._build_termination_evaluation_prompt(
            final_state, termination_description
        )
        try:
            structured_llm = self.termination_llm.with_structured_output(
                TerminationEvaluationOutput
            )
            evaluation = await asyncio.to_thread(
                structured_llm.invoke,
                prompt,
            )
        except Exception as structured_exc:
            logger.warning(
                "Structured termination evaluation failed for %s; falling back to JSON parsing: %s",
                self.task.task_name,
                structured_exc,
            )
            response = await asyncio.to_thread(
                self.termination_llm.invoke,
                prompt,
            )
            if not response or not response.content:
                raise RuntimeError(
                    "LLM returned an empty termination evaluation response"
                )
            evaluation = parse_llm_json_payload(
                response.content,
                expected_type=dict,
                error_prefix="Termination evaluation LLM response",
            )

        return structured_output_to_dict(evaluation)

    async def _notify_collaborators_and_dependents_failure(
        self, reason: str = "Unknown reason"
    ) -> None:
        """Send failure notifications to collaboration partners and dependents when the AP cannot continue."""
        failure_message = {
            "type": "dependency_failed",
            "sender_id": self.agent_id,
            "sender_task_id": self.task.task_id,
            "failed_tasks": [self.task.task_id],
            "reason": reason or "Agent program could not initialize or execute",
            "timestamp": datetime.now().isoformat(),
        }

        # Notify direct collaboration partners
        for partner_task_id in self.collaboration_partners:
            partner_agent_id = self.message_broker.get_agent_id_for_task(partner_task_id)
            if partner_agent_id:
                try:
                    await self.message_broker.send(partner_agent_id, failure_message)
                    print(
                        f"Agent {self.agent_id} notified collaborator {partner_task_id} about failure: {reason}\n\n"
                    )
                except Exception as e:
                    print(
                        f"Agent {self.agent_id} failed to notify collaborator {partner_task_id}: {e}\n\n"
                    )

        # Notify dependents via graph
        dependents = self.dependency_graph.get("dependents", {}).get(
            self.task.task_id, []
        )
        for dependent_task_id in dependents:
            dependent_agent_id = self.message_broker.get_agent_id_for_task(dependent_task_id)
            if dependent_agent_id:
                try:
                    await self.message_broker.send(dependent_agent_id, failure_message)
                    print(
                        f"Agent {self.agent_id} notified dependent {dependent_task_id} about failure: {reason}\n\n"
                    )
                except Exception as e:
                    print(
                        f"Agent {self.agent_id} failed to notify dependent {dependent_task_id}: {e}\n\n"
                    )

    async def _notify_collaborators(self) -> None:
        """Send task_completed notifications to collaboration partners."""
        if not self.collaboration_partners:
            return

        message = {
            "type": "task_completed",
            "sender_id": self.agent_id,
            "sender_task_id": self.task.task_id,
            "task_name": self.task.task_name,
            "final_state": self.state.copy(),
            "goal_reached": self.goal_reached,
            "timestamp": datetime.now().isoformat(),
        }

        for partner_task_id in self.collaboration_partners:
            partner_agent_id = self.message_broker.get_agent_id_for_task(
                partner_task_id
            )
            if partner_agent_id:
                try:
                    await self.message_broker.send(partner_agent_id, message)
                    print(
                        f"Agent {self.agent_id} sent task_completed to {partner_agent_id}\n\n"
                    )
                except Exception as e:
                    print(
                        f"Agent {self.agent_id} failed to notify {partner_agent_id}: {e}\n\n"
                    )

    async def _notify_dependents(
        self, service_response: Any, payload: Dict[str, Any]
    ) -> None:
        """Send completion messages to downstream agents defined by the graph."""
        dependents = self.dependency_graph.get("dependents", {}).get(
            self.task.task_id, []
        )
        if not dependents:
            return

        nodes = self.dependency_graph.get("nodes", {})
        message = {
            "type": "task_completed",
            "sender_id": self.agent_id,
            "sender_task_id": self.task.task_id,
            "task_name": self.task.task_name,
            "service_response": service_response,
            "payload": payload,
            "timestamp": datetime.now().isoformat(),
        }

        for dependent_task_id in dependents:
            dependent_node = nodes.get(dependent_task_id, {})
            dependent_task_name = dependent_node.get("task_name") or dependent_task_id
            dependent_agent_id = f"agent-{dependent_task_name}"
            await self.message_broker.send(dependent_agent_id, message)

    async def start(self):
        """Start the agent program execution."""
        try:
            # Register collaboration partners for this AP
            await self.message_broker.register_collaboration(
                task_id=self.task.task_id,
                can_collaborate_with=self.collaboration_partners,
            )
            print(f"Agent {self.agent_id} started")

            self.is_running = True

            # Start background message monitor
            self._monitor_active = True
            self._message_monitor_task = asyncio.create_task(self._message_monitor())

            await self._execute_task(self.task)

            self.is_completed = True
            self.completion_result = {
                "status": "success",
                "task_id": self.task.task_id,
                "task_name": self.task.task_name,
                "final_state": self.state.copy(),
                "service_address": self.service_address,
                "service_response": self.service_response,
                "dependency_messages": self.dependency_messages.copy(),
                "goal_reached": self.goal_reached,
                "execution_history": self.execution_history.copy(),
            }

        except Exception as e:
            self.is_running = False
            self.is_completed = True
            error_message = str(e) or repr(e) or e.__class__.__name__
            error_details = f"{e.__class__.__name__}: {error_message}"
            self.error = error_details
            self.completion_result = {
                "status": "failed",
                "task_id": self.task.task_id,
                "task_name": self.task.task_name,
                "error": error_details,
            }

            print(f"Agent {self.agent_id} failed: {error_details}")
            logger.exception("Agent %s failed", self.agent_id)

            # Notify collaborators and dependents about the failure before cleanup
            try:
                await self._notify_collaborators_and_dependents_failure(reason=error_details)
            except Exception as notify_error:
                print(f"Error notifying failure to collaborators/dependents: {notify_error}")

        finally:
            self.is_running = False
            await self._cleanup()

    def _request_service_json(
        self,
        method: str,
        url: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = 10.0,
    ) -> Tuple[int, Any]:
        """Call a generated service endpoint and return its HTTP status code and JSON body."""
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                try:
                    body = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    # Response is not JSON, return raw text
                    body = raw
                return response.status, body
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                body = raw
            return exc.code, body

    async def _execute_task(self, task: TaskDefinition):
        """Execute a task with AI-driven planning and evaluation."""
        retry_count = 0
        iteration = 0

        if self.task_dependency_ids != []:
            self.dependency_messages = await self._wait_for_dependency_messages()
        if not self.service_address:
            raise RuntimeError("Service URL not available for task execution")

        # if getattr(task, "requires_ui", False) and self.service_address:
        #     ui_url = f"{self.service_address}/"
        #     print(f"\n{'=' * 20}")
        #     print("Opening UI")
        #     print(f"{'=' * 20}\n")
        #     # Use subprocess to open Firefox in a new window to avoid conflicts
        #     # with existing Firefox sessions
        #     try:
        #         subprocess.run(
        #             ["firefox", "--new-window", ui_url],
        #             timeout=5,
        #             capture_output=True,
        #         )
        #     except Exception:
        #         # Fallback to webbrowser if subprocess fails
        #         import webbrowser

        #         webbrowser.open(ui_url)

        api_schema_url = f"{self.service_address}/api-schema"
        print(f"Agent {self.agent_id}: fetching API schema from {api_schema_url}")

        api_schema = None
        schema_timeout = 10
        schema_max_retries = 3
        for attempt in range(schema_max_retries):
            try:
                api_schema = await asyncio.to_thread(
                    request_json, "GET", api_schema_url, timeout=schema_timeout
                )
                break
            except Exception as e:
                if attempt < schema_max_retries - 1:
                    wait_time = 2.0 * (2**attempt)
                    print(f"Agent {self.agent_id}: failed to fetch API schema ")
                    await asyncio.sleep(wait_time)
                else:
                    raise RuntimeError(
                        f"Unable to fetch API schema for service after {schema_max_retries} attempts: {e}"
                    )
        if not api_schema:
            raise RuntimeError("API schema not fetched after retries")
        plan = None
        last_observation = None
        while iteration < self.max_iterations:
            try:
                iteration += 1

                # Process any pending messages before planning
                await self._check_and_process_messages(timeout=0.1)

                # For UI tasks, fetch /api/status automatically before planning
                if getattr(task, "requires_ui", False):
                    status_code, last_observation = await asyncio.to_thread(
                        self._request_service_json,
                        "GET",
                        f"{self.service_address}/api/status",
                    )
                    if status_code == 200 and last_observation:
                        # Update state with the observation
                        self.state["last_observation"] = last_observation
                        # Track turn for multiplayer change detection
                        if isinstance(last_observation, dict):
                            self.state["last_turn"] = last_observation.get("turn")
                            self.state["last_api_status"] = last_observation

                # Skip planning if goal already reached
                if self.goal_reached:
                    break

                plan = await self._plan_next_action(api_schema, last_observation)
                self.last_action = plan.copy()
                self.execution_history.append(
                    {
                        "iteration": iteration,
                        "phase": "plan",
                        "plan": plan,
                    }
                )
                print(f"Plan: {plan}\n")

                # Handle inter-agent messaging if specified
                messages = plan.get("messages")
                if messages and not self.goal_reached:
                    for message in messages.values():
                        message_response = await self._send_agent_message(message)
                        if message_response:
                            self.state["last_message_response"] = message_response
                        self.execution_history.append(
                            {
                                "iteration": iteration,
                                "phase": "message_sent",
                                "message": message,
                                "response": message_response,
                            }
                        )

                endpoint = plan.get("endpoint")
                method = (
                    str(plan.get("method", "")).lower() if plan.get("method") else None
                )

                # Service call is optional - AP might only send/receive messages
                request_payload = None
                if endpoint and method:
                    operation = (api_schema.get("paths") or {}).get(endpoint) or {}

                    request_payload = self._build_request_payload(
                        operation.get(method) or {}, plan
                    )
                    request_url = f"{self.service_address}{endpoint}"
                    print(f"Requesting {method.upper()} {request_url}\n\n")
                    if method == "get":
                        status_code, self.service_response = await asyncio.to_thread(
                            self._request_service_json, method.upper(), request_url
                        )
                    else:
                        status_code, self.service_response = await asyncio.to_thread(
                            self._request_service_json,
                            method.upper(),
                            request_url,
                            request_payload,
                        )

                    if status_code != 200:
                        print(
                            f"Request failed with status {status_code}: {request_url}. "
                            f"Response: {self.service_response}"
                        )
                        continue

                    print(f"Service response: {self.service_response}\n\n")
                    exec_context = ExecutionContext(
                        agent_id=self.agent_id,
                        task_name=task.task_name,
                    )
                    exec_context.update_state("service_address", self.service_address)
                    exec_context.update_state("api_schema", api_schema)
                    exec_context.update_state("last_plan", plan)
                    exec_context.update_state("last_endpoint", endpoint)
                    exec_context.update_state("last_method", method)
                    exec_context.update_state("last_payload", request_payload)
                    exec_context.update_state("last_response", self.service_response)
                    exec_context.update_state(
                        "dependency_messages", self.dependency_messages.copy()
                    )

                    final_state = exec_context.get_all_state()
                    self.state.update(final_state)

                    # Track last /api/status and turn for multiplayer change detection
                    if endpoint == "/api/status" and self.service_response:
                        self.state["last_api_status"] = self.service_response
                        if isinstance(self.service_response, dict):
                            self.state["last_turn"] = self.service_response.get("turn")
                else:
                    # Still update state with current context even if no service call
                    if not hasattr(self, "last_endpoint"):
                        self.state["last_endpoint"] = None
                        self.state["last_method"] = None
                        self.state["last_payload"] = None
                        self.state["last_response"] = None

                eval_result = await self._evaluate_objective(self.state)
                self.goal_reached = eval_result.get("objective_reached")
                self.execution_history.append(
                    {
                        "iteration": iteration,
                        "phase": "execute",
                        "endpoint": endpoint,
                        "method": method,
                        "payload": request_payload or {},
                        "response": self.service_response,
                        "evaluation": eval_result,
                    }
                )
                self.state.update(
                    {"last_evaluation": eval_result, "iteration": iteration}
                )

                # Reset error state
                self.state["last_error"] = ""
                self.state["last_failed_endpoint"] = ""
                self.state["last_failed_method"] = ""

                if self.goal_reached:
                    await self._notify_collaborators()
                    await self._notify_dependents(
                        self.service_response,
                        request_payload or {},
                    )
                    self._monitor_active = False
                    return

            except Exception as e:
                retry_count += 1
                error_message = str(e) or repr(e) or e.__class__.__name__
                error_details = f"{e.__class__.__name__}: {error_message}"
                wait_time = task.retry_delay_sec * (
                    task.retry_backoff_multiplier ** (retry_count - 1)
                )
                print(
                    f"{retry_count}/{task.max_retries} failed. "
                    f"Reason: {error_details}. Next retry in {wait_time}s"
                )

                # Aggiungi l'errore allo state per il prossimo planning
                self.state["last_error"] = error_details
                self.state["last_failed_endpoint"] = (
                    plan.get("endpoint") if plan else ""
                )
                self.state["last_failed_method"] = plan.get("method") if plan else ""

                if retry_count >= task.max_retries:
                    # Notify collaborators and dependents before raising
                    await self._notify_collaborators_and_dependents_failure(
                        reason=f"Failed after {retry_count} retries: {error_details}"
                    )
                    raise RuntimeError(
                        f"Task {task.task_name} failed after {retry_count} retries"
                    )
                print(f"Retrying task {task.task_name}")
                await asyncio.sleep(wait_time)

        raise RuntimeError(
            f"Task {task.task_name} exceeded max AI planning iterations ({self.max_iterations})"
        )

    async def _message_monitor(self):
        """Background coroutine that continuously monitors for incoming messages."""
        monitor_interval = 0.5
        try:
            while self._monitor_active and not self.is_completed:
                await self._check_and_process_messages(timeout=1)
                await asyncio.sleep(monitor_interval)
        except asyncio.CancelledError:
            logger.debug("Message monitor coroutine cancelled")
        except Exception as e:
            logger.error(f"Message monitor error: {e}")

    async def _check_and_process_messages(self, timeout: float = 0.1):
        """Check for and process any incoming inter-agent messages."""
        message = await self.message_broker.get_message(self.agent_id, timeout=timeout)
        if not message:
            return

        msg_type = message.get("type")

        if msg_type == "dependency_failed":
            # Handle dependency failure notification from controller
            await self._handle_dependency_failed(message)

        elif msg_type == "task_completed":
            # Handle task_completed from collaborators
            await self._handle_collaborator_completed(message)

        elif msg_type == "request":
            await self._handle_request_message(message)

        elif msg_type == "response":
            await self._handle_response_message(message)

        else:
            logger.warning(
                f"Agent {self.agent_id} received unknown message type: {msg_type}"
            )

    async def _handle_request_message(self, message: Dict[str, Any]):
        """Handle incoming request that expects a response."""
        if "incoming_requests" not in self.state:
            self.state["incoming_requests"] = []

        self.state["incoming_requests"].append(
            {
                "request_id": message.get("request_id"),
                "sender_id": message.get("sender_id"),
                "content": message.get("content", {}),
                "timestamp": message.get("timestamp"),
            }
        )

        print(
            f"Agent {self.agent_id} received request from {message.get('sender_id')}: "
            f"{message.get('content')}\n\n"
        )

    async def _handle_dependency_failed(self, message: Dict[str, Any]):
        """Handle notification that a dependency task failed during creation."""
        failed_tasks = message.get("failed_tasks", [])
        reason = message.get("reason", "Unknown reason")

        print(
            f"Agent {self.agent_id} received dependency failure notification: "
            f"tasks {failed_tasks} failed - {reason}"
        )

        # Mark this task as failed due to dependency failure
        self.is_running = False
        self.is_completed = True
        error_message = f"Dependency failure: {', '.join(failed_tasks)} failed during creation. {reason}"
        self.error = error_message
        self.completion_result = {
            "status": "failed",
            "task_id": self.task.task_id,
            "task_name": self.task.task_name,
            "error": error_message,
            "reason": "dependency_failed",
        }

        # Stop the message monitor
        self._monitor_active = False

    async def _handle_collaborator_completed(self, message: Dict[str, Any]):
        """Handle task_completed notification from a collaboration partner."""
        sender_task_id = message.get("sender_task_id")
        if sender_task_id not in self.collaboration_partners:
            print(
                f"Agent {self.agent_id} received task_completed from unknown sender: {sender_task_id}"
            )
            return
        sender_agent_id = message.get("sender_id")
        goal_reached = message.get("goal_reached", False)

        print(f"Agent {self.agent_id} received task_completed from {sender_agent_id} ")

        # Store the collaborator completion info
        if "collaborator_completed" not in self.state:
            self.state["collaborator_completed"] = {}

        self.state["collaborator_completed"][sender_agent_id] = {
            "goal_reached": goal_reached,
            "final_state": message.get("final_state", {}),
            "timestamp": message.get("timestamp"),
        }

        self.collaboration_partners.remove(sender_task_id)

    async def _handle_response_message(self, message: Dict[str, Any]):
        """Handle incoming response to a prior request."""
        if "incoming_messages" not in self.state:
            self.state["incoming_messages"] = []

        self.state["incoming_messages"].append(
            {
                "request_id": message.get("request_id") or "",
                "sender_id": message.get("sender_id"),
                "content": message.get("content", {}),
                "timestamp": message.get("timestamp"),
            }
        )

        # Set last_message_response for immediate access by the LLM
        self.state["last_message_response"] = message.get("content", {})
        self.pending_outbound_request = None

        print(f"Agent {self.agent_id} received response: {message.get('content')}\n\n")

    async def _send_agent_message(self, message_spec: Dict[str, Any]) -> Any:
        """Send a message to another agent based on LLM plan."""
        target_task_id = message_spec.get("target_task_id")

        if not target_task_id:
            raise ValueError("send_message spec must include target_task_id")

        if not self.message_broker.validate_messaging_allowed(
            self.task.task_id, target_task_id
        ):
            raise ValueError(
                f"Task {self.task.task_id} is not allowed to message {target_task_id}"
            )

        # Generate a unique message ID to prevent duplicate sends
        msg_type = message_spec.get("type")
        content_str = json.dumps(message_spec.get("content", {}), sort_keys=True, default=str)
        message_id = f"{target_task_id}:{msg_type}:{content_str}"

        if message_id in self._sent_message_ids:
            print(f"Agent {self.agent_id}: skipping duplicate message to {target_task_id} (already sent)\n\n")
            return None

        self._sent_message_ids.add(message_id)

        target_agent_id = self.message_broker.get_agent_id_for_task(target_task_id)
        if not target_agent_id:
            raise ValueError(f"Unknown target task: {target_task_id}")

        message = {
            "type": msg_type,
            "sender_task_id": self.task.task_id,
            "sender_id": self.agent_id,
            "target_task_id": target_task_id,
            "content": message_spec.get("content", {}),
            "timestamp": datetime.now().isoformat(),
        }

        request_id = message_spec.get("request_id") or None
        if request_id:
            message["request_id"] = request_id
            print(f"Agent {self.agent_id} responded to {target_agent_id}")
            print(f"Agent {self.agent_id} sending message: {message}\n\n")
            await self.message_broker.respond(request_id, message)
            # Clear the processed request from incoming_requests
            incoming = self.state.get("incoming_requests", [])
            self.state["incoming_requests"] = [
                r for r in incoming if r.get("request_id") != request_id
            ]
            return None
        elif message_spec.get("wait_for_response", False) and (
            "incoming_requests" not in self.state
            or len(self.state["incoming_requests"]) == 0
        ):
            timeout_sec = 60.0
            max_retries = 3
            retry_delay = 5.0

            for attempt in range(max_retries):
                request_id = str(uuid.uuid4())
                self.pending_outbound_request = request_id
                print(f"Agent {self.agent_id} sending request to {target_agent_id}")
                print(f"Agent {self.agent_id} sending message: {message}\n\n")
                response = await self.message_broker.request(
                    sender_id=self.agent_id,
                    target_agent_id=target_agent_id,
                    request=message,
                    timeout=timeout_sec,
                    request_id=request_id,
                )
                if response is not None:
                    self.pending_outbound_request = None
                    return response

                if attempt < max_retries - 1:
                    print(
                        f"Agent {self.agent_id}: no response from {target_agent_id}, "
                        f"retrying in {retry_delay}s..."
                    )
                    await asyncio.sleep(retry_delay)

            self.pending_outbound_request = None
            print(
                f"Agent {self.agent_id}: all {max_retries} attempts failed "
                f"to reach {target_agent_id}"
            )
            return None
        elif msg_type == "response" or msg_type == "request":
            print(f"Agent {self.agent_id} sending message to {target_agent_id} ")
            print(f"Agent {self.agent_id} sending message: {message}\n\n")
            await self.message_broker.send(target_agent_id, message)
            return None
        else:
            return None

    async def _cleanup(self):
        """Clean up resources after execution."""
        print(f"Cleaning up resources for agent {self.agent_id}")

        # Stop message monitor
        self._monitor_active = False
        if self._message_monitor_task and not self._message_monitor_task.done():
            self._message_monitor_task.cancel()
            try:
                await self._message_monitor_task
            except asyncio.CancelledError:
                pass

        try:
            await self.message_broker.cleanup_agent(self.agent_id)
        except Exception as e:
            print(f"Cleanup error: {e}")
