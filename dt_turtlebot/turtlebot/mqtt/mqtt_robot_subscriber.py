"""
MQTT Subscriber for TurtleBot 3

Subscribes to MQTT command topics and executes corresponding robot commands.
Executes commands directly on TurtleBot3Adapter.

Inherits common MQTT functionality from BaseMQTTSubscriber.
Implements TurtleBot-specific command handlers.
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import rclpy

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from dt_turtlebot.turtlebot.robot import TurtleBot3Adapter
from shared.mqtt.command_mqtt_subscriber import CommandMQTTSubscriber


class MQTTSubscriber(CommandMQTTSubscriber):
    """
    MQTT Subscriber that receives commands and executes them on TurtleBot 3
    """

    def __init__(
        self,
        robot: TurtleBot3Adapter,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        base_topic: str = "turtlebot",
        client_id: str = "turtlebot_subscriber",
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """
        Initialize TurtleBot 3 MQTT Subscriber

        Args:
            robot: TurtleBot3Adapter instance to control
            broker_host: MQTT broker hostname
            broker_port: MQTT broker port
            base_topic: Base topic to subscribe to
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

        # Type guard: ensure robot is not None (TurtleBot3Adapter is required for robot subscriber)
        assert self.robot is not None, "robot must not be None for robot subscriber"

    def _register_default_callbacks(self):
        """Register default TurtleBot command callbacks"""
        self.callbacks = {
            "move": self._cmd_move,
            "forward": self._cmd_forward,
            "backward": self._cmd_backward,
            "turn_left": self._cmd_turn_left,
            "turn_right": self._cmd_turn_right,
            "rotate": self._cmd_rotate,
            "stop": self._cmd_stop,
        }

    # TurtleBot-specific command handlers
    def _cmd_move(self, data: Dict[str, Any]):
        """Move robot with velocity command"""
        linear_x = data.get("linear_x", 0.0)
        linear_y = data.get("linear_y", 0.0)
        angular_z = data.get("angular_z", 0.0)
        self.robot.move(linear_x, linear_y, angular_z)

    def _cmd_forward(self, data: Dict[str, Any]):
        """Move forward"""
        velocity = data.get("velocity", 0.1)
        self.robot.forward(velocity)

    def _cmd_backward(self, data: Dict[str, Any]):
        """Move backward"""
        velocity = data.get("velocity", 0.1)
        self.robot.backward(velocity)

    def _cmd_turn_left(self, data: Dict[str, Any]):
        """Turn left"""
        angular_velocity = data.get("angular_velocity", 0.5)
        self.robot.turn_left(angular_velocity)

    def _cmd_turn_right(self, data: Dict[str, Any]):
        """Turn right"""
        angular_velocity = data.get("angular_velocity", 0.5)
        self.robot.turn_right(angular_velocity)

    def _cmd_rotate(self, data: Dict[str, Any]):
        """Rotate in place"""
        angular_velocity = data.get("angular_velocity", 0.5)
        self.robot.rotate(angular_velocity)

    def _cmd_stop(self, data: Dict[str, Any]):
        """Stop movement"""
        self.robot.stop_moving()


def main():
    parser = argparse.ArgumentParser(description="TurtleBot 3 MQTT Subscriber")
    parser.add_argument("--broker", default="localhost", help="MQTT broker hostname")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--topic", default="turtlebot", help="Base MQTT topic")
    parser.add_argument(
        "--client-id", default="turtlebot_subscriber", help="MQTT client ID"
    )
    parser.add_argument("--username", default=None, help="MQTT broker username")
    parser.add_argument("--password", default=None, help="MQTT broker password")

    args = parser.parse_args()

    rclpy.init()

    mqtt_sub = None
    robot = None
    try:
        robot = TurtleBot3Adapter(model="burger")
        robot.start()

        mqtt_sub = MQTTSubscriber(
            robot,
            broker_host=args.broker,
            broker_port=args.port,
            base_topic=args.topic,
            client_id=args.client_id,
            username=args.username,
            password=args.password,
        )

        mqtt_sub.connect()
        time.sleep(1)
        mqtt_sub.start_monitoring()

        print(
            f"[INFO] Listening for commands on {args.broker}:{args.port}/{args.topic}/*"
        )
        print("[INFO] Press Ctrl+C to stop")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user")
    except Exception as e:
        print(f"[ERROR] Error: {e}")
    finally:
        if mqtt_sub is not None:
            mqtt_sub.stop_monitoring()
            mqtt_sub.disconnect()
        if robot is not None:
            robot.stop()


if __name__ == "__main__":
    main()
