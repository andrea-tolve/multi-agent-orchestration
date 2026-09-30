import time
from abc import abstractmethod
from typing import Any, Dict, Optional


class DigitalRobot:
    def __init__(self, state: Any, INFO_VARIABLES_TOPIC: str, INFO_METHODS_TOPIC: str):
        self.state = state
        self._state_sub: Any
        self._cmd_pub: Any
        self.INFO_VARIABLES_TOPIC = INFO_VARIABLES_TOPIC
        self.INFO_METHODS_TOPIC = INFO_METHODS_TOPIC

    @abstractmethod
    def connect(
        self,
        username: str,
        password: str,
        broker: str = "localhost",
        port: int = 1883,
    ):
        pass

    def disconnect(self):
        if self._state_sub:
            self._state_sub.stop_monitoring()
            self._state_sub.disconnect()
        if self._cmd_pub:
            self._cmd_pub.disconnect()

    def _perform_discovery(self, timeout: float = 5.0):
        """
        Perform automatic discovery of robot capabilities.

        Subscribes to discovery topics and waits for the robot to publish
        its capabilities, then registers them in the state.

        Args:
            timeout: Maximum time to wait for discovery info in seconds
        """
        print(f"[INFO] Starting discovery with {timeout}s timeout...")

        # Subscribe to discovery topics
        self._state_sub.mqtt_client.subscribe(self.INFO_VARIABLES_TOPIC, qos=0)
        self._state_sub.mqtt_client.subscribe(self.INFO_METHODS_TOPIC, qos=0)

        # Wait for discovery info
        start_time = time.time()
        while not self._discovery_complete and (time.time() - start_time) < timeout:
            time.sleep(0.1)

        if not self._discovery_complete:
            print(
                f"[WARNING] Discovery timeout after {timeout}s. "
                "Some capabilities may not be available."
            )

        # Unsubscribe from discovery topics
        self._state_sub.mqtt_client.unsubscribe(self.INFO_VARIABLES_TOPIC)
        self._state_sub.mqtt_client.unsubscribe(self.INFO_METHODS_TOPIC)

        # Subscribe to newly discovered topics
        if hasattr(self._state_sub, "_subscribe_to_registered_topics"):
            self._state_sub._subscribe_to_registered_topics()

        # Start monitoring for state updates
        self._state_sub.start_monitoring()

        print(
            f"[INFO] Discovery complete. "
            f"Registered {len(self.state.variables_metadata)} variables, "
            f"{len(self.state.methods_metadata)} methods"
        )

    def _on_state_change(self, data: Dict[str, Any]):
        """
        Handle state change events from MQTT subscriber.

        This is called when a message arrives on any subscribed topic.
        We use this to detect discovery messages.

        Args:
            data: Dict with 'topic' and 'payload' keys
        """
        topic = data.get("topic")
        payload = data.get("payload")

        if topic == self.INFO_VARIABLES_TOPIC:
            self._register_variables_from_discovery(payload)
            self._variables_discovered = True
            self._check_discovery_complete()

        elif topic == self.INFO_METHODS_TOPIC:
            self._register_methods_from_discovery(payload)
            self._methods_discovered = True
            self._check_discovery_complete()

    def _check_discovery_complete(self):
        """Check if discovery is complete"""
        self._discovery_complete = (
            self._variables_discovered and self._methods_discovered
        )

    def _register_variables_from_discovery(
        self, variables_info: Optional[Dict[str, Any]]
    ):
        """
        Register variables discovered from MQTT discovery topic.

        Args:
            variables_info: Dict mapping variable names to their metadata
        """
        if not isinstance(variables_info, dict):
            print(
                f"[ERROR] Expected dict for variables info, got {type(variables_info)}"
            )
            return

        registered_count = 0
        known_metadata_fields = {
            "name",
            "mqtt_topic",
            "data_type",
            "description",
            "is_array",
        }
        for var_name, var_meta in variables_info.items():
            if var_name == "timestamp":
                continue
            try:
                extra_metadata = {
                    key: value
                    for key, value in var_meta.items()
                    if key not in known_metadata_fields
                }
                self.state.register_variable(
                    name=var_meta.get("name", var_name),
                    mqtt_topic=var_meta.get("mqtt_topic", f"turtlebot/{var_name}"),
                    data_type=var_meta.get("data_type", "any"),
                    description=var_meta.get("description", ""),
                    is_array=var_meta.get("is_array", False),
                    extra_metadata=extra_metadata,
                )
                registered_count += 1
            except Exception as e:
                print(f"[ERROR] Failed to register variable '{var_name}': {e}")

        print(f"[INFO] Registered {registered_count} variables from discovery")

    def _register_methods_from_discovery(self, methods_info: Optional[Dict[str, Any]]):
        """
        Register methods discovered from MQTT discovery topic.

        Args:
            methods_info: Dict mapping method names to their metadata
        """
        if not isinstance(methods_info, dict):
            print(f"[ERROR] Expected dict for methods info, got {type(methods_info)}")
            return

        registered_count = 0
        known_metadata_fields = {
            "name",
            "mqtt_command_topic",
            "description",
            "parameters",
            "return_type",
        }
        for method_name, method_meta in methods_info.items():
            if method_name == "timestamp":
                continue
            try:
                extra_metadata = {
                    key: value
                    for key, value in method_meta.items()
                    if key not in known_metadata_fields
                }
                self.state.register_method(
                    name=method_meta.get("name", method_name),
                    mqtt_command_topic=method_meta.get(
                        "mqtt_command_topic", f"turtlebot/{method_name}"
                    ),
                    description=method_meta.get("description", ""),
                    parameters=method_meta.get("parameters", {}),
                    return_type=method_meta.get("return_type", "void"),
                    extra_metadata=extra_metadata,
                )
                registered_count += 1
            except Exception as e:
                print(f"[ERROR] Failed to register method '{method_name}': {e}")

        print(f"[INFO] Registered {registered_count} methods from discovery")

    def get_capabilities(self) -> dict:
        """
        Get robot capabilities (registered variables and methods).

        Returns:
            Dictionary describing available state variables and methods
        """
        return self.state.discovery_info()

    def get_state(self) -> dict:
        """
        Get complete robot state.

        Returns:
            Current state dictionary
        """
        return self.state.get_state()

    def get_state_json(self) -> str:
        """
        Get complete robot state as JSON.

        Returns:
            JSON string representation of state
        """
        return self.state.get_state_json()

    def is_healthy(self, timeout: float = 5.0) -> bool:
        """
        Check if robot state is healthy.

        Args:
            timeout: Maximum age of state in seconds

        Returns:
            True if state is recent, False otherwise
        """
        return self.state.is_healthy(timeout)

    def send_command(self, method_name: str, **kwargs) -> bool:
        """
        Send a command to the robot using discovered method metadata.

        This is the generic way to send any command after discovery.

        Args:
            method_name: Name of the method/command to invoke
            **kwargs: Parameters for the command

        Returns:
            True if command was sent successfully, False otherwise
        """
        if self._cmd_pub is None:
            print("[ERROR] Publisher not available")
            return False
        return self._cmd_pub.send_command(method_name, **kwargs)

    def get_available_commands(self) -> Dict[str, str]:
        """
        Get list of available commands.

        Returns:
            Dictionary mapping command names to descriptions
        """
        if self._cmd_pub is None:
            return {}
        return self._cmd_pub.get_available_methods()
