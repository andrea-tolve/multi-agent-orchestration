"""MQTT publisher for periodically publishing robot state."""

import threading
import time
from abc import ABC, abstractmethod
from typing import Any

from .command_mqtt_publisher import CommandMQTTPublisher


class StateMQTTPublisher(CommandMQTTPublisher, ABC):
    """Base publisher that periodically publishes robot state."""

    def __init__(self, robot: Any, *args, **kwargs):
        super().__init__(robot=robot, *args, **kwargs)
        self.publish_thread = None
        self.publish_interval = 0.5

    @abstractmethod
    def _publish_state(self):
        """Publish robot-specific state to MQTT topics."""
        pass

    def start(self):
        if self.running:
            return
        self.running = True
        self.publish_thread = threading.Thread(target=self._publish_loop, daemon=True)
        self.publish_thread.start()
        print("[INFO] MQTT Publisher started")

    def stop(self):
        self.running = False
        if self.publish_thread:
            self.publish_thread.join(timeout=5.0)
        print("[INFO] MQTT Publisher stopped")

    def _publish_loop(self):
        while self.running:
            try:
                self._publish_state()
                time.sleep(self.publish_interval)
            except Exception as e:
                print(f"[ERROR] Error in publish loop: {e}")
                time.sleep(1.0)
