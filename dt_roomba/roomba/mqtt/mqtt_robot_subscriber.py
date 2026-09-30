"""
MQTT Subscriber for Roomba

Subscribes to MQTT command topics and executes corresponding robot commands.
Executes commands directly on PyRoombaAdapter.

Inherits common MQTT functionality from BaseMQTTSubscriber.
Implements Roomba-specific command handlers.

Usage:
    python -m roomba.mqtt.mqtt_robot_subscriber --broker localhost --port 1883 --topic roomba
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from dt_roomba.roomba.robot import PyRoombaAdapter
from shared.mqtt.command_mqtt_subscriber import CommandMQTTSubscriber


class MQTTSubscriber(CommandMQTTSubscriber):
    """
    MQTT Subscriber that receives commands and executes them on Roomba
    """

    def __init__(
        self,
        robot: PyRoombaAdapter,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        base_topic: str = "roomba",
        client_id: str = "roomba_subscriber",
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """
        Initialize Roomba MQTT Subscriber

        Args:
            robot: PyRoombaAdapter instance to control
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

        # Type guard: ensure robot is not None (PyRoombaAdapter is required for robot subscriber)
        assert self.robot is not None, "robot must not be None for robot subscriber"

    def _register_default_callbacks(self):
        """Register default Roomba command callbacks"""
        self.callbacks = {
            "clean": self._cmd_clean,
            "max_clean": self._cmd_max_clean,
            "spot_clean": self._cmd_spot_clean,
            "dock": self._cmd_dock,
            "mode": self._cmd_mode,
            "power_off": self._cmd_power_off,
            "move": self._cmd_move,
            "stop": self._cmd_stop,
            "drive": self._cmd_drive,
            "drive_pwm": self._cmd_drive_pwm,
            "motors": self._cmd_motors,
            "buttons": self._cmd_buttons,
            "song": self._cmd_song,
            "play_song": self._cmd_play_song,
        }

    # Roomba-specific command handlers
    def _cmd_clean(self, data: Dict[str, Any]):
        """Start cleaning"""
        self.robot.start_cleaning()

    def _cmd_max_clean(self, data: Dict[str, Any]):
        """Start max cleaning"""
        self.robot.start_max_cleaning()

    def _cmd_spot_clean(self, data: Dict[str, Any]):
        """Start spot cleaning"""
        self.robot.start_spot_cleaning()

    def _cmd_dock(self, data: Dict[str, Any]):
        """Start seek dock"""
        self.robot.start_seek_dock()

    def _cmd_mode(self, data: Dict[str, Any]):
        """Change robot mode"""
        mode = data.get("mode", "safe")
        if mode == "passive":
            self.robot.change_mode_to_passive()
        elif mode == "safe":
            self.robot.change_mode_to_safe()
        elif mode == "full":
            self.robot.change_mode_to_full()
        else:
            print(f"[WARNING] Unknown mode: {mode}")

    def _cmd_power_off(self, data: Dict[str, Any]):
        """Turn off power"""
        self.robot.turn_off_power()

    def _cmd_move(self, data: Dict[str, Any]):
        """Move robot"""
        velocity = data.get("velocity", 0.0)
        angle = data.get("angle", 0.0)
        self.robot.move(velocity, angle)

    def _cmd_stop(self, data: Dict[str, Any]):
        """Stop movement"""
        self.robot.move(0, 0)

    def _cmd_drive(self, data: Dict[str, Any]):
        """Send drive command"""
        velocity = data.get("velocity", 0)
        radius = data.get("radius", 0)
        self.robot.send_drive_cmd(velocity, radius)

    def _cmd_drive_pwm(self, data: Dict[str, Any]):
        """Send drive PWM command"""
        left_pwm = data.get("left_pwm", 0)
        right_pwm = data.get("right_pwm", 0)
        self.robot.send_drive_pwm(left_pwm, right_pwm)

    def _cmd_motors(self, data: Dict[str, Any]):
        """Send motors command (drive direct)"""
        left = data.get("left", 0)
        right = data.get("right", 0)
        self.robot.send_drive_direct(left, right)

    def _cmd_buttons(self, data: Dict[str, Any]):
        """Send buttons command"""
        button_bytes = data.get("buttons", 0)
        self.robot.send_buttons_cmd(button_bytes)

    def _cmd_song(self, data: Dict[str, Any]):
        """Send song command"""
        song_number = data.get("song_number", 0)
        note_number_list = data.get("note_number_list", [])
        note_duration_list = data.get("note_duration_list", [])
        if note_number_list and note_duration_list:
            song_length = len(note_number_list)
            self.robot.send_song_cmd(
                song_number, song_length, note_number_list, note_duration_list
            )

    def _cmd_play_song(self, data: Dict[str, Any]):
        """Play song"""
        song_number = data.get("song_number", 0)
        self.robot.send_play_cmd(song_number)


def main():
    parser = argparse.ArgumentParser(description="Roomba MQTT Subscriber")
    parser.add_argument("--broker", default="localhost", help="MQTT broker hostname")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--topic", default="roomba", help="Base MQTT topic")
    parser.add_argument(
        "--client-id", default="roomba_subscriber", help="MQTT client ID"
    )
    parser.add_argument("--username", default=None, help="MQTT broker username")
    parser.add_argument("--password", default=None, help="MQTT broker password")
    parser.add_argument(
        "--port-serial", default="/dev/ttyUSB0", help="Serial port for Roomba"
    )

    args = parser.parse_args()

    mqtt_sub = None
    try:
        robot = PyRoombaAdapter(port=args.port_serial)

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
            f"[INFO] Listening for commands on {args.broker}:{args.port}/{args.topic}/commands/*"
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


if __name__ == "__main__":
    main()
