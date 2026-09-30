"""
MQTT Publisher for TurtleBot 3 Digital Twin

Publishes robot state to MQTT broker at regular intervals.

Inherits common MQTT functionality from BaseMQTTPublisher.
Implements TurtleBot-specific state publishing.
"""

import argparse
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import rclpy

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from dt_turtlebot.turtlebot.robot import TurtleBot3Adapter
from shared.mqtt.state_mqtt_publisher import StateMQTTPublisher


class MQTTPublisher(StateMQTTPublisher):
    """MQTT Publisher for TurtleBot 3 state"""

    def __init__(
        self,
        robot: TurtleBot3Adapter,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        base_topic: str = "turtlebot",
        client_id: str = "turtlebot3_publisher",
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """
        Initialize TurtleBot 3 MQTT Publisher

        Args:
            robot: TurtleBot3Adapter instance
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
        self._info_published = False
        self._last_published_payloads = {}
        self._last_published_at = {}
        self._max_silence_seconds = (
            10.0  # every topic is published at most every 10 seconds except scan
        )
        self._scan_max_silence_seconds = (
            0.0  # with 0.0 scan is published only when changed
        )
        self._change_thresholds = {
            "pose": 0.02,
            "velocity": 0.02,
            "imu": 0.05,
            "battery": 0.01,
            "scan": 0.1,
            "joints": 0.02,
        }

    def _sector_average(self, values: Any) -> Optional[float]:
        if not isinstance(values, list):
            return None
        valid_values = [
            float(value)
            for value in values
            if isinstance(value, (int, float))
            and math.isfinite(float(value))
            and value > 0
        ]
        if not valid_values:
            return None
        return sum(valid_values) / len(valid_values)

    def _scan_sector_averages(self, ranges_by_sector: Any) -> dict:
        if not isinstance(ranges_by_sector, dict):
            return {}
        return {
            sector: average
            for sector in ("front", "left", "right", "behind")
            if (average := self._sector_average(ranges_by_sector.get(sector)))
            is not None
        }

    def _compact_scan(self, scan_info):
        ranges = scan_info.get("ranges") or {}
        return {
            "ranges": ranges,
            "sector_averages": self._scan_sector_averages(ranges),
            "sector_n_points": scan_info.get("sector_n_points") or {},
            "range_min": scan_info.get("range_min"),
            "angle_min": scan_info.get("angle_min"),
            "angle_max": scan_info.get("angle_max"),
        }

    def _scan_average_delta(self, current: dict, previous: dict) -> float:
        current_averages = current.get("sector_averages") or self._scan_sector_averages(
            current.get("ranges") or {}
        )
        previous_averages = previous.get(
            "sector_averages"
        ) or self._scan_sector_averages(previous.get("ranges") or {})
        sectors = set(current_averages.keys()) | set(previous_averages.keys())
        if not sectors:
            return 0.0

        max_delta = 0.0
        for sector in sectors:
            current_average = current_averages.get(sector)
            previous_average = previous_averages.get(sector)
            if current_average is None or previous_average is None:
                return float("inf")
            max_delta = max(max_delta, abs(current_average - previous_average))
        return max_delta

    def _max_numeric_delta(self, current: Any, previous: Any) -> float:
        if isinstance(current, (int, float)) and isinstance(previous, (int, float)):
            if not math.isfinite(float(current)) or not math.isfinite(float(previous)):
                return 0.0 if current == previous else float("inf")
            return abs(float(current) - float(previous))

        if isinstance(current, dict) and isinstance(previous, dict):
            keys = set(current.keys()) | set(previous.keys())
            if not keys:
                return 0.0
            return max(
                self._max_numeric_delta(current.get(key), previous.get(key))
                for key in keys
            )

        if isinstance(current, list) and isinstance(previous, list):
            if len(current) != len(previous):
                return float("inf")
            if not current:
                return 0.0
            return max(
                self._max_numeric_delta(current_value, previous_value)
                for current_value, previous_value in zip(current, previous)
            )

        return 0.0 if current == previous else float("inf")

    def _should_publish(self, topic: str, payload: dict) -> bool:
        previous_payload = self._last_published_payloads.get(topic)
        if previous_payload is None:
            return True

        now = time.time()
        last_published_at = self._last_published_at.get(topic, 0.0)
        threshold = self._change_thresholds.get(topic, 0.0)

        if topic == "scan":
            if (
                self._scan_max_silence_seconds > 0
                and now - last_published_at >= self._scan_max_silence_seconds
            ):
                return True
            return self._scan_average_delta(payload, previous_payload) >= threshold

        if now - last_published_at >= self._max_silence_seconds:
            return True

        return self._max_numeric_delta(payload, previous_payload) >= threshold

    def _publish_if_changed(self, topic: str, payload: dict):
        if not self._should_publish(topic, payload):
            return
        now = time.time()
        if self._publish_topic(topic, {**payload, "timestamp": now}):
            self._last_published_payloads[topic] = payload
            self._last_published_at[topic] = now

    def _publish_state(self):
        """Publish TurtleBot state to MQTT topics"""
        if not self.connected or self.robot is None:
            return

        try:
            self._publish_if_changed("pose", self.robot.get_pose())
            self._publish_if_changed("velocity", self.robot.get_velocity())
            self._publish_if_changed("imu", self.robot.get_imu())
            self._publish_if_changed("battery", self.robot.get_battery())
            self._publish_if_changed("scan", self._compact_scan(self.robot.get_scan()))
            self._publish_if_changed("joints", self.robot.get_joints())

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
    parser = argparse.ArgumentParser(description="TurtleBot 3 MQTT Publisher")
    parser.add_argument("--broker", default="localhost", help="MQTT broker hostname")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--topic", default="turtlebot", help="Base MQTT topic")
    parser.add_argument(
        "--interval", type=float, default=0.5, help="Publishing interval in seconds"
    )
    parser.add_argument(
        "--client-id", default="turtlebot3_publisher", help="MQTT client ID"
    )
    parser.add_argument("--username", default=None, help="MQTT broker username")
    parser.add_argument("--password", default=None, help="MQTT broker password")

    args = parser.parse_args()

    rclpy.init()

    mqtt_pub = None
    robot = None
    try:
        robot = TurtleBot3Adapter(model="burger")
        robot.start()

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
        time.sleep(2)
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
        if robot is not None:
            robot.stop()


if __name__ == "__main__":
    main()
