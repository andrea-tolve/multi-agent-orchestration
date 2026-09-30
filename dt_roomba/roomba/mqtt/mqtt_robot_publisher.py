"""
MQTT Publisher for Roomba

Publishes robot state to MQTT broker at regular intervals.
Reads state directly from PyRoombaAdapter.

Inherits common MQTT functionality from BaseMQTTPublisher.
Implements Roomba-specific state publishing.

Usage:
    python -m roomba.mqtt.mqtt_robot_publisher --broker localhost --port 1883 --topic roomba
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from dt_roomba.roomba.robot import PyRoombaAdapter
from shared.mqtt.state_mqtt_publisher import StateMQTTPublisher


class MQTTPublisher(StateMQTTPublisher):
    """MQTT Publisher for Roomba state"""

    def __init__(
        self,
        robot: PyRoombaAdapter,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        base_topic: str = "roomba",
        client_id: str = "roomba_publisher",
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """
        Initialize Roomba MQTT Publisher

        Args:
            robot: PyRoombaAdapter instance
            broker_host: MQTT broker hostname
            broker_port: MQTT broker port
            base_topic: Base topic for publishing
            client_id: MQTT client ID
            username: MQTT broker username
            password: MQTT broker password
        """
        super().__init__(
            robot=robot,
            broker_host=broker_host,
            broker_port=broker_port,
            base_topic=base_topic,
            client_id=client_id,
            username=username,
            password=password,
        )

        # Type guard: ensure robot is not None (PyRoombaAdapter is required for robot publisher)
        assert self.robot is not None, "robot must not be None for robot publisher"
        self._info_published = False

    def _publish_state(self):
        """Publish Roomba state to MQTT topics"""
        if not self.connected:
            return

        try:
            # Request and publish battery state
            self.robot.request_voltage()
            self.robot.request_current()
            self.robot.request_temperature()
            self.robot.request_charge()
            self.robot.request_capacity()

            battery_data = {
                "voltage": self.robot.voltage,
                "current": self.robot.current,
                "temperature": self.robot.temperature,
                "charge": self.robot.battery_charge,
                "capacity": self.robot.battery_capacity,
                "timestamp": time.time(),
            }
            self._publish_topic("battery", battery_data)

            # Request and publish sensor data
            self.robot.request_distance()
            self.robot.request_angle()
            self.robot.request_encoder_counts()

            sensor_data = {
                "distance": self.robot.distance,
                "angle": self.robot.angle,
                "encoder_left": self.robot.encoder_left,
                "encoder_right": self.robot.encoder_right,
                "timestamp": time.time(),
            }
            self._publish_topic("sensors", sensor_data)

            # Request and publish mode and charging state
            self.robot.request_oi_mode()
            self.robot.request_charging_state()

            mode_data = {
                "oi_mode": self.robot.oi_mode,
                "charging_state": self.robot.charging_state,
                "timestamp": time.time(),
            }
            self._publish_topic("mode", mode_data)

            if not self._info_published:
                variables = self.robot.get_variables()
                self._publish_topic(
                    "info/variables",
                    {**variables, "timestamp": time.time()},
                    retain=True,
                )

                methods = self.robot.get_methods()
                self._publish_topic(
                    "info/methods", {**methods, "timestamp": time.time()}, retain=True
                )
                self._info_published = True

        except Exception as e:
            print(f"[ERROR] Error publishing state: {e}")


def main():
    parser = argparse.ArgumentParser(description="Roomba MQTT Publisher")
    parser.add_argument("--broker", default="localhost", help="MQTT broker hostname")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--topic", default="roomba", help="Base MQTT topic")
    parser.add_argument(
        "--interval", type=float, default=0.1, help="Publishing interval in seconds"
    )
    parser.add_argument(
        "--client-id", default="roomba_publisher", help="MQTT client ID"
    )
    parser.add_argument("--username", default=None, help="MQTT broker username")
    parser.add_argument("--password", default=None, help="MQTT broker password")
    parser.add_argument(
        "--port-serial", default="/dev/ttyUSB0", help="Serial port for Roomba"
    )

    args = parser.parse_args()

    mqtt_pub = None
    try:
        robot = PyRoombaAdapter(port=args.port_serial)

        mqtt_pub = MQTTPublisher(
            robot,
            broker_host=args.broker,
            broker_port=args.port,
            base_topic=args.topic,
            client_id=args.client_id,
            username=args.username,
            password=args.password,
        )
        mqtt_pub.publish_interval = args.interval

        mqtt_pub.connect()
        time.sleep(1)
        mqtt_pub.start()

        print(
            f"[INFO] Publishing robot state to {args.broker}:{args.port}/{args.topic}/*"
        )
        print("[INFO] Press Ctrl+C to stop")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user")
    except Exception as e:
        print(f"[ERROR] Error: {e}")
    finally:
        if mqtt_pub is not None:
            mqtt_pub.stop()
            mqtt_pub.disconnect()


if __name__ == "__main__":
    main()
