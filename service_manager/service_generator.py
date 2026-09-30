from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage

from shared.utils import (
    load_llm_model,
    parse_llm_json_payload,
    strip_code_fences,
)


def _safe_identifier(value: Any, fallback: str = "task") -> str:
    text = re.sub(r"[^0-9a-zA-Z_]+", "_", str(value or fallback).strip())
    text = text.strip("_") or fallback
    if text[0].isdigit():
        text = f"{fallback}_{text}"
    return text


def _route_method(value: Any) -> str:
    method = str(value or "GET").strip().upper()
    return (
        method
        if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
        else "GET"
    )


class ServiceGenerator:
    """Generate service source files using Google Gemini via LangChain.

    The Service Manager sends a compact task plan. This class prepares a code
    skeleton and prompts a Gemini model to produce the final source code that
    the manager will then write to disk.
    """

    MAX_RETRIES = 3

    def __init__(self):
        self._model = os.getenv("SERVICE_GENERATOR_MODEL")
        self._llm = load_llm_model(self._model, temperature=0.8)
        self._current_service_name = ""
        self._generation_mode = os.getenv("SERVICE_GENERATION_MODE", "scaffold")
        self.total_tokens = 0

    def _extract_service_spec(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        task_plan: Dict[str, Any] = {}
        raw_task_plan = payload.get("task_plan")
        if isinstance(raw_task_plan, dict):
            task_plan = raw_task_plan

        service_spec = task_plan.get("service_spec")
        if isinstance(service_spec, dict):
            return deepcopy(service_spec)

        service_building = task_plan.get("service_building")
        if isinstance(service_building, dict):
            return deepcopy(service_building)

        payload_service_spec = payload.get("service_spec")
        if isinstance(payload_service_spec, dict):
            return deepcopy(payload_service_spec)

        payload_service_building = payload.get("service_building")
        if isinstance(payload_service_building, dict):
            return deepcopy(payload_service_building)

        else:
            return {}

    def _extract_tasks(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        tasks: List[Dict[str, Any]] = []
        if isinstance(payload, dict):
            task_plan: Dict[str, Any] = {}
            raw_task_plan = payload.get("task_plan")
            if isinstance(raw_task_plan, dict):
                task_plan = raw_task_plan

            maybe_tasks = payload.get("tasks") or task_plan.get("tasks")
            if isinstance(maybe_tasks, list):
                tasks = [t for t in maybe_tasks if isinstance(t, dict)]
        return tasks

    def _requirements(self, payload: Dict[str, Any]) -> str:
        dependencies = ["Flask==2.3.2", "paho-mqtt==1.6.1"]
        task_plan: Dict[str, Any] = {}
        if isinstance(payload, dict):
            raw_task_plan = payload.get("task_plan")
            if isinstance(raw_task_plan, dict):
                task_plan = raw_task_plan

        nested_output_contract: Dict[str, Any] = {}
        raw_output_contract = task_plan.get("output_contract")
        if isinstance(raw_output_contract, dict):
            nested_output_contract = raw_output_contract

        for source in (payload, task_plan, nested_output_contract):
            if not isinstance(source, dict):
                continue
            for key in ("dependencies", "requirements"):
                items = source.get(key)
                if isinstance(items, list):
                    for item in items:
                        dep = str(item).strip()
                        if dep and dep not in dependencies:
                            dependencies.append(dep)

        return "\n".join(dependencies) + "\n"

    def _build_route_hints(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        hints: List[
            Dict[str, Any]
        ] = []  # hints help the model to generate correct endpoints
        for index, task in enumerate(tasks, start=1):
            endpoint = task.get("endpoint") or task.get("route") or task.get("path")
            if not endpoint:
                continue

            title = (
                task.get("title")
                or task.get("description")
                or task.get("objective")
                or task.get("summary")
                or ""
            )
            hint = {
                "task_id": str(
                    task.get("id") or task.get("task_id") or f"task_{index}"
                ),
                "function_name": _safe_identifier(
                    task.get("id") or task.get("task_id") or f"task_{index}"
                ),
                "title": str(title),
                "endpoint": str(endpoint),
                "method": _route_method(task.get("method")),
                "description": str(task.get("description") or title),
            }
            hints.append(hint)

        return hints

    def _build_mqtt_contract(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Since there were problems when accessing the MQTT broker, we build a simplified contract from the robot capabilities."""
        contract = payload.get("mqtt_contract")
        if isinstance(contract, dict):
            return deepcopy(contract)

        capabilities = payload.get("robot_capabilities")
        if not isinstance(capabilities, dict):
            capabilities = {}

        variables = capabilities.get("variables") or {}
        methods = capabilities.get("methods") or {}

        state_topics = {}
        for variable_name, metadata in variables.items():
            if isinstance(metadata, dict) and metadata.get("mqtt_topic"):
                state_topics[variable_name] = metadata.get("mqtt_topic")

        command_topics = dict(capabilities.get("command_topics") or {})
        for method_name, metadata in methods.items():
            if isinstance(metadata, dict) and metadata.get("mqtt_command_topic"):
                command_topics[method_name] = metadata.get("mqtt_command_topic")

        return {
            "robot_id": capabilities.get("robot_id"),
            "allowed_publish_topics": command_topics,
            "allowed_subscribe_topics": state_topics,
            "rule": "Use only these exact MQTT topics. Do not invent, derive, prefix, or suffix topics.",
        }

    def _build_skeletons(
        self,
        service_name: str,
        service_spec: Dict[str, Any],
        tasks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        service_description = str(service_spec.get("service_description") or "")
        route_hints = self._build_route_hints(tasks)

        app_py_skeleton = f"""
import argparse
import json
import os
import ssl

from flask import Flask, jsonify, request, send_file


app = Flask(__name__)

# API Schema definition - describes all endpoints and their contracts
API_SCHEMA = {{
    "openapi": "3.0.0",
    "info": {{
        "title": "{service_name}",
        "description": "{service_description}"
    }},
    "paths": {{
        # GENERATED_PATHS_PLACEHOLDER
        # Each route should follow this format:
        # "/endpoint": {{
        #     "method": {{
        #         "summary": "Brief description",
        #         "description": "Detailed description",
        #         "requestBody": {{
        #             "content": {{
        #                 "application/json": {{
        #                     "schema": {{
        #                         "type": "object",
        #                         "properties": {{
        #                             "param_name": {{"type": "string", "description": "..."}},
        #                         }},
        #                         "required": ["param_name"]
        #                     }}
        #                 }}
        #             }}
        #         }},
        #         "responses": {{
        #             "200": {{
        #                 "description": "Success response",
        #                 "content": {{
        #                     "application/json": {{
        #                         "schema": {{
        #                             "type": "object",
        #                             "properties": {{
        #                                 "result": {{"type": "string"}}, # Important: use include each value inside ""
        #                                 "success": {{"type": "boolean"}}
        #                             }}
        #                         }}
        #                     }}
        #                 }}
        #             }}
        #         }}
        #     }}
        # }}
    }}
}}

@app.route('/api-schema')
def api_schema():
    return jsonify(API_SCHEMA)

@app.route('/')
def ui():
    return send_file('ui.html')


def main():
    parser = argparse.ArgumentParser(description="Run the generated service")
    parser.add_argument("--port", type=int, default=CONTAINER_PORT)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
"""

        requirements_skeleton = self._requirements({"task_plan": {"dependencies": []}})

        return {
            "app.py": app_py_skeleton,
            "requirements.txt": requirements_skeleton,
            "route_hints": route_hints,
        }

    def _build_ui_prompt_section(self, service_spec: Dict[str, Any]) -> str:
        """Build the UI generation section for the prompt if required."""
        if not service_spec.get("requires_ui"):
            return ""

        ui_description = service_spec.get("ui_description")
        api_endpoint = service_spec.get("api_endpoint") or "/api/default"

        return (
            """
UI REQUIREMENTS:
- Generate ui.html file with the complete HTML+CSS+JS UI
- The ui.html must be completely self-contained (inline CSS/JS, no external files)
- The ui.html page must be accessible via GET at / (root URL)

CRITICAL: Do NOT use blocking GET behavior. Use polling instead:

1. POST """
            + api_endpoint
            + """:
   - Stores user interaction data internally
   - Sets a flag indicating new data is available
   - Supports optional "completed" field to signal user finished

2. GET /api/status (NO blocking):
   - Returns: {data: <stored data>, completed: boolean}
   - The AP polls this endpoint repeatedly

3. Do NOT implement blocking GET - use polling via /api/status
"""
        )

    def _build_ui_rules_section(self, service_spec: Dict[str, Any]) -> List[str]:
        """Build UI generation rules if required."""
        if not service_spec.get("requires_ui"):
            return []

        api_endpoint = service_spec.get("api_endpoint") or "/api/default"
        return [
            "If requires_ui is true, include ui.html in the files array with the complete HTML content",
            f"POST {api_endpoint} must store user interaction data and update status (do NOT serve ui.html here)",
            "GET /api/status must return {data, completed} - NO blocking behavior",
            "The ui.html page is served via GET at the root URL /",
            "Implement internal state to track: last interaction data, completion flag",
            "Do NOT implement blocking GET behavior - use polling via /api/status instead",
        ]

    def _build_generation_messages(
        self, payload: Dict[str, Any], service_name: str
    ) -> List[Dict[str, str]]:
        service_spec = self._extract_service_spec(payload)
        tasks = self._extract_tasks(payload)
        skeletons = self._build_skeletons(service_name, service_spec, tasks)
        task_plan = (
            payload.get("task_plan")
            if isinstance(payload.get("task_plan"), dict)
            else {}
        )

        # mqtt_contract = self._build_mqtt_contract(payload)

        ui_section = self._build_ui_prompt_section(service_spec)
        system_prompt = (
            "You are a Python service generator. "
            "Generate complete, working source code, not partial patches. "
            "Return exactly one valid JSON object and nothing else. "
            "The output will be parsed with json.loads(). "
            "If the JSON is invalid, the response is considered a failure. "
            'The JSON must have the structure {"app_name":...,"files":[{"path":...,"content":...},...],"run":...}. '
            "The files array must contain full file contents. "
            "File paths must be written without a leading slash (for example app.py, not /app.py). "
            "IMPORTANT: DO NOT generate any endpoints that aren't useful for reaching the objective (like health/). "
            "Remember to import all necessary methods and libraries when generating the app.py file. "
            "If the app is Flask-based, do not enable debug mode. "
            "Preserve the requested app name, keep the code minimal, and include only app.py and requirements.txt. "
            "Use the skeletons as the starting point and keep the app generic and reusable as a Flask starter. "
            "Ensure app.py binds to 0.0.0.0 and parses --port with argparse. "
            "If the task plan defines HTTP endpoints, implement them. "
            "Do not use true/false values for boolean fields; use Python boolean literals (True/False) instead."
            "For some endpoints, parameters are optional. This means that you don't have to specify only the topic name without any payload."
            "CRITICAL: In API_SCHEMA, NEVER use boolean values (true/false/True/False). "
            "Use strings like 'yes'/'no' or integers like 1/0 instead of booleans. "
            "This applies to every field in API_SCHEMA - there must be no true/false anywhere. "
            "CRITICAL: Replace EVERY placeholder marker, especially GENERATED_PATHS_PLACEHOLDER in API_SCHEMA. "
            " IMPORTANT: Generate a complete valid OpenAPI 3.0.0 schema that can be retrieved at endpoint /api-schema. "
            "Each path must have the HTTP method (lowercase), summary, description, requestBody, and responses. "
            "Do not return incomplete code or stubs. "
            "Do not return the skeleton unchanged. "
            "Every endpoint becomes a Flask route AND is documented in API_SCHEMA paths. "
            "If any placeholder remains or JSON is invalid or schema is incomplete, the output fails."
            "CRITICAL FOR GAMES WITH REAL HUMAN OPPONENTS: If the service is a game where a human plays against OTHER REAL PLAYERS (located on other agents/robots/machines), you MUST:\n"
            "- NEVER generate fake opponents controlled by the CPU or random logic (do NOT use random.choice(), np.random, or any AI/bot logic for opponent moves)\n"
            "- The opponent is a REAL HUMAN on another machine/agent\n"
            "- The opponent's move ARRIVES VIA INTER-AGENT MESSAGING forwarded by the Agent Program\n"
            "- Generate a POST /api/opponent-move endpoint:\n"
            "  1. Route: POST /api/opponent-move\n"
            "  Do not use any other name for the endpoint — it must be exactly 'POST /api/opponent-move'\n"
            "  This must be the only endpoint that needs to be called by the Agent Program to store the opponent's move\n"
            "  Do not generate any other unnecessary endpoints or routes\n"
            "  2. Stores the opponent's move in game state\n"
            "  3. Does NOT calculate game result here — only stores the opponent's move\n"
            "  4. Sets completed=True ONLY when BOTH local and opponent moves are present\n"
            "  5. Update user's UI to show the game result (e.g. show the winner, score, or game board)\n"
            "- Example game_state structure:\n"
            "  game_state = {\n"
            '      "data": None,\n'
            '      "opponent_move": None,\n'
            '      "local_move": None,\n'
            '      "completed": False,\n'
            '      "last_update": time.time()\n'
            "  }\n"
            f"{ui_section}"
        )
        ui_rules = self._build_ui_rules_section(service_spec)

        if self._generation_mode == "free":
            print("[service_generator] Mode: FREE (no skeletons, direct generation)")
            human_prompt = json.dumps(
                {
                    "service_spec": service_spec,
                    "service_name": service_name,
                    "robot_capabilities": payload.get("robot_capabilities") or {},
                    "output_contract": {
                        "required_files": ["app.py", "requirements.txt"],
                        "optional_files": ["ui.html"]
                        if service_spec.get("requires_ui")
                        else [],
                        "runtime_entrypoint": f"python app.py --port {service_spec.get('container_port') or 8000}",
                    },
                    "generation_rules": [
                        "Use Flask",
                        "Keep only essential files",
                        "Return full file contents",
                        "Do not use markdown fences",
                        "Do not add explanatory text",
                        "Ignore any references to skeletons or placeholders, as they are not provided to you.",
                        "Replace GENERATED_PATHS_PLACEHOLDER with actual OpenAPI path definitions",
                        "For each route_hint, create a Flask route and a corresponding API_SCHEMA path entry",
                        "HTTP methods in API_SCHEMA must be lowercase: get, post, put, patch, delete",
                        "Every endpoint must have a 200 response with schema definition",
                        "Use JSON Schema format for requestBody and response schemas",
                        "API_SCHEMA must be valid Python dict literal AND valid JSON",
                        "NEVER use boolean values (true/false/True/False) anywhere in API_SCHEMA. Use strings like 'yes'/'no' or integers like 1/0 instead.",
                        "If requires_ui is true, include ui.html as a separate file with complete HTML content",
                        "  - POST api_endpoint stores user interaction data (do NOT serve ui.html here)",
                        "  - GET /api/status returns {data, completed} - NO blocking",
                        "  - Serve ui.html via GET at root URL /",
                        "  - Implement polling-based flow: AP polls /api/status for new data",
                        "MULTIPLAYER GAME RULE: For games where the player faces a REAL HUMAN on another agent/machine, NEVER generate CPU opponents. The opponent is NOT a bot - they are a real person connected via inter-agent messaging. DO NOT use random.choice(), np.random, or any internal logic to generate opponent moves. Use the /api/opponent-move endpoint to receive opponent moves forwarded by the AP.",
                        "If the task definition includes multiplayer_move_endpoint, generate a POST /api/opponent-move endpoint that stores the opponent's move in game state. Do NOT calculate game results in this endpoint — only store the move. Do NOT generate CPU/random opponent logic.",
                        "Use the units declared in constraints; never convert milliseconds unless the capability explicitly asks for milliseconds",
                        f"{ui_rules if ui_rules else ''}",
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )

        else:
            print("[service_generator] Mode: SCAFFOLD (with skeletons and route hints)")
            human_prompt = json.dumps(
                {
                    "service_spec": service_spec,
                    "task_plan": task_plan,
                    "tasks": tasks,
                    "multiplayer_move_endpoint": service_spec.get(
                        "multiplayer_move_endpoint"
                    ),
                    "robot_capabilities": payload.get("robot_capabilities") or {},
                    "skeletons": skeletons,
                    "api_schema_template_help": {
                        "purpose": "Replace GENERATED_PATHS_PLACEHOLDER with OpenAPI 3.0.0 path definitions",
                        "for_each_route_hint": "Create both a Flask @app.route() and an API_SCHEMA path entry",
                        "path_entry_structure": "/endpoint_name: { http_method: { summary, description, requestBody, responses } }",
                        "json_schema_example": {
                            "type": "object",
                            "properties": {"param": {"type": "string"}},
                            "required": ["param"],
                        },
                    },
                    "output_contract": {
                        "required_files": ["app.py", "requirements.txt"],
                        "optional_files": ["ui.html"]
                        if service_spec.get("requires_ui")
                        else [],
                        "runtime_entrypoint": "python app.py --port <container_port>",
                    },
                    "generation_rules": [
                        "Use Flask",
                        "Keep only essential files",
                        "Return full file contents",
                        "Do not use markdown fences",
                        "Do not add explanatory text",
                        "Replace GENERATED_PATHS_PLACEHOLDER with actual OpenAPI path definitions",
                        "For each route_hint, create a Flask route and a corresponding API_SCHEMA path entry",
                        "HTTP methods in API_SCHEMA must be lowercase: get, post, put, patch, delete",
                        "Every endpoint must have a 200 response with schema definition",
                        "Use JSON Schema format for requestBody and response schemas",
                        "API_SCHEMA must be valid Python dict literal AND valid JSON",
                        "NEVER use boolean values (true/false/True/False) anywhere in API_SCHEMA. Use strings like 'yes'/'no' or integers like 1/0 instead.",
                        "If requires_ui is true, include ui.html as a separate file with complete HTML content",
                        "  - POST api_endpoint stores user interaction data (do NOT serve ui.html here)",
                        "  - GET /api/status returns {data, completed} - NO blocking",
                        "  - Serve ui.html via GET at root URL /",
                        "  - Implement polling-based flow: AP polls /api/status for new data",
                        "MULTIPLAYER GAME RULE: For games where the player faces a REAL HUMAN on another agent/machine, NEVER generate CPU opponents. The opponent is NOT a bot - they are a real person connected via inter-agent messaging. DO NOT use random.choice(), np.random, or any internal logic to generate opponent moves. Use the /api/opponent-move endpoint to receive opponent moves forwarded by the AP.",
                        "If the task definition includes multiplayer_move_endpoint, generate a POST /api/opponent-move endpoint that stores the opponent's move in game state. Do NOT calculate game results in this endpoint — only store the move. Do NOT generate CPU/random opponent logic.",
                        "Use the units declared in constraints; never convert milliseconds unless the capability explicitly asks for milliseconds",
                        f"{ui_rules if ui_rules else ''}",
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": human_prompt},
        ]

    def _call_model(self, messages: List[Dict[str, str]]) -> str:
        print(
            f"[service_generator._call_model] calling model={self._model} messages={len(messages)}"
        )

        lc_messages = []
        for message in messages:
            role = str(message.get("role") or "user").lower()
            content = str(message.get("content") or "")
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            else:
                lc_messages.append(HumanMessage(content=content))

        print("[service_generator._call_model] Invoking LLM...")
        response = self._llm.invoke(
            lc_messages,
        )
        self.total_tokens = getattr(response, "usage_metadata", {}).get(
            "total_tokens", 0
        )
        print(f"[service_generator._call_model] usage_metadata={self.total_tokens}")

        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        print(
            f"[service_generator._call_model] Response received, length={len(str(content))} chars"
        )
        return str(content)

    def _extract_json_payload(self, text: str) -> Dict[str, Any]:
        raw_text = strip_code_fences(text)
        return parse_llm_json_payload(
            raw_text,
            expected_type=dict,
            error_prefix="Model response",
        )

    def _build_retry_messages(
        self,
        original_messages: List[Dict[str, str]],
        error: str,
        attempt: int,
    ) -> List[Dict[str, str]]:
        """Build a retry message with JSON error feedback."""
        retry_instruction = f"""
[JSON PARSING ERROR - Attempt {attempt}/{self.MAX_RETRIES}]
The previous response was NOT valid JSON. Error: {error}

IMPORTANT FIXES REQUIRED:
    "IMPORTANT: Write all property names and values enclosed in double quotes.\n"
    "IMPORTANT: Write a comma after each property value.\n"

Please regenerate the complete JSON response with these fixes applied.
"""
        return original_messages + [{"role": "user", "content": retry_instruction}]

    def _validate_generated_spec(
        self, generated_spec: Dict[str, Any], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        files = generated_spec.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("The model did not return any files.")

        validated_files: List[Dict[str, Any]] = []
        for entry in files:
            if not isinstance(entry, dict):
                continue
            relative_path = str(entry.get("path") or "").strip()
            content = entry.get("content", "")
            if not relative_path:
                continue

            normalized_path = os.path.normpath(relative_path)
            if os.path.isabs(relative_path) or normalized_path.startswith(".."):
                raise ValueError(f"Unsafe path received from model: {relative_path}")

            validated_files.append(
                {
                    "path": normalized_path,
                    "content": str(content),
                }
            )

        if not validated_files:
            raise ValueError("The model returned no valid file entries.")

        service_spec = self._extract_service_spec(payload)
        container_port = int(service_spec.get("container_port") or 8000)

        generated_spec = dict(generated_spec)
        generated_spec["app_name"] = self._current_service_name or str(
            service_spec.get("service_name") or "service"
        )
        generated_spec["files"] = validated_files
        generated_spec.setdefault("run", f"python app.py --port {container_port}")
        return generated_spec

    def generate(
        self, payload: Dict[str, Any], service_name: str, robot_id: str
    ) -> Dict[str, Any]:
        self.robot_id = robot_id
        self._current_service_name = service_name
        plan = deepcopy(payload if isinstance(payload, dict) else {})
        print("[service_generator] === GENERATION STARTED ===")

        print("[service_generator] Building generation messages...")
        messages = self._build_generation_messages(plan, service_name)

        generated_spec = None
        last_error = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            print(
                f"[service_generator] Calling LLM model (attempt {attempt}/{self.MAX_RETRIES})..."
            )
            model_output = self._call_model(messages)

            print("[service_generator] Extracting JSON payload from model output...")
            try:
                generated_spec = self._extract_json_payload(model_output)
                if isinstance(generated_spec, dict) and "files" in generated_spec:
                    for f in generated_spec.get("files", []):
                        print(f"  - {f.get('path')}: {len(f.get('content', ''))} bytes")
                break
            except ValueError as exc:
                last_error = str(exc)
                print(f"[service_generator] JSON parsing failed: {last_error}")
                if attempt < self.MAX_RETRIES:
                    print("[service_generator] Retrying with error feedback...")
                    messages = self._build_retry_messages(
                        messages, last_error, attempt + 1
                    )
                    generated_spec = None
                else:
                    print(f"[service_generator] All {self.MAX_RETRIES} attempts failed")
                    raise

        if generated_spec is None:
            raise ValueError("Could not generate valid JSON after retries")

        validated_spec = self._validate_generated_spec(generated_spec, plan)
        validated_spec["task_plan"] = plan

        print("[service_generator] === GENERATION COMPLETED ===")
        return validated_spec
