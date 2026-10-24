"""Re-judge selected roles of a stored full-flow trial from its evidence.zip.

The stored FLOW folder is read only. The archive hash, the run inventory and
the saved plan are checked before any packet is built; packets are rebuilt in
memory with the current collector, so the file-size limit of ``judge-openai``
(200KB packet files) does not apply, while the judge payload limit still does.
The default is a preview; execution needs the batch approval hash printed by
that preview and writes only to a new output folder.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from .full_flow import ROLES, FlowSpec, collect_packets
from .openai_judge import encode
from .claude_judge import MAX_CALLS as CLAUDE_MAX_CALLS, is_claude_model
from .judges import evaluate, preview as judge_preview
from .v2_recorded import digest, inventory, read, write

VERSION = "rejudge-flow-1.0"
MAX_ARCHIVE_BYTES = 500_000_000
MAX_MEMBERS = 50_000


def _trial(flow: Path, trial: int):
    flow = flow.resolve()
    record_path = flow / f"trial-{trial}" / "record.json"
    preview_path = flow / "preview.json"
    if not record_path.is_file() or not preview_path.is_file():
        raise ValueError("Stored trial record or preview is missing")
    record, info = read(record_path), read(preview_path)
    archive = flow / f"trial-{trial}" / "evidence.zip"
    if not archive.is_file() or record.get("evidence_archive_sha256") != digest(archive):
        raise ValueError("Evidence archive missing or hash differs from the trial record")
    if not re.fullmatch(r"RUN-[0-9A-Za-z-]{1,60}", str(record.get("run_id") or "")):
        raise ValueError("Trial record has no Run ID")
    return record, info["plan"], archive


def _extract(archive: Path, destination: Path):
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > MAX_MEMBERS or sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
            raise ValueError("Evidence archive exceeds the extraction budget")
        root = destination.resolve()
        for member in members:
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(root) or member.filename.startswith("/"):
                raise ValueError("Evidence archive entry escapes the workspace")
        bundle.extractall(destination)


def _packets(flow: Path, trial: int, roles, work: Path):
    record, plan, archive = _trial(flow, trial)
    _extract(archive, work)
    run = work / "p1" / "runs" / record["run_id"]
    if inventory(run) != record.get("artifact_hashes"):
        raise ValueError("Extracted Run differs from the recorded artifact hashes")
    spec = FlowSpec.model_validate(plan["spec"])
    packets = collect_packets(run, work, spec, plan, record.get("checks") or [])
    missing = [r for r in roles if r not in packets]
    return record, archive, spec, {r: packets[r] for r in roles if r in packets}, missing


def make_preview(flow: Path, trial: int, roles, model: str | None = None):
    roles = [r for r in ROLES if r in set(roles)]
    if not roles:
        raise ValueError("Select at least one role")
    work = Path(tempfile.mkdtemp(prefix="qaf-rejudge-"))
    try:
        record, archive, spec, packets, missing = _packets(flow, trial, roles, work)
        model = model or spec.judge_model
        rows = {}
        for role, packet in packets.items():
            try:
                info = judge_preview(packet, model)
                rows[role] = dict(status="READY", approval_sha256=info["approval_sha256"],
                                  payload_bytes=info["payload_bytes"],
                                  payload_limit_bytes=info["payload_limit_bytes"],
                                  rubric_items=len(packet.rubric))
            except ValueError as exc:
                # e.g. payload over the judge limit: reported, never trimmed silently.
                rows[role] = dict(status="BLOCKED", reason=str(exc)[:300])
        for role in missing:
            rows[role] = dict(status="NOT_AVAILABLE", reason="No packet for this role in the stored trial")
        binding = dict(version=VERSION, flow=flow.resolve().name, trial=trial, model=model,
                       evidence_archive_sha256=digest(archive),
                       roles={r: v.get("approval_sha256") for r, v in rows.items()})
        return dict(version=VERSION, flow=flow.resolve().name, trial=trial, run_id=record["run_id"],
                    model=model, roles=rows,
                    maximum_api_attempts=sum(v["status"] == "READY" for v in rows.values())
                    * (CLAUDE_MAX_CALLS if is_claude_model(model) else 1),
                    executable=all(v["status"] == "READY" for v in rows.values()),
                    original_scores_unchanged=True,
                    note="미리보기는 호출하지 않습니다. 실행 결과는 새 출력 폴더에만 쓰며 원래 FLOW 기록과 점수는 바꾸지 않습니다.",
                    approval_sha256=sha256(encode(binding).encode("utf-8")).hexdigest())
    finally:
        shutil.rmtree(work, ignore_errors=True)


def execute(flow: Path, trial: int, roles, approval_sha256: str, output_root: Path,
            model: str | None = None, *, client_factory=None) -> Path:
    info = make_preview(flow, trial, roles, model)
    if approval_sha256 != info["approval_sha256"]:
        raise ValueError("Execution requires approval bound to this exact preview")
    if not info["executable"]:
        raise ValueError("Some selected roles are not ready; see the preview")
    flow, output_root = flow.resolve(), output_root.resolve()
    if output_root.is_relative_to(flow):
        raise ValueError("Output must not be inside the stored FLOW folder")
    out = output_root / ("REJUDGE-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + info["flow"]
                         + f"-trial-{trial}")
    out.mkdir(parents=True, exist_ok=False)
    write(out / "preview.json", info)
    summary = dict(info, status="RUNNING", results={})
    work = Path(tempfile.mkdtemp(prefix="qaf-rejudge-"))
    try:
        _, _, _, packets, _ = _packets(flow, trial, list(info["roles"]), work)
        for role, packet in packets.items():
            row = info["roles"][role]
            try:
                fresh = judge_preview(packet, info["model"])
                if fresh["approval_sha256"] != row["approval_sha256"]:
                    raise ValueError("Packet changed after preview")
                judged = evaluate(packet, info["model"], row["approval_sha256"], out / role,
                                  client_factory=client_factory)
                verdict = read(judged / "result.json")
                summary["results"][role] = dict(status=verdict.get("status"), assessment=verdict.get("assessment"),
                                                output=judged.relative_to(out).as_posix())
                if verdict.get("status") == "ERROR":
                    break
            except Exception as exc:
                summary["results"][role] = dict(status="ERROR", reason=type(exc).__name__)
                break  # stop the batch: no further paid calls after an error
            finally:
                write(out / "summary.json", summary)
        summary["status"] = ("ERROR" if any(r["status"] == "ERROR" for r in summary["results"].values())
                             else "COMPLETED")
    except Exception as exc:
        summary.update(status="ERROR", reason=type(exc).__name__)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
        write(out / "summary.json", summary)
    return out
