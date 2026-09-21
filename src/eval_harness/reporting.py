from __future__ import annotations

import html
import os
from pathlib import Path

from .models import RunManifest


def _flag(value: bool | None) -> str:
    if value is True:
        return "TRUE"
    if value is False:
        return "FALSE"
    return "PENDING"


def export_html(manifest: RunManifest, target: Path) -> Path:
    stage_blocks: list[str] = []
    for stage in manifest.stages:
        checks = "".join(
            f"<li><b>{html.escape(check.criterion_id)}</b> "
            f"<span class='status {html.escape(check.status.value.lower())}'>{html.escape(check.status.value)}</span> "
            f"{html.escape(check.title)}<small>{html.escape(check.reason)}</small></li>"
            for check in stage.checks
        )
        findings = "".join(
            f"<li><b>{html.escape(finding.finding_id)}</b> {html.escape(finding.title)}"
            f"<small>{html.escape(finding.reason)}</small></li>"
            for finding in stage.findings
        ) or "<li class='muted'>등록된 Finding 없음</li>"
        stage_blocks.append(
            f"""
            <section>
              <header><div><span class='step'>{stage.step:02d}</span><h2>{html.escape(stage.agent_name)}</h2></div>
              <span class='status {html.escape(stage.status.value.lower())}'>{html.escape(stage.status.value)}</span></header>
              <p>{html.escape(stage.agent_mode)} · {html.escape(stage.component_type)} · SUT EXECUTED: {_flag(stage.sut_executed)}</p>
              <p>{html.escape(stage.checkpoint_id)} · {html.escape(stage.checkpoint_status)}</p>
              <div class='cols'><div><h3>검사</h3><ul>{checks}</ul></div><div><h3>Finding</h3><ul>{findings}</ul></div></div>
            </section>
            """
        )

    decision = manifest.release_decision.value if manifest.release_decision else "PENDING"
    summary = manifest.summary
    document = f"""<!doctype html>
<html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{html.escape(manifest.run_id)} 평가 리포트</title><style>
:root{{--bg:#090e16;--panel:#101824;--line:#263449;--text:#f4f7fb;--muted:#9cb0c8;--blue:#58a6ff;--red:#ff6b6b;--green:#3ddc97;--amber:#f0b44d}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 system-ui,sans-serif}}
main{{width:min(1180px,calc(100% - 32px));margin:40px auto}} h1,h2,h3,p{{margin-top:0}} .hero{{display:flex;justify-content:space-between;gap:24px;border-bottom:1px solid var(--line);padding-bottom:24px;margin-bottom:18px}}
.decision{{font-size:28px;color:var(--amber)}} .truth,.summary{{display:grid;grid-template-columns:repeat(5,1fr);border:1px solid var(--line);border-radius:8px;background:var(--panel);margin:14px 0}} .truth div,.summary div{{padding:13px;border-right:1px solid var(--line);overflow-wrap:anywhere}} .truth div:last-child,.summary div:last-child{{border-right:0}} .truth small,.summary small{{display:block;color:var(--muted);font:10px monospace}} .truth b,.summary b{{color:var(--blue)}}
section{{border:1px solid var(--line);padding:24px;margin:14px 0;border-radius:8px;background:var(--panel)}} section header{{display:flex;justify-content:space-between;align-items:center;gap:18px}} section header div{{display:flex;gap:12px;align-items:center}} .step{{color:var(--blue);font-family:monospace}} .cols{{display:grid;grid-template-columns:1fr 1fr;gap:32px}} ul{{padding-left:20px}} li{{margin:10px 0}} small{{display:block;color:var(--muted)}} .status{{font:700 12px monospace;padding:4px 8px;border:1px solid var(--line);border-radius:5px}} .pass{{color:var(--green)}} .fail,.hold,.error,.blocked{{color:var(--red)}} .review,.abstain,.not_applicable,.human_review{{color:var(--amber)}} .muted,p{{color:var(--muted)}} .mono{{font-family:monospace}} @media(max-width:760px){{.hero,.cols{{display:block}}.truth,.summary{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main>
<div class='hero'><div><p>QA AGENT EVALUATION REPORT</p><h1>{html.escape(manifest.run_id)}</h1><p>{html.escape(manifest.source_name)} · {html.escape(manifest.execution_mode)} · {html.escape(manifest.started_at)}</p></div><div><p>최종 평가 판정</p><b class='decision'>{html.escape(decision)}</b></div></div>
<div class='truth'><div><small>SUITE</small><b>{html.escape(manifest.suite)}</b></div><div><small>SUT EXECUTED</small><b>{_flag(manifest.sut_executed)}</b></div><div><small>STABILITY EVALUATED</small><b>{_flag(manifest.stability_evaluated)}</b></div><div><small>SOURCE UNCHANGED</small><b>{_flag(manifest.source_unchanged)}</b></div><div><small>TRIAL</small><b>{manifest.trial_count}</b></div></div>
<div class='summary'><div><small>PASS</small><b>{summary.checks_passed}</b></div><div><small>FAIL</small><b>{summary.checks_failed}</b></div><div><small>ABSTAIN</small><b>{summary.checks_abstained}</b></div><div><small>N/A</small><b>{summary.checks_not_applicable}</b></div><div><small>FINDING</small><b>{summary.total_findings}</b></div></div>
<p class='mono'>SOURCE SHA-256 · {html.escape(manifest.source_hash_after or manifest.source_hash_before or 'PENDING')}</p>
{''.join(stage_blocks)}</main></body></html>"""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(document, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target