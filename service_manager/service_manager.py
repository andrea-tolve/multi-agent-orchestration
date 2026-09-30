import importlib
import json
import os
import py_compile
import shutil
import socket
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from subprocess import call
from typing import Dict

import yaml
from kubernetes import client, config
from langchain_core.messages import HumanMessage, SystemMessage

from service_manager.app_templates import (
    DEPLOYMENT_TEMPLATE,
    DOCKERFILE_CONTENT,
    SERVICE_TEMPLATE,
)
from service_manager.service_generator import ServiceGenerator
from shared.utils import (
    load_llm_model,
    parse_llm_json_payload,
    request_json,
)

SYSTEM_PROMPT_DELEGATION_ONLY = (
    "You are a senior service-planning assistant. "
    "DELEGATION RULE (MUST FOLLOW): Return status='error' with error_type='delegate_required' if ANY of:\n"
    "  • Request requires operations on a DIFFERENT robot than the one running this service manager\n"
    "  • Request explicitly says 'remote agent', 'remote robot', 'robot on Roomba', and you are not that robot\n"
    "DELEGATION RULE FOR MULTIPLAYER GAMES:\n"
    "• Use the task name to determine delegation:\n"
    "  - If task name contains 'local-robot' or 'local_robot', do NOT delegate (you are the local player).\n"
    "  - If task name contains 'remote' or references another robot's session, DELEGATE.\n"
    "• Ignore conflicting keywords in the description\n"
    'If delegation is NOT needed, return a simple acknowledgment: {"status": "not_delegate"}\n\n'
    "Output format: Return exactly one JSON object and nothing else.\n"
    "IMPORTANT: Write all property names and values enclosed in double quotes.\n"
)

SYSTEM_PROMPT = (
    f"{SYSTEM_PROMPT_DELEGATION_ONLY}\n"
    "The JSON must include a normalized service_spec, a short ordered list of implementation tasks, "
    "and an output_contract describing the expected files and runtime entrypoint.\n"
    "If the service should expose HTTP endpoints, include endpoint and method fields on the relevant tasks.\n"
    "Prefer the fewest possible tasks, keep them actionable, and include only the information needed "
    "by the generator to create the service.\n\n"
)


class ServiceManager:
    def __init__(
        self,
        robot_id=None,
        robot_capabilities=None,
        manager_url=None,
        mqtt_robot=False,
        agent_controller_url=None,
    ):

        # --- Configuration ---
        self.IMAGE_TAG = "latest"
        # Set this environment variable if you want to push to Docker Hub
        # export DOCKER_HUB_USERNAME="your_docker_hub_username"
        self.DOCKER_HUB_USERNAME = os.getenv("DOCKER_HUB_USERNAME")
        # Initialize the cloud robot client
        self.robot_id = robot_id
        self.caps = robot_capabilities
        self._llm_model = os.getenv("SERVICE_PLANNER_MODEL")
        self._service_generator = ServiceGenerator()
        self.total_tokens = 0

        self.registry = {}
        self._manager_url = manager_url
        self._agent_controller_url = agent_controller_url

        # Per-service locks for concurrent service creation
        self._service_locks: Dict[str, threading.Lock] = {}
        self._registry_lock = threading.Lock()

        # Global registry configuration
        self._global_registry_url = "http://127.0.0.1:6000"
        self._global_registry_ttl_seconds = 60
        self._global_registry_heartbeat_interval_seconds = int(
            self._global_registry_ttl_seconds / 2
        )
        # Event to signal the heartbeat thread to stop
        self._global_registry_heartbeat_stop_event = threading.Event()
        self._global_registry_heartbeat_thread = None
        self._register_robot_in_global_registry()

        self.llm = load_llm_model(self._llm_model)
        self.system_prompt = SYSTEM_PROMPT

        # How many attempts to ask the LLM to produce compilable code before giving up
        self.GENERATION_MAX_ATTEMPTS = 2

    def _utc_now(self):
        return datetime.now(timezone.utc).isoformat()

    def _get_service_lock(self, service_name: str) -> threading.Lock:
        """Get or create a per-service lock for concurrent service creation."""
        with self._registry_lock:
            if service_name not in self._service_locks:
                self._service_locks[service_name] = threading.Lock()
            return self._service_locks[service_name]

    def _find_free_port(self, start_port, max_port=65535):
        if start_port is None:
            start_port = 1
        if start_port < 1:
            start_port = 1
        for port in range(int(start_port), max_port + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError:
                    continue
                return port
        raise RuntimeError(f"No free port found in range {start_port}-{max_port}")

    def _flatten_usage_metadata(self, value, prefix=""):
        flat = {}
        if value is None:
            return flat
        if hasattr(value, "items"):
            for key, nested in value.items():
                key_name = str(key)
                next_prefix = f"{prefix}_{key_name}" if prefix else key_name
                flat.update(self._flatten_usage_metadata(nested, next_prefix))
            return flat
        if hasattr(value, "__dict__") and not isinstance(value, (str, bytes)):
            return self._flatten_usage_metadata(vars(value), prefix)
        if isinstance(value, (list, tuple, set)):
            flat[prefix or "value"] = json.dumps(list(value))
            return flat
        flat[prefix or "value"] = value
        return flat

    def _register_robot_in_global_registry(self):
        if not self._global_registry_url:
            return None

        if not self.robot_id:
            return None

        # Add common capabilities that all robots have
        capabilities = deepcopy(self.caps) or {}
        capabilities["ui_creation"] = True

        try:
            request_json(
                "POST",
                self._global_registry_url + "/robots",
                {
                    "robot_name": self.robot_id,
                    "manager_url": self._manager_url,
                    "controller_url": self._agent_controller_url,
                    "capabilities": capabilities,
                    "ttl_seconds": self._global_registry_ttl_seconds,
                },
            )
        except Exception as exc:
            print(
                f"Warning: could not register robot '{self.robot_id}' in global registry: {exc}"
            )
            return None

    def _sync_with_global_registry(self, action, service_name, executable_service):

        if not self._global_registry_url:
            return None

        if not self.robot_id:
            return None

        if action == "register" and service_name and executable_service:
            try:
                return request_json(
                    "POST",
                    self._global_registry_url + f"/robots/{self.robot_id}/services",
                    {"executable_service": executable_service},
                )
            except Exception as exc:
                print(
                    f"Warning: could not register service '{service_name}' in global registry: {exc}"
                )
                return None

        if action == "remove" and service_name:
            try:
                return request_json(
                    "DELETE",
                    self._global_registry_url
                    + f"/robots/{self.robot_id}/services/{service_name}",
                )
            except Exception as exc:
                print(
                    f"Warning: could not remove service '{service_name}' from global registry: {exc}"
                )
                return None

        return None

    def unregister_from_global_registry(self):
        if not self._global_registry_url:
            return None
        if not self.robot_id:
            return None
        try:
            return request_json(
                "DELETE", self._global_registry_url + f"/robots/{self.robot_id}"
            )
        except Exception as exc:
            print(
                f"Warning: could not unregister robot '{self.robot_id}' from global registry: {exc}"
            )
            return None

    def _remove_service_from_global_registry(self, service_name):
        return self._sync_with_global_registry("remove", service_name, None)

    def _query_global_registry(self, service_name, service_description):
        """Queries the global registry for the best matching robot or service."""
        if not self._global_registry_url:
            return None

        try:
            response = request_json(
                "POST",
                self._global_registry_url + "/query",
                {
                    "service_name": service_name,
                    "service_description": service_description,
                    "requester_robot_name": self.robot_id,
                },
                timeout=300,
            )
            if not response:
                return None

            service_info = response.get("service") or response.get("executable_service")
            delegate_url = response.get("manager_url") or (service_info or {}).get(
                "manager_url"
            )
            robot_name = response.get("robot_name")

            enriched_response = dict(response)
            if (
                service_info is not None
                and "executable_service" not in enriched_response
            ):
                enriched_response["executable_service"] = service_info
                self.best_service = service_info
            if service_info is not None and "service" not in enriched_response:
                enriched_response["service"] = service_info
            enriched_response["delegate_url"] = delegate_url
            enriched_response["current_manager_url"] = self._manager_url

            # Determine if delegation is needed
            is_different_manager = delegate_url and delegate_url != self._manager_url
            is_robot_match = enriched_response.get("found")

            enriched_response["should_delegate"] = bool(
                is_robot_match and is_different_manager
            )
            enriched_response["decision_hint"] = "delegate"

            print(
                f"[_query_global_registry] Found: {enriched_response.get('found')}, "
                f"Robot: {robot_name}, "
                f"Should delegate: {enriched_response['should_delegate']}, "
                f"Reason: {enriched_response.get('reason', 'N/A')}"
            )

            return enriched_response
        except Exception as exc:
            print(f"Warning: could not query global registry: {exc}")
            return None

    def _delegate_service_creation(
        self, service_building, delegate_url, registry_result=None
    ):
        """
        Returns a delegation info dict instead of forwarding to the remote SM.
        The AgentController will forward the task to the remote controller.
        """
        if not delegate_url:
            print("[_delegate_service_creation] No delegate_url provided")
            return None

        # Prefer robot_name from registry_result if available, otherwise look it up
        robot_id = registry_result.get("robot_name") if registry_result else None
        if not robot_id:
            print(
                f"[_delegate_service_creation] Warning: could not find robot_id for manager_url {delegate_url}"
            )
            return None

        try:
            print(
                f"[_delegate_service_creation] Fetching robot info: robot_id={robot_id}, delegate_url={delegate_url}"
            )
            registry_resp = request_json(
                "GET",
                self._global_registry_url + f"/robots/{robot_id}",
                timeout=30,
            )
            if not registry_resp:
                print(
                    f"[_delegate_service_creation] Registry returned no robot info for {robot_id}"
                )
                return None

            controller_url = registry_resp.get("controller_url")
            if not controller_url:
                print(
                    f"[_delegate_service_creation] Robot {robot_id} has no controller_url"
                )
                return None

        except Exception as exc:
            print(
                f"[_delegate_service_creation] Could not query GlobalRegistry for robot {robot_id}: {exc}"
            )
            return None

        service_building = dict(service_building)
        service_building["is_delegated"] = True

        result = {
            "delegation_required": True,
            "delegate_controller_url": controller_url,
            "delegate_manager_url": delegate_url,
            "remote_robot_id": robot_id,
            "service_building": service_building,
        }
        print(
            f"[_delegate_service_creation] Delegation prepared: controller={controller_url}, robot={robot_id}"
        )
        return result

    def heartbeat_global_registry(self):
        """Send a heartbeat to the global registry to keep the robot alive."""
        if not self._global_registry_url:
            return None

        if not self.robot_id:
            return None

        try:
            return request_json(
                "POST",
                self._global_registry_url + f"/robots/{self.robot_id}/heartbeat",
                {
                    "manager_url": self._manager_url,
                    "capabilities": self.caps,
                    "ttl_seconds": self._global_registry_ttl_seconds,
                },
            )
        except Exception:
            return None

    def start_global_registry_heartbeat(self, interval_seconds=None):
        if not self._global_registry_url:
            return None

        if not self.robot_id:
            return None

        thread = self._global_registry_heartbeat_thread
        if thread and thread.is_alive():
            return thread

        if interval_seconds is None:
            interval_seconds = self._global_registry_heartbeat_interval_seconds

        self._global_registry_heartbeat_stop_event.clear()

        # Loop to send heartbeats at the specified interval
        def _loop():
            while not self._global_registry_heartbeat_stop_event.wait(interval_seconds):
                self.heartbeat_global_registry()

        thread = threading.Thread(target=_loop, daemon=True)
        thread.start()
        self._global_registry_heartbeat_thread = thread
        return thread

    def stop_global_registry_heartbeat(self):
        self._global_registry_heartbeat_stop_event.set()
        thread = self._global_registry_heartbeat_thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        self._global_registry_heartbeat_thread = None
        return None

    # Helper method to create a consistent service record structure
    def _service_record(
        self, service_name, service_description="", executable_service=None
    ):
        executable_service = executable_service or {}
        return {
            "service_name": service_name,
            "service_description": service_description,
            "executable_service": executable_service,
            "status": "created",
            "desired_state": "stopped",
            "health": "unknown",
            "last_checked": None,
            "last_restart_attempt": None,
            "restart_count": 0,
        }

    def _set_service_address(self, service_name, service_address=None):
        """Synchronize the runtime service address in the registry record."""
        service_key = self._resolve_service_key(service_name)
        record = self.registry.get(service_key)
        if not record:
            return None

        executable_service = dict(record.get("executable_service") or {})
        if service_address:
            executable_service["service_address"] = service_address
        else:
            executable_service.pop("service_address", None)
        record["executable_service"] = executable_service
        return record

    # This method attempts to resolve a service name to the correct key in the registry
    def _resolve_service_key(self, service_name):
        if service_name in self.registry:
            return service_name

        # Check if the service_name matches any known service's name or executable_service's service_name.
        for stored_service_name, record in self.registry.items():
            executable_service = record.get("executable_service") or {}
            if record.get("service_name") == service_name:
                return stored_service_name
            if executable_service.get("service_name") == service_name:
                return stored_service_name

        return service_name

    def _load_docker_module(self):
        try:
            return importlib.import_module("docker")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Docker SDK for Python is required to build images. Install it with: pip install -r requirements.txt"
            ) from exc

    def _load_kubernetes_clients(self):
        try:
            config.load_kube_config()
        except config.ConfigException:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                return None, None
        return client.AppsV1Api(), client.CoreV1Api()

    def _build_service_generation_messages(self, service_building):
        service_name = service_building.get("service_name") or "service"
        service_description = service_building.get("service_description") or ""
        app_dir = service_building.get("app_dir") or service_name
        service_port = int(service_building.get("service_port") or 80)
        container_port = int(service_building.get("container_port") or 8000)
        kind = service_building.get("kind") or "kubernetes"
        is_delegated = service_building.get("is_delegated")
        generation_mode = os.getenv("SERVICE_GENERATION_MODE", "scaffold")

        planner_service_description = service_description

        if planner_service_description and not is_delegated:
            delegation_hint = (
                "\n\nDELEGATION GUIDANCE - Return error with error_type='delegate_required' if:"
                f"\n  • Request mentions specific robot types (roomba, turtlebot, etc.) that aren't matching the {self.robot_id}"
                "\n  • Required capability is NOT in the provided 'capabilities' list"
                "\n  • The request mentions 'remote agent', 'remote robot', or refers to a different robot"
            )
            planner_service_description += delegation_hint
        elif planner_service_description and is_delegated:
            planner_service_description += (
                "\n\nIMPORTANT: This request has already been delegated to you as the final service manager."
                "\n You MUST create this service using your available capabilities."
                "\n Do NOT return delegate_required - generate the service code instead."
                "\n If the service seems impossible with your capabilities, still attempt to generate"
                "\n a reasonable implementation rather than failing."
            )

        # Use delegation-only prompt for free mode
        if generation_mode == "free":
            system_prompt = SYSTEM_PROMPT_DELEGATION_ONLY
            human_prompt = (
                "Evaluate this service creation request and decide whether to delegate or handle locally.\n\n"
                f"Service name: {service_name}\n"
                f"Planner service description and delegation hint: {planner_service_description}\n"
                f"Capabilities: {json.dumps(self.caps or {}, ensure_ascii=False)}\n\n"
                'Return {"status": "not_delegate"} if you can handle it locally, '
                'or {"status": "error", "error_type": "delegate_required"} if delegation is needed.'
            )
            return [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_prompt),
            ]

        # Standard scaffold mode with full task planning
        prompt_payload = json.dumps(
            {
                "service_spec": {
                    "service_name": service_name,
                    "service_description": planner_service_description,
                    "app_dir": app_dir,
                    "service_port": service_port,
                    "container_port": container_port,
                    "kind": kind,
                    "is_delegated": is_delegated,
                    "requires_ui": service_building.get("requires_ui", False),
                    "ui_description": service_building.get("ui_description"),
                    "api_endpoint": service_building.get("api_endpoint"),
                },
                "capabilities": self.caps or {},
                "planner_requirements": {
                    "task_count": "3_to_6",
                    "output": "normalized_json_only",
                    "focus": "planning_not_code",
                    "generation_scope": "application_code_only",
                    "excluded_files": [
                        "Dockerfile",
                        "Kubernetes manifests",
                        "deployment configs",
                    ],
                },
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )

        human_prompt = (
            "Create a compact task plan for the downstream service generator. "
            "Return only JSON and do not write code. "
            "Include a normalized service_spec, a small ordered list of tasks, "
            "and an output_contract limited to app.py and requirements.txt. "
            "Do not include Dockerfile, Kubernetes manifests, or any infrastructure tasks; those remain the ServiceManager's responsibility. "
            "When the service exposes HTTP routes, add endpoint and method fields to the relevant tasks.\n\n"
            f"{prompt_payload}"
        )

        return [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=human_prompt),
        ]

    def _extract_json_payload(self, text):
        return parse_llm_json_payload(
            text,
            expected_type=dict,
            error_prefix="Model response",
        )

    def _write_generated_files(self, generated_spec, paths):
        files = generated_spec.get("files") or []
        if not files:
            raise ValueError("The model did not return any files to write.")

        app_dir = paths["app_dir"]
        app_file = paths["app_file"]
        requirements = paths["requirements"]

        print(f"[_write_generated_files] Starting with {len(files)} files")
        print(
            f"[_write_generated_files] Files to write: {[f.get('path') for f in files]}"
        )

        if os.path.exists(app_dir):
            print(f"[_write_generated_files] Removing existing directory: {app_dir}")
            shutil.rmtree(app_dir)
        os.makedirs(app_dir, exist_ok=True)
        print(f"[_write_generated_files] Created directory: {app_dir}")

        app_root_name = os.path.basename(app_dir.rstrip(os.sep))

        for entry in files:
            relative_path = (entry.get("path") or "").strip()
            content = entry.get("content", "")
            if not relative_path:
                print("[_write_generated_files] Skipping entry with no path")
                continue

            normalized_path = os.path.normpath(relative_path)
            if os.path.isabs(relative_path) or normalized_path.startswith(".."):
                raise ValueError(f"Unsafe path received from model: {relative_path}")

            path_parts = normalized_path.split(os.sep)
            if path_parts and path_parts[0] == app_root_name:
                normalized_path = (
                    os.path.join(*path_parts[1:]) if len(path_parts) > 1 else ""
                )

            if not normalized_path:
                print(
                    f"[_write_generated_files] Skipping entry after normalization: {relative_path}"
                )
                continue

            file_path = os.path.join(app_dir, normalized_path)
            file_parent = os.path.dirname(file_path)
            if file_parent:
                os.makedirs(file_parent, exist_ok=True)
            with open(file_path, "w") as f:
                f.write(content)
            print(
                f"[_write_generated_files] Wrote file: {file_path} ({len(content)} bytes)"
            )

        print("[_write_generated_files] Checking required files exist")
        print(
            f"[_write_generated_files] APP_FILE ({app_file}) exists: {os.path.exists(app_file)}"
        )
        print(
            f"[_write_generated_files] REQUIREMENTS_PATH ({requirements}) exists: {os.path.exists(requirements)}"
        )

        if not os.path.exists(app_file):
            raise ValueError("The generated service is missing app.py.")
        if not os.path.exists(requirements):
            raise ValueError("The generated service is missing requirements.txt.")
        print("[_write_generated_files] All required files present")

    def _compile_python_files_in_app(self, paths):
        """Compile all Python files in the generated application directory and
        return a list of compile errors (empty list if none).
        """
        app_dir = paths["app_dir"]
        errors = []
        if not os.path.exists(app_dir):
            errors.append(
                {"file": "<app_dir>", "error": "Application directory not found"}
            )
            return errors

        for root, _, files in os.walk(app_dir):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(root, fn)
                try:
                    py_compile.compile(p, doraise=True)
                except py_compile.PyCompileError as e:
                    rel = os.path.relpath(p, app_dir)
                    errors.append({"file": rel, "error": str(e)})
        return errors

    def _apply_generated_patches(self, generated_spec):
        """Apply a set of file patches returned by the model without wiping the
        entire application directory. `generated_spec` is expected to contain a
        `files` array with relative paths and full file contents.
        """
        files = generated_spec.get("files") or []
        if not files:
            raise ValueError("The model did not return any files to apply.")

        app_root_name = os.path.basename(self.APP_DIR.rstrip(os.sep))

        for entry in files:
            relative_path = (entry.get("path") or "").strip()
            content = entry.get("content", "")
            if not relative_path:
                continue

            normalized_path = os.path.normpath(relative_path)
            if os.path.isabs(relative_path) or normalized_path.startswith(".."):
                raise ValueError(f"Unsafe path received from model: {relative_path}")

            path_parts = normalized_path.split(os.sep)
            if path_parts and path_parts[0] == app_root_name:
                normalized_path = (
                    os.path.join(*path_parts[1:]) if len(path_parts) > 1 else ""
                )

            if not normalized_path:
                continue

            file_path = os.path.join(self.APP_DIR, normalized_path)
            file_parent = os.path.dirname(file_path)
            if file_parent:
                os.makedirs(file_parent, exist_ok=True)
            with open(file_path, "w") as f:
                f.write(content)

    def _invoke_service_generator(self, payload, service_name):
        try:
            return self._service_generator.generate(
                payload, service_name, self.robot_id or ""
            )
        except Exception as exc:
            print(f"[service_manager] service generator failed: {exc}")
            raise RuntimeError(f"Could not generate service code: {exc}") from exc

    def _generate_service_files(self, service_building, paths):
        """
        Generates a compact task plan with Gemini and sends it to the service generator.
        Returns (delegated, best_service, delegation_result) instead of modifying instance state.
        """
        service_name = service_building.get("service_name")
        service_description = service_building.get("service_description") or ""
        is_delegated = service_building.get("is_delegated") or False
        delegation_result = None
        best_service = None
        generation_mode = os.getenv("SERVICE_GENERATION_MODE", "scaffold")

        # Preemptive delegation check: if service name indicates remote execution, delegate immediately
        if not is_delegated and service_name and "remote" in service_name.lower():
            registry_result = self._query_global_registry(
                service_name, service_description
            )
            if registry_result and registry_result.get("found"):
                robot_name = registry_result.get("robot_name")
                delegate_url = registry_result.get("delegate_url")
                if delegate_url and delegate_url != self._manager_url:
                    delegation_result = self._delegate_service_creation(
                        service_building,
                        delegate_url,
                        registry_result,
                    )
                    if delegation_result is not None:
                        return True, best_service, delegation_result

        # Initial generation prompt
        if generation_mode == "scaffold" or (
            generation_mode == "free" and not is_delegated
        ):
            current_messages = self._build_service_generation_messages(service_building)
            # If the service can't be generated locally, delegate to the global registry.
            response = self.llm.invoke(
                current_messages,
            )
            if self.total_tokens == 0:
                self.total_tokens = getattr(response, "usage_metadata", {}).get(
                    "total_tokens", 0
                )
            else:
                self.total_tokens += getattr(response, "usage_metadata", {}).get(
                    "total_tokens", 0
                )

            if not response or not getattr(response, "content", None):
                raise RuntimeError("Planner returned no response.")

            content = response.content
            if isinstance(content, list):
                result = "".join(
                    block.get("text", "")
                    for block in content
                    if block.get("type") == "text"
                )
            else:
                result = content

            if not result or not str(result).strip():
                raise RuntimeError("Planner returned an empty response.")

            try:
                generated_plan = self._extract_json_payload(result)
            except Exception as exc:
                raise ValueError(
                    f"Could not parse planner response as JSON: {exc}"
                ) from exc

            if not isinstance(generated_plan, dict):
                raise ValueError("Planner response must be a JSON object.")

            # If the plan is an error, query the global registry for a matching service.
            plan_status = str(generated_plan.get("status") or "").lower()
            error_type = str(generated_plan.get("error_type") or "").lower()
            print(
                f"[_generate_service_files] Planner status: {plan_status}, error_type: {error_type}"
            )

            if plan_status == "error" or error_type:
                registry_result = (
                    self._query_global_registry(service_name, service_description)
                    if not is_delegated
                    else None
                )

                # Unified delegation: if registry found anything, delegate to the robot
                # The remote robot will decide internally whether to reuse an existing service or create new
                if registry_result and registry_result.get("found"):
                    robot_name = registry_result.get("robot_name")
                    delegate_url = registry_result.get("delegate_url")

                    # Always delegate to the robot that can satisfy this request
                    # Whether it's because a service exists on that robot or because the robot can create it
                    if delegate_url and delegate_url != self._manager_url:
                        delegation_result = (
                            self._delegate_service_creation(
                                service_building,
                                delegate_url,
                                registry_result,
                            )
                            if not is_delegated
                            else None
                        )
                        if delegation_result is not None:
                            return True, best_service, delegation_result

                # Case 4: No suitable match found - give up
                error_msg = f"Service '{service_name}' requires capabilities not available locally (error: {error_type})"
                if registry_result:
                    error_msg += f". Registry search: {registry_result.get('reason', 'no reason provided')}"
                print(f"[_generate_service_files] ✗ {error_msg}")
                raise RuntimeError(error_msg)

            if generated_plan.get("error"):
                raise ValueError(str(generated_plan.get("error")))

            if generation_mode == "free" and plan_status == "not_delegate":
                print(
                    "[_generate_service_files] FREE mode: skipping task plan, passing empty task_plan to generator"
                )
                generated_plan = {}  # Empty task plan for free mode
        else:
            generated_plan = {}

        generated_spec = self._invoke_service_generator(
            {
                "service_building": service_building,
                "task_plan": generated_plan,
                "current_manager_url": self._manager_url,
                "robot_capabilities": self.caps or {},
            },
            service_name,
        )

        self.total_tokens += self._service_generator.total_tokens
        print(
            f"[_generate_service_files] total generator tokens: {self._service_generator.total_tokens}"
        )

        if not generated_spec:
            raise ValueError("Service generator did not return any result.")

        if isinstance(generated_spec, dict) and generated_spec.get("error"):
            raise ValueError(str(generated_spec.get("error")))

        try:
            self._write_generated_files(generated_spec, paths)
        except Exception as exc:
            raise ValueError(f"Could not write generated files: {exc}") from exc

        compile_errors = self._compile_python_files_in_app(paths)
        if compile_errors:
            raise ValueError(
                "Generated service files did not compile: "
                + json.dumps(compile_errors, ensure_ascii=False)
            )

        return False, best_service, delegation_result

    # This method checks the health of the service by inspecting the Kubernetes Deployment and Service status
    def _update_service_health(self, service_name):
        record = self.registry.get(service_name)
        if not record:
            return None

        if record.get("desired_state") == "stopped":
            record["last_checked"] = self._utc_now()
            return record

        api_apps, api_core = self._load_kubernetes_clients()
        if not api_apps or not api_core:
            record["health"] = "unknown"
            record["last_checked"] = self._utc_now()
            return record

        deployment_name = f"{service_name}-deployment"
        service_name_k8s = f"{service_name}-service"

        try:
            deployment = api_apps.read_namespaced_deployment(
                name=deployment_name, namespace="default"
            )
            # The health logic can be more sophisticated, but for simplicity:
            available_replicas = (
                getattr(deployment.status, "available_replicas", 0) or 0
            )
            desired_replicas = getattr(deployment.status, "replicas", 0) or 0
            if available_replicas > 0:
                record["health"] = "healthy"
                record["status"] = "running"
            elif desired_replicas > 0:
                record["health"] = "degraded"
                record["status"] = "running"
            else:
                record["health"] = "stopped"
                record["status"] = "stopped"

            # Now check the Service to get the endpoint (if LoadBalancer type)
            try:
                service = api_core.read_namespaced_service(
                    name=service_name_k8s, namespace="default"
                )
                service_port = (record.get("executable_service") or {}).get(
                    "service_port"
                ) or 80
                if (
                    service.status
                    and service.status.load_balancer
                    and service.status.load_balancer.ingress
                ):
                    ingress = service.status.load_balancer.ingress[0]
                    endpoint = ingress.ip or ingress.hostname
                    record["endpoint"] = endpoint
                    self._set_service_address(
                        service_name,
                        f"http://{endpoint}:{service_port}" if endpoint else None,
                    )
                else:
                    record["endpoint"] = None
                    self._set_service_address(service_name, None)
            except Exception:
                record["endpoint"] = None
                self._set_service_address(service_name, None)
        except Exception as e:
            if getattr(e, "status", None) == 404:
                record["health"] = "missing"
                record["status"] = "stopped"
                record["endpoint"] = None
            else:
                record["health"] = "error"

        record["last_checked"] = self._utc_now()
        return record

    def _maybe_restart_service(self, service_name):
        record = self.registry.get(service_name)
        if not record:
            return None

        # Respect an explicit stop request
        if record.get("desired_state") == "stopped":
            return record

        if record.get("health") in ["healthy", "degraded"]:
            return record

        executable_service = record.get("executable_service") or {}
        if not executable_service:
            return record

        # Prevent rapid restart loops by checking the last restart attempt time, and only allowing a restart if a certain amount of time has passed (e.g., 5 minutes)
        last_restart_attempt = record.get("last_restart_attempt")
        if last_restart_attempt:
            elapsed_seconds = (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(last_restart_attempt)
            ).total_seconds()
            if elapsed_seconds < 300:
                return record

        record["last_restart_attempt"] = self._utc_now()
        record["restart_count"] = record.get("restart_count", 0) + 1
        try:
            self.startService(service_name)
        except Exception:
            record["health"] = "error"
        return self.registry.get(service_name, record)

    def getAllServices(self):
        """Returns all known services and refreshes their health state."""
        for service_name in list(self.registry.keys()):
            self._update_service_health(service_name)
            self._maybe_restart_service(service_name)
        return [deepcopy(service) for service in self.registry.values()]

    def getServiceDetails(self, serviceName):
        """Returns the details for a single service."""
        service_key = self._resolve_service_key(serviceName)
        if service_key not in self.registry:
            return None
        self._update_service_health(service_key)
        self._maybe_restart_service(service_key)
        return deepcopy(self.registry[service_key])

    def searchService(self, serviceName, serviceDescription):
        """Searches for services by name and/or description."""
        serviceName = (serviceName or "").lower()
        serviceDescription = (serviceDescription or "").lower()

        matches = []
        for service in self.getAllServices():
            name_match = (
                serviceName and serviceName in service.get("service_name", "").lower()
            )
            description_match = (
                serviceDescription
                and serviceDescription in service.get("service_description", "").lower()
            )
            if not serviceName and not serviceDescription:
                matches.append(service)
            elif name_match or description_match:
                matches.append(service)
        return matches

    def deleteService(self, serviceName):
        """Deletes a previously created service."""
        service_key = self._resolve_service_key(serviceName)
        record = self.registry.get(service_key)
        if not record:
            raise ValueError(f"Unknown service '{serviceName}'")

        # Do not allow deletion of running services
        if (
            record.get("status") == "running"
            or record.get("desired_state") == "running"
        ):
            raise ValueError(
                f"Service '{serviceName}' is running. Stop it before deletion."
            )

        # Best-effort: remove Docker image (if Docker SDK is available and image exists)
        try:
            docker_module = self._load_docker_module()
            try:
                docker_client = docker_module.from_env()
                image_name = (
                    f"{self.DOCKER_HUB_USERNAME}/{service_key}"
                    if self.DOCKER_HUB_USERNAME
                    else service_key
                )
                try:
                    docker_client.images.remove(
                        f"{image_name}:{self.IMAGE_TAG}", force=True
                    )
                    print(f"Removed Docker image: {image_name}:{self.IMAGE_TAG}")
                except Exception as e:
                    print(
                        f"Could not remove Docker image {image_name}:{self.IMAGE_TAG}: {e}"
                    )
            except Exception as e:
                print(
                    f"Could not connect to Docker daemon to remove image for '{service_key}': {e}"
                )
        except Exception:
            # Docker SDK not installed; nothing to do here
            pass

        # Remove generated application directory from disk
        try:
            executable = record.get("executable_service") or {}
            app_dir = executable.get("app_dir") or service_key
            app_path = os.path.join(os.getcwd(), "apps_dir", app_dir)
            if os.path.exists(app_path):
                shutil.rmtree(app_path)
        except Exception as e:
            print(f"Could not remove application directory for '{service_key}': {e}")

        # Finally, remove the service from the registry
        try:
            del self.registry[service_key]
        except KeyError:
            # Already removed concurrently by another operation
            pass

        self._remove_service_from_global_registry(service_key)
        return f"Successfully deleted service {service_key}"

    def createService(self, serviceBuilding):
        """Creates a new service record and scaffolds a local executable service if possible."""

        # Normalize infos
        service_name = serviceBuilding.get("service_name")
        if not service_name:
            service_name = f"service-{len(self.registry) + 1}"

        # Acquire per-service lock for concurrent creation
        service_lock = self._get_service_lock(service_name)
        with service_lock:
            app_dir = serviceBuilding.get("app_dir") or service_name
            service_port = self._find_free_port(80)
            container_port = serviceBuilding.get("container_port")
            service_description = serviceBuilding.get("service_description") or ""
            is_delegated = serviceBuilding.get("is_delegated") or False

            # UI requirements
            requires_ui = serviceBuilding.get("requires_ui", False)
            ui_description = serviceBuilding.get("ui_description") or ""
            api_endpoint = serviceBuilding.get("api_endpoint") or ""

            # Normalize serviceBuilding dictionary
            serviceBuilding = dict(serviceBuilding)
            serviceBuilding["service_name"] = service_name
            serviceBuilding["app_dir"] = app_dir
            serviceBuilding["service_port"] = service_port
            serviceBuilding["container_port"] = container_port
            serviceBuilding["service_description"] = service_description
            serviceBuilding["is_delegated"] = is_delegated
            serviceBuilding["requires_ui"] = requires_ui
            serviceBuilding["ui_description"] = ui_description
            serviceBuilding["api_endpoint"] = api_endpoint

            # Build paths locally instead of modifying instance state
            paths = self._build_paths(service_name, app_dir)

            # Generate service files using the AI Agent
            delegate, best_service, delegation_result = self._generate_service_files(
                serviceBuilding, paths
            )

            # Stop creation if the service was delegated to another manager
            if delegate:
                resp = {
                    "response": delegation_result or best_service,
                }
                # If delegation was at the controller level, include delegation info
                if isinstance(delegation_result, dict) and delegation_result.get(
                    "delegation_required"
                ):
                    resp["delegation_required"] = True
                    resp["delegate_controller_url"] = delegation_result.get(
                        "delegate_controller_url"
                    )
                    resp["remote_robot_id"] = delegation_result.get("remote_robot_id")
                    resp["response"] = delegation_result.get("service_building")
                return resp

            # Create the executable service definition that will be used to start the service
            executable_service = {
                "service_name": service_name,
                "service_address": None,
                "app_dir": app_dir,
                "service_port": service_port,
                "container_port": container_port,
                "owner_manager_url": self._manager_url,
                "kind": "kubernetes",
                "requires_ui": requires_ui,
                "ui_description": ui_description,
                "api_endpoint": api_endpoint,
            }

            # Create the service structure in the registry
            record = self._service_record(
                service_name, service_description, executable_service
            )
            record["desired_state"] = "stopped"
            record["status"] = "created"
            record["total_tokens"] = self.total_tokens
            with self._registry_lock:
                self.registry[service_name] = record
            return deepcopy(record)

    def _build_paths(self, service_name: str, app_directory: str) -> Dict[str, str]:
        """Build path dictionary without modifying instance state."""
        app_dir = os.path.join(os.getcwd(), "apps_dir", app_directory)
        return {
            "app_dir": app_dir,
            "requirements": os.path.join(app_dir, "requirements.txt"),
            "app_file": os.path.join(app_dir, "app.py"),
            "dockerfile": os.path.join(app_dir, "Dockerfile"),
            "image_full_name": (
                f"{self.DOCKER_HUB_USERNAME}/{service_name}"
                if self.DOCKER_HUB_USERNAME
                else service_name
            ),
        }

    def setup_paths(self, service_name, app_directory):
        """Sets up global path variables based on the provided app_directory."""

        self.APP_DIR = os.path.join(os.getcwd(), "apps_dir", app_directory)
        self.REQUIREMENTS_PATH = os.path.join(self.APP_DIR, "requirements.txt")
        self.APP_FILE = os.path.join(self.APP_DIR, "app.py")
        self.DOCKERFILE_PATH = os.path.join(self.APP_DIR, "Dockerfile")
        self.IMAGE_FULL_NAME = (
            f"{self.DOCKER_HUB_USERNAME}/{service_name}"
            if self.DOCKER_HUB_USERNAME
            else service_name
        )

    def generate_dockerfile(self, service_name, container_port, paths):
        """Generates a Dockerfile in the application directory."""
        # The DOCKERFILE_CONTENT now needs the app_name to be formatted into it
        formatted_dockerfile_content = DOCKERFILE_CONTENT.format(
            app_name=service_name, container_port=container_port
        )
        dockerfile_path = paths["dockerfile"]
        with open(dockerfile_path, "w") as f:
            f.write(formatted_dockerfile_content)
        print(f"Generated Dockerfile at {dockerfile_path}")

    def build_docker_image(self, docker_client, image_name, image_tag, build_context):
        """Builds the Docker image."""
        print(
            f"Building Docker image: {image_name}:{image_tag} from {build_context}..."
        )
        try:
            image, build_logs = docker_client.images.build(
                path=build_context,
                tag=f"{image_name}:{image_tag}",
                rm=True,  # Remove intermediate containers
            )
            for line in build_logs:
                if "stream" in line:
                    print(line["stream"].strip())
            print(f"Successfully built Docker image: {image.tags[0]}")
            return image
        except Exception as e:
            print(f"Error building Docker image: {e}")
            build_log = getattr(e, "build_log", None)
            if build_log:
                for line in build_log:
                    if "stream" in line:
                        print(line["stream"].strip())
            raise

    def prune_docker_images(self, docker_client):
        """Removes dangling Docker images left behind by repeated builds."""
        try:
            result = docker_client.images.prune(filters={"dangling": True})
            reclaimed_space = result.get("SpaceReclaimed", 0)
            print(
                f"Removed dangling Docker images. Space reclaimed: {reclaimed_space} bytes."
            )
        except Exception as e:
            print(f"Could not prune dangling Docker images: {e}")

    def generate_kubernetes_manifests(
        self, service_name, image_full_name, image_tag, service_port, container_port
    ):
        """Generates Kubernetes Deployment and Service YAMLs."""
        deployment_manifest = DEPLOYMENT_TEMPLATE.format(
            app_name=service_name,
            image_full_name=image_full_name,
            image_tag=image_tag,
            container_port=container_port,
        )
        service_manifest = SERVICE_TEMPLATE.format(
            app_name=service_name,
            service_port=service_port,
            container_port=container_port,
        )

        print("\n--- Generated Kubernetes Deployment manifest ---")
        print(deployment_manifest)
        print("\n--- Generated Kubernetes Service manifest ---")
        print(service_manifest)

        return deployment_manifest, service_manifest

    def deploy_to_kubernetes(self, service_name, deployment_manifest, service_manifest):
        """Deploys the application to Kubernetes."""
        print("\nLoading Kubernetes configuration...")

        api_apps, api_core = self._load_kubernetes_clients()
        if not api_apps or not api_core:
            raise RuntimeError(
                "Could not load Kubernetes configuration. Ensure kubeconfig is set or the code is running inside a Kubernetes cluster."
            )

        # Deploy Deployment
        print(f"Creating/Updating Deployment '{service_name}-deployment'...")
        dep_obj = yaml.safe_load(deployment_manifest)
        try:
            api_apps.create_namespaced_deployment(body=dep_obj, namespace="default")
            print("Deployment created successfully.")
        except Exception as e:
            if getattr(e, "status", None) == 409:  # Conflict, already exists, so update
                print("Deployment already exists, attempting to update...")
                api_apps.replace_namespaced_deployment(
                    name=f"{service_name}-deployment", body=dep_obj, namespace="default"
                )
                print("Deployment updated successfully.")
            else:
                print(f"Error creating/updating Deployment: {e}")
                raise

        # Deploy Service
        print(f"Creating/Updating Service '{service_name}-service'...")
        svc_obj = yaml.safe_load(service_manifest)
        try:
            api_core.create_namespaced_service(body=svc_obj, namespace="default")
            print("Service created successfully.")
        except Exception as e:
            if getattr(e, "status", None) == 409:  # Conflict, already exists, so update
                print("Service already exists, attempting to update...")
                api_core.replace_namespaced_service(
                    name=f"{service_name}-service", body=svc_obj, namespace="default"
                )
                print("Service updated successfully.")
            else:
                print(f"Error creating/updating Service: {e}")
                raise

    def cleanup_kubernetes_resources(self, service_name):
        """Deletes the deployed Kubernetes resources."""
        print("\n--- Cleaning up Kubernetes resources ---")
        api_apps, api_core = self._load_kubernetes_clients()
        if not api_apps or not api_core:
            print(
                "Could not load Kubernetes configuration. Ensure kubeconfig is set or the code is running inside a Kubernetes cluster."
            )
            return

        try:
            api_apps.delete_namespaced_deployment(
                name=f"{service_name}-deployment", namespace="default"
            )
            print(f"Deployment '{service_name}-deployment' deleted.")
        except Exception as e:
            if getattr(e, "status", None) == 404:
                print(
                    f"Deployment '{service_name}-deployment' not found, skipping deletion."
                )
            else:
                print(f"Error deleting Deployment: {e}")

        try:
            api_core.delete_namespaced_service(
                name=f"{service_name}-service", namespace="default"
            )
            print(f"Service '{service_name}-service' deleted.")
        except Exception as e:
            if getattr(e, "status", None) == 404:
                print(f"Service '{service_name}-service' not found, skipping deletion.")
            else:
                print(f"Error deleting Service: {e}")

    def startService(self, service_name):
        """Starts a service by name or executable spec and registers it locally."""

        # 1. Resolve the service details
        if isinstance(service_name, str):
            service_key = self._resolve_service_key(service_name)
            record = self.registry.get(service_key)
            if not record:
                raise ValueError(f"Unknown service '{service_name}'")
            executableService = dict(record.get("executable_service") or {})

        else:
            raise TypeError("executableService must be a string or a dictionary")

        # Acquire per-service lock
        service_lock = self._get_service_lock(service_name)
        with service_lock:
            record_executable = dict(record.get("executable_service") or {})
            executableService = {**record_executable, **dict(executableService)}
            executableService.setdefault("service_name", service_name)
            if not executableService.get("app_dir"):
                executableService["app_dir"] = (
                    record_executable.get("app_dir") or service_name
                )
            service_port = self._find_free_port(80)
            container_port = executableService.get("container_port") or 8000
            executableService["service_port"] = service_port
            executableService["container_port"] = container_port

            app_dir = executableService.get("app_dir") or service_key
            paths = self._build_paths(service_name, app_dir)

            with self._registry_lock:
                self.registry[service_key]["executable_service"] = dict(
                    executableService
                )
            self._set_service_address(service_key, None)

            # 3. Generate Dockerfile
            self.generate_dockerfile(service_name, container_port, paths)

            # 4. Initialize Docker client
            try:
                docker_module = self._load_docker_module()
                docker_client = docker_module.from_env()
                docker_client.ping()  # Test connection to Docker daemon
                print("Successfully connected to Docker daemon.")
            except Exception as e:
                print(f"Error connecting to Docker daemon: {e}")
                print("Please ensure Docker Desktop is running.")
                return

            # 5. Build Docker Image
            try:
                self.build_docker_image(
                    docker_client, service_name, self.IMAGE_TAG, paths["app_dir"]
                )
                self.prune_docker_images(docker_client)
            except Exception:
                print("Docker image build failed. Exiting.")
                return

            # 6. Push Docker Image (if self.DOCKER_HUB_USERNAME is set)
            try:
                pass  # push_docker_image(docker_client, paths["image_full_name"], self.IMAGE_TAG)
            except Exception:
                print("Docker image push failed. Exiting.")
                # Continue if push fails, as local K8s might still work
                # For production, you'd want to exit here.

            # 7. Generate Kubernetes Manifests
            deployment_manifest, service_manifest = self.generate_kubernetes_manifests(
                service_name,
                paths["image_full_name"],
                self.IMAGE_TAG,
                service_port,
                container_port,
            )

            # 8. Deploy to Kubernetes
            try:
                self.deploy_to_kubernetes(
                    service_name, deployment_manifest, service_manifest
                )
            except Exception:
                print("Kubernetes deployment failed. Exiting.")
                return

            print("\n--- Deployment Complete ---")
            print(f"Application '{service_name}' deployed to Kubernetes.")
            print(
                "You can check its status with: `kubectl get pods -l app=my-python-app`"
            )
            print("And its service with: `kubectl get svc my-python-app-service`")

            # Wait for service to get an external IP (if LoadBalancer type)
            print(
                "\nWaiting for LoadBalancer IP (may take a moment for some K8s environments)..."
            )
            try:
                _, api_core = self._load_kubernetes_clients()
                if not api_core:
                    raise RuntimeError("Could not load Kubernetes configuration.")

                for _ in range(30):  # Wait up to 5 minutes (30 * 10 seconds)
                    service = api_core.read_namespaced_service(
                        name=f"{service_name}-service", namespace="default"
                    )
                    if (
                        service.status.load_balancer
                        and service.status.load_balancer.ingress
                    ):
                        ingress = service.status.load_balancer.ingress[0]
                        external_address = ingress.ip or ingress.hostname
                        if external_address:
                            service_address = (
                                f"http://{external_address}:{service_port}"
                            )
                            print(f"Application accessible at: {service_address}")
                            executableService["service_address"] = service_address
                            self._set_service_address(service_name, service_address)
                            break
                    print("Waiting for LoadBalancer IP...")
                    time.sleep(10)
                else:
                    print(
                        "LoadBalancer IP not assigned within timeout. Check service status manually."
                    )
                    print(
                        "For Docker Desktop, it's often accessible at http://localhost"
                    )
            except Exception as e:
                print(f"Could not retrieve LoadBalancer IP: {e}")
                print("For Docker Desktop, it's often accessible at http://localhost")

            # 9. Update the service record to reflect that it's now running
            with self._registry_lock:
                self.registry[service_key]["status"] = "running"
                self.registry[service_key]["desired_state"] = "running"
                self.registry[service_key]["health"] = "healthy"
                self.registry[service_key]["last_checked"] = self._utc_now()
            self._sync_with_global_registry("register", service_name, executableService)

            return f"Successfully started the app {service_name}"

    def shutdownService(self, serviceName):
        """Stops a service and updates the registry."""
        self.cleanup_kubernetes_resources(serviceName)
        service_key = self._resolve_service_key(serviceName)
        record = self.registry.get(service_key)
        if not record:
            raise ValueError(f"Unknown service '{serviceName}'")

        if record.get("status") != "running":
            raise ValueError(f"Service '{serviceName}' is not running")

        self._set_service_address(service_key, None)
        record["endpoint"] = None
        record["status"] = "stopped"
        record["desired_state"] = "stopped"
        record["health"] = "stopped"
        record["last_checked"] = self._utc_now()
        self._remove_service_from_global_registry(service_key)
        return f"Successfully stopped the app {serviceName}"
