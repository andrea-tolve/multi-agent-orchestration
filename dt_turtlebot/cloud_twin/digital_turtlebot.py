"""
Digital TurtleBot 3 Cloud Twin with Automatic Discovery

Uses MQTT-based discovery to automatically register state variables and methods.
The physical robot publishes its capabilities, and the cloud twin discovers and registers them.
"""

import sys
import time
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dt_turtlebot.cloud_twin.mqtt_cloud_publisher import MQTTPublisher
from dt_turtlebot.cloud_twin.mqtt_cloud_subscriber import MQTTSubscriber
from shared.digital_robot import DigitalRobot
from shared.digital_robot_state import DigitalRobotState


class DigitalTurtlebot(DigitalRobot):
    """
    Digital Twin for TurtleBot 3 using automatic MQTT-based discovery.
    """

    # MQTT topics for discovery
    INFO_VARIABLES_TOPIC = "turtlebot/info/variables"
    INFO_METHODS_TOPIC = "turtlebot/info/methods"

    def __init__(self, robot_id: str = "turtlebot3"):
        """
        Initialize DigitalTurtlebot with discovery-based state management.

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

    def get_pose(self):
        """Get current pose (x, y, theta)"""
        return self.state.get_variable("pose") or {}

    def get_velocity(self):
        """Get current velocity (linear_x, linear_y, angular_z)"""
        return self.state.get_variable("velocity") or {}

    def get_imu(self):
        """Get IMU data (acceleration, gyro, orientation)"""
        return self.state.get_variable("imu") or {}

    def get_battery(self):
        """Get battery data (voltage, percentage)"""
        return self.state.get_variable("battery") or {}

    def get_scan(self):
        """Get LiDAR scan data"""
        return self.state.get_variable("scan") or {}

    def get_joints(self):
        """Get joint states"""
        return self.state.get_variable("joints") or {}

    def get_state(self) -> dict:
        return super().get_state()

    def get_capabilities(self) -> dict:
        return super().get_capabilities()

    def move(
        self,
        linear_x: float = 0.0,
        linear_y: float = 0.0,
        angular_z: float = 0.0,
    ) -> bool:
        """
        Send velocity command

        Args:
            linear_x: Linear velocity in X direction (m/s)
            linear_y: Linear velocity in Y direction (m/s)
            angular_z: Angular velocity around Z axis (rad/s)
        """

        if self.state and "move" in self.state.methods_metadata:
            return self.send_command(
                "move",
                linear_x=linear_x,
                linear_y=linear_y,
                angular_z=angular_z,
            )

        return False

    def forward(self, velocity: float = 0.1) -> bool:
        """
        Move forward

        Args:
            velocity: Forward velocity (m/s)
        """

        if self.state and "forward" in self.state.methods_metadata:
            return self.send_command("forward", velocity=velocity)

        return False

    def backward(self, velocity: float = 0.1) -> bool:
        """
        Move backward

        Args:
            velocity: Backward velocity (m/s)
        """

        if self.state and "backward" in self.state.methods_metadata:
            return self.send_command("backward", velocity=velocity)

        return False

    def turn_left(self, angular_velocity: float = 0.5) -> bool:
        """
        Turn left

        Args:
            angular_velocity: Angular velocity (rad/s)
        """

        if self.state and "turn_left" in self.state.methods_metadata:
            return self.send_command("turn_left", angular_velocity=angular_velocity)

        return False

    def turn_right(self, angular_velocity: float = 0.5) -> bool:
        """
        Turn right

        Args:
            angular_velocity: Angular velocity (rad/s)
        """

        if self.state and "turn_right" in self.state.methods_metadata:
            return self.send_command("turn_right", angular_velocity=angular_velocity)

        return False

    def rotate(self, angular_velocity: float = 0.5) -> bool:
        """
        Rotate in place

        Args:
            angular_velocity: Angular velocity (rad/s)
        """

        if self.state and "rotate" in self.state.methods_metadata:
            return self.send_command("rotate", angular_velocity=angular_velocity)

        return False

    def stop_moving(self) -> bool:
        """Stop moving"""

        if self.state and "stop_moving" in self.state.methods_metadata:
            return self.send_command("stop_moving")

        return False
