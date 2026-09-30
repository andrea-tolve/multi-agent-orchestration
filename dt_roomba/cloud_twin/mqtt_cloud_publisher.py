"""
Cloud Twin MQTT Publisher for Roomba

Publishes commands to the robot via MQTT.

Inherits common MQTT functionality from BaseMQTTPublisher.
Implements Roomba-specific command publishing methods.
"""

import sys
from pathlib import Path
from typing import Any

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.mqtt.command_mqtt_publisher import CommandMQTTPublisher


class MQTTPublisher(CommandMQTTPublisher):
    """
    Cloud Twin MQTT Publisher for sending commands to Roomba
    """

    def __init__(
        self,
        username: str,
        password: str,
        state: Any,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        base_topic: str = "roomba",
        client_id: str = "roomba_cloud_publisher",
    ):
        """
        Initialize Roomba Cloud Twin MQTT Publisher

        Args:
            username: MQTT broker username
            password: MQTT broker password
            state: Any state object to publish
            broker_host: MQTT broker hostname
            broker_port: MQTT broker port
            base_topic: Base topic for commands
            client_id: MQTT client ID
        """
        super().__init__(
            robot=state,
            broker_host=broker_host,
            broker_port=broker_port,
            base_topic=base_topic,
            client_id=client_id,
            username=username,
            password=password,
        )
