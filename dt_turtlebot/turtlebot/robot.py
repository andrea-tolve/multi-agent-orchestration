import json
import math
import threading
import time
from typing import Any, Dict

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import BatteryState, Imu, JointState, LaserScan

from dt_turtlebot.turtlebot.constants import ROBOT, ROS2_TOPICS


class TurtleBot3Adapter(Node):
    """
    Manages:
    - Robot state (pose, velocity, IMU, battery, sensors)
    - ROS2 subscriptions (odometry, IMU, lidar, battery, joints)
    - ROS2 publisher (velocity commands)
    - Command interface (move, forward, backward, turn, rotate, stop)
    """

    def __init__(self, model: str = "burger", node_name: str = "turtlebot3_adapter"):
        super().__init__(node_name)

        # Model and configuration
        self.model = model.upper()
        self.config = getattr(ROBOT, self.model)
        self.exec = None
        self.executor_thread = None

        # Pose
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0

        # Velocity
        self.linear_vel_x = 0.0
        self.linear_vel_y = 0.0
        self.angular_vel_z = 0.0

        # IMU
        self.accel_x = 0.0
        self.accel_y = 0.0
        self.accel_z = 0.0
        self.gyro_x = 0.0
        self.gyro_y = 0.0
        self.gyro_z = 0.0
        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0

        # Lidar
        self.scan_data = []
        self.scan_intensities = []
        self.scan_angle_min = 0.0
        self.scan_angle_max = 0.0
        self.scan_angle_increment = 0.0
        self.scan_time_increment = 0.0
        self.scan_scan_time = 0.0
        self.scan_range_min = 0.0
        self.scan_range_max = 0.0

        # Battery
        self.battery_voltage = 0.0
        self.battery_percentage = 0.0

        # Joints
        self.joint_names = []
        self.joint_positions = []
        self.joint_velocities = []
        self.joint_efforts = []

        # Timestamps
        self.last_update = time.time()

        # Running flag
        self.running = False

        self.odom_sub = self.create_subscription(
            Odometry, ROS2_TOPICS.ODOM, self._odom_callback, 10
        )

        self.imu_sub = self.create_subscription(
            Imu, ROS2_TOPICS.IMU, self._imu_callback, qos_profile_sensor_data
        )

        self.scan_sub = self.create_subscription(
            LaserScan, ROS2_TOPICS.SCAN, self._scan_callback, qos_profile_sensor_data
        )

        self.battery_sub = self.create_subscription(
            BatteryState, ROS2_TOPICS.BATTERY, self._battery_callback, 10
        )

        self.joint_states_sub = self.create_subscription(
            JointState, ROS2_TOPICS.JOINT_STATES, self._joint_states_callback, 10
        )

        self.cmd_vel_pub = self.create_publisher(Twist, ROS2_TOPICS.CMD_VEL, 10)

    def start(self):
        """Start the adapter and ROS2 executor"""
        if self.running:
            return
        self.running = True
        self.exec = MultiThreadedExecutor(num_threads=2)
        self.exec.add_node(self)
        self.executor_thread = threading.Thread(target=self.exec.spin, daemon=True)
        self.executor_thread.start()
        self.get_logger().info("TurtleBot3Adapter started")

    def stop(self):
        """Stop the adapter and ROS2 executor"""
        if not self.running:
            return

        self.running = False
        self.move(0.0, 0.0, 0.0)  # Stop robot

        if self.exec:
            self.exec.shutdown()

        if self.executor_thread:
            self.executor_thread.join(timeout=5.0)

    def move(
        self,
        linear_vel_x: float = 0.0,
        linear_vel_y: float = 0.0,
        angular_vel_z: float = 0.0,
    ):
        """
        Send velocity command to robot

        Args:
            linear_vel_x: Linear velocity in X direction (m/s)
            linear_vel_y: Linear velocity in Y direction (m/s)
            angular_vel_z: Angular velocity around Z axis (rad/s)
        """
        if not self.running:
            return

        # Clamp velocities to limits
        max_linear = self.config.MAX_LINEAR_VEL
        max_angular = self.config.MAX_ANGULAR_VEL

        linear_vel_x = max(-max_linear, min(max_linear, linear_vel_x))
        linear_vel_y = max(-max_linear, min(max_linear, linear_vel_y))
        angular_vel_z = max(-max_angular, min(max_angular, angular_vel_z))

        # Create and publish Twist message
        twist = Twist()
        twist.linear.x = float(linear_vel_x)
        twist.linear.y = float(linear_vel_y)
        twist.linear.z = 0.0
        twist.angular.x = 0.0
        twist.angular.y = 0.0
        twist.angular.z = float(angular_vel_z)

        self.cmd_vel_pub.publish(twist)
        self.get_logger().debug(
            f"Velocity command: linear_x={linear_vel_x}, angular_z={angular_vel_z}"
        )

    def forward(self, velocity: float = 0.1):
        """Move forward"""
        self.move(linear_vel_x=velocity)

    def backward(self, velocity: float = 0.1):
        """Move backward"""
        self.move(linear_vel_x=-velocity)

    def turn_left(self, angular_velocity: float = 0.5):
        """Turn left"""
        self.move(angular_vel_z=angular_velocity)

    def turn_right(self, angular_velocity: float = 0.5):
        """Turn right"""
        self.move(angular_vel_z=-angular_velocity)

    def rotate(self, angular_velocity: float = 0.5):
        """Rotate in place"""
        self.move(angular_vel_z=angular_velocity)

    def stop_moving(self):
        """Stop robot"""
        self.move(0.0, 0.0, 0.0)
        self.get_logger().info("Robot stopped")

    def get_state(self) -> Dict[str, Any]:
        """Get complete robot state"""
        return {
            "model": self.model,
            "pose": {
                "x": self.x,
                "y": self.y,
                "theta": self.theta,
            },
            "velocity": {
                "linear_x": self.linear_vel_x,
                "linear_y": self.linear_vel_y,
                "angular_z": self.angular_vel_z,
            },
            "imu": {
                "acceleration": {
                    "x": self.accel_x,
                    "y": self.accel_y,
                    "z": self.accel_z,
                },
                "angular_velocity": {
                    "x": self.gyro_x,
                    "y": self.gyro_y,
                    "z": self.gyro_z,
                },
                "orientation": {
                    "roll": self.roll,
                    "pitch": self.pitch,
                    "yaw": self.yaw,
                },
            },
            "scan": self.get_scan(),
            "battery": {
                "voltage": self.battery_voltage,
                "percentage": self.battery_percentage,
            },
            "joints": {
                "names": self.joint_names,
                "positions": self.joint_positions,
                "velocities": self.joint_velocities,
                "efforts": self.joint_efforts,
            },
            "last_update": self.last_update,
        }

    def get_state_json(self) -> str:
        """Get state as JSON string"""
        return json.dumps(self.get_state(), default=str, indent=2)

    def get_pose(self) -> Dict[str, float]:
        """Get robot pose"""
        return {
            "x": self.x,
            "y": self.y,
            "theta": self.theta,
        }

    def get_velocity(self) -> Dict[str, float]:
        """Get robot velocity"""
        return {
            "linear_x": self.linear_vel_x,
            "linear_y": self.linear_vel_y,
            "angular_z": self.angular_vel_z,
        }

    def get_imu(self) -> Dict[str, Any]:
        """Get IMU data"""
        return {
            "acceleration": {
                "x": self.accel_x,
                "y": self.accel_y,
                "z": self.accel_z,
            },
            "angular_velocity": {
                "x": self.gyro_x,
                "y": self.gyro_y,
                "z": self.gyro_z,
            },
            "orientation": {
                "roll": self.roll,
                "pitch": self.pitch,
                "yaw": self.yaw,
            },
        }

    def get_battery(self) -> Dict[str, float]:
        """Get battery state"""
        return {
            "voltage": self.battery_voltage,
            "percentage": self.battery_percentage,
        }

    def get_scan(self) -> Dict[str, Any]:
        """
        Get lidar scan data grouped by robot-relative direction.

        The returned scan payload no longer exposes `ranges` as a flat list.
        Instead, `ranges` is a dictionary of four sector lists:
        - `ranges["front"]`: measurements from -45 to 45
        - `ranges["left"]`: measurements from 45 to 135
        - `ranges["right"]`: measurements from -135 to -45
        - `ranges["behind"]`: measurements from 135 to 180 and -180 to -135

        Example access:
            scan = robot.get_scan()
            front_ranges = scan["ranges"]["front"]
            closest_front_obstacle = min(front_ranges) if front_ranges else None
        """
        grouped_ranges = self._group_scan_ranges_by_direction()
        return {
            "ranges": grouped_ranges,
            "n_points": sum(len(values) for values in grouped_ranges.values()),
            "sector_n_points": {
                direction: len(values) for direction, values in grouped_ranges.items()
            },
            "intensities": self.scan_intensities,
            "angle_min": self.scan_angle_min,
            "angle_max": self.scan_angle_max,
            "angle_increment": self.scan_angle_increment,
            "range_min": self.scan_range_min,
            "range_max": self.scan_range_max,
            "sectors": {
                "front": "-45 to 45",
                "left": "45 to 135",
                "right": "-135 to -45",
                "behind": "135 to 180 and -180 to -135",
            },
        }

    def get_joints(self) -> Dict[str, Any]:
        """Get joint states"""
        return {
            "names": self.joint_names,
            "positions": self.joint_positions,
            "velocities": self.joint_velocities,
            "efforts": self.joint_efforts,
        }

    def get_variables(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all state variables that this robot publishes.

        Returns a dictionary describing all available variables with their
        MQTT topics and metadata. Used for discovery by the cloud twin.

        Returns:
            Dict mapping variable name to metadata including mqtt_topic
        """
        return {
            "pose": {
                "name": "pose",
                "mqtt_topic": "turtlebot/pose",
                "data_type": "dict",
                "description": "Robot position and orientation (x, y, theta)",
            },
            "velocity": {
                "name": "velocity",
                "mqtt_topic": "turtlebot/velocity",
                "data_type": "dict",
                "description": "Robot linear and angular velocities",
            },
            "imu": {
                "name": "imu",
                "mqtt_topic": "turtlebot/imu",
                "data_type": "dict",
                "description": "Inertial measurement unit data (acceleration, gyro, orientation)",
            },
            "battery": {
                "name": "battery",
                "mqtt_topic": "turtlebot/battery",
                "data_type": "dict",
                "description": "Battery voltage and percentage",
            },
            "scan": {
                "name": "scan",
                "mqtt_topic": "turtlebot/scan",
                "data_type": "dict",
                "description": (
                    "LiDAR scan payload. `ranges` is grouped by robot-relative "
                    "direction instead of being a flat array. Access sector ranges "
                    "with `scan['ranges']['front']`, `scan['ranges']['left']`, "
                    "`scan['ranges']['right']`, or `scan['ranges']['behind']`."
                ),
                "schema": {
                    "ranges": {
                        "front": "list[float] for -45 to 45",
                        "left": "list[float] for 45 to 135",
                        "right": "list[float] for -135 to -45",
                        "behind": "list[float] for 135 to 180 and -180 to -135",
                    },
                    "n_points": "Total number of valid range measurements across all sectors",
                    "sector_n_points": "Number of valid range measurements per sector",
                    "angle_min": "Original LaserScan minimum angle in radians",
                    "angle_max": "Original LaserScan maximum angle in radians",
                    "angle_increment": "Original LaserScan angular increment in radians",
                    "range_min": "Minimum valid range in meters",
                    "range_max": "Maximum valid range in meters",
                },
                "is_array": False,
            },
            "joints": {
                "name": "joints",
                "mqtt_topic": "turtlebot/joints",
                "data_type": "dict",
                "description": "Joint positions, velocities, and efforts",
            },
        }

    def get_methods(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all methods/commands that this robot supports.

        Returns a dictionary describing all available methods with their
        MQTT command topics and parameters. Used for discovery by the cloud twin.

        Returns:
            Dict mapping method name to metadata including mqtt_command_topic and parameters
        """
        return {
            "move": {
                "name": "move",
                "mqtt_command_topic": "turtlebot/move",
                "description": "Send velocity command to robot",
                "parameters": {
                    "linear_x": {
                        "type": "float",
                        "required": True,
                        "default": 0.0,
                        "description": "Linear velocity in X direction (m/s)",
                        "constraints": {
                            "minimum": -self.config.MAX_LINEAR_VEL,
                            "maximum": self.config.MAX_LINEAR_VEL,
                            "unit": "m/s",
                            "behavior": "Values outside this range are clamped by the robot adapter, but generated services should stay within range.",
                        },
                    },
                    "linear_y": {
                        "type": "float",
                        "required": True,
                        "default": 0.0,
                        "description": "Linear velocity in Y direction (m/s)",
                        "constraints": {
                            "minimum": -self.config.MAX_LINEAR_VEL,
                            "maximum": self.config.MAX_LINEAR_VEL,
                            "unit": "m/s",
                            "behavior": "Values outside this range are clamped by the robot adapter, but generated services should stay within range.",
                        },
                    },
                    "angular_z": {
                        "type": "float",
                        "required": True,
                        "default": 0.0,
                        "description": "Angular velocity around Z axis (rad/s)",
                        "constraints": {
                            "minimum": -self.config.MAX_ANGULAR_VEL,
                            "maximum": self.config.MAX_ANGULAR_VEL,
                            "unit": "rad/s",
                            "behavior": "Values outside this range are clamped by the robot adapter, but generated services should stay within range.",
                        },
                    },
                },
            },
            "forward": {
                "name": "forward",
                "mqtt_command_topic": "turtlebot/forward",
                "description": "Move forward",
                "parameters": {
                    "velocity": {
                        "type": "float",
                        "required": False,
                        "default": 0.1,
                        "description": "Forward velocity (m/s)",
                        "constraints": {
                            "minimum": 0.0,
                            "maximum": self.config.MAX_LINEAR_VEL,
                            "unit": "m/s",
                        },
                    },
                },
            },
            "backward": {
                "name": "backward",
                "mqtt_command_topic": "turtlebot/backward",
                "description": "Move backward",
                "parameters": {
                    "velocity": {
                        "type": "float",
                        "required": False,
                        "default": 0.1,
                        "description": "Backward velocity (m/s)",
                        "constraints": {
                            "minimum": 0.0,
                            "maximum": self.config.MAX_LINEAR_VEL,
                            "unit": "m/s",
                            "note": "Use a positive value; the adapter applies the backward direction internally.",
                        },
                    },
                },
            },
            "turn_left": {
                "name": "turn_left",
                "mqtt_command_topic": "turtlebot/turn_left",
                "description": "Turn left",
                "parameters": {
                    "angular_velocity": {
                        "type": "float",
                        "required": False,
                        "default": 0.5,
                        "description": "Angular velocity (rad/s)",
                        "constraints": {
                            "minimum": 0.0,
                            "maximum": self.config.MAX_ANGULAR_VEL,
                            "unit": "rad/s",
                            "note": "Use a positive value; the adapter applies the left-turn direction internally.",
                        },
                    },
                },
            },
            "turn_right": {
                "name": "turn_right",
                "mqtt_command_topic": "turtlebot/turn_right",
                "description": "Turn right",
                "parameters": {
                    "angular_velocity": {
                        "type": "float",
                        "required": False,
                        "default": 0.5,
                        "description": "Angular velocity (rad/s)",
                        "constraints": {
                            "minimum": 0.0,
                            "maximum": self.config.MAX_ANGULAR_VEL,
                            "unit": "rad/s",
                            "note": "Use a positive value; the adapter applies the right-turn direction internally.",
                        },
                    },
                },
            },
            "rotate": {
                "name": "rotate",
                "mqtt_command_topic": "turtlebot/rotate",
                "description": "Rotate in place",
                "parameters": {
                    "angular_velocity": {
                        "type": "float",
                        "required": False,
                        "default": 0.5,
                        "description": "Angular velocity (rad/s)",
                        "constraints": {
                            "minimum": -self.config.MAX_ANGULAR_VEL,
                            "maximum": self.config.MAX_ANGULAR_VEL,
                            "unit": "rad/s",
                        },
                    },
                },
            },
            "stop_moving": {
                "name": "stop_moving",
                "mqtt_command_topic": "turtlebot/stop",
                "description": "Stop all movement",
                "parameters": {},
            },
        }

    def _odom_callback(self, msg: Odometry):
        """Handle odometry updates"""
        self.x = msg.pose.pose.position.x
        self.y = msg.pose.pose.position.y

        # Extract yaw from quaternion
        orientation = msg.pose.pose.orientation
        self.theta = self._quaternion_to_yaw(
            orientation.x, orientation.y, orientation.z, orientation.w
        )

        self.linear_vel_x = msg.twist.twist.linear.x
        self.linear_vel_y = msg.twist.twist.linear.y
        self.angular_vel_z = msg.twist.twist.angular.z

        self.last_update = time.time()

    def _imu_callback(self, msg: Imu):
        """Handle IMU updates"""
        self.accel_x = msg.linear_acceleration.x
        self.accel_y = msg.linear_acceleration.y
        self.accel_z = msg.linear_acceleration.z

        self.gyro_x = msg.angular_velocity.x
        self.gyro_y = msg.angular_velocity.y
        self.gyro_z = msg.angular_velocity.z

        # Extract Euler angles from quaternion
        orientation = msg.orientation
        self.roll, self.pitch, self.yaw = self._quaternion_to_euler(
            orientation.x, orientation.y, orientation.z, orientation.w
        )

        self.last_update = time.time()

    def _group_scan_ranges_by_direction(self) -> Dict[str, list]:
        """Group valid lidar ranges into front/left/right/behind sectors."""
        grouped_ranges = {
            "front": [],
            "left": [],
            "right": [],
            "behind": [],
        }

        for index, scan_range in enumerate(self.scan_data):
            if not math.isfinite(scan_range) or scan_range == 0.0:
                continue
            if self.scan_range_min and scan_range < self.scan_range_min:
                continue
            if self.scan_range_max and scan_range > self.scan_range_max:
                continue

            angle = self.scan_angle_min + index * self.scan_angle_increment
            normalized_angle = math.atan2(math.sin(angle), math.cos(angle))
            angle_degrees = math.degrees(normalized_angle)

            if -45.0 <= angle_degrees <= 45.0:
                grouped_ranges["front"].append(scan_range)
            elif 45.0 < angle_degrees <= 135.0:
                grouped_ranges["left"].append(scan_range)
            elif -135.0 <= angle_degrees < -45.0:
                grouped_ranges["right"].append(scan_range)
            else:
                grouped_ranges["behind"].append(scan_range)

        return grouped_ranges

    def _scan_callback(self, msg: LaserScan):
        """Handle lidar scan updates"""
        self.scan_data = list(msg.ranges)
        self.scan_intensities = (
            list(msg.intensities) if hasattr(msg, "intensities") else []
        )
        self.scan_angle_min = msg.angle_min
        self.scan_angle_max = msg.angle_max
        self.scan_angle_increment = msg.angle_increment
        self.scan_time_increment = msg.time_increment
        self.scan_scan_time = msg.scan_time
        self.scan_range_min = msg.range_min
        self.scan_range_max = msg.range_max

        self.last_update = time.time()

    def _battery_callback(self, msg: BatteryState):
        """Handle battery updates"""
        self.battery_voltage = msg.voltage
        self.battery_percentage = msg.percentage

        self.last_update = time.time()

    def _joint_states_callback(self, msg: JointState):
        """Handle joint states updates"""
        self.joint_names = list(msg.name)
        self.joint_positions = list(msg.position)
        self.joint_velocities = list(msg.velocity)
        self.joint_efforts = list(msg.effort)

        self.last_update = time.time()

    @staticmethod
    def _quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
        """Convert quaternion to yaw angle"""
        sin_yaw = 2.0 * (w * z + x * y)
        cos_yaw = 1.0 - 2.0 * (y * y + z * z)
        return math.atan2(sin_yaw, cos_yaw)

    @staticmethod
    def _quaternion_to_euler(x: float, y: float, z: float, w: float) -> tuple:
        """Convert quaternion to Euler angles (roll, pitch, yaw)"""
        # Roll
        sin_roll = 2.0 * (w * x + y * z)
        cos_roll = 1.0 - 2.0 * (x * x + y * y)
        roll = math.atan2(sin_roll, cos_roll)

        # Pitch
        sin_pitch = 2.0 * (w * y - z * x)
        sin_pitch = max(-1.0, min(1.0, sin_pitch))
        pitch = math.asin(sin_pitch)

        # Yaw
        sin_yaw = 2.0 * (w * z + x * y)
        cos_yaw = 1.0 - 2.0 * (y * y + z * z)
        yaw = math.atan2(sin_yaw, cos_yaw)

        return roll, pitch, yaw
