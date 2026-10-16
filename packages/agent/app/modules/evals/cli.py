"""CLI for eval operations — local equivalent of the HTTP trigger.

Subcommands:
  upload-dataset   Push cases from a local JSON file to a LangSmith dataset.
  run              Run a dataset through the agent and score via the default judges.
  list-runs        Show recent LangSmith experiments for a dataset.
  open             Print/open the LangSmith URL for an experiment.

Day-to-day usage example:
  python -m app.modules.evals.cli upload-dataset \\
      --name web_agent_v1 \\
      --path packages/agent/eval/datasets/web_agent_v1.json

  python -m app.modules.evals.cli run --dataset web_agent_v1 --experiment my-tweak

Each ``run`` ends by printing the LangSmith experiment URL so you can click
into the comparison view immediately.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import webbrowser
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# upload-dataset
# ---------------------------------------------------------------------------


def _cmd_upload_dataset(args: argparse.Namespace) -> int:
    from app.modules.evals.dataset import load_cases, upload_to_langsmith

    version, cases = load_cases(args.path)
    print(f"Loaded {len(cases)} cases from {args.path} (version={version})")
    if args.dry_run:
        print("--dry-run: not uploading")
        return 0
    ds_id = upload_to_langsmith(args.name, cases, description=args.description or "")
    print(f"Uploaded to LangSmith dataset {args.name!r} (id={ds_id})")
    return 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


async def _run_async(args: argparse.Namespace) -> int:
    from app.modules.evals.runner import run_dataset

    if args.no_judges:
        evaluators: list | None = []
    elif args.routing_only:
        from app.modules.evals.judges import routing_judges

        evaluators = routing_judges()  # trajectory only — skill routing datasets
    else:
        evaluators = None  # None → default_judges()
    metadata: dict = {}
    if args.git_sha:
        metadata["git_sha"] = args.git_sha
    if args.note:
        metadata["note"] = args.note

    result = await run_dataset(
        args.dataset,
        experiment_prefix=args.experiment,
        evaluators=evaluators,
        metadata=metadata,
        is_preview=args.preview,
        max_concurrency=args.concurrency,
        explain_tool_choice=args.explain_tool_choice,
    )
    experiment_name = getattr(result, "experiment_name", None) or args.experiment
    print(f"\nEval run finished. Experiment: {experiment_name}")
    url = _experiment_url(experiment_name)
    if url:
        print(f"LangSmith URL: {url}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    return asyncio.run(_run_async(args))


# ---------------------------------------------------------------------------
# list-runs
# ---------------------------------------------------------------------------


def _cmd_list_runs(args: argparse.Namespace) -> int:
    from langsmith import Client

    client = Client()
    # ``list_projects`` is the closest API for "experiments matching a dataset".
    # LangSmith experiments belong to a session/project; we filter by name prefix.
    seen = 0
    for project in client.list_projects(name_contains=args.dataset):
        if args.limit and seen >= args.limit:
            break
        ts = getattr(project, "start_time", "")
        print(f"  {project.name:<60} {ts}")
        seen += 1
    if not seen:
        print(f"(no experiments matching dataset={args.dataset!r})")
    return 0


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------


def _cmd_open(args: argparse.Namespace) -> int:
    url = _experiment_url(args.experiment)
    if not url:
        print("Could not compute experiment URL (is LANGSMITH_ENDPOINT set?)", file=sys.stderr)
        return 1
    print(url)
    if not args.no_browser:
        webbrowser.open(url)
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _experiment_url(experiment_name: str) -> Optional[str]:
    """Best-effort search URL for a named experiment under the configured project."""
    try:
        from app.core.config import app_settings

        endpoint = (app_settings.LANGSMITH_ENDPOINT or "").rstrip("/")
        if not endpoint:
            return None
        web = endpoint.replace("api.smith.langchain.com", "smith.langchain.com")
        project = app_settings.LANGSMITH_PROJECT
        return f"{web}/o/-/projects/p/{project}/experiments?search={experiment_name}"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evals", description="Web-agent eval CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # upload-dataset
    p_up = sub.add_parser("upload-dataset", help="Upload cases from JSON to LangSmith")
    p_up.add_argument("--name", required=True, help="LangSmith dataset name")
    p_up.add_argument("--path", required=True, type=Path, help="Path to cases JSON file")
    p_up.add_argument("--description", default="", help="Optional dataset description")
    p_up.add_argument("--dry-run", action="store_true", help="Parse + validate, don't upload")
    p_up.set_defaults(func=_cmd_upload_dataset)

    # run
    p_run = sub.add_parser("run", help="Run a dataset through the agent + judges")
    p_run.add_argument("--dataset", required=True, help="LangSmith dataset name")
    p_run.add_argument("--experiment", default="eval", help="Experiment name prefix")
    p_run.add_argument("--no-judges", action="store_true", help="Skip RAGAS/programmatic scoring")
    p_run.add_argument("--routing-only", action="store_true",
                       help="Score with the trajectory judge alone (tool-routing skill datasets)")
    p_run.add_argument("--preview", action="store_true",
                       help="Pass is_preview=True (skips DB writes, hermetic)")
    p_run.add_argument("--explain-tool-choice", action="store_true",
                       help="Add tool_selection_reasoning + prompt_rule_followed columns "
                            "(asks the agent's own model why it chose each tool; +1 call/case)")
    p_run.add_argument("--concurrency", type=int, default=2, help="Max parallel cases")
    p_run.add_argument("--git-sha", default="", help="Stamp experiment metadata with this git_sha")
    p_run.add_argument("--note", default="", help="Free-form note metadata for the run")
    p_run.set_defaults(func=_cmd_run)

    # list-runs
    p_list = sub.add_parser("list-runs", help="List recent experiments for a dataset")
    p_list.add_argument("--dataset", required=True)
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=_cmd_list_runs)

    # open
    p_open = sub.add_parser("open", help="Print/open the LangSmith URL for an experiment")
    p_open.add_argument("experiment", help="Experiment name (or prefix)")
    p_open.add_argument("--no-browser", action="store_true",
                        help="Print URL only — don't launch a browser")
    p_open.set_defaults(func=_cmd_open)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
