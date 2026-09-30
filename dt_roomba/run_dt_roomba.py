import os
import time
from re import M

import rclpy
from dotenv import load_dotenv

from dt_roomba.cloud_twin.digital_roomba import DigitalRoomba
from service_manager.service_manager import ServiceManager
from service_manager.service_manager_server import run_server


def test_play_song(robot):
    print("\n5. Testing play song...")
    robot.change_mode("safe")
    time.sleep(1)
    # Midi note numbers
    f4 = 65
    a4 = 69
    c5 = 72
    # note lengths
    MEASURE = 160  #
    HALF = int(MEASURE / 2)
    Q = int(MEASURE / 4)  # quarter note
    ED = int(MEASURE * 3 / 16)  # eighth note
    S = int(MEASURE / 16)  # sixteenth note
    # Play Imperial March from Star Wars
    robot.send_song(
        0, [a4, a4, a4, f4, c5, a4, f4, c5, a4], [Q, Q, Q, ED, S, Q, ED, S, HALF]
    )
    robot.play_song(0)
    time.sleep(10)


PORT = 5001


def main():
    load_dotenv()
    robot = DigitalRoomba()
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

    sm = ServiceManager(
        robot_id=robot.state.robot_id,
        robot_capabilities=robot.get_capabilities(),
        manager_url=f"http://127.0.0.1:{PORT}",
        mqtt_robot=True,
        agent_controller_url="http://127.0.0.1:8081",
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

"""
curl -X POST "http://127.0.0.1:5000/create_service" \
  -H "Content-Type: application/json" \
  -d '{
  "service_name": "imperial-march-app",
  "service_description": "Create a service that plays the Imperial March from Star Wars, exposing the api via GET /imperial",
  "app_dir": "imperial_march_app",
  "kind": "kubernetes"
}'

curl -X POST "http://127.0.0.1:5001/start_service" \
  -H "Content-Type: application/json" \
  -d '{
  "service_name": "play-imperial-march-roomba"
}'
"""
