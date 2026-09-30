"""MQTT publisher for robot commands."""

import json
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt

from .base_mqtt_client import BaseMQTTClient


class CommandMQTTPublisher(BaseMQTTClient):
    """Publish robot commands, using method metadata when available."""

    def __init__(self, robot: Any = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.robot = robot

    def send_command(self, method_name: str, **parameters) -> bool:
        """Send a command dynamically based on discovered method metadata."""
        if not self.robot:
            print("[WARNING] State not available, cannot send dynamic command")
            return False

        method_meta = self.robot.methods_metadata.get(method_name)
        if not method_meta:
            print(f"[ERROR] Method '{method_name}' not found in metadata")
            return False

        command_topic = method_meta.mqtt_command_topic
        if not command_topic:
            print(f"[ERROR] No command topic for method '{method_name}'")
            return False

        payload = self._validate_and_prepare_parameters(
            method_name, method_meta, parameters
        )
        if payload is None:
            return False

        if command_topic.startswith(f"{self.base_topic}/"):
            relative_topic = command_topic[len(self.base_topic) + 1 :]
        else:
            relative_topic = command_topic

        return self._publish_topic(relative_topic, payload)

    def _validate_and_prepare_parameters(
        self, method_name: str, method_meta: Any, provided_params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        params_meta = method_meta.parameters
        if not params_meta:
            return {}

        payload = {}
        for param_name, param_info in params_meta.items():
            if param_name in provided_params:
                payload[param_name] = provided_params[param_name]
            elif param_info.get("required", False):
                print(
                    f"[ERROR] Required parameter '{param_name}' not provided for method '{method_name}'"
                )
                return None
            elif "default" in param_info:
                payload[param_name] = param_info["default"]

        for param_name, value in provided_params.items():
            if param_name not in payload:
                payload[param_name] = value

        return payload

    def _publish_topic(
        self, topic: str, payload: Dict[str, Any], retain: bool = False
    ) -> bool:
        """Publish a JSON message to a topic relative to base_topic."""
        if not self.connected:
            return False

        full_topic = f"{self.base_topic}/{topic}"
        try:
            message = json.dumps(payload, default=str)
            result = self.client.publish(full_topic, message, qos=0, retain=retain)
            return result.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            print(f"[ERROR] Failed to publish to {full_topic}: {e}")
            return False
