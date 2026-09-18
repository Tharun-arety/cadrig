"""Contract-driven execution, verification and evaluation for CAD agents."""

from .action_graph import ActionGraphContext, ActionGraphPlanner, ActionPlanningError
from .adapters.freecad import FreeCADKernelAdapter, FreeCADUnavailableError
from .contract_compiler import ContractCompilationError, ContractCompiler, ContractContext
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
from .design_contracts import (
    DESIGN_CONTRACT_SCHEMA_VERSION,
    DesignContract,
    DesignPredicate,
    PredicateKind,
)
from .executor import ExecutionEngine
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
from .native_agent import AgentRun, AgentRunStatus, NativeAgentError, NativeCADAgent
from .registry import AdapterRegistry
from .tracing import AgentPhase, AgentTrace, TraceEvent
from .verification import ContractVerifier, VerificationIssue, VerificationReport

__all__ = [
    "ACTION_PLAN_SCHEMA_VERSION",
    "DESIGN_CONTRACT_SCHEMA_VERSION",
    "Action",
    "ActionGraphContext",
    "ActionGraphPlanner",
    "ActionKind",
    "ActionPlan",
    "ActionPlanningError",
    "AdapterMetadata",
    "AdapterRegistry",
    "AgentPhase",
    "AgentRun",
    "AgentRunStatus",
    "AgentTrace",
    "ContractCompilationError",
    "ContractCompiler",
    "ContractContext",
    "ContractVerifier",
    "DesignContract",
    "DesignPredicate",
    "Diagnostic",
    "DocumentSnapshot",
    "ExecutionEngine",
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
    "NativeAgentError",
    "NativeCADAgent",
    "PredicateKind",
    "TraceEvent",
    "VerificationIssue",
    "VerificationReport",
]

__version__ = "0.2.0a1"
