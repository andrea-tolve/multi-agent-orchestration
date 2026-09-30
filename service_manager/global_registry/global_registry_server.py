"""Flask REST server for the global registry."""

from __future__ import annotations

from flask import Flask, jsonify, request

from .global_registry import registry

app = Flask(__name__)


def _robot_name_from_payload(payload):
    return payload.get("robot_name") or payload.get("robot_id")


def _ttl_from_payload(payload):
    ttl = payload.get("ttl_seconds")
    if ttl in [None, ""]:
        return None
    try:
        parsed = int(ttl)
        return parsed if parsed > 0 else None
    except Exception:
        return None


@app.get("/")
def home():
    return jsonify(
        {
            "message": "Global registry server is running",
            "endpoints": [
                "GET /health",
                "GET /registry",
                "GET /robots",
                "POST /robots",
                "POST /robots/<robot_name>/heartbeat",
                "GET /robots/<robot_name>",
                "DELETE /robots/<robot_name>",
                "GET /robots/<robot_name>/services",
                "POST /robots/<robot_name>/services",
                "DELETE /robots/<robot_name>/services/<service_name>",
                "GET /services",
                "POST /query",
            ],
        }
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.get("/registry")
def get_registry():
    return jsonify(registry.to_dict()), 200


@app.get("/robots")
def list_robots():
    return jsonify(registry.list_robots()), 200


@app.post("/robots")
def register_robot():
    data = request.get_json() or {}
    robot_name = _robot_name_from_payload(data)
    manager_url = data.get("manager_url")
    capabilities = data.get("capabilities") or {}
    ttl_seconds = _ttl_from_payload(data)

    if not robot_name:
        return jsonify({"error": "robot_name is required"}), 400
    if not isinstance(manager_url, str) or not manager_url:
        return jsonify({"error": "manager_url is required"}), 400
    if not isinstance(capabilities, dict):
        return jsonify({"error": "capabilities must be an object"}), 400

    robot = registry.register_robot(
        robot_name,
        manager_url,
        controller_url=data.get("controller_url"),
        capabilities=capabilities,
        ttl_seconds=ttl_seconds,
    )
    return jsonify(robot), 200


@app.post("/robots/<robot_name>/heartbeat")
def heartbeat_robot(robot_name: str):
    data = request.get_json() or {}
    manager_url = data.get("manager_url")
    capabilities = data.get("capabilities")
    ttl_seconds = _ttl_from_payload(data)

    if capabilities is not None and not isinstance(capabilities, dict):
        return jsonify({"error": "capabilities must be an object"}), 400

    try:
        robot = registry.heartbeat_robot(
            robot_name,
            manager_url=manager_url,
            capabilities=capabilities,
            ttl_seconds=ttl_seconds,
            controller_url=data.get("controller_url"),
        )
        return jsonify(robot), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/robots/<robot_name>")
def get_robot(robot_name: str):
    robot = registry.get_robot(robot_name)
    if not robot:
        return jsonify({"error": f"robot '{robot_name}' not found or expired"}), 404
    return jsonify(robot), 200


@app.delete("/robots/<robot_name>")
def delete_robot(robot_name: str):
    robot = registry.unregister_robot(robot_name)
    if not robot:
        return jsonify({"error": f"robot '{robot_name}' not found"}), 404
    return jsonify(robot), 200


@app.get("/robots/<robot_name>/services")
def list_robot_services(robot_name: str):
    robot = registry.get_robot(robot_name)
    if not robot:
        return jsonify({"error": f"robot '{robot_name}' not found or expired"}), 404
    return jsonify(robot.get("services") or {}), 200


@app.post("/robots/<robot_name>/services")
def add_robot_service(robot_name: str):
    data = request.get_json() or {}
    executable_service = data.get("executable_service") or data
    if not isinstance(executable_service, dict):
        return jsonify({"error": "executable_service must be an object"}), 400

    try:
        service = registry.add_service(robot_name, executable_service)
        return jsonify(service), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.delete("/robots/<robot_name>/services/<service_name>")
def remove_robot_service(robot_name: str, service_name: str):
    removed = registry.remove_service(robot_name, service_name)
    if not removed:
        return jsonify({"error": f"service '{service_name}' not found"}), 404
    return jsonify(removed), 200


@app.get("/services")
def list_services():
    return jsonify(registry.list_services()), 200


@app.post("/query")
def query_registry():
    data = request.get_json()
    service_name = data.get("service_name")
    service_description = data.get("service_description")
    requester_robot_name = data.get("requester_robot_name")
    any_remote_agent = bool(data.get("any_remote_agent"))
    result = registry.query(
        service_name,
        service_description,
        requester_robot_name=requester_robot_name,
        any_remote_agent=any_remote_agent,
    )
    return jsonify(result), 200


def run_server(host: str = "0.0.0.0", port: int = 6000, debug: bool = False):
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_server()
