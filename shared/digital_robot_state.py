"""
Generic Digital Robot State Management

This module provides a flexible, dictionary-based state representation for digital robots.
It allows robots to discover and register variables and methods dynamically through MQTT topics.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class VariableMetadata:
    """Metadata for a state variable"""

    name: str
    mqtt_topic: str
    data_type: str = "any"  # Type hint: "float", "int", "str", "dict", "list", "any"
    description: str = ""
    last_value: Any = None
    last_update_timestamp: Optional[float] = None
    is_array: bool = False
    extra_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MethodMetadata:
    """Metadata for a callable method/command"""

    name: str
    mqtt_command_topic: str
    description: str = ""
    parameters: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # parameters format: {"param_name": {"type": "float", "required": True, "default": 0.0}}
    return_type: str = "void"
    extra_metadata: Dict[str, Any] = field(default_factory=dict)


class DigitalRobotState:
    """
    Generic Digital Robot State using dictionary-based representation.

    This class decouples the cloud twin from specific robot implementations by:
    - Using a flexible dictionary to store state variables
    - Supporting dynamic variable discovery
    - Supporting dynamic method discovery
    - Mapping state to MQTT topics for pub/sub

    This allows the same digital twin to work with TurtleBot, Roomba, or any other robot
    once the variable and method MQTT topics are discovered or configured.
    """

    def __init__(self, robot_id: str = "generic_robot"):
        """
        Initialize generic digital robot state.

        Args:
            robot_id: Unique identifier for the robot instance
        """
        self.robot_id = robot_id
        self.initialized_timestamp = time.time()

        # Core state: variable_name -> last_known_value
        self.state: Dict[str, Any] = {}

        # Metadata for variables (topic subscriptions)
        self.variables_metadata: Dict[str, VariableMetadata] = {}

        # Metadata for methods/commands (topic publications)
        self.methods_metadata: Dict[str, MethodMetadata] = {}

        # Connection state
        self.last_update_timestamp: Optional[float] = None
        self.is_connected = False

        # Topic to variable mapping for efficient message routing
        self._topic_to_variable: Dict[str, str] = {}

    def register_variable(
        self,
        name: str,
        mqtt_topic: str,
        data_type: str = "any",
        description: str = "",
        is_array: bool = False,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Register a state variable that will be received from MQTT.

        Args:
            name: Variable name (how it will be accessed in state dict)
            mqtt_topic: MQTT topic where this variable is published
            data_type: Expected data type ("float", "int", "str", "dict", "list", "any")
            description: Human-readable description
            is_array: Whether this variable contains array data
        """
        metadata = VariableMetadata(
            name=name,
            mqtt_topic=mqtt_topic,
            data_type=data_type,
            description=description,
            is_array=is_array,
            extra_metadata=extra_metadata or {},
        )
        self.variables_metadata[name] = metadata
        self._topic_to_variable[mqtt_topic] = name
        # Initialize state variable if not already present
        if name not in self.state:
            self.state[name] = None

    def register_method(
        self,
        name: str,
        mqtt_command_topic: str,
        parameters: Optional[Dict[str, Dict[str, Any]]] = None,
        description: str = "",
        return_type: str = "void",
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Register a callable method/command that will be sent via MQTT.

        Args:
            name: Method name
            mqtt_command_topic: MQTT topic to publish command to
            parameters: Dict of parameter metadata
                Example: {
                    "velocity": {"type": "float", "required": True, "default": 0.1},
                    "duration": {"type": "float", "required": False, "default": 1.0}
                }
            description: Human-readable description
            return_type: Expected return type
        """
        if parameters is None:
            parameters = {}
        metadata = MethodMetadata(
            name=name,
            mqtt_command_topic=mqtt_command_topic,
            description=description,
            parameters=parameters,
            return_type=return_type,
            extra_metadata=extra_metadata or {},
        )
        self.methods_metadata[name] = metadata

    def update_state(self, variable_name: str, value: Any) -> None:
        """
        Update a state variable with a new value.

        Args:
            variable_name: Name of the variable to update
            value: New value for the variable
        """
        if variable_name in self.variables_metadata:
            self.state[variable_name] = value
            self.variables_metadata[variable_name].last_value = value
            self.variables_metadata[variable_name].last_update_timestamp = time.time()
            self.last_update_timestamp = time.time()
        else:
            # Allow dynamic variable addition (not in metadata)
            self.state[variable_name] = value
            self.last_update_timestamp = time.time()

    def update_from_mqtt_payload(
        self, mqtt_topic: str, payload: Dict[str, Any]
    ) -> None:
        """
        Update state from MQTT message payload.

        Args:
            mqtt_topic: MQTT topic the message came from
            payload: Parsed JSON payload
        """
        variable_name = self._topic_to_variable.get(mqtt_topic)
        if variable_name:
            # If payload is a dict with multiple fields, update the entire payload as the variable
            self.update_state(variable_name, payload)
        else:
            # Topic not registered, but still update generic state
            # Extract variable name from topic (last segment)
            var_name = mqtt_topic.split("/")[-1]
            self.update_state(var_name, payload)

    def get_state(self) -> Dict[str, Any]:
        """
        Get the complete robot state.

        Returns:
            Dictionary containing current state of all variables
        """
        return {
            "robot_id": self.robot_id,
            "is_connected": self.is_connected,
            "state": self.state.copy(),
            "last_update": self.last_update_timestamp,
            "initialized_at": self.initialized_timestamp,
        }

    def get_state_json(self) -> str:
        """
        Get the complete robot state as JSON string.

        Returns:
            JSON representation of state
        """
        return json.dumps(self.get_state(), default=str, indent=2)

    def get_variable(self, variable_name: str) -> Optional[Any]:
        """
        Get a specific variable value.

        Args:
            variable_name: Name of the variable

        Returns:
            Current value or None if not found
        """
        return self.state.get(variable_name)

    def get_variables_metadata(self) -> Dict[str, Dict[str, Any]]:
        """
        Get metadata for all registered variables.

        Returns:
            Dictionary of variable metadata
        """
        variables = {}
        for name, metadata in self.variables_metadata.items():
            metadata_dict = asdict(metadata)
            extra_metadata = metadata_dict.pop("extra_metadata", {}) or {}
            metadata_dict.update(extra_metadata)
            variables[name] = metadata_dict
        return variables

    def get_methods_metadata(self) -> Dict[str, Dict[str, Any]]:
        """
        Get metadata for all registered methods.

        Returns:
            Dictionary of method metadata
        """
        methods = {}
        for name, metadata in self.methods_metadata.items():
            metadata_dict = asdict(metadata)
            extra_metadata = metadata_dict.pop("extra_metadata", {}) or {}
            metadata_dict.update(extra_metadata)
            methods[name] = metadata_dict
        return methods

    def get_subscribed_topics(self) -> List[str]:
        """
        Get all MQTT topics this robot subscribes to (receives data from).

        Returns:
            List of MQTT topics for variables
        """
        return [metadata.mqtt_topic for metadata in self.variables_metadata.values()]

    def get_command_topics(self) -> Dict[str, str]:
        """
        Get all MQTT topics this robot publishes commands to.

        Returns:
            Dict mapping method name to command topic
        """
        return {
            name: metadata.mqtt_command_topic
            for name, metadata in self.methods_metadata.items()
        }

    def is_healthy(self, timeout: float = 5.0) -> bool:
        """
        Check if robot state is healthy (recently updated).

        Args:
            timeout: Timeout in seconds

        Returns:
            True if last update is recent, False otherwise
        """
        if self.last_update_timestamp is None:
            return False
        age = time.time() - self.last_update_timestamp
        return age < timeout

    def to_dict(self) -> Dict[str, Any]:
        """
        Export complete state to dictionary.

        Returns:
            Dictionary representation of state
        """
        return self.get_state()

    def discovery_info(self) -> Dict[str, Any]:
        """
        Get discovery information about this robot.

        This includes all metadata needed to understand and communicate with the robot.

        Returns:
            Dictionary with variables and methods metadata
        """
        return {
            "robot_id": self.robot_id,
            "variables": self.get_variables_metadata(),
            "methods": self.get_methods_metadata(),
            "subscribed_topics": self.get_subscribed_topics(),
            "command_topics": self.get_command_topics(),
        }
