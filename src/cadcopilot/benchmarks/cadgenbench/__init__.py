"""CADGenBench runner, verification and submission packaging."""

from .compose import CompositionResult, compose_runs
from .harness_eval import evaluate_harness
from .matrix import (
    CadgenbenchMatrixConfig,
    MatrixResult,
    ModelPricing,
    load_model_pricing,
    run_matrix,
)
from .package import PackageResult, package_run
from .report import report_runs, summarize_run
from .runner import CadgenbenchRunConfig, run_official_baseline
from .sanity import VerificationReport, verify_run
from .scheduler import CadgenbenchCohortConfig, CohortResult, run_cohort

__all__ = [
    "CadgenbenchCohortConfig",
    "CadgenbenchMatrixConfig",
    "CadgenbenchRunConfig",
    "CohortResult",
    "CompositionResult",
    "MatrixResult",
    "ModelPricing",
    "PackageResult",
    "VerificationReport",
    "compose_runs",
    "evaluate_harness",
    "load_model_pricing",
    "package_run",
    "report_runs",
    "run_cohort",
    "run_matrix",
    "run_official_baseline",
    "summarize_run",
    "verify_run",
]
