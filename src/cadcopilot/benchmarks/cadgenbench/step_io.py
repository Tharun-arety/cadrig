"""STEP compatibility helpers exposed to CADGenBench editing agents."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any


def robust_export_step(shape: Any, destination: str | Path) -> str:
    """Atomically export with Build123d, falling back to direct OpenCascade transfer.

    The fallback preserves the supplied BREP as-is. It does not heal geometry or
    infer any edit operation.
    """
    from build123d import export_step

    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.{uuid.uuid4().hex}.tmp.step")
    try:
        try:
            export_step(shape, temporary)
            method = "build123d"
        except RuntimeError:
            temporary.unlink(missing_ok=True)
            _export_with_stepcontrol(shape, temporary)
            method = "ocp-stepcontrol"
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("STEP exporter did not produce a nonempty file")
        temporary.replace(destination)
        return method
    finally:
        temporary.unlink(missing_ok=True)


def _export_with_stepcontrol(shape: Any, destination: Path) -> None:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    wrapped = getattr(shape, "wrapped", shape)
    writer = STEPControl_Writer()
    if writer.Transfer(wrapped, STEPControl_AsIs) != IFSelect_RetDone:
        raise RuntimeError("OpenCascade could not transfer the shape to STEP")
    if writer.Write(str(destination)) != IFSelect_RetDone:
        raise RuntimeError("OpenCascade could not write the STEP file")
