"""Shared MQTT client base for publishers and subscribers."""

import os
import threading
import time
from typing import Optional

import paho.mqtt.client as mqtt


class BaseMQTTClient:
    """Common MQTT connection, credentials, TLS, and monitoring logic."""

    def __init__(
        self,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        base_topic: str = "robot",
        client_id: str = "mqtt_client",
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.base_topic = base_topic
        self.client_id = client_id
        self.username = username
        self.password = password

        self.client = mqtt.Client(client_id=client_id)
        self.client.max_queued_messages_set(1)
        self.client.max_inflight_messages_set(1)
        self.mqtt_client = (
            self.client
        )  # Backward-compatible alias used by older subscribers.
        self.client.on_connect = self._on_mqtt_connect
        self.client.on_disconnect = self._on_mqtt_disconnect
        self.connected = False
        self.mqtt_connected = False

        if username and password:
            self.client.username_pw_set(username, password)

        if broker_port == 8883:
            self.client.tls_set()
            self.client.tls_insecure_set(False)

        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None

    def _set_connected(self, value: bool):
        self.connected = value
        self.mqtt_connected = value

    def _log_prefix(self) -> str:
        class_name = self.__class__.__name__.lower()
        if "subscriber" in class_name:
            role = "subscriber"
        elif "publisher" in class_name:
            role = "publisher"
        else:
            role = "client"
        return f"[MQTT {role}:{self.client_id}]"

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._set_connected(True)
            print(
                f"[INFO] {self._log_prefix()} Connected to MQTT broker at {self.broker_host}:{self.broker_port}"
            )
        else:
            self._set_connected(False)
            print(f"[ERROR] {self._log_prefix()} MQTT connection failed with code {rc}")

    def _on_mqtt_disconnect(self, client, userdata, rc):
        self._set_connected(False)
        if rc != 0:
            reason = mqtt.error_string(rc)
            print(
                f"[ERROR] {self._log_prefix()} Unexpected MQTT disconnection with code {rc} ({reason})"
            )
        else:
            print(f"[INFO] {self._log_prefix()} Disconnected from MQTT broker")

    def connect(self):
        try:
            print(
                f"[INFO] {self._log_prefix()} Connecting to MQTT broker at {self.broker_host}:{self.broker_port}"
            )
            self.client.connect(self.broker_host, self.broker_port, keepalive=30)
            self.client.loop_start()
        except Exception as e:
            print(f"[ERROR] {self._log_prefix()} Failed to connect to MQTT broker: {e}")
            raise

    def disconnect(self):
        try:
            self.client.disconnect()
            self.client.loop_stop()
        except Exception as e:
            print(f"[ERROR] Error disconnecting: {e}")

    def start_monitoring(self):
        if self.running:
            return
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        print("[INFO] Monitoring started")

    def stop_monitoring(self):
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5.0)

    def _monitor_loop(self):
        while self.running:
            try:
                if not self.connected:
                    print("[WARNING] MQTT not connected")
                time.sleep(5)
            except Exception as e:
                print(f"[ERROR] Monitoring error: {e}")
