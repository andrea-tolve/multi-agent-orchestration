from flask import Flask, jsonify, request

from service_manager.service_manager import ServiceManager

app = Flask(__name__)


def set_service_manager(sm: ServiceManager):
    """Replace the global SERVICE_MANAGER instance with a provided one."""
    global SERVICE_MANAGER
    SERVICE_MANAGER = sm


def run_server(
    service_manager: ServiceManager,
    host: str = "0.0.0.0",
    port: int = 5000,
    debug: bool = False,
):
    """Helper to set the SERVICE_MANAGER and run the Flask server."""
    set_service_manager(service_manager)
    app.run(host=host, port=port, debug=debug)


@app.get("/")
def home():
    """A simple home route."""
    return "Hello from Service Manager! Use /create_service, /start_service, /services, /services/<service_name>, /search_service, and /stop_service."


@app.post("/create_service")
def create_service():
    data = request.get_json()

    if not data:
        print("[create_service] Error: Request must be JSON")
        return jsonify({"error": "Request must be JSON"}), 400

    try:
        service_name = data["service_name"]

        existing_service = SERVICE_MANAGER.getServiceDetails(service_name)
        if existing_service:
            print(
                f"[create_service] Service already exists: {service_name}, status={existing_service.get('status')}"
            )
            if (
                existing_service.get("status") == "running"
                or existing_service.get("desired_state") == "running"
            ):
                print("[create_service] Service already running, returning existing")
                return jsonify(existing_service), 200

            print("[create_service] Starting existing service")
            SERVICE_MANAGER.startService(service_name)
            return jsonify(SERVICE_MANAGER.getServiceDetails(service_name)), 200

        service_building = {
            "service_name": service_name,
            "service_description": data.get("service_description"),
            "app_dir": data.get("app_dir"),
            "service_port": None,
            "container_port": 8000,
            "kind": data.get("kind"),
            "requires_ui": data.get("requires_ui", False),
            "ui_description": data.get("ui_description"),
            "api_endpoint": data.get("api_endpoint"),
            "is_delegated": data.get("is_delegated", False),
        }

        created_service = SERVICE_MANAGER.createService(service_building)

        # If the service was delegated to another manager, return the response from that manager
        if isinstance(created_service, dict) and created_service.get(
            "delegation_required"
        ):
            print("[create_service] Service delegated, returning delegated response")
            return jsonify(created_service), 200

        SERVICE_MANAGER.startService(service_name)
        return jsonify(SERVICE_MANAGER.getServiceDetails(service_name)), 200
    except Exception as e:
        import traceback

        print(f"[create_service] ERROR: {type(e).__name__}: {str(e)}")
        print(f"[create_service] Traceback: {traceback.format_exc()}")
        return jsonify(
            {"status": "error", "message": f"Failed to create app: {str(e)}"}
        ), 500


@app.post("/start_service")
def start_service():
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request must be JSON"}), 400

    service_name = data.get("service_name")

    if not service_name:
        return jsonify({"error": "'service_name' needs to be specified!"}), 400

    try:
        existing_service = SERVICE_MANAGER.getServiceDetails(service_name)
        if not existing_service:
            return jsonify(
                {
                    "error": f"Service '{service_name}' not found. Create it first with /create_service."
                }
            ), 404
        if (
            existing_service.get("status") == "running"
            or existing_service.get("desired_state") == "running"
        ):
            return jsonify(existing_service), 200
        result = SERVICE_MANAGER.startService(service_name)
        return jsonify(
            {
                "message": result,
                "service": SERVICE_MANAGER.getServiceDetails(service_name),
            }
        ), 200
    except Exception as e:
        return jsonify(
            {"status": "error", "message": f"Failed to start app: {str(e)}"}
        ), 500


@app.get("/services")
def get_all_services():
    return jsonify(SERVICE_MANAGER.getAllServices()), 200


@app.get("/services/<service_name>")
def get_service_details(service_name):
    service = SERVICE_MANAGER.getServiceDetails(service_name)
    if not service:
        return jsonify({"error": f"Service '{service_name}' not found"}), 404
    return jsonify(service), 200


@app.post("/search_service")
def search_service():
    data = request.get_json() or {}
    service_name = data.get("service_name") or data.get("serviceName") or ""
    service_description = data.get("service_description") or ""
    return jsonify(
        SERVICE_MANAGER.searchService(service_name, service_description)
    ), 200


@app.post("/stop_service")
def stop_service():
    data = request.get_json()

    if not data:
        print("[stop_service] Error: Request must be JSON")
        return jsonify({"error": "Request must be JSON"}), 400

    service_name = data.get("service_name")

    if not service_name:
        print("[stop_service] Error: service_name not specified")
        return jsonify({"error": "'service_name' needs to be specified!"}), 400

    try:
        print(f"[stop_service] Shutting down service: {service_name}")
        result = SERVICE_MANAGER.shutdownService(service_name)
        print(f"[stop_service] Shutdown result: {result}")
        details = SERVICE_MANAGER.getServiceDetails(service_name)
        print(f"[stop_service] Service details after shutdown: {details}")
        return jsonify(
            {
                "message": result,
                "service": details,
            }
        ), 200
    except Exception as e:
        import traceback

        print(f"[stop_service] ERROR: {type(e).__name__}: {str(e)}")
        print(f"[stop_service] Traceback: {traceback.format_exc()}")
        return jsonify(
            {"status": "error", "message": f"Failed to stop service: {str(e)}"}
        ), 500


@app.delete("/services/<service_name>")
def delete_service(service_name):
    """Deletes a service using the ServiceManager.deleteService method."""
    try:
        # Verify the service exists first for clearer error code semantics
        existing = SERVICE_MANAGER.getServiceDetails(service_name)
        if not existing:
            return jsonify({"error": f"Service '{service_name}' not found"}), 404

        result = SERVICE_MANAGER.deleteService(service_name)
        return jsonify({"message": result}), 200
    except ValueError as e:
        msg = str(e)
        # Unknown service -> 404, running service or other validation -> 400
        status_code = 404 if msg.lower().startswith("unknown service") else 400
        return jsonify({"status": "error", "message": msg}), status_code
    except Exception as e:
        return jsonify(
            {"status": "error", "message": f"Failed to delete service: {str(e)}"}
        ), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)


"""
Test start_service (bash / curl)

curl -X POST "http://127.0.0.1:5000/create_service" \
  -H "Content-Type: application/json" \
  -d '{
  "service_name": "app1",
  "service_description": "Simple Flask app which prints hello world",
  "app_dir": "my_app_code",
  "kind": "kubernetes"
}'

curl -X POST "http://127.0.0.1:5000/start_service" \
-H "Content-Type: application/json" \
-d '{
"service_name": "app1"
}'

Test list and inspect services (bash / curl)

curl "http://127.0.0.1:5000/services"
curl "http://127.0.0.1:5000/services/app1"

Test search_service (bash / curl)

curl -X POST "http://127.0.0.1:5000/search_service" \
  -H "Content-Type: application/json" \
  -d '{
  "service_name": "app1",
  "service_description": "Flask"
}'

Test stop_service (bash / curl)

curl -X POST "http://127.0.0.1:5001/stop_service" \
  -H "Content-Type: application/json" \
  -d '{
  "service_name": "app1"
}'
"""
