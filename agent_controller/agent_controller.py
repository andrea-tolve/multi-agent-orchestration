"""
Agent Controller: orchestrates goal decomposition and agent program management.
Primary decomposition logic is code-based for efficiency; LLM assists only for complex reasoning.
"""

import argparse
import asyncio
import csv
import importlib
import logging
import os
import re
import signal
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from shared.utils import (
    load_llm_model,
    parse_llm_json_payload,
    request_json,
    request_json_async,
)

from .agent_program import AgentProgram
from .definitions import (
    GoalDecompositionOutput,
    TaskDefinition,
)
from .message_broker import MessageBroker

PromptTemplate = None

PromptTemplate = importlib.import_module("langchain_core.prompts").PromptTemplate

logger = logging.getLogger(__name__)


class AgentController:
    """
    Orchestrates goal decomposition and manages Agent Programs.
    Uses code-based decomposition for efficiency and LLM for complex reasoning.
    """

    def __init__(self, service_manager_url, global_registry_url, controller_port=8080):
        self.service_manager_url = service_manager_url
        self.global_registry_url = global_registry_url
        self.message_broker = MessageBroker(
            global_registry_url=self.global_registry_url
        )

        # Execution state
        self.tasks: List[TaskDefinition] = []
        self.agent_programs: Dict[str, AgentProgram] = {}
        self.goal_dependency_graph: Dict[str, Any] = {}
        self._token_lock = threading.Lock()
        self._time_lock = threading.Lock()
        self._total_time: float = 0.0
        self._total_tokens: int = 0
        # Remote routing: task_id → {agent_id, controller_url}
        self._remote_controller_urls: Dict[str, Dict[str, str]] = {}
        # Tracked remote agents for /status/<task_id> polling
        self._spawned_agents: Dict[str, dict] = {}
        # Our own controller URL (used to tell remote brokers where we are)
        self._local_controller_url = f"http://127.0.0.1:{controller_port}"

        # Own Flask server
        self._app = Flask(__name__)
        self._register_routes(self._app)

        # Initialize LLM for complex decompositions
        self._init_llm()

    def _init_llm(self):
        """Initialize LLM client for reasoning-heavy decompositions."""
        try:
            self.llm = load_llm_model(os.getenv("SERVICE_MODEL"), temperature=0.5)
        except Exception as exc:
            logger.warning(
                "LLM is not available. Complex decompositions will be limited: %s",
                exc,
            )
            self.llm = None

    def _create_service_for_task(self, task: TaskDefinition) -> Tuple[dict, bool]:
        """
        Call SM.createService for the task and return (response, is_delegated).
        The controller decides delegation here before building the AP.
        """
        payload = {
            "service_name": task.task_name,
            "service_description": (task.service_description or task.description),
            "app_dir": task.task_name,
            "kind": "kubernetes",
            "requires_ui": getattr(task, "requires_ui", False),
            "ui_description": getattr(task, "ui_description", ""),
            "api_endpoint": getattr(task, "api_endpoint", ""),
        }
        if getattr(task, "multiplayer_move_endpoint", None):
            payload["multiplayer_move_endpoint"] = task.multiplayer_move_endpoint

        start = time.time()
        response = request_json(
            "POST",
            self.service_manager_url + "/create_service",
            payload,
            timeout=500,
        )
        end = time.time()
        with self._time_lock:
            self._total_time += end - start

        if not response:
            raise Exception(f"Empty response from SM for task {task.task_name}")

        delegate = response.get("delegation_required", False)
        with self._token_lock:
            self._total_tokens += response.get("total_tokens", 0)
        return response, delegate

    def _create_agent_program_for_task(
        self, task: TaskDefinition, dependency_graph: Dict[str, Any]
    ) -> Tuple[AgentProgram | None, bool]:
        """
        Returns (ap, delegate).
        If delegation_required=True: does NOT create local AP, delegates to remote controller.
        If local: creates AP using pre-computed service_response.
        """
        service_response, delegate = self._create_service_for_task(task)

        if delegate:
            remote_controller_url = service_response.get("delegate_controller_url")
            print(
                f"Delegating task {task.task_id} to remote controller {remote_controller_url}"
            )
            task_id = task.task_id
            task.is_delegated = True
            if not remote_controller_url:
                raise RuntimeError(
                    f"delegation_required=True but no delegate_controller_url for task {task_id}"
                )
            # Forward task to remote controller via HTTP
            remote_response = request_json(
                "POST",
                f"{remote_controller_url}/spawn_ap",
                task.to_dict(),
                timeout=500,
            )

            if remote_response is None:
                raise RuntimeError(
                    f"Failed to spawn agent program for task {task_id}: no response from remote controller"
                )
            if remote_response.get("error"):
                print(remote_response.get("error"))
                return None, False

            remote_agent_id = remote_response.get("agent_id")
            if remote_agent_id:
                # Store task_id -> controller_url for polling
                self._remote_controller_urls[task_id] = {
                    "agent_id": remote_agent_id,
                    "controller_url": remote_controller_url,
                }
                print(
                    f"[_spawn_agent] remote_agent_id: {remote_agent_id}, task_id: {task_id}"
                )
                # Register in our broker so we can route inbound messages
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                if loop.is_running():
                    asyncio.create_task(
                        self.message_broker.register_remote_agent(
                            task_id, remote_agent_id, remote_controller_url
                        )
                    )
                else:
                    loop.run_until_complete(
                        self.message_broker.register_remote_agent(
                            task_id, remote_agent_id, remote_controller_url
                        )
                    )

            return None, True

        print(f"Create local AP for task {task.task_id}")

        # Local AP — use factory to avoid calling SM again
        ap = AgentProgram.from_service_response(
            task=task,
            service_response=service_response,
            agent_service_manager_url=self.service_manager_url,
            message_broker=self.message_broker,
            dependency_graph=dependency_graph,
        )
        return ap, False

    def _print_task_definitions(self, tasks: List[TaskDefinition]) -> None:
        """Print decomposed tasks with dependencies and termination descriptions."""
        for task in tasks:
            termination_condition = (
                getattr(task, "termination_condition", "") or "<none>"
            )
            print(
                "Task definition:\n"
                f"  id: {task.task_id}\n"
                f"  name: {task.task_name}\n"
                f"  description: {task.description}\n"
                f"  service_description: {task.service_description}\n"
                f"  dependencies: {list(task.depends_on)}\n"
                f"  information_dependencies: {list(task.information_dependencies)}\n"
                f"  termination_condition: {termination_condition}\n"
            )
            if task.requires_ui:
                print(
                    f"  requires_ui: {task.requires_ui}\n"
                    f"  ui_description: {task.ui_description}\n"
                    f"  api_endpoint: {task.api_endpoint}"
                )

    async def _notify_failed_tasks(
        self, failed_task_ids: set, dependency_graph: Dict[str, Any]
    ) -> None:
        """
        Send dependency_failed notifications to tasks that depend on failed tasks.
        Called after service creation phase when some tasks fail.
        """
        nodes = dependency_graph.get("nodes", {})
        dependents_map = dependency_graph.get("dependents", {})

        for failed_task_id in failed_task_ids:
            dependents = dependents_map.get(failed_task_id, [])
            if not dependents:
                continue

            failure_message = {
                "type": "dependency_failed",
                "sender_id": f"controller",
                "failed_tasks": [failed_task_id],
                "reason": f"Task {failed_task_id} failed during service creation",
                "timestamp": datetime.now().isoformat(),
            }

            for dependent_task_id in dependents:
                dependent_agent_id = self.message_broker.get_agent_id_for_task(
                    dependent_task_id
                )
                if dependent_agent_id:
                    try:
                        await self.message_broker.send(
                            dependent_agent_id, failure_message
                        )
                        print(
                            f"[_notify_failed_tasks] Notified {dependent_task_id} "
                            f"about failure of {failed_task_id}\n\n"
                        )
                    except Exception as e:
                        print(
                            f"[_notify_failed_tasks] Failed to notify {dependent_task_id}: {e}\n\n"
                        )

    def _build_dependency_graph(self, tasks: List[TaskDefinition]) -> Dict[str, Any]:
        """Build a dependency graph from the decomposed tasks."""
        nodes: Dict[str, Dict[str, Any]] = {}
        edges: Dict[str, List[str]] = {}
        in_degree: Dict[str, int] = {}
        dependents: Dict[str, List[str]] = defaultdict(list)

        for task in tasks:
            nodes[task.task_id] = {
                "task_id": task.task_id,
                "task_name": task.task_name,
                "description": task.description,
                "depends_on": list(task.depends_on),
            }
            edges[task.task_id] = list(task.depends_on)
            in_degree[task.task_id] = 0

        for task in tasks:
            for dependency_id in task.depends_on:
                if dependency_id not in nodes:
                    raise ValueError(
                        f"Task {task.task_id} depends on unknown task {dependency_id}"
                    )
                dependents[dependency_id].append(task.task_id)
                in_degree[task.task_id] += 1

        ready = [task_id for task_id, degree in in_degree.items() if degree == 0]
        topological_order: List[str] = []

        while ready:
            current = ready.pop(0)
            topological_order.append(current)

            for dependent_id in dependents.get(current, []):
                in_degree[dependent_id] -= 1
                if in_degree[dependent_id] == 0:
                    ready.append(dependent_id)

        if len(topological_order) != len(tasks):
            unresolved = [
                task_id for task_id, degree in in_degree.items() if degree > 0
            ]
            raise ValueError(
                "Circular dependency detected among tasks. "
                f"Unresolved tasks: {unresolved}"
            )

        return {
            "nodes": nodes,
            "edges": edges,
            "dependents": dict(dependents),
            "in_degree": {
                task_id: len(nodes[task_id]["depends_on"]) for task_id in nodes
            },
            "topological_order": topological_order,
        }

    async def _distribute_routing_to_remote_controllers(self):
        """
        For each remote controller, send routes for tasks owned by THIS controller.
        Remote agents need to know how to reach local agents on this controller.
        """
        # Build complete routing table: task_id → {agent_id, controller_url}
        routing: Dict[str, Dict[str, str]] = {}

        # Remote tasks (known by this controller)
        for task_id, info in self._remote_controller_urls.items():
            routing[task_id] = info

        # Local tasks → this controller
        for aid, ap in self.agent_programs.items():
            routing[ap.task.task_id] = {
                "agent_id": aid,
                "controller_url": self._local_controller_url,
            }

        # Routes owned by THIS controller (for sending to remote controllers)
        local_routes = {
            task_id: info
            for task_id, info in routing.items()
            if info.get("controller_url") == self._local_controller_url
        }

        print(f"[distribute_routes] routing: {routing}")
        print(f"[distribute_routes] local_routes (to distribute): {local_routes}")

        # Send local routes to each remote controller
        for task_id, info in self._remote_controller_urls.items():
            remote_controller_url = info.get("controller_url")
            if not remote_controller_url:
                continue
            # Filter: exclude routes that belong to the remote controller itself
            routes_to_send = {
                tid: route
                for tid, route in local_routes.items()
                if route.get("controller_url") != remote_controller_url
            }
            print(f"Sending to {remote_controller_url}: {routes_to_send}")
            try:
                await request_json_async(
                    "POST",
                    f"{remote_controller_url}/broker/update_routes",
                    payload={"routes": routes_to_send},
                    timeout=30.0,
                )
            except Exception as e:
                print(
                    f"[distribute_routes] Failed to update {remote_controller_url}: {e}"
                )

    async def _wait_for_remote_agents(self):
        """Poll /status/<task_id> on remote controllers until all complete."""
        pending = dict(self._remote_controller_urls)
        while pending:
            completed = set()
            for task_id, remote in list(pending.items()):
                print(
                    f"[wait_for_remote_agents] polling {task_id} at {remote.get('controller_url')}"
                )
                try:
                    resp = await request_json_async(
                        "GET",
                        f"{remote.get('controller_url')}/status/{task_id}",
                        timeout=10.0,
                    )

                    if resp is None:
                        continue
                    if resp.get("status") is None:
                        pending.pop(task_id, None)
                    elif resp.get("status") in ("completed", "failed"):
                        self._spawned_agents[task_id] = resp
                        completed.add(task_id)
                except Exception:
                    pass
            for t in completed:
                pending.pop(t, None)
            if pending:
                await asyncio.sleep(5)

    def _write_token_usage(self, success_rate=-1.0):
        FIELDNAMES = [
            "type",
            "total_tokens",
            "time (s)",
            "success_rate (%)",
        ]

        row = {
            "type": os.environ.get("SERVICE_GENERATION_MODE"),
            "total_tokens": self._total_tokens,
            "time (s)": f"{self._total_time:.2f}",
            "success_rate (%)": success_rate,
        }

        csv_path = "llm_info/info.csv"

        file_exists = os.path.exists(csv_path)
        file_is_empty = (not file_exists) or os.path.getsize(csv_path) == 0

        with open(csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)

            if file_is_empty:
                writer.writeheader()

            writer.writerow(row)

        self._total_tokens = 0
        self._total_time = 0

    async def run(self, goal_description: str) -> Dict[str, Any]:
        """
        Execute a goal by decomposing it into tasks and creating agent programs.
        If a task creation fails, notify dependent tasks via message broker.
        """
        try:
            # Decompose goal into tasks
            self.tasks = await self._decompose_goal(goal_description)
            if len(self.tasks) == 0:
                return {"status": "failed", "message": "No tasks were created"}
            print(f"Goal decomposed into {len(self.tasks)} tasks")
            self._print_task_definitions(self.tasks)

            # Build dependency graph from the created tasks
            dependency_graph = self._build_dependency_graph(self.tasks)
            self.goal_dependency_graph = dependency_graph

            # Phase 1: service creation + delegation (runs in worker threads)
            local_aps: List[AgentProgram] = []
            delegated_task_ids: set = set()
            failed_task_ids: set = set()
            num_success = 0
            failed = False

            def create_one(task, dep_graph):
                try:
                    ap, is_delegated = self._create_agent_program_for_task(
                        task, dep_graph
                    )
                    if is_delegated:
                        delegated_task_ids.add(task.task_id)
                        print(f"Task {task.task_id} delegated to remote controller")
                    return ap, None, is_delegated  # (result, error, is_delegated)
                except Exception as e:
                    error_msg = str(e)
                    print(
                        f"Failed to create agent program for task {task.task_id}: {error_msg}"
                    )
                    failed_task_ids.add(task.task_id)
                    return None, error_msg, False

            created = await asyncio.gather(
                *[
                    asyncio.to_thread(create_one, task, dependency_graph)
                    for task in self.tasks
                ],
                return_exceptions=False,
            )
            # Process results
            for i, (ap, error, is_delegated) in enumerate(created):
                if ap is not None and not is_delegated:
                    local_aps.append(ap)

            # Calculate number of services created successfully
            num_success = len(local_aps) + len(delegated_task_ids)

            if failed_task_ids:
                failed = True
                print(f"[run] {len(failed_task_ids)} tasks failed during creation")
                # Notify dependents of failed tasks
                await self._notify_failed_tasks(failed_task_ids, dependency_graph)
            else:
                self.agent_programs = {ap.agent_id: ap for ap in local_aps}
                # Phase 2: distribute routing info to remote controllers
                if self._remote_controller_urls:
                    await self._distribute_routing_to_remote_controllers()

                # Phase 3: run local APs
                if local_aps:
                    print(f"[run] Running {len(local_aps)} local agent programs")
                    await self._run_agent_programs(local_aps)

                # Phase 4: wait for remote APs
                if self._remote_controller_urls:
                    print("[run] Waiting for remote agent programs to complete")
                    await self._wait_for_remote_agents()

                # Phase 5: final status check
                all_local_succeeded = all(
                    ap.is_completed and not ap.error for ap in local_aps
                )
                all_remote_succeeded = all(
                    self._spawned_agents.get(tid, {}).get("status") == "completed"
                    for tid in delegated_task_ids
                )
                # Check if all local and remote APs have succeeded
                if not all_local_succeeded and not all_remote_succeeded:
                    failed = True


            # Stop services
            print("[run] Stopping services")
            for _, ap in self.agent_programs.items():
                request_json(
                    "POST",
                    f"{self.service_manager_url}/stop_service",
                    {"service_name": ap.task.task_name},
                )

            # Stop remote services
            if self._remote_controller_urls:
                print("[run] Stopping remote services")
                for task in self.tasks:
                    if task.is_delegated:
                        url = self._remote_controller_urls[task.task_id].get(
                            "controller_url"
                        )
                        request_json(
                            "POST",
                            f"{url}/stop_service",
                            {"service_name": task.task_name},
                        )
            success_rate = num_success / len(self.tasks) * 100
            self._write_token_usage(success_rate)
            if failed:
                return {
                    "status": "failed",
                }
            else:
                return {
                    "status": "success",
                }

        except Exception as e:
            print(f"Goal execution failed: {str(e)}")
            return {
                "status": "failed",
                "error": str(e),
            }

    async def _decompose_goal(self, goal_description) -> List[TaskDefinition]:
        """
        Decompose a goal into tasks.
        Uses code-based patterns for common cases; LLM for complex goals.
        """

        if self.llm:
            tasks = await self._decompose_with_llm(goal_description)
            return tasks

        # No decomposition possible
        raise ValueError(
            f"Cannot decompose goal: {goal_description}. "
            "No matching patterns and LLM not available."
        )

    def _normalize_k8s_name(self, value: str, fallback: str = "task") -> str:
        """Normalize names to a lowercase K8s-safe slug."""
        normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        if not normalized:
            normalized = fallback
        if not re.match(r"^[a-z0-9]", normalized):
            normalized = f"{fallback}-{normalized}"
        normalized = re.sub(r"-+", "-", normalized)
        return normalized[:63].rstrip("-") or fallback

    async def _decompose_with_llm(self, goal_description) -> List[TaskDefinition]:
        """
        Use LLM for complex goal decomposition.
        Returns structured task definitions from LLM reasoning.
        """

        if PromptTemplate is None:
            raise ValueError(
                "LLM dependencies are not available for goal decomposition"
            )

        prompt = PromptTemplate(
            input_variables=["goal"],
            template="""Decompose this goal into atomic executable tasks.

            Goal: {goal}

            Return the goal decomposition using the requested structured schema.

            Expected schema:
            {{
              "tasks": [
                {{
                  "task_id": "task_002",
                  "task_name": "task_name",
                  "description": "What the Agent Program should do (coordination, messaging, orchestration logic)",
                  "service_description": "What the Flask service itself should do (the technical action only)",
                  "termination_condition": "Natural-language condition that tells the Agent Program when this task is complete.",
                  "dependencies": [
                    "task_001"
                  ],
                  "information_dependencies": [],
                  "requires_ui": false,
                  "ui_description": null,
                  "api_endpoint": null
                }}
              ]
            }}

            UI Tasks schema (when requires_ui is true):
            {{
              "task_id": "task_003",
              "task_name": "task_name",
              "description": "Brief task description",
              "service_description": "Description of what the Flask service itself should do (the technical action, without coordination logic).",
              "termination_condition": "Natural-language condition that tells the Agent Program when this task is complete.",
              "dependencies": [],
              "information_dependencies": [],
              "requires_ui": true,
              "ui_description": "form with three buttons: rock, paper, scissors; shows the game result against the computer",
              "api_endpoint": "/api/rps/play"
            }}

            Rules:
            - "IMPORTANT: Write property names and values enclose in double quotes"
            - Use task ids in task_XXX format.
            - IMPORTANT: Task names don't have to include "-service" or similar suffixes.
            - Minimize the total number of tasks required to achieve the goal.
            - Prefer a single task whenever one cohesive task can accomplish the goal end-to-end.
            - If the goal contains substantially different activities, responsibilities, domains, or robot capabilities, split them into separate tasks even if this increases the task count.
            - Do not split the goal into setup-only or scaffolding-only tasks unless they are strictly necessary.
            - Every task is deployed as a Flask web service.
            - Task and service names must be lowercase and K8s-safe (lowercase alphanumeric plus hyphen only, no spaces).
            - IMPORTANT: Preserve specific names. If the goal mentions specific names (robot names, capability names, service names, or other relevant identifiers), these names MUST be preserved verbatim in the task description and service description. Do not replace, generalize, or remove names that the user explicitly mentioned. For example, if the goal says "use robot-x" or "capability-y", the task description should include "robot-x" and "capability-y" exactly as specified.

            DESCRIPTION vs SERVICE_DESCRIPTION:
            - "description": What the AGENT PROGRAM should do. Include coordination logic, messaging strategy, orchestration, turn-taking, etc.
              This is the AP's responsibility - how it should interact with other APs.
            - "service_description": What the FLASK SERVICE should do. ONLY the technical action/capability, keep it simple not too long or too complex.
              The service should NOT know about other services or agents. It just performs its assigned task.
            - Example (counting task):
              - description: "Coordinate with agent-two to count from 0 to 5 via message passing. Alternate calls."
              - service_description: "Receive a number, increment it, return the result."
            - Example (UI task):
              - description: "Render a form and process user input, sending results to the logic agent."
              - service_description: "Display a form with fields X, Y, Z and accept POST requests with user data."

            - Task descriptions must NOT describe implementation technologies, sensors, APIs, algorithms, data sources, or robot capabilities unless the user explicitly names them.
            - Express tasks in capability-neutral terms focused on the desired outcome.
            - Dependencies must reference only previous task ids.
            - `termination_condition` must explain in natural language what evidence in the Agent Program state indicates that the task is complete.
            - The Agent Program will evaluate this description at runtime after each API call using its current state, response, payload, and dependency messages.
            - Keep tasks atomic, but allow a task to perform multiple internal steps if needed to reach the goal with fewer tasks.
            - For UI tasks: set `requires_ui` to true and provide a clear `ui_description` describing the UI components and interactions.
            - For UI tasks: set `api_endpoint` to the REST endpoint that the UI should call to perform the main action.
            - Do NOT set `requires_ui` to true unless the goal requires a visual browser-based user interface.
            - IMPORTANT: When creating a UI task, ALWAYS embed the game logic, business logic, and UI rendering in a SINGLE task. Do NOT split UI and game logic into separate tasks. The Flask service for a UI task should contain BOTH the HTML/JS for the frontend AND the game logic in the same service. Only separate into multiple tasks if the goal explicitly requires different robots or fundamentally different capabilities.

            INFORMATION DEPENDENCIES:
            - Use "information_dependencies" to specify which other tasks' Agent Programs this task needs to exchange messages with during execution.
            - This is DIFFERENT from "dependencies": "dependencies" = start ordering (task must wait), "information_dependencies" = runtime collaboration (simultaneous execution with message exchange).
            - Only tasks that execute in parallel and need to actively exchange data during execution should be listed in information_dependencies.
            - information_dependencies are bidirectional recommendations (if task A lists task B, consider also listing task A in task B's information_dependencies if they truly collaborate).
            - List task_ids (not task_names) in "information_dependencies".

            MULTIPLAYER GAME RULES:
            - For games with multiple real players (each running on their own agent/machine), create ONE task per player: one has 'local-' in the name, one has 'remote-' in the name.
            - Each task must have the other players' task_ids in "information_dependencies" (bidirectional — both players list each other).
            - The "api_endpoint" is what the local player's UI calls to submit their own move.
            - The Flask service should NOT have any CPU/random opponent logic — it only stores moves and reports results.
            - The AP orchestrates:
              - For local moves: poll local /api/status → detect own move → send move to partners via messaging.
              - Do not keep sending moves/messages to partners — just send once the local move to report and relevant information.
              - For remote moves: wait for incoming messages from partners → forward received moves to local /api/opponent-move.
              IMPORTANT: Polling only detects LOCAL moves (your own player's actions). Remote players' moves arrive via incoming messages, not polling.
            """,
        )
        if not self.llm:
            raise ValueError("LLM not available for goal decomposition")
        try:
            prompt_text = prompt.format(goal=goal_description)

            try:
                structured_llm = self.llm.with_structured_output(
                    GoalDecompositionOutput
                )
                decomposition = structured_llm.invoke(
                    prompt_text,
                )
            except Exception as structured_exc:
                logger.warning(
                    "Structured decomposition failed; falling back to JSON parsing: %s",
                    structured_exc,
                )
                response = self.llm.invoke(
                    prompt_text,
                )
                if not response or not response.content:
                    raise ValueError("LLM response is empty")
                decomposition = parse_llm_json_payload(
                    response.content,
                    expected_type=dict,
                    error_prefix="Decomposition LLM response",
                )

            if isinstance(decomposition, dict):
                tasks_list = decomposition.get("tasks", [])
            else:
                tasks_list = decomposition.tasks

            if not tasks_list:
                raise ValueError(
                    "LLM decomposition must contain a non-empty 'tasks' list."
                )

            tasks = []
            for task in tasks_list:
                # Support both dict (from JSON fallback) and Pydantic object (from structured output)
                if isinstance(task, dict):
                    raw_task_name = task.get("task_name", "")
                    task_id = task.get("task_id", "")
                    description = task.get("description", "")
                    service_description = task.get("service_description", "")
                    termination_condition = task.get("termination_condition", "")
                    dependencies = task.get("dependencies", [])
                    information_dependencies = task.get("information_dependencies", [])
                    requires_ui = task.get("requires_ui", False)
                    ui_description = task.get("ui_description")
                    api_endpoint = task.get("api_endpoint")
                else:
                    raw_task_name = task.task_name
                    task_id = task.task_id
                    description = task.description
                    service_description = task.service_description
                    termination_condition = task.termination_condition
                    dependencies = task.dependencies
                    information_dependencies = task.information_dependencies
                    requires_ui = task.requires_ui
                    ui_description = task.ui_description
                    api_endpoint = task.api_endpoint

                temp = TaskDefinition(
                    task_id=task_id,
                    task_name=self._normalize_k8s_name(raw_task_name, task_id),
                    description=description,
                    service_description=service_description,
                    termination_condition=termination_condition,
                )
                for dep in dependencies:
                    temp.add_dependency(dep)

                # Process information dependencies
                if information_dependencies:
                    for info_dep_id in information_dependencies:
                        temp.add_information_dependency(info_dep_id)

                # Set UI requirements if specified
                if requires_ui:
                    temp.set_ui_requirements(
                        requires_ui=True,
                        ui_description=ui_description,
                        api_endpoint=api_endpoint,
                    )

                tasks.append(temp)
            return tasks
        except Exception as e:
            error_message = str(e) or repr(e) or e.__class__.__name__
            logger.exception("LLM decomposition failed: %s", error_message)
            raise

    async def _run_agent_programs(self, agent_programs: List[AgentProgram]):
        """Run all agent programs concurrently and wait for completion."""
        for ap in agent_programs:
            await self.message_broker.register_agent(ap.agent_id, ap.task.task_id)

        async def run_ap(ap: AgentProgram):
            try:
                await ap.start()
            except Exception as e:
                print(f"Agent program {ap.agent_id} error: {str(e)}")

        await asyncio.gather(*[run_ap(ap) for ap in agent_programs])

    def _register_routes(self, app: Flask):
        """Register Flask HTTP routes."""

        @app.route("/run", methods=["POST"])
        def run_goal():
            """
            Accept a goal description and execute it end-to-end:
            decompose → create services + agent programs → run them.
            """
            data = request.get_json()
            if not data:
                return jsonify({"error": "Request must be JSON"}), 400
            goal = data.get("goal")
            if not goal:
                return jsonify({"error": "'goal' is required"}), 400

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            try:
                result = loop.run_until_complete(self.run(goal))
            except Exception as e:
                return jsonify({"status": "error", "error": str(e)}), 500
            return jsonify(result)

        @app.route("/spawn_ap", methods=["POST"])
        def spawn_ap():
            print(f"spawn_ap: {request.get_json()}")
            data = request.get_json()
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            result = {}
            try:
                result = loop.run_until_complete(
                    self._spawn_agent_program_async(
                        task_def=data,
                        service_manager_url=self.service_manager_url,
                        is_delegated=data.get("is_delegated"),
                    )
                )
            except Exception as e:
                return jsonify({"error": str(e)}), 500
            return jsonify(result)

        @app.route("/stop_service", methods=["POST"])
        def stop_service():
            data = request.get_json()
            service_name = data.get("service_name")
            if not service_name:
                return jsonify({"error": "service_name is required"}), 400
            try:
                request_json(
                    "POST",
                    f"{self.service_manager_url}/stop_service",
                    {"service_name": service_name},
                )
                return jsonify({"status": "ok"})
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @app.route("/broker/forward", methods=["POST"])
        def broker_forward():
            data = request.get_json()
            target_agent_id = data.get("target_agent_id")
            message = data.get("message")
            if not target_agent_id or not message:
                return jsonify(
                    {"error": "target_agent_id and message are required"}
                ), 400
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    self.message_broker.deliver_forwarded_message(
                        target_agent_id, message
                    )
                )
            except Exception as e:
                return jsonify({"error": str(e)}), 500
            return jsonify({"status": "delivered"})

        @app.route("/broker/request", methods=["POST"])
        def broker_request():
            data = request.get_json()
            target_agent_id = data.get("target_agent_id")
            message = data.get("message")
            if not target_agent_id or not message:
                return jsonify(
                    {"error": "target_agent_id and message are required"}
                ), 400
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    self.message_broker.request(
                        sender_id=message.get("sender_id"),
                        target_agent_id=target_agent_id,
                        request=message,
                        timeout=60.0,
                    )
                )
            except Exception as e:
                return jsonify({"error": str(e)}), 500
            return jsonify(result or {"status": "ok"})

        @app.route("/broker/update_routes", methods=["POST"])
        def broker_update_routes():
            """Receive routing table from another controller."""
            data = request.get_json() or {}
            routes = data.get("routes", {})
            print(f"Received routes: {routes}")
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            for task_id, remote in routes.items():
                if loop.is_running():
                    asyncio.create_task(
                        self.message_broker.register_remote_agent(
                            task_id,
                            remote.get("agent_id"),
                            remote.get("controller_url"),
                        )
                    )
                else:
                    loop.run_until_complete(
                        self.message_broker.register_remote_agent(
                            task_id,
                            remote.get("agent_id"),
                            remote.get("controller_url"),
                        )
                    )
            return jsonify({"status": "ok"})

        @app.route("/status/<task_id>", methods=["GET"])
        def get_task_status(task_id):
            """Return status of a spawned agent program."""
            info = self._spawned_agents.get(task_id, {})
            return jsonify(
                {
                    "status": info.get("status", "unknown"),
                    "completion_result": info.get("completion_result"),
                }
            )

    async def _spawn_agent_program_async(
        self,
        task_def: Dict[str, Any],
        service_manager_url: str | None = None,
        is_delegated: bool = False,
    ) -> Dict[str, Any]:
        """Create and start an AgentProgram from a task definition dict."""
        task_id = task_def.get("task_id", "")
        task_name = task_def.get("task_name", task_id)
        description = task_def.get("description", "")
        service_desc = task_def.get("service_description", "")
        termination = task_def.get("termination_condition", "")
        deps = task_def.get("dependencies", [])
        info_deps = task_def.get("information_dependencies", [])
        requires_ui = task_def.get("requires_ui", False)
        ui_description = task_def.get("ui_description", None)
        api_endpoint = task_def.get("api_endpoint", None)

        task = TaskDefinition(
            task_id=task_id,
            task_name=task_name,
            description=description,
            service_description=service_desc,
            termination_condition=termination,
        )
        task.is_delegated = is_delegated
        task.set_ui_requirements(requires_ui, ui_description, api_endpoint)

        for dep in deps:
            task.add_dependency(dep)
        for info_dep in info_deps:
            task.add_information_dependency(info_dep)

        effective_sm_url = service_manager_url or self.service_manager_url

        # Build a minimal dependency graph (single node for a spawned AP)
        dep_graph = {
            "nodes": {
                task_id: {
                    "task_id": task_id,
                    "task_name": task_name,
                    "depends_on": deps,
                }
            },
            "edges": {task_id: deps},
            "dependents": {},
            "in_degree": {task_id: 0},
            "topological_order": [task_id],
        }

        ap = AgentProgram(
            agent_id="agent-" + task_name,
            task=task,
            agent_service_manager_url=effective_sm_url,
            message_broker=self.message_broker,
            dependency_graph=dep_graph,
        )
        with self._token_lock:
            self._total_tokens = ap.task.service.get("total_tokens", 0)
        with self._time_lock:
            self._total_time = ap.task.service.get("total_time", 0)

        self._write_token_usage()

        self.agent_programs[ap.agent_id] = ap
        await self.message_broker.register_agent(ap.agent_id, ap.task.task_id)

        # Track for /status/<task_id>
        self._spawned_agents[task_id] = {
            "status": "running",
            "completion_result": None,
        }

        tracked_ap = ap
        task_id_for_tracking = task_id

        async def tracked_start():
            try:
                await tracked_ap.start()
            finally:
                self._spawned_agents[task_id_for_tracking] = {
                    "status": "failed" if tracked_ap.error else "completed",
                    "completion_result": tracked_ap.completion_result,
                }

        def run_in_thread():
            """Run the agent in a new event loop in a background thread."""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(tracked_start())
            finally:
                loop.close()

        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()

        return {"agent_id": ap.agent_id, "task_id": task_id, "status": "spawned"}

    def run_server(self, host: str = "0.0.0.0", port: int = 8080, debug: bool = False):
        """Run the Flask HTTP server for this controller."""

        def signal_handler(signum, frame):
            print("\n[run_server] Caught SIGINT, cleaning up...")
            self._write_token_usage(0.0)
            print("[run_server] Exiting with failure status")
            exit(1)

        signal.signal(signal.SIGINT, signal_handler)
        self._app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="TurtleBot 3 MQTT Publisher")
    parser.add_argument(
        "--manager-port", type=int, default=5000, help="MQTT broker port"
    )
    parser.add_argument(
        "--controller-port", type=int, default=8080, help="Agent controller port"
    )

    args = parser.parse_args()
    SERVICE_MANAGER_URL = f"http://127.0.0.1:{args.manager_port}"
    AGENT_CONTROLLER_PORT = args.controller_port
    GLOBAL_REGISTRY_URL = "http://127.0.0.1:6000"
    controller = AgentController(
        service_manager_url=SERVICE_MANAGER_URL,
        global_registry_url=GLOBAL_REGISTRY_URL,
        controller_port=AGENT_CONTROLLER_PORT,
    )
    controller.run_server(port=AGENT_CONTROLLER_PORT, debug=False)


"""
curl -X POST "http://127.0.0.1:8080/run"   -H "Content-Type: application/json"   -d '{"goal": "play rock paper scissors vs cpu"}'
curl -X POST "http://127.0.0.1:8080/run"   -H "Content-Type: application/json"   -d '{"goal": "Create a rock-paper-scissors game where the LOCAL ROBOT player uses one UI and the remote player uses another UI. Each player selects rock, paper, or scissors from their respective UIs. Once both have chosen, reveal both selections and declare the winner on both UIs."}'
curl -X POST "http://127.0.0.1:8080/run"   -H "Content-Type: application/json"   -d '{"goal": "Create a Tic Tac Toe multiplayer game where two players, one local and one remote, play against each other, each with their own browser UI. One player is remote and plays as X and the other is local and plays as O. The two Agent Programs must communicate via inter-agent messaging to exchange moves. Each player has a separate UI that shows the game board, the players symbol (X or O), and the current game status. When a player clicks a cell, their move is sent to the other players agent via inter-agent messaging, and both UIs update to show the new board state. The game ends when one player wins or its a draw."}'
curl -X POST "http://127.0.0.1:8080/run" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Create a TurtleBot remote controller web app with a UI that allows the user to send movement commands to a TurtleBot robot via MQTT. The UI should provide a control pad with directional buttons (forward, backward, left, right) and speed control. When the user clicks a direction button, publish the corresponding command (e.g., move_forward, move_backward, turn_left, turn_right) to the MQTT broker topic that the TurtleBot subscribes to. The UI should also display real-time feedback showing the last command sent and the robot connection status. The app should use a clean, intuitive interface suitable for controlling a robot remotely."
  }'

curl -X POST "http://127.0.0.1:8080/run" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Create a counting app with two agents that take turns incrementing a shared counter from 0 to 5. The local agent starts by incrementing the counter to 1, then sends a message to remote agent with the current count. remote agent receives the message, increments the counter to 2, and sends it back to local agent. The agents continue alternating turns until one agent increments the counter to 5. The remote agent communicates via inter-agent messaging with the local agent."
  }'
curl -X POST "http://127.0.0.1:8080/run" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Two agents: one is local and monitor the environment to see if someone is moving near the turtlebot (like 0.5). The other remote agent waits for a message from the first one and then plays the imperial march from star wars. "
  }'
"""
