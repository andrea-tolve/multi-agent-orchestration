# Multi-Agent Orchestration System

A distributed multi-agent system for orchestrating robot capabilities and AI agents through goal decomposition, service management, and inter-agent communication via MQTT.

## System Architecture

The system consists of several key components:

- **Global Registry**: Central service registry that tracks all available robots and services
- **Service Manager**: Manages service creation, lifecycle, and robot capability exposure
- **Agent Controller**: Decomposes goals into tasks and orchestrates agent programs
- **Digital Representations**: Cloud twins for physical robots (TurtleBot, Roomba) that expose capabilities via MQTT
- **Message Broker**: Enables inter-agent communication for coordinated task execution

## Prerequisites

### Local Development Setup

1. **Minikube** - For running Kubernetes locally
2. **MetalLB** - For load balancing and service exposure
3. **Python 3.10+** - For running the orchestration system
4. **Docker** - For building and running containerized services

### Installation Steps

#### 1. Start Minikube

```bash
minikube start
```

#### 2. Configure Docker Environment

```bash
eval $(minikube docker-env)
```

#### 3. Install MetalLB

```bash
kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/main/config/manifests/metallb-native.yaml
```

#### 4. Configure MetalLB IP Address Pool

Create a file `metallb-config.yaml`:

```yaml
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: local-pool
  namespace: metallb-system
spec:
  addresses:
  - 192.168.1.240-192.168.1.250
```

Then apply the configuration:

```bash
kubectl apply -f metallb-config.yaml
```

#### 5. Configure MetalLB L2 Advertisement

Create a file `metallb-l2.yaml`:

```yaml
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: local-l2
  namespace: metallb-system
spec:
  ipAddressPools:
    - local-pool
```

Then apply the configuration:

```bash
kubectl apply -f metallb-l2.yaml
```

#### 6. Install Python Dependencies

```bash
pip install -r requirements.txt
```

## Running the System

The system requires multiple services to be running. Start them in this order:

### 1. Start Global Registry

The central registry that tracks all robots and services:

```bash
python -m service_manager.global_registry.global_registry_server
```

The registry runs on `http://127.0.0.1:6000`

### 2. Start Digital Robot Representations

Each robot (TurtleBot, Roomba) runs a Service Manager.

#### Start TurtleBot

```bash
python -m dt_turtlebot.run_dt_turtlebot
```

- Service Manager: `http://127.0.0.1:5000`

```bash
python -m agent_controller.agent_controller
```

- Agent Controller: `http://127.0.0.1:8080`

#### Start Roomba

```bash
python -m dt_roomba.run_dt_roomba
```

- Service Manager: `http://127.0.0.1:5001`

```bash
python -m agent_controller.agent_controller --manager-port 5001 --controller-port 8081
```

- Agent Controller: `http://127.0.0.1:8080`

### 3. Submit a Goal

Once all services are running, submit a goal to any Agent Controller via HTTP POST:

```bash
curl -X POST "http://127.0.0.1:8080/run" \
  -H "Content-Type: application/json" \
  -d '{"goal": "Create a rock-paper-scissors game where the LOCAL ROBOT player uses one UI and the remote player uses another UI. Each player selects rock, paper, or scissors from their respective UIs. Once both have chosen, reveal both selections and declare the winner on both UIs."}'
```

## Goal Decomposition and Execution

When you submit a goal:

1. **Decomposition**: The Agent Controller decomposes the goal into atomic tasks using LLM reasoning
2. **Service Creation**: Each task is created as a Flask microservice in Kubernetes
3. **Delegation**: If a task can run on a different robot, it's delegated to that robot's controller
4. **Execution**: Agent Programs coordinate execution and handle inter-agent communication
5. **Result**: Services run in parallel, exchanging messages until task completion

## Example Goals

### Rock-Paper-Scissors Multiplayer Game

```bash
curl -X POST "http://127.0.0.1:8080/run"   -H "Content-Type: application/json"   -d '{"goal": "Create a rock-paper-scissors game where the LOCAL ROBOT player uses one UI and the remote player uses another UI. Each player selects rock, paper, or scissors from their respective UIs. Once both have chosen, reveal both selections and declare the winner on both UIs."}'
```

### Tic Tac Toe Multiplayer

```bash
curl -X POST "http://127.0.0.1:8080/run"   -H "Content-Type: application/json"   -d '{"goal": "Create a Tic Tac Toe multiplayer game where two players, one local and one remote, play against each other, each with their own browser UI. One player is remote and plays as X and the other is local and plays as O. The two Agent Programs must communicate via inter-agent messaging to exchange moves. Each player has a separate UI that shows the game board, the players symbol (X or O), and the current game status. When a player clicks a cell, their move is sent to the other players agent via inter-agent messaging, and both UIs update to show the new board state. The game ends when one player wins or its a draw."}'

```

## Environment Variables

Configure these in a `.env` file:

```
ENABLE_MQTT=true
BROKER_HOST=<mqtt_broker_host>
BROKER_PORT=<mqtt_broker_port>
MQTT_USERNAME=<mqtt_username>
MQTT_PASSWORD=<mqtt_password>
SERVICE_PLANNER_MODEL = <llm_model>
SERVICE_GENERATOR_MODEL = <llm_model>
SERVICE_MODEL = <llm_model>
SERVICE_EVALUTATION_MODEL = <llm_model>
SERVICE_MATCHING_MODEL = <llm_model>
SERVICE_GENERATION_MODE = "free/scaffold"
```

## ROS Dependencies

Requires ROS 2 Humble:

- rclpy
- geometry_msgs
- nav_msgs
- sensor_msgs
- std_msgs

## Troubleshooting

### MetalLB not assigning IPs

Verify MetalLB is running:

```bash
kubectl get pods -n metallb-system
```

Check if IP pool and advertisement are configured:

```bash
kubectl get ipaddresspools -n metallb-system
kubectl get l2advertisements -n metallb-system
```

### Services not appearing in Kubernetes

Check pod status:

```bash
kubectl get pods
```

View logs:

```bash
kubectl logs <pod_name>
```

### Global Registry connection refused

Ensure the registry is running and listening on port 6000:

```bash
netstat -tlnp | grep 6000
```
