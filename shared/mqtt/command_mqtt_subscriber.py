"""MQTT subscriber for command execution."""

import json
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Union

from .base_mqtt_client import BaseMQTTClient


class CommandMQTTSubscriber(BaseMQTTClient, ABC):
    """Subscribe to command topics and dispatch payloads to command callbacks."""

    def __init__(self, robot: Any, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.robot = robot
        self.client.on_message = self._on_mqtt_message
        self.callbacks: Dict[str, Union[Callable, List[Callable]]] = {}
        self._register_default_callbacks()

    @abstractmethod
    def _register_default_callbacks(self):
        pass

    def _add_callback(self, key: str, callback: Callable):
        self.callbacks[key] = callback

    def register_callback(self, command: str, callback: Callable):
        self._add_callback(command, callback)
        if self.connected:
            self.client.subscribe(self._topic_for_command(command), qos=0)

    def _topic_for_command(self, command: str) -> str:
        return f"{self.base_topic}/{command}"

    def _subscribe_to_command_topics(self):
        for command in self.callbacks.keys():
            self.client.subscribe(self._topic_for_command(command), qos=0)
        print(
            f"[INFO] Subscribed to {len(self.callbacks)} command topics under {self.base_topic}"
        )

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._set_connected(True)
            self._subscribe_to_command_topics()
        else:
            print(f"[ERROR] MQTT connection failed with code {rc}")
            self._set_connected(False)

    def _on_mqtt_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = json.loads(msg.payload.decode("utf-8"))
            topic_parts = topic.split("/")
            if len(topic_parts) == 2 and topic_parts[0] == self.base_topic:
                command = topic_parts[1]
                self._execute_command(command, payload)
        except json.JSONDecodeError:
            print(f"[ERROR] Failed to decode JSON message on topic {msg.topic}")
        except Exception as e:
            print(f"[ERROR] Error processing message: {e}")

    def _execute_command(self, command: str, data: Dict[str, Any]):
        try:
            if command in self.callbacks:
                callback = self.callbacks[command]
                if isinstance(callback, list):
                    for cb in callback:
                        cb(data)
                else:
                    callback(data)
                print(f"[INFO] Executed command: {command}")
            else:
                print(f"[WARNING] Unknown command: {command}")
        except Exception as e:
            print(f"[ERROR] Error executing command {command}: {e}")
