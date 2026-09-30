"""In-memory global registry for robots and their services.

A robot stays valid only while its heartbeat is fresh.
When `last_seen` exceeds the configured TTL, the robot is considered expired
and is removed from the active registry on the next query.
"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from shared.utils import (
    load_llm_model,
    parse_llm_json_payload,
    strip_code_fences,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> float:
    return time.time()


def _to_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def _safe_compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class GlobalRegistry:
    def __init__(self, default_ttl_seconds: Optional[int] = None):
        load_dotenv()
        self._lock = RLock()
        self._robots: Dict[str, Dict[str, Any]] = {}
        env_default_ttl = os.getenv("GLOBAL_REGISTRY_TTL_SECONDS", "60")
        self.default_ttl_seconds = _to_int(default_ttl_seconds or env_default_ttl, 60)
        self._matching_llm = load_llm_model(
            os.getenv("SERVICE_MATCHING_MODEL"), temperature=0.1
        )
        self._last_matching_decision = None
        self._matching_min_confidence = 0.25

    def _normalize_ttl_seconds(
        self, ttl_seconds: Optional[int], existing: Optional[Dict[str, Any]] = None
    ) -> int:
        if ttl_seconds is not None:
            return _to_int(ttl_seconds, self.default_ttl_seconds)
        if existing and existing.get("ttl_seconds"):
            return _to_int(existing.get("ttl_seconds"), self.default_ttl_seconds)
        return self.default_ttl_seconds

    def _is_expired(self, robot: Dict[str, Any]) -> bool:
        last_seen = robot.get("last_seen")
        if last_seen is None:
            return True
        ttl_seconds = self._normalize_ttl_seconds(robot.get("ttl_seconds"), robot)
        try:
            return (_now_epoch() - float(last_seen)) > ttl_seconds
        except Exception:
            return True

    def _prune_expired_locked(self) -> None:
        """Removes expired robots from the registry."""
        expired = [
            name for name, robot in self._robots.items() if self._is_expired(robot)
        ]
        for name in expired:
            self._robots.pop(name, None)

    def _get_active_robot_locked(self, robot_name: str) -> Optional[Dict[str, Any]]:
        robot = self._robots.get(robot_name)
        if not robot:
            return None
        if self._is_expired(robot):
            self._robots.pop(robot_name, None)
            return None
        return robot

    def _build_robot_record(
        self,
        robot_name: str,
        manager_url: str,
        controller_url: Optional[str] = None,
        capabilities: Optional[Dict[str, Any]] = None,
        ttl_seconds: Optional[int] = None,
        existing: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = _now_epoch()
        existing = existing or {}
        return {
            "robot_name": robot_name,
            "manager_url": manager_url or existing.get("manager_url", ""),
            "controller_url": controller_url or existing.get("controller_url", ""),
            "capabilities": capabilities
            if capabilities is not None
            else existing.get("capabilities") or {},
            "services": deepcopy(existing.get("services") or {}),
            "ttl_seconds": self._normalize_ttl_seconds(ttl_seconds, existing),
            "last_seen": now,
            "created_at": existing.get("created_at") or _utc_now(),
        }

    def register_robot(
        self,
        robot_name: str,
        manager_url: str,
        controller_url: str,
        capabilities: Optional[Dict[str, Any]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not robot_name:
            raise ValueError("robot_name is required")
        if not manager_url:
            raise ValueError("manager_url is required")

        with self._lock:
            existing = self._robots.get(robot_name) or {}
            robot_record = self._build_robot_record(
                robot_name=robot_name,
                manager_url=manager_url,
                controller_url=controller_url,
                capabilities=capabilities,
                ttl_seconds=ttl_seconds,
                existing=existing,
            )
            self._robots[robot_name] = robot_record
            return deepcopy(robot_record)

    def heartbeat_robot(
        self,
        robot_name: str,
        manager_url: Optional[str] = None,
        controller_url: Optional[str] = None,
        capabilities: Optional[Dict[str, Any]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not robot_name:
            raise ValueError("robot_name is required")

        with self._lock:
            existing = (
                self._get_active_robot_locked(robot_name)
                or self._robots.get(robot_name)
                or {}
            )
            if not existing and not manager_url:
                raise ValueError("manager_url is required for first heartbeat")

            robot_record = self._build_robot_record(
                robot_name=robot_name,
                manager_url=existing.get("manager_url", manager_url),
                controller_url=controller_url or existing.get("controller_url"),
                capabilities=capabilities,
                ttl_seconds=ttl_seconds,
                existing=existing,
            )
            self._robots[robot_name] = robot_record
            return deepcopy(robot_record)

    def unregister_robot(self, robot_name: str) -> Optional[Dict[str, Any]]:
        if not robot_name:
            raise ValueError("robot_name is required")
        with self._lock:
            removed = self._robots.pop(robot_name, None)
            return deepcopy(removed) if removed else None

    def add_service(
        self, robot_name: str, executable_service: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not robot_name:
            raise ValueError("robot_name is required")
        if not isinstance(executable_service, dict):
            raise TypeError("executable_service must be a dictionary")

        service_name = executable_service.get("service_name")
        if not service_name:
            raise ValueError("executable_service.service_name is required")

        with self._lock:
            robot = self._get_active_robot_locked(robot_name)
            if robot is None:
                raise ValueError(f"robot '{robot_name}' is offline or unknown")

            service_record = deepcopy(executable_service)
            service_record.setdefault("created_at", _utc_now())
            robot.setdefault("services", {})[service_name] = service_record
            robot["last_seen"] = _now_epoch()
            self._robots[robot_name] = robot
            return deepcopy(service_record)

    def remove_service(
        self, robot_name: str, service_name: str
    ) -> Optional[Dict[str, Any]]:
        if not robot_name:
            raise ValueError("robot_name is required")
        if not service_name:
            raise ValueError("service_name is required")

        with self._lock:
            robot = self._get_active_robot_locked(robot_name)
            if not robot:
                return None
            services = robot.get("services") or {}
            if service_name not in services:
                return None
            removed = services.pop(service_name)
            robot["last_seen"] = _now_epoch()
            self._robots[robot_name] = robot
            return deepcopy(removed)

    def is_robot_online(self, robot_name: str) -> bool:
        with self._lock:
            return self._get_active_robot_locked(robot_name) is not None

    def get_robot(self, robot_name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            robot = self._get_active_robot_locked(robot_name)
            return deepcopy(robot) if robot else None

    def list_robots(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            self._prune_expired_locked()
            return deepcopy(self._robots)

    def list_services(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            self._prune_expired_locked()
            services: Dict[str, Dict[str, Any]] = {}
            for robot_name, robot in self._robots.items():
                for service_name, service in (robot.get("services") or {}).items():
                    services[f"{robot_name}:{service_name}"] = deepcopy(service)
            return services

    def _active_registry_for_query(
        self, requester_robot_name: str = ""
    ) -> Dict[str, Dict[str, Any]]:
        return {
            robot_name: deepcopy(robot)
            for robot_name, robot in self._robots.items()
            if not requester_robot_name or robot_name != requester_robot_name
        }

    def _build_matching_candidates(
        self,
        active_registry: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Extract service and robot candidates from the active registry."""
        candidates: List[Dict[str, Any]] = []

        for robot_name, robot in active_registry.items():
            services = robot.get("services") or {}
            if isinstance(services, dict):
                for service_name, service in services.items():
                    if not isinstance(service, dict):
                        continue
                    candidates.append(
                        {
                            "candidate_id": f"service:{robot_name}:{service_name}",
                            "kind": "service",
                            "robot_name": robot_name,
                            "service_name": service_name,
                            "details": {
                                "service_name": service.get("service_name")
                                or service_name,
                                "service_description": service.get(
                                    "service_description", ""
                                ),
                                "description": service.get("description", ""),
                                "kind": service.get("kind", ""),
                            },
                        }
                    )

            capabilities = robot.get("capabilities") or {}
            candidates.append(
                {
                    "candidate_id": f"robot:{robot_name}",
                    "kind": "robot",
                    "robot_name": robot_name,
                    "service_name": None,
                    "details": {
                        "robot_name": robot.get("robot_name") or robot_name,
                        "capabilities": deepcopy(capabilities),
                    },
                }
            )

        return candidates

    def _extract_json_payload(self, text: str) -> Dict[str, Any]:
        raw_text = strip_code_fences(text)
        return parse_llm_json_payload(
            raw_text,
            expected_type=dict,
            error_prefix="Registry matching LLM response",
        )

    def _llm_match(
        self,
        service_name: str,
        service_description: str,
        active_registry: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not active_registry:
            return None

        query_text = f"{service_name} {service_description}".strip()
        candidates = self._build_matching_candidates(
            active_registry=active_registry,
        )
        if not candidates:
            return None

        candidate_map = {
            candidate["candidate_id"]: candidate for candidate in candidates
        }
        prompt_candidates = [
            {
                "candidate_id": candidate["candidate_id"],
                "kind": candidate["kind"],
                "robot_name": candidate["robot_name"],
                "service_name": candidate["service_name"],
                "details": candidate["details"],
            }
            for candidate in candidates
        ]

        prompt_payload = {
            "request": {
                "service_name": service_name,
                "service_description": service_description,
                "query": query_text,
            },
            "candidates": prompt_candidates,
            "matching_procedure": [
                "Evaluate every candidate using its structured details.",
                "Choose kind='robot' when robot can plausibly generate the requested service.",
                "Ignore infrastructure details such as HTTP routes, app directories, Docker, Kubernetes, ports, and deployment.",
                "Choose kind='none' only if no candidate can satisfy the request.",
            ],
            "minimum_confidence": self._matching_min_confidence,
            "output_contract": {
                "found": "boolean",
                "kind": "robot|none",
                "robot_name": "exact robot_name from selected candidate, or null",
                "candidate_id": "exact candidate_id from selected candidate, or null",
                "confidence": "number from 0 to 1",
                "reason": "short explanation",
            },
        }
        prompt = (
            "You are a registry matcher. Return only one valid JSON object and no markdown.\n\n"
            "Goal: Find the best candidate (service or robot) that can satisfy the requested service.\n\n"
            "Matching Rules:\n"
            "1. EVALUATE ALL CANDIDATES before making a selection.\n"
            "2. REMOTE AGENT: If the request is for 'remote agent', 'another agent', "
            "   or any variant\n"
            "   the goal is to find an available remote agent,\n"
            "   return kind='robot' with the first available robot and confidence=1.0.\n"
            "   Examples:\n"
            "   - 'remote agent', 'any remote agent', 'another robot'\n"
            "   - 'inter-agent communication', 'agent collaboration'\n"
            "   - 'any available agent', 'remote interaction partner'\n"
            "3. ACCEPT robots whose capabilities align with the request (even loosely).\n"
            "4. IGNORE infrastructure details (ports, Docker, Kubernetes, file paths, HTTP routes).\n"
            "5. ROBOT CAPABILITY MATCHING: If a robot has capabilities that could generate the service,\n"
            "   consider it a match. For example:\n"
            "   - Robot with 'motion' capability can create movement services\n"
            "   - Robot with 'sensing' capability can create sensor services\n"
            "   - Robot with 'collaboration' capability can create agent coordination services\n"
            "   - Robot with 'ui_creation' capability can create game UIs, forms, dashboards\n"
            "   - Any robot can create UI-based services (games, forms, dashboards)\n"
            "6. If confidence is >= minimum_confidence, select it.\n"
            "7. If no perfect match found but a candidate is plausible, prefer it over 'none'.\n"
            "8. Use only robot_name, service_name, and candidate_id from the candidates list.\n"
            "9. Return JSON only. No comments, no markdown.\n\n"
            f"{_safe_compact_json(prompt_payload)}"
        )

        response = self._matching_llm.invoke(
            prompt,
        )
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )

        decision = self._extract_json_payload(str(content))
        self._last_matching_decision = {
            "decision": decision,
            "candidates": prompt_candidates,
        }
        found = bool(decision.get("found"))
        chosen_kind = str(decision.get("kind") or "").lower()
        candidate_id = str(decision.get("candidate_id") or "")
        robot_name = str(decision.get("robot_name") or "")

        try:
            match_score = float(decision.get("confidence", 0.0))
        except Exception:
            match_score = 0.0

        if (
            not found
            or chosen_kind == "none"
            or not candidate_id
            or match_score < self._matching_min_confidence
        ):
            return None

        selected_candidate = candidate_map.get(candidate_id)
        if not selected_candidate:
            return None

        # Strict validation: the LLM must be consistent with the selected candidate.
        if selected_candidate.get("kind") != chosen_kind:
            return None
        if selected_candidate.get("robot_name") != robot_name:
            return None

        robot = active_registry.get(robot_name)
        if not robot:
            return None

        llm_reason = str(decision.get("reason") or "").strip()
        if chosen_kind != "none":
            return {
                "robot_name": robot_name,
                "manager_url": robot.get("manager_url", ""),
                "capabilities": deepcopy(robot.get("capabilities") or {}),
                "services": deepcopy(robot.get("services") or {}),
                "match_score": match_score,
                "llm_reason": llm_reason,
                "candidate_id": candidate_id,
            }

        return None

    def _result_from_match(
        self,
        match: Dict[str, Any],
        *,
        matching_method: str,
        default_reason: str,
    ) -> Dict[str, Any]:
        return {
            "found": True,
            "reason": match.get("llm_reason") or default_reason,
            "robot_name": match.get("robot_name"),
            "manager_url": match.get("manager_url", ""),
            "capabilities": deepcopy(match.get("capabilities") or {}),
            "match_score": match.get("match_score"),
            "candidate_id": match.get("candidate_id"),
            "matching_method": matching_method,
        }

    def query(
        self,
        service_name: str,
        service_description: str = "",
        requester_robot_name: str = "",
        any_remote_agent: bool = False,
    ) -> Dict[str, Any]:
        with self._lock:
            self._prune_expired_locked()

            if not self._robots:
                return {
                    "found": False,
                    "kind": "none",
                    "reason": "registry is empty",
                }

            active_registry = self._active_registry_for_query(
                requester_robot_name=requester_robot_name
            )

        if not active_registry:
            return {
                "found": False,
                "kind": "none",
                "reason": "no active candidate robots after excluding requester",
            }

        try:
            llm_match = self._llm_match(
                service_name, service_description, active_registry
            )
        except Exception as exc:
            print(f"Warning: registry LLM matching failed: {exc}")
            llm_match = None

        if llm_match:
            return self._result_from_match(
                llm_match,
                matching_method="llm",
                default_reason="best semantic registry match found",
            )

        return {
            "found": False,
            "kind": "none",
            "reason": "no compatible semantic match found",
        }

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            self._prune_expired_locked()
            return deepcopy(self._robots)


registry = GlobalRegistry()
