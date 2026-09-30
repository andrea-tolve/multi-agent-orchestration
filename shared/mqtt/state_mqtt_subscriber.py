"""MQTT subscriber for cloud-twin state updates."""

import json
from typing import Any, Callable, Dict, List, cast

from .base_mqtt_client import BaseMQTTClient


class StateMQTTSubscriber(BaseMQTTClient):
    """Subscribe to registered state topics and update a digital robot state object."""

    def __init__(self, state: Any, *args, **kwargs):
        if state is None:
            raise ValueError("state must be provided")
        super().__init__(*args, **kwargs)
        self.state = state
        self.client.on_message = self._on_mqtt_message
        self.callbacks: Dict[str, List[Callable]] = {
            "state_change": [],
            "connected": [],
            "variable_updated": [],
        }

    def _add_callback(self, key: str, callback: Callable):
        if key in self.callbacks:
            self.callbacks[key].append(callback)

    def register_callback(self, event_type: str, callback: Callable):
        self._add_callback(event_type, callback)

    def _trigger_callbacks(self, event_type: str, data: Any):
        callbacks = cast(List[Callable], self.callbacks.get(event_type, []))
        for callback in callbacks:
            try:
                callback(data)
            except Exception as e:
                print(f"[ERROR] Error in callback: {e}")

    def _subscribe_to_registered_topics(self):
        if self.state is None:
            print("[WARNING] State is None, cannot subscribe to topics")
            return

        subscribed_count = 0
        for variable_name, metadata in self.state.variables_metadata.items():
            topic = metadata.mqtt_topic
            try:
                self.client.subscribe(topic, qos=0)
                subscribed_count += 1
                print(f"[INFO] Subscribed to topic: {topic}")
            except Exception as e:
                print(f"[ERROR] Failed to subscribe to topic {topic}: {e}")

        print(f"[INFO] Successfully subscribed to {subscribed_count} topics")

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(
                f"[INFO] {self._log_prefix()} Connected to MQTT broker at {self.broker_host}:{self.broker_port}"
            )
            self._set_connected(True)
            self.state.is_connected = True
            if self.state.variables_metadata:
                self._subscribe_to_registered_topics()
            self._trigger_callbacks("connected", True)
        else:
            print(f"[ERROR] {self._log_prefix()} MQTT connection failed with code {rc}")
            self._set_connected(False)
            self.state.is_connected = False
            self._trigger_callbacks("connected", False)

    def _on_mqtt_disconnect(self, client, userdata, rc):
        self._set_connected(False)
        if self.state is not None:
            self.state.is_connected = False
        self._trigger_callbacks("connected", False)

        if rc != 0:
            print(
                f"[ERROR] {self._log_prefix()} Unexpected MQTT disconnection with code {rc}"
            )
        else:
            print(f"[INFO] {self._log_prefix()} Disconnected from MQTT broker")

    def _on_mqtt_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload_str = msg.payload.decode("utf-8")
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                payload = payload_str

            self.state.update_from_mqtt_payload(topic, payload)
            self._trigger_callbacks(
                "state_change", {"topic": topic, "payload": payload}
            )

            variable_name = self.state._topic_to_variable.get(topic)
            if variable_name:
                self._trigger_callbacks(
                    "variable_updated",
                    {"variable": variable_name, "value": payload},
                )
        except Exception as e:
            print(f"[ERROR] Error processing message on topic {msg.topic}: {e}")
