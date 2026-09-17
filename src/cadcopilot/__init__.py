"""Kernel-neutral contracts and execution primitives for CADRIG."""

from .adapters.freecad import FreeCADKernelAdapter, FreeCADUnavailableError
from .agent import CopilotAgent, CopilotResult
from .contracts import (
    ACTION_PLAN_SCHEMA_VERSION,
    Action,
    ActionKind,
    ActionPlan,
    AdapterMetadata,
    Diagnostic,
    DocumentSnapshot,
    ExecutionReceipt,
    ExecutionStatus,
    FeatureSnapshot,
)
from .executor import CopilotExecutor
from .macros import (
    FreeCADMacroExecutor,
    FreeCADMacroGenerator,
    MacroArtifact,
    MacroDiagnostic,
    MacroExecutionError,
    MacroExecutionReceipt,
    MacroGenerationError,
    MacroPolicy,
)
from .planner import CopilotPlanner, PlanningContext, PlanningError, action_plan_json_schema
from .registry import AdapterRegistry

__all__ = [
    "ACTION_PLAN_SCHEMA_VERSION",
    "Action",
    "ActionKind",
    "ActionPlan",
    "AdapterMetadata",
    "AdapterRegistry",
    "CopilotAgent",
    "CopilotExecutor",
    "CopilotPlanner",
    "CopilotResult",
    "Diagnostic",
    "DocumentSnapshot",
    "ExecutionReceipt",
    "ExecutionStatus",
    "FeatureSnapshot",
    "FreeCADKernelAdapter",
    "FreeCADMacroExecutor",
    "FreeCADMacroGenerator",
    "FreeCADUnavailableError",
    "MacroArtifact",
    "MacroDiagnostic",
    "MacroExecutionError",
    "MacroExecutionReceipt",
    "MacroGenerationError",
    "MacroPolicy",
    "PlanningContext",
    "PlanningError",
    "action_plan_json_schema",
]

__version__ = "0.1.0a1"
