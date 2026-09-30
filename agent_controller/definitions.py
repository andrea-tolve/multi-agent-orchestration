from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TerminationConditionOutput(BaseModel):
    """Structured LLM output for a task termination condition."""

    description: str = Field(
        description="Natural-language condition that indicates when the task is complete."
    )
    max_duration_sec: float = Field(
        default=100.0,
        description="Maximum task duration in seconds.",
    )


class TaskDecompositionOutput(BaseModel):
    """Structured LLM output for one decomposed task."""

    task_id: str = Field(description="Task id in task_XXX format.")
    task_name: str = Field(description="Lowercase K8s-safe task name.")
    description: str = Field(
        description="Behavior of the Agent Program orchestrating this task (what it should do, how it should coordinate)."
    )
    service_description: str = Field(
        description="Description of what the Flask service itself should do (the technical action, without coordination logic).",
    )
    termination_condition: str = Field(
        description="Natural-language completion condition for the Agent Program."
    )
    dependencies: List[str] = Field(
        default_factory=list,
        description="Task ids that must complete before this task can run.",
    )
    information_dependencies: List[str] = Field(
        default_factory=list,
        description="Task ids that this task exchanges information with during execution via messaging.",
    )
    requires_ui: bool = Field(
        default=False,
        description="Set to true if this task requires a browser-based user interface.",
    )
    ui_description: Optional[str] = Field(
        default=None,
        description="Natural language description of the UI components and interactions required.",
    )
    api_endpoint: Optional[str] = Field(
        default=None,
        description="The REST API endpoint that the UI should call (e.g. /api/rps/play).",
    )


class GoalDecompositionOutput(BaseModel):
    """Structured LLM output for goal decomposition."""

    tasks: List[TaskDecompositionOutput] = Field(
        description="Atomic executable tasks needed to satisfy the goal."
    )


class AgentPlanOutput(BaseModel):
    """Structured LLM output for the next Agent Program action."""

    endpoint: Optional[str] = Field(
        default=None,
        description="OpenAPI endpoint path to call (optional if only sending messages).",
    )
    method: Optional[str] = Field(
        default=None,
        description="HTTP method to use, lowercase (optional if only sending messages).",
    )
    payload: Optional[Dict[str, Any]] = Field(
        default=None,
        description="JSON payload for non-GET requests, or null when not needed.",
    )
    reason: str = Field(description="Brief reason for choosing this action.")
    messages: Optional[Dict[str, Dict[str, Any]]] = Field(
        default=None,
        description="Optional inter-agent messages. Schema: {message_001: {type, target_task_id, content, wait_for_response}, message_002: {...}}",
    )


class TerminationEvaluationOutput(BaseModel):
    """Structured LLM output for termination evaluation."""

    objective_reached: bool = Field(
        description="True only when the termination condition is satisfied."
    )
    reason: str = Field(description="Brief explanation of the decision.")
    final_actions: List[str] = Field(default_factory=list)


def structured_output_to_dict(value: Any) -> Dict[str, Any]:
    """Convert LangChain structured outputs to a plain dictionary."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"Unsupported structured output type: {type(value).__name__}")


class TaskDefinition:
    """Definition of a task to be executed by an Agent Program."""

    def __init__(
        self,
        task_id: str,
        task_name: str,
        description: str,
        service_description: str,
        termination_condition: str,
    ):
        self.task_id = task_id
        self.task_name = task_name
        self.description = description
        self.service_description = service_description
        self.service: dict = {}
        self.is_delegated = False
        self.termination_condition = termination_condition

        # Retry configuration
        self.max_retries = 10
        self.retry_delay_sec = 1.0
        self.retry_backoff_multiplier = 2.0

        # Timeout configuration
        self.execution_timeout_sec: Optional[float] = None

        # Dependencies: other tasks that must complete before this one
        self.depends_on: List[str] = []

        # Information dependencies: other tasks this one exchanges messages with during execution
        self.information_dependencies: List[str] = []

        # UI configuration
        self.requires_ui: bool = False
        self.ui_description: Optional[str] = None
        self.api_endpoint: Optional[str] = None

        # Multiplayer configuration
        self.multiplayer_move_endpoint: Optional[Dict[str, Any]] = None

        # Delegation (populated by AgentController on delegation decision)
        self.delegation_required: bool = False
        self.delegate_controller_url: Optional[str] = None

        # Metadata
        self.created_at = None

    def set_service(self, service_def: dict) -> "TaskDefinition":
        """Set the executable service definition."""
        self.service = service_def
        return self

    def add_service(self, service_def: dict) -> "TaskDefinition":
        """Add a service definition to the task."""
        self.service_definition = service_def
        return self

    def add_dependency(self, task_id: str) -> "TaskDefinition":
        """Add a task dependency."""
        if task_id not in self.depends_on:
            self.depends_on.append(task_id)
        return self

    def add_information_dependency(self, task_id: str) -> "TaskDefinition":
        """Add an information dependency - task to exchange messages with during execution."""
        if task_id not in self.information_dependencies:
            self.information_dependencies.append(task_id)
        return self

    def set_ui_requirements(
        self,
        requires_ui: bool,
        ui_description: Optional[str] = None,
        api_endpoint: Optional[str] = None,
    ) -> "TaskDefinition":
        """Set UI requirements for this task."""
        self.requires_ui = requires_ui
        self.ui_description = ui_description
        self.api_endpoint = api_endpoint
        return self

    def set_multiplayer_endpoint(
        self, endpoint: Optional[Dict[str, Any]]
    ) -> "TaskDefinition":
        """Set the multiplayer move endpoint definition."""
        self.multiplayer_move_endpoint = endpoint
        return self

    def set_semantic_domain(self, domain: str) -> "TaskDefinition":
        """Set the semantic domain for task grouping."""
        self.semantic_domain = domain
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "description": self.description,
            "service_description": self.service_description,
            "service": self.service,
            "termination_condition": self.termination_condition,
            "max_retries": self.max_retries,
            "depends_on": self.depends_on,
            "information_dependencies": self.information_dependencies,
            "requires_ui": self.requires_ui,
            "ui_description": self.ui_description,
            "api_endpoint": self.api_endpoint,
            "multiplayer_move_endpoint": self.multiplayer_move_endpoint,
            "is_delegated": self.is_delegated,
        }


class ExecutionContext:
    """Context for primitive execution containing state and utilities."""

    def __init__(self, agent_id: str, task_name: str):
        self.agent_id = agent_id
        self.task_name = task_name
        self.state: Dict[str, Any] = {}
        self.start_time = datetime.now()

    def update_state(self, key: str, value: Any):
        """Update execution state."""
        self.state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get value from execution state."""
        return self.state.get(key, default)

    def get_all_state(self) -> Dict[str, Any]:
        """Get full execution state."""
        return self.state.copy()

    def elapsed_time_sec(self) -> float:
        """Get elapsed time since context creation."""
        return (datetime.now() - self.start_time).total_seconds()
