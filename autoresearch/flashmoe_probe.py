"""
Probe the local Flash-MoE Gemma 4 branch against the current GGUF and machine.

Usage:
    uv run flashmoe_probe.py
    uv run flashmoe_probe.py --banks 8 --banks 16 --banks 32
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
DEFAULT_FLASHMOE_ROOT = ROOT.parent / "anemll-flash-llama.cpp-gemma4"
DEFAULT_MODEL_PATH = ROOT.parent / "models" / "gemma-4-26B-A4B-it-Q4_K_M.gguf"
DEFAULT_OUTPUT_PATH = RESULTS_DIR / "flashmoe_probe_latest.json"
DEFAULT_SMOKE_ROOT = RESULTS_DIR / "flashmoe_smoke"
DEFAULT_BANKS = [8, 16, 32]
DEFAULT_MIN_FREE_GB = 4.0
DEFAULT_DISK_MARGIN_GB = 2.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--flashmoe-root",
        default=str(DEFAULT_FLASHMOE_ROOT),
        help="Path to the Anemll Gemma-4 Flash-MoE branch checkout.",
    )
    parser.add_argument(
        "--model",
        default=str(DEFAULT_MODEL_PATH),
        help="Path to the local GGUF model file.",
    )
    parser.add_argument(
        "--banks",
        action="append",
        type=int,
        default=[],
        help="Slot-bank sizes to estimate. Repeat for multiple values.",
    )
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=DEFAULT_MIN_FREE_GB,
        help="Comfort floor to reserve for other processes.",
    )
    parser.add_argument(
        "--disk-margin-gb",
        type=float,
        default=DEFAULT_DISK_MARGIN_GB,
        help="Extra free disk margin required beyond the sidecar payload.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Where to write the JSON probe artifact.",
    )
    parser.add_argument(
        "--smoke-layer",
        type=int,
        help="Optionally extract and verify one routed layer to prove the sidecar path on this exact GGUF.",
    )
    parser.add_argument(
        "--smoke-root",
        default=str(DEFAULT_SMOKE_ROOT),
        help="Where to write the optional smoke sidecar output.",
    )
    parser.add_argument(
        "--sidecar",
        help="Optional sidecar directory or manifest path to inspect for routed coverage.",
    )
    return parser.parse_args()


def gib(bytes_value: float) -> float:
    return bytes_value / float(1 << 30)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_machine_state(min_free_gb: float) -> dict[str, float]:
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    total_gb = gib(memory.total)
    available_gb = gib(memory.available)
    used_gb = gib(memory.total - memory.available)
    swap_used_gb = gib(swap.used)
    reserve_gb = used_gb + min_free_gb
    return {
        "total_gb": total_gb,
        "used_gb": used_gb,
        "available_gb": available_gb,
        "swap_used_gb": swap_used_gb,
        "launch_reserve_gb": reserve_gb,
        "min_free_gb": min_free_gb,
    }


def run_inspect(
    flashmoe_root: Path,
    model_path: Path,
    sidecar_path: Path | None = None,
) -> dict[str, Any]:
    tool_path = flashmoe_root / "tools" / "flashmoe-sidecar" / "flashmoe_sidecar.py"
    if not tool_path.exists():
        raise FileNotFoundError(f"Flash-MoE inspect tool not found: {tool_path}")

    command = [
        sys.executable,
        str(tool_path),
        "inspect",
        "--model",
        str(model_path),
        "--families",
        "routed",
        "--json",
    ]
    if sidecar_path is not None:
        command.extend(["--sidecar", str(sidecar_path)])
    completed = subprocess.run(
        command,
        cwd=flashmoe_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def run_smoke_extract_verify(
    flashmoe_root: Path,
    model_path: Path,
    smoke_root: Path,
    layer: int,
) -> dict[str, Any]:
    tool_path = flashmoe_root / "tools" / "flashmoe-sidecar" / "flashmoe_sidecar.py"
    out_dir = smoke_root / f"layer_{layer:03d}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)

    extract_command = [
        sys.executable,
        str(tool_path),
        "extract",
        "--model",
        str(model_path),
        "--layers",
        str(layer),
        "--families",
        "routed",
        "--out-dir",
        str(out_dir),
        "--force",
    ]
    verify_command = [
        sys.executable,
        str(tool_path),
        "verify",
        "--model",
        str(model_path),
        "--layers",
        str(layer),
        "--families",
        "routed",
        "--sidecar",
        str(out_dir),
    ]

    extract = subprocess.run(
        extract_command,
        cwd=flashmoe_root,
        capture_output=True,
        text=True,
        check=True,
    )
    verify = subprocess.run(
        verify_command,
        cwd=flashmoe_root,
        capture_output=True,
        text=True,
        check=True,
    )

    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "layer": layer,
        "out_dir": str(out_dir),
        "manifest_path": str(manifest_path),
        "entry_count": len(manifest["entries"]),
        "exact_bytes": sum(int(item["exact_byte_length"]) for item in manifest["entries"]),
        "extract_stdout": extract.stdout.strip(),
        "verify_stdout": verify.stdout.strip(),
    }


def build_report(
    inspect_data: dict[str, Any],
    model_path: Path,
    flashmoe_root: Path,
    banks: list[int],
    min_free_gb: float,
    disk_margin_gb: float,
    smoke_result: dict[str, Any] | None,
) -> dict[str, Any]:
    model_info = inspect_data["model"]
    gguf_info = inspect_data["gguf"]
    model_size_bytes = model_path.stat().st_size
    routed_bytes = int(gguf_info["total_bytes"])
    expert_count = int(model_info["expert_count"])
    expert_used_count = int(model_info["expert_used_count"])
    dense_shared_bytes = model_size_bytes - routed_bytes
    slot_bytes_all_layers = routed_bytes / float(expert_count)
    machine = current_machine_state(min_free_gb)
    disk = shutil.disk_usage(model_path.parent)
    disk_free_gb = gib(disk.free)
    sidecar_required_gb = gib(routed_bytes)
    sidecar_recommended_free_gb = sidecar_required_gb + disk_margin_gb
    sidecar_shortfall_gb = max(sidecar_required_gb - disk_free_gb, 0.0)
    sidecar_recommended_shortfall_gb = max(sidecar_recommended_free_gb - disk_free_gb, 0.0)

    bank_estimates: list[dict[str, Any]] = []
    for bank in sorted(set(banks)):
        bank_bytes = slot_bytes_all_layers * bank
        resident_bytes = dense_shared_bytes + bank_bytes
        bank_estimates.append(
            {
                "bank": bank,
                "slot_bank_gb": gib(bank_bytes),
                "resident_dense_shared_gb": gib(dense_shared_bytes),
                "resident_model_plus_bank_gb": gib(resident_bytes),
                "resident_plus_floor_gb": gib(resident_bytes) + min_free_gb,
                "resident_plus_floor_fraction_of_ram": (
                    (gib(resident_bytes) + min_free_gb) / machine["total_gb"]
                    if machine["total_gb"] > 0
                    else 0.0
                ),
                "native_topk_match": bank >= expert_used_count,
            }
        )

    report = {
        "updated_at": utc_now(),
        "flashmoe_root": str(flashmoe_root),
        "model_path": str(model_path),
        "model_arch": model_info["arch"],
        "expert_count": expert_count,
        "expert_used_count": expert_used_count,
        "model_size_gb": gib(model_size_bytes),
        "routed_expert_gb": gib(routed_bytes),
        "dense_shared_gb": gib(dense_shared_bytes),
        "slot_bytes_all_layers_gb": gib(slot_bytes_all_layers),
        "machine_state": machine,
        "disk_state": {
            "free_gb": disk_free_gb,
            "sidecar_required_gb": sidecar_required_gb,
            "sidecar_recommended_free_gb": sidecar_recommended_free_gb,
            "full_sidecar_extract_fits_now": disk_free_gb >= sidecar_required_gb,
            "full_sidecar_extract_recommended_now": disk_free_gb >= sidecar_recommended_free_gb,
            "shortfall_gb": sidecar_shortfall_gb,
            "recommended_shortfall_gb": sidecar_recommended_shortfall_gb,
            "disk_margin_gb": disk_margin_gb,
        },
        "routed_tensor_count": gguf_info["tensor_count"],
        "routed_by_family_gb": {
            family: gib(stats["bytes"]) for family, stats in gguf_info["by_family"].items()
        },
        "bank_estimates": bank_estimates,
        "smoke_result": smoke_result,
    }
    sidecar = inspect_data.get("sidecar")
    if isinstance(sidecar, dict):
        summary = sidecar.get("summary", {})
        missing = list(sidecar.get("missing_from_sidecar", []))
        extra = list(sidecar.get("extra_in_sidecar", []))
        report["sidecar_state"] = {
            "manifest_path": sidecar.get("manifest_path"),
            "tensor_count": int(summary.get("tensor_count", 0)),
            "total_bytes_gb": gib(int(summary.get("total_bytes", 0))),
            "missing_count": len(missing),
            "extra_count": len(extra),
            "full_routed_coverage": len(missing) == 0,
            "slot_bank_testable": len(missing) == 0,
            "resident_bank_testable": int(summary.get("tensor_count", 0)) > 0,
            "first_missing": missing[:5],
            "first_extra": extra[:5],
        }
    else:
        report["sidecar_state"] = None
    return report


def print_report(report: dict[str, Any], output_path: Path) -> None:
    machine = report["machine_state"]
    disk = report["disk_state"]

    print("Flash-MoE Gemma 4 probe")
    print(f"  Flash-MoE root:    {report['flashmoe_root']}")
    print(f"  Model:             {report['model_path']}")
    print(f"  Arch:              {report['model_arch']}")
    print(f"  Experts:           {report['expert_count']} total / {report['expert_used_count']} active")
    print(f"  Model size:        {report['model_size_gb']:.3f} GiB")
    print(f"  Routed experts:    {report['routed_expert_gb']:.3f} GiB")
    print(f"  Dense/shared:      {report['dense_shared_gb']:.3f} GiB")
    print(f"  One slot all layers:{report['slot_bytes_all_layers_gb']:.3f} GiB")
    print()
    print("Current machine state")
    print(f"  Used memory:       {machine['used_gb']:.2f} GiB / {machine['total_gb']:.2f} GiB")
    print(f"  Available memory:  {machine['available_gb']:.2f} GiB")
    print(f"  Swap used:         {machine['swap_used_gb']:.2f} GiB")
    print(f"  Launch reserve:    {machine['launch_reserve_gb']:.2f} GiB")
    print()
    print("Disk check")
    print(f"  Free disk:         {disk['free_gb']:.2f} GiB")
    print(f"  Sidecar payload:   {disk['sidecar_required_gb']:.2f} GiB")
    print(f"  Safer target:      {disk['sidecar_recommended_free_gb']:.2f} GiB")
    print(f"  Fits now:          {'yes' if disk['full_sidecar_extract_fits_now'] else 'no'}")
    print(f"  Recommended now:   {'yes' if disk['full_sidecar_extract_recommended_now'] else 'no'}")
    if not disk["full_sidecar_extract_fits_now"]:
        print(f"  Shortfall now:     {disk['shortfall_gb']:.2f} GiB")
    if not disk["full_sidecar_extract_recommended_now"]:
        print(f"  Shortfall safer:   {disk['recommended_shortfall_gb']:.2f} GiB")
    print()
    print("Bank estimates")
    for item in report["bank_estimates"]:
        print(
            "  "
            f"bank {item['bank']:>2}: "
            f"slot {item['slot_bank_gb']:.3f} GiB, "
            f"resident+bank {item['resident_model_plus_bank_gb']:.3f} GiB, "
            f"resident+floor {item['resident_plus_floor_gb']:.3f} GiB"
        )
    print()
    smoke = report.get("smoke_result")
    if smoke:
        print("Smoke check")
        print(f"  Layer:            {smoke['layer']}")
        print(f"  Entries:          {smoke['entry_count']}")
        print(f"  Exact bytes:      {gib(smoke['exact_bytes']):.3f} GiB")
        print(f"  Output dir:       {smoke['out_dir']}")
        print(f"  Verify:           {smoke['verify_stdout']}")
        print()
    sidecar = report.get("sidecar_state")
    if sidecar:
        print("Sidecar coverage")
        print(f"  Manifest:         {sidecar['manifest_path']}")
        print(f"  Tensors:          {sidecar['tensor_count']}")
        print(f"  Total bytes:      {sidecar['total_bytes_gb']:.3f} GiB")
        print(f"  Missing routed:   {sidecar['missing_count']}")
        print(f"  Extra routed:     {sidecar['extra_count']}")
        print(f"  Resident-bank:    {'yes' if sidecar['resident_bank_testable'] else 'no'}")
        print(f"  Slot-bank:        {'yes' if sidecar['slot_bank_testable'] else 'no'}")
        if sidecar["first_missing"]:
            print(f"  First missing:    {', '.join(sidecar['first_missing'])}")
        print()
    print(f"Artifact:           {output_path}")


def main() -> int:
    args = parse_args()
    flashmoe_root = Path(args.flashmoe_root).expanduser().resolve()
    model_path = Path(args.model).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    smoke_root = Path(args.smoke_root).expanduser().resolve()
    banks = args.banks or list(DEFAULT_BANKS)
    sidecar_path = Path(args.sidecar).expanduser().resolve() if args.sidecar else None

    inspect_data = run_inspect(flashmoe_root, model_path, sidecar_path=sidecar_path)
    smoke_result = None
    if args.smoke_layer is not None:
        smoke_result = run_smoke_extract_verify(
            flashmoe_root=flashmoe_root,
            model_path=model_path,
            smoke_root=smoke_root,
            layer=args.smoke_layer,
        )
    report = build_report(
        inspect_data=inspect_data,
        model_path=model_path,
        flashmoe_root=flashmoe_root,
        banks=banks,
        min_free_gb=args.min_free_gb,
        disk_margin_gb=args.disk_margin_gb,
        smoke_result=smoke_result,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_report(report, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
