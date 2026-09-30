"""
Digital Roomba Cloud Twin with Automatic Discovery

Uses MQTT-based discovery to automatically register state variables and methods.
The physical robot publishes its capabilities, and the cloud twin discovers and registers them.
"""

import sys
import time
from pathlib import Path
from typing import Dict, Optional

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dt_roomba.cloud_twin.mqtt_cloud_publisher import MQTTPublisher
from dt_roomba.cloud_twin.mqtt_cloud_subscriber import MQTTSubscriber
from shared.digital_robot import DigitalRobot
from shared.digital_robot_state import DigitalRobotState


class DigitalRoomba(DigitalRobot):
    """
    Digital Twin for Roomba using automatic MQTT-based discovery.
    """

    # MQTT topics for discovery
    INFO_VARIABLES_TOPIC = "roomba/info/variables"
    INFO_METHODS_TOPIC = "roomba/info/methods"

    def __init__(self, robot_id: str = "roomba"):
        """
        Initialize DigitalRoomba with discovery-based state management.

        Args:
            robot_id: Unique identifier for this robot instance
        """
        self.state = DigitalRobotState(robot_id=robot_id)
        super().__init__(self.state, self.INFO_VARIABLES_TOPIC, self.INFO_METHODS_TOPIC)

        self._discovery_complete = False
        self._variables_discovered = False
        self._methods_discovered = False

    def connect(
        self,
        username: str,
        password: str,
        broker: str = "localhost",
        port: int = 1883,
        auto_discover: bool = True,
        discover_timeout: float = 5.0,
    ):
        """
        Connect to MQTT broker and optionally perform automatic discovery.

        Args:
            username: MQTT broker username
            password: MQTT broker password
            broker: MQTT broker hostname (default: localhost)
            port: MQTT broker port (default: 1883)
            auto_discover: If True, wait for discovery info before connecting (default: True)
            discover_timeout: Maximum time to wait for discovery info in seconds
        """
        # Create publisher for commands (with state for dynamic command support)
        self._cmd_pub = MQTTPublisher(
            username=username,
            password=password,
            broker_host=broker,
            broker_port=port,
            base_topic="roomba",
            state=self.state,
        )

        # Create subscriber for state updates
        self._state_sub = MQTTSubscriber(
            username=username,
            password=password,
            state=self.state,
            broker_host=broker,
            broker_port=port,
        )

        # Register callbacks for discovery
        self._state_sub.register_callback("state_change", self._on_state_change)

        # Connect both
        self._state_sub.connect()
        time.sleep(2)
        self._cmd_pub.connect()
        time.sleep(2)

        # Perform automatic discovery if requested
        if auto_discover:
            self._perform_discovery(timeout=discover_timeout)
        else:
            # Start monitoring without discovery
            self._state_sub.start_monitoring()

    def get_battery(self):
        """Get battery data (voltage, current, temperature, charge, capacity)"""
        return self.state.get_variable("battery") or {}

    def get_sensors(self):
        """Get sensor data (distance, angle, encoder_left, encoder_right)"""
        return self.state.get_variable("sensors") or {}

    def get_mode(self):
        """Get robot mode data (oi_mode, charging_state)"""
        return self.state.get_variable("mode") or {}

    def get_state(self) -> dict:
        return super().get_state()

    def get_capabilities(self) -> dict:
        return super().get_capabilities()

    def start_cleaning(self) -> bool:
        """Start cleaning"""
        if self.state and "clean" in self.state.methods_metadata:
            return self.send_command("clean")
        return False

    def start_max_cleaning(self) -> bool:
        """Start max cleaning"""
        if self.state and "max_clean" in self.state.methods_metadata:
            return self.send_command("max_clean")
        return False

    def start_spot_cleaning(self) -> bool:
        """Start spot cleaning"""
        if self.state and "spot_clean" in self.state.methods_metadata:
            return self.send_command("spot_clean")
        return False

    def start_seek_dock(self) -> bool:
        """Start seeking dock"""
        if self.state and "dock" in self.state.methods_metadata:
            return self.send_command("dock")
        return False

    def change_mode(self, mode: str = "safe") -> bool:
        """
        Change robot mode

        Args:
            mode: "passive", "safe", or "full"
        """
        if self.state and "mode" in self.state.methods_metadata:
            return self.send_command("mode", mode=mode)
        return False

    def turn_off_power(self) -> bool:
        """Turn off power"""
        if self.state and "power_off" in self.state.methods_metadata:
            return self.send_command("power_off")
        return False

    def move(self, velocity: float = 0.0, angle: float = 0.0) -> bool:
        """
        Move robot

        Args:
            velocity: Velocity value
            angle: Angle value
        """
        if self.state and "move" in self.state.methods_metadata:
            return self.send_command("move", velocity=velocity, angle=angle)
        return False

    def stop_moving(self) -> bool:
        """Stop movement"""
        if self.state and "stop" in self.state.methods_metadata:
            return self.send_command("stop")
        return False

    def drive(self, velocity: int = 0, radius: int = 0) -> bool:
        """
        Send drive command

        Args:
            velocity: Linear velocity
            radius: Radius of curvature
        """
        if self.state and "drive" in self.state.methods_metadata:
            return self.send_command("drive", velocity=velocity, radius=radius)
        return False

    def drive_pwm(self, left_pwm: int = 0, right_pwm: int = 0) -> bool:
        """
        Send drive PWM command

        Args:
            left_pwm: Left motor PWM
            right_pwm: Right motor PWM
        """
        if self.state and "drive_pwm" in self.state.methods_metadata:
            return self.send_command(
                "drive_pwm", left_pwm=left_pwm, right_pwm=right_pwm
            )
        return False

    def drive_direct(self, left: int = 0, right: int = 0) -> bool:
        """
        Send drive direct command

        Args:
            left: Left motor velocity
            right: Right motor velocity
        """
        if self.state and "motors" in self.state.methods_metadata:
            return self.send_command("motors", left=left, right=right)
        return False

    def send_buttons(self, buttons: int = 0) -> bool:
        """
        Send buttons command

        Args:
            buttons: Button byte value
        """
        if self.state and "buttons" in self.state.methods_metadata:
            return self.send_command("buttons", buttons=buttons)
        return False

    def send_song(
        self,
        song_number: int = 0,
        note_number_list: Optional[list] = None,
        note_duration_list: Optional[list] = None,
    ) -> bool:
        """
        Send song command

        Args:
            song_number: Song index
            note_number_list: List of note numbers
            note_duration_list: List of note durations
        """
        if self.state and "song" in self.state.methods_metadata:
            if note_number_list is None:
                note_number_list = []
            if note_duration_list is None:
                note_duration_list = []
            return self.send_command(
                "song",
                song_number=song_number,
                note_number_list=note_number_list,
                note_duration_list=note_duration_list,
            )
        return False

    def play_song(self, song_number: int = 0) -> bool:
        """
        Play song

        Args:
            song_number: Song index to play
        """
        if self.state and "play_song" in self.state.methods_metadata:
            return self.send_command("play_song", song_number=song_number)
        return False
