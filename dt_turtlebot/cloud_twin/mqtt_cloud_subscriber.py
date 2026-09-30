"""
Generic Cloud Twin MQTT Subscriber

Subscribes to MQTT topics and updates the digital robot state dictionary.
Works with any robot by using the registered variables from the state object.
"""

import sys
from pathlib import Path
from typing import Any

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.mqtt.state_mqtt_subscriber import StateMQTTSubscriber


class MQTTSubscriber(StateMQTTSubscriber):
    """Cloud Twin MQTT Subscriber that updates TurtleBot digital state."""

    def __init__(
        self,
        username: str,
        password: str,
        state: Any,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        base_topic: str = "turtlebot",
        client_id: str = "cloud_subscriber",
    ):
        super().__init__(
            state=state,
            broker_host=broker_host,
            broker_port=broker_port,
            base_topic=base_topic,
            client_id=client_id,
            username=username,
            password=password,
        )
