import os
import time

from dotenv import load_dotenv

from dt_turtlebot.cloud_twin.digital_turtlebot import DigitalTurtlebot
from service_manager.service_manager import ServiceManager
from service_manager.service_manager_server import run_server

PORT = 5000


def test_print_state_variables(robot: DigitalTurtlebot):
    """Print all discovered state variables and their current values."""
    print("\n[TEST] TurtleBot state variables")

    if not robot.state.variables_metadata:
        print("[TEST] No state variables discovered")
        return

    current_state = robot.get_state().get("state", {})
    for variable_name, metadata in robot.state.variables_metadata.items():
        value = current_state.get(variable_name, metadata.last_value)
        print(
            f"[TEST] - {variable_name} "
            f"(type={metadata.data_type}, topic={metadata.mqtt_topic}): {value}"
        )


def test_send_movement_commands(robot: DigitalTurtlebot):
    """Send two movement commands: move forward briefly, then stop."""
    print("\n[TEST] Sending TurtleBot movement commands")

    first_result = robot.move(linear_x=0.05, linear_y=0.0, angular_z=0.0)
    print(f"[TEST] move forward command sent: {first_result}")

    time.sleep(1)

    second_result = robot.move(linear_x=0.0, linear_y=0.0, angular_z=0.0)
    print(f"[TEST] stop movement command sent: {second_result}")


def main():
    load_dotenv()
    robot = DigitalTurtlebot()
    if os.getenv("ENABLE_MQTT", "false").lower() == "true":
        BROKER_HOST = os.getenv("BROKER_HOST")
        BROKER_PORT = os.getenv("BROKER_PORT")
        MQTT_USERNAME = os.getenv("MQTT_USERNAME")
        MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

        if not BROKER_HOST or not BROKER_PORT or not MQTT_USERNAME or not MQTT_PASSWORD:
            print("Environment variables not set")
            return

        robot.connect(MQTT_USERNAME, MQTT_PASSWORD, BROKER_HOST, int(BROKER_PORT))
    else:
        print("MQTT not enabled")

    time.sleep(2)

    # test_print_state_variables(robot)
    # test_send_movement_commands(robot)

    sm = ServiceManager(
        robot.state.robot_id,
        robot.get_capabilities(),
        manager_url=f"http://127.0.0.1:{PORT}",
        mqtt_robot=True,
        agent_controller_url="http://127.0.0.1:8080",
    )

    try:
        sm.start_global_registry_heartbeat()
        run_server(service_manager=sm, host="0.0.0.0", port=PORT, debug=False)
    finally:
        sm.stop_global_registry_heartbeat()
        sm.unregister_from_global_registry()
        robot.disconnect()


if __name__ == "__main__":
    main()
