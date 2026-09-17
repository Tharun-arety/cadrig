"""Minimal command-line client for exercising the protocol."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from cadcopilot.adapters.freecad import FreeCADKernelAdapter, FreeCADUnavailableError
from cadcopilot.adapters.memory import MemoryKernelAdapter
from cadcopilot.agent import CopilotAgent
from cadcopilot.contracts import ActionPlan, ContractError
from cadcopilot.executor import CopilotExecutor
from cadcopilot.macros import FreeCADMacroGenerator, MacroGenerationError
from cadcopilot.models import OpenAICompatibleClient
from cadcopilot.planner import CopilotPlanner, PlanningError
from cadcopilot.registry import AdapterRegistry


def _executor() -> CopilotExecutor:
    registry = AdapterRegistry()
    registry.register(MemoryKernelAdapter())
    try:
        registry.register(FreeCADKernelAdapter())
    except FreeCADUnavailableError:
        pass
    registry.discover()
    return CopilotExecutor(registry)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cadrig")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("adapters", help="list registered CAD-host adapters")
    run_parser = subparsers.add_parser("run", help="validate and execute a typed plan")
    run_parser.add_argument("plan", type=Path)
    run_parser.add_argument("--adapter", default="memory")
    run_parser.add_argument("--dry-run", action="store_true")
    ask_parser = subparsers.add_parser(
        "ask", help="plan a natural-language request with your model and CAD adapter"
    )
    ask_parser.add_argument("intent")
    ask_parser.add_argument("--document", required=True)
    ask_parser.add_argument("--adapter", default="memory")
    ask_parser.add_argument("--model-base-url")
    ask_parser.add_argument("--model")
    ask_parser.add_argument("--api-key-env", default="CADCOPILOT_MODEL_API_KEY")
    ask_parser.add_argument("--timeout", type=float, default=60)
    ask_parser.add_argument(
        "--response-format",
        choices=("json_schema", "json_object", "none"),
        default="json_schema",
        help="structured-output mode supported by the selected model endpoint",
    )
    ask_parser.add_argument(
        "--apply",
        action="store_true",
        help="commit the plan; without this flag the adapter performs a dry run",
    )
    macro_parser = subparsers.add_parser(
        "macro", help="generate a reviewed FreeCAD .FCMacro with your model"
    )
    macro_parser.add_argument("intent")
    macro_parser.add_argument("--output", required=True, type=Path)
    macro_parser.add_argument("--model-base-url")
    macro_parser.add_argument("--model")
    macro_parser.add_argument("--api-key-env", default="CADCOPILOT_MODEL_API_KEY")
    macro_parser.add_argument("--timeout", type=float, default=60)
    macro_parser.add_argument(
        "--response-format",
        choices=("json_schema", "json_object", "none"),
        default="json_schema",
    )
    macro_parser.add_argument("--freecad-version", default="1.1")
    macro_parser.add_argument(
        "--policy",
        choices=("safe", "review"),
        default="safe",
        help="safe rejects risky code; review saves it with diagnostics but never runs it",
    )
    macro_parser.add_argument("--overwrite", action="store_true")

    benchmark_parser = subparsers.add_parser(
        "benchmark", help="run and package reproducible external CAD benchmarks"
    )
    benchmark_names = benchmark_parser.add_subparsers(dest="benchmark_name", required=True)
    cadgenbench_parser = benchmark_names.add_parser(
        "cadgenbench", help="CADGenBench generation and editing benchmark"
    )
    cadgenbench_commands = cadgenbench_parser.add_subparsers(
        dest="benchmark_command", required=True
    )

    benchmark_run = cadgenbench_commands.add_parser(
        "run", help="delegate a run to the official CADGenBench baseline"
    )
    benchmark_run.add_argument("fixtures", nargs="*")
    benchmark_run.add_argument("--all", action="store_true", dest="run_all")
    benchmark_run.add_argument(
        "--output-root", type=Path, default=Path("results") / "cadgenbench"
    )
    benchmark_run.add_argument("--model")
    benchmark_run.add_argument("--backend", choices=("build123d", "cadquery"), default="build123d")
    benchmark_run.add_argument("--parallel", type=int, default=1)
    benchmark_run.add_argument("--max-iter", type=int)
    benchmark_run.add_argument("--max-tokens", type=int)
    benchmark_run.add_argument("--max-tokens-per-call", type=int)
    benchmark_run.add_argument("--max-duration", type=float)
    benchmark_run.add_argument(
        "--reasoning-effort", choices=("minimal", "low", "medium", "high")
    )
    benchmark_run.add_argument(
        "--data-repo", default="HuggingAI4Engineering/cadgenbench-data"
    )

    benchmark_cohort = cadgenbench_commands.add_parser(
        "cohort", help="run or resume isolated fixtures under a shared token/cost budget"
    )
    benchmark_cohort.add_argument("fixtures", nargs="*")
    benchmark_cohort.add_argument("--all", action="store_true", dest="run_all")
    benchmark_cohort.add_argument("--cohort-dir", type=Path, required=True)
    benchmark_cohort.add_argument("--dataset-dir", type=Path)
    benchmark_cohort.add_argument("--sanity-script", type=Path)
    benchmark_cohort.add_argument("--model", required=True)
    benchmark_cohort.add_argument(
        "--backend", choices=("build123d", "cadquery"), default="build123d"
    )
    benchmark_cohort.add_argument("--token-budget", type=int, required=True)
    benchmark_cohort.add_argument("--max-tokens-per-task", type=int, required=True)
    benchmark_cohort.add_argument("--max-tokens-per-call", type=int)
    benchmark_cohort.add_argument("--max-iter", type=int)
    benchmark_cohort.add_argument("--max-duration", type=float)
    benchmark_cohort.add_argument(
        "--reasoning-effort", choices=("minimal", "low", "medium", "high")
    )
    benchmark_cohort.add_argument("--input-usd-per-million", type=float)
    benchmark_cohort.add_argument("--output-usd-per-million", type=float)
    benchmark_cohort.add_argument("--cost-budget-usd", type=float)
    benchmark_cohort.add_argument(
        "--data-repo", default="HuggingAI4Engineering/cadgenbench-data"
    )

    benchmark_matrix = cadgenbench_commands.add_parser(
        "matrix", help="run paired fixture cohorts across models and CAD kernels"
    )
    benchmark_matrix.add_argument("fixtures", nargs="*")
    benchmark_matrix.add_argument("--all", action="store_true", dest="run_all")
    benchmark_matrix.add_argument("--matrix-dir", type=Path, required=True)
    benchmark_matrix.add_argument("--dataset-dir", type=Path)
    benchmark_matrix.add_argument("--sanity-script", type=Path)
    benchmark_matrix.add_argument("--model", dest="models", action="append", required=True)
    benchmark_matrix.add_argument(
        "--backend",
        dest="backends",
        action="append",
        choices=("build123d", "cadquery"),
    )
    benchmark_matrix.add_argument("--token-budget-per-cell", type=int, required=True)
    benchmark_matrix.add_argument("--max-tokens-per-task", type=int, required=True)
    benchmark_matrix.add_argument("--max-tokens-per-call", type=int)
    benchmark_matrix.add_argument("--max-iter", type=int)
    benchmark_matrix.add_argument("--max-duration", type=float)
    benchmark_matrix.add_argument(
        "--reasoning-effort", choices=("minimal", "low", "medium", "high")
    )
    benchmark_matrix.add_argument(
        "--pricing-file",
        type=Path,
        help="JSON rates and per-cell cost limit for every selected model",
    )
    benchmark_matrix.add_argument(
        "--data-repo", default="HuggingAI4Engineering/cadgenbench-data"
    )

    benchmark_verify = cadgenbench_commands.add_parser(
        "verify", help="check run completeness and STEP validity"
    )
    benchmark_verify.add_argument("run_dir", type=Path)
    benchmark_verify.add_argument("--dataset-dir", type=Path)
    benchmark_verify.add_argument("--sanity-script", type=Path)
    benchmark_verify.add_argument("--require-sanity", action="store_true")

    benchmark_package = cadgenbench_commands.add_parser(
        "package", help="create a leaderboard submission ZIP"
    )
    benchmark_package.add_argument("run_dir", type=Path)
    benchmark_package.add_argument("-o", "--output", type=Path)
    benchmark_package.add_argument("--submitter")
    benchmark_package.add_argument("--name", dest="submission_name")
    benchmark_package.add_argument("--agent-url")
    benchmark_package.add_argument("--notes")
    benchmark_package.add_argument("--agree", action="store_true")
    benchmark_package.add_argument("--allow-incomplete", action="store_true")
    benchmark_package.add_argument("--dataset-dir", type=Path)
    benchmark_package.add_argument("--sanity-script", type=Path)
    benchmark_package.add_argument("--require-sanity", action="store_true")

    benchmark_report = cadgenbench_commands.add_parser(
        "report", help="summarize and compare CADGenBench experiment runs"
    )
    benchmark_report.add_argument("run_dirs", nargs="+", type=Path)
    benchmark_harness_eval = cadgenbench_commands.add_parser(
        "harness-eval", help="measure portability, recovery, observability, latency and cost"
    )
    benchmark_harness_eval.add_argument("run_dirs", nargs="+", type=Path)
    benchmark_harness_eval.add_argument("-o", "--output", type=Path)
    return parser


def _run_cadgenbench_command(args: argparse.Namespace) -> int:
    from cadcopilot.benchmarks.cadgenbench import (
        CadgenbenchCohortConfig,
        CadgenbenchMatrixConfig,
        CadgenbenchRunConfig,
        evaluate_harness,
        load_model_pricing,
        package_run,
        report_runs,
        run_cohort,
        run_matrix,
        run_official_baseline,
        verify_run,
    )

    try:
        if args.benchmark_command == "run":
            config = CadgenbenchRunConfig(
                output_root=args.output_root,
                fixtures=tuple(args.fixtures),
                run_all=args.run_all,
                model=args.model,
                backend=args.backend,
                parallel=args.parallel,
                max_iterations=args.max_iter,
                max_tokens=args.max_tokens,
                max_tokens_per_call=args.max_tokens_per_call,
                max_duration=args.max_duration,
                reasoning_effort=args.reasoning_effort,
                data_repo=args.data_repo,
            )
            run_dir = run_official_baseline(config, cwd=Path.cwd())
            print(json.dumps({"status": "completed", "run_dir": str(run_dir)}, indent=2))
            return 0

        if args.benchmark_command == "cohort":
            from cadcopilot.benchmarks.cadgenbench.dataset import discover_dataset_tasks

            if args.run_all == bool(args.fixtures):
                raise ValueError("select either fixtures or --all")
            fixtures = tuple(args.fixtures)
            if args.run_all:
                if args.dataset_dir is None:
                    raise ValueError("--all requires --dataset-dir for authoritative task discovery")
                fixtures = discover_dataset_tasks(args.dataset_dir)
                if not fixtures:
                    raise ValueError(f"no CADGenBench tasks found under {args.dataset_dir}")
            result = run_cohort(
                CadgenbenchCohortConfig(
                    cohort_dir=args.cohort_dir,
                    fixtures=fixtures,
                    model=args.model,
                    total_token_budget=args.token_budget,
                    max_tokens_per_task=args.max_tokens_per_task,
                    backend=args.backend,
                    max_iterations=args.max_iter,
                    max_tokens_per_call=args.max_tokens_per_call,
                    max_duration=args.max_duration,
                    reasoning_effort=args.reasoning_effort,
                    input_usd_per_million=args.input_usd_per_million,
                    output_usd_per_million=args.output_usd_per_million,
                    total_cost_budget_usd=args.cost_budget_usd,
                    dataset_dir=args.dataset_dir,
                    sanity_script=args.sanity_script,
                    data_repo=args.data_repo,
                ),
                cwd=Path.cwd(),
            )
            print(
                json.dumps(
                    {
                        "status": result.status,
                        "cohort_dir": str(result.cohort_dir),
                        "completed": result.completed,
                        "failed": result.failed,
                        "pending": result.pending,
                        "total_tokens": result.total_tokens,
                        "cost_usd": result.cost_usd,
                    },
                    indent=2,
                )
            )
            return 0 if result.status == "completed" else 1

        if args.benchmark_command == "matrix":
            from cadcopilot.benchmarks.cadgenbench.dataset import discover_dataset_tasks

            if args.run_all == bool(args.fixtures):
                raise ValueError("select either fixtures or --all")
            fixtures = tuple(args.fixtures)
            if args.run_all:
                if args.dataset_dir is None:
                    raise ValueError("--all requires --dataset-dir for authoritative task discovery")
                fixtures = discover_dataset_tasks(args.dataset_dir)
                if not fixtures:
                    raise ValueError(f"no CADGenBench tasks found under {args.dataset_dir}")
            result = run_matrix(
                CadgenbenchMatrixConfig(
                    matrix_dir=args.matrix_dir,
                    fixtures=fixtures,
                    models=tuple(args.models),
                    backends=tuple(args.backends or ("build123d",)),
                    token_budget_per_cell=args.token_budget_per_cell,
                    max_tokens_per_task=args.max_tokens_per_task,
                    max_iterations=args.max_iter,
                    max_tokens_per_call=args.max_tokens_per_call,
                    max_duration=args.max_duration,
                    reasoning_effort=args.reasoning_effort,
                    pricing=load_model_pricing(args.pricing_file)
                    if args.pricing_file is not None
                    else (),
                    dataset_dir=args.dataset_dir,
                    sanity_script=args.sanity_script,
                    data_repo=args.data_repo,
                ),
                cwd=Path.cwd(),
            )
            print(
                json.dumps(
                    {
                        "status": result.status,
                        "matrix_dir": str(result.matrix_dir),
                        "cells": result.cell_count,
                        "completed_cells": result.completed_cells,
                        "incomplete_cells": result.incomplete_cells,
                        "total_tokens": result.total_tokens,
                        "debited_tokens": result.debited_tokens,
                        "cost_usd": result.cost_usd,
                        "harness_evaluation": str(result.harness_evaluation)
                        if result.harness_evaluation is not None
                        else None,
                    },
                    indent=2,
                )
            )
            return 0 if result.status == "completed" else 1

        if args.benchmark_command == "verify":
            report = verify_run(
                args.run_dir,
                dataset_dir=args.dataset_dir,
                sanity_script=args.sanity_script,
                require_sanity=args.require_sanity,
            )
            print(json.dumps(report.to_dict(), indent=2))
            return 0 if report.passed else 1

        if args.benchmark_command == "report":
            print(json.dumps(report_runs(args.run_dirs), indent=2))
            return 0

        if args.benchmark_command == "harness-eval":
            from cadcopilot.benchmarks.cadgenbench.common import write_json_atomic

            evaluation = evaluate_harness(args.run_dirs)
            if args.output is not None:
                write_json_atomic(args.output.resolve(), evaluation)
            print(json.dumps(evaluation, indent=2))
            return 0

        result = package_run(
            args.run_dir,
            output=args.output,
            submitter=args.submitter,
            submission_name=args.submission_name,
            agent_url=args.agent_url,
            notes=args.notes,
            agree_to_publish=args.agree,
            allow_incomplete=args.allow_incomplete,
            dataset_dir=args.dataset_dir,
            sanity_script=args.sanity_script,
            require_sanity=args.require_sanity or not args.allow_incomplete,
        )
        print(
            json.dumps(
                {
                    "status": "packaged",
                    "output": str(result.output),
                    "fixtures": result.fixture_count,
                    "candidates": result.candidate_count,
                    "agree_to_publish": result.meta["agree_to_publish"],
                },
                indent=2,
            )
        )
        return 0
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "benchmark":
        return _run_cadgenbench_command(args)
    executor = _executor()
    if args.command == "adapters":
        print(json.dumps([item.to_dict() for item in executor.adapters()], indent=2))
        return 0

    if args.command == "macro":
        base_url = args.model_base_url or os.environ.get("CADCOPILOT_MODEL_BASE_URL")
        model_name = args.model or os.environ.get("CADCOPILOT_MODEL")
        if not base_url or not model_name:
            print(
                json.dumps(
                    {
                        "error": (
                            "set --model-base-url and --model, or the "
                            "CADCOPILOT_MODEL_BASE_URL and CADCOPILOT_MODEL environment variables"
                        )
                    },
                    indent=2,
                )
            )
            return 2
        try:
            model = OpenAICompatibleClient(
                base_url=base_url,
                model=model_name,
                api_key=os.environ.get(args.api_key_env),
                timeout_seconds=args.timeout,
                response_format=args.response_format,
            )
            artifact = FreeCADMacroGenerator(model).generate(
                args.intent,
                freecad_version=args.freecad_version,
                policy_mode=args.policy,
            )
            destination = artifact.write(args.output, overwrite=args.overwrite)
        except (MacroGenerationError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, indent=2))
            return 2
        result = artifact.to_dict(include_code=False)
        result["output"] = str(destination)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "ask":
        base_url = args.model_base_url or os.environ.get("CADCOPILOT_MODEL_BASE_URL")
        model_name = args.model or os.environ.get("CADCOPILOT_MODEL")
        if not base_url or not model_name:
            print(
                json.dumps(
                    {
                        "error": (
                            "set --model-base-url and --model, or the "
                            "CADCOPILOT_MODEL_BASE_URL and CADCOPILOT_MODEL environment variables"
                        )
                    },
                    indent=2,
                )
            )
            return 2
        try:
            model = OpenAICompatibleClient(
                base_url=base_url,
                model=model_name,
                api_key=os.environ.get(args.api_key_env),
                timeout_seconds=args.timeout,
                response_format=args.response_format,
            )
            result = CopilotAgent(executor, CopilotPlanner(model)).run(
                intent=args.intent,
                adapter_id=args.adapter,
                document_id=args.document,
                apply=args.apply,
            )
        except (KeyError, ValueError, PlanningError) as exc:
            print(json.dumps({"error": str(exc)}, indent=2))
            return 2
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.receipt.accepted else 1

    try:
        payload = json.loads(args.plan.read_text(encoding="utf-8"))
        plan = ActionPlan.from_dict(payload)
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    receipt = executor.execute(args.adapter, plan, dry_run=args.dry_run)
    print(json.dumps(receipt.to_dict(), indent=2))
    return 0 if receipt.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
