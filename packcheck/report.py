"""Milestone 3: the work-order report, as a printable HTML page.

A supervisor wants, at the end of a day or for one work order, a paper they can sign:
every pack inspected, the differences the worker CONFIRMED (with close-ups), the dates
and lot codes read. The browser's "print to PDF" is the PDF export; print CSS at the
bottom keeps each pack on one block and hides the buttons.

Why plain Python strings instead of a template engine (Jinja2): the report is one
function, the HTML is short, and `html.escape` is the only rule to remember (every value
that came from a user or a file goes through it). A template engine would be one more
thing to explain for no gain at this size.

Text comes from web/strings.json, so the report exists in every language the pages do.
"""

import json
from datetime import datetime
from html import escape
from pathlib import Path

STRINGS_PATH = Path(__file__).resolve().parent / "web" / "strings.json"
_strings = None


def text(language, key, **values):
    """One sentence from strings.json in the given language, falling back to English."""
    global _strings
    if _strings is None:
        _strings = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))
    table = _strings.get(language) or _strings["en"]
    sentence = table.get(key) or _strings["en"].get(key) or key
    for name, value in values.items():
        sentence = sentence.replace("{" + name + "}", str(value))
    return sentence


def render_report(inspections, day=None, work_order=None, language="en", runs_url="/runs"):
    """Build the whole report page. `inspections` come from storage.list_inspections."""
    t = lambda key, **values: escape(text(language, key, **values))   # noqa: E731  (short on purpose: used 40 times)

    scope = []
    if day:
        scope.append(f"{t('report.day')}: {escape(day)}")
    if work_order:
        scope.append(f"{t('report.work_order')}: {escape(work_order)}")

    parts = [
        "<!DOCTYPE html>",
        f'<html lang="{escape(language)}"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{t('report.title')}</title>",
        f"<style>{STYLE}</style></head><body><main>",
        f"<header><h1>{t('report.title')}</h1>",
        f"<p class='scope'>{' &middot; '.join(scope) if scope else ''}</p>",
        f"<p class='generated'>{t('report.generated')} {escape(datetime.now().strftime('%Y-%m-%d %H:%M'))}</p>",
        f"<button class='no-print' onclick='window.print()'>{t('report.print')}</button></header>",
        _summary(inspections, t),
    ]

    if not inspections:
        parts.append(f"<p>{t('report.no_inspections')}</p>")
    for group in _group_by_pack(inspections):
        parts.append("<section class='pack'>")
        for inspection in group:
            parts.append(_inspection_block(inspection, t, runs_url))
        parts.append("</section>")

    parts.append(
        "<footer class='signature'>"
        f"<div>{t('report.signature_checked_by')}: ____________________</div>"
        f"<div>{t('report.signature')}: ____________________</div>"
        f"<div>{t('report.signature_date')}: ______________</div>"
        "</footer></main></body></html>"
    )
    return "\n".join(parts)


def _summary(inspections, t):
    counts = {
        "report.packs_inspected": len(inspections),
        "report.with_confirmed": sum(_has_decision(item, "confirm") for item in inspections),
        "report.with_dismissed_only": sum(bool(item["differences"]) and not _has_decision(item, "confirm") and not _has_undecided(item) for item in inspections),
        "report.undecided": sum(_has_undecided(item) for item in inspections),
        "report.readings_invalid": sum(item["readings_status"] == "invalid" for item in inspections),
        "report.readings_check": sum(item["readings_status"] == "check_by_hand" for item in inspections),
        "report.alignment_failed": sum(item["status"] == "alignment_failed" for item in inspections),
    }
    rows = "".join(f"<tr><th>{t(key)}</th><td>{value}</td></tr>" for key, value in counts.items())
    return f"<table class='summary'>{rows}</table>"


def _has_decision(inspection, decision):
    return any(difference["decision"] and difference["decision"]["decision"] == decision for difference in inspection["differences"])


def _has_undecided(inspection):
    return any(difference["decision"] is None for difference in inspection["differences"])


def _group_by_pack(inspections):
    """Front and back of the same physical pack (same pack_id) are printed together."""
    groups, by_pack = [], {}
    for inspection in inspections:
        pack_id = inspection.get("pack_id")
        if pack_id and pack_id in by_pack:
            by_pack[pack_id].append(inspection)
        else:
            group = [inspection]
            groups.append(group)
            if pack_id:
                by_pack[pack_id] = group
    return groups


def _inspection_block(inspection, t, runs_url):
    run = escape(inspection["run"])
    status_key = f"report.status.{inspection['status']}"
    when = escape(inspection["created_at"].replace("T", " "))
    head = [
        f"<h2>{escape(inspection['product_name'])}</h2>",
        "<table class='facts'>",
        f"<tr><th>{t('report.time')}</th><td>{when}</td></tr>",
        f"<tr><th>{t('report.inspector')}</th><td>{escape(inspection.get('inspector') or '-')}</td></tr>",
        f"<tr><th>{t('report.work_order')}</th><td>{escape(inspection.get('work_order') or '-')}</td></tr>",
        f"<tr><th>{t('report.status')}</th><td class='status-{escape(inspection['status'])}'>{t(status_key)}",
    ]
    if inspection["status"] == "alignment_failed":
        head.append(f"<br><small>{escape(inspection['message'])}</small>")
    head.append("</td></tr></table>")

    parts = [f"<article class='inspection status-{escape(inspection['status'])}'>"] + head

    if inspection["differences"]:
        parts.append(f"<h3>{t('report.difference')}s</h3><table class='differences'><thead><tr>"
                     f"<th>#</th><th>{t('report.kind')}</th><th>&Delta;E</th><th>{t('report.decision')}</th><th>{t('report.note')}</th><th></th></tr></thead><tbody>")
        for difference in inspection["differences"]:
            decision = difference["decision"]
            if decision is None:
                decision_text, css = t("differences.undecided"), "undecided"
            elif decision["decision"] == "confirm":
                decision_text, css = t("differences.confirmed"), "confirmed"
            else:
                decision_text, css = t("differences.dismissed"), "dismissed"
            who = f" <small>({t('differences.by')} {escape(decision['decided_by'])})</small>" if decision and decision.get("decided_by") else ""
            note = escape(decision["note"]) if decision and decision.get("note") else ""
            kind = t("differences.content") if difference["kind"] == "content" else t("differences.colour")
            closeup = f"<img src='{runs_url}/{run}/{escape(difference['closeup'])}' alt='' loading='lazy'>" if difference.get("closeup") else ""
            parts.append(f"<tr class='{css}'><td>{difference['number']}</td><td>{kind}</td><td>{difference['mean_delta_e']}</td>"
                         f"<td>{decision_text}{who}</td><td>{note}</td><td class='closeup'>{closeup}</td></tr>")
        parts.append("</tbody></table>")

    readings = [reading for reading in inspection["readings"] if reading["name"]]
    if readings:
        parts.append(f"<h3>{t('report.readings')}</h3><table class='readings'><thead><tr>"
                     f"<th>{t('report.zone')}</th><th>{t('report.field')}</th><th>{t('report.read')}</th><th>{t('report.verdict')}</th><th>{t('report.reason')}</th></tr></thead><tbody>")
        for reading in readings:
            verdict = {True: ("readings.valid", "valid"), False: ("readings.invalid_short", "invalid"), None: ("readings.check", "check")}[reading["valid"]]
            parts.append(f"<tr class='{verdict[1]}'><td>{escape(reading['zone'])}</td><td>{escape(reading['name'])}</td>"
                         f"<td>{escape(reading['text'] or '-')}</td><td>{t(verdict[0])}</td><td>{escape(reading['reason'] or '')}</td></tr>")
        parts.append("</tbody></table>")
    elif inspection["readings"]:
        # Zones without rules: show the raw text so a person can check it.
        for reading in inspection["readings"]:
            parts.append(f"<p class='raw-reading'><b>{escape(reading['zone'])}</b>: {escape(reading['text'] or t('readings.nothing_read'))}</p>")

    parts.append(f"<p class='comparison-link no-print'><a href='{runs_url}/{run}/comparison.jpg' target='_blank' rel='noopener'>{t('report.comparison')}</a></p>")
    parts.append("</article>")
    return "".join(parts)


STYLE = """
  :root { --ink: #14283A; --rule: #C9D2D8; --magenta: #D6006F; --pass: #1F8A4C; --warn: #B8860B; }
  * { box-sizing: border-box; }
  body { margin: 0; color: var(--ink); background: #fff; font: 400 11pt/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 52rem; margin: 0 auto; padding: 1.5rem 1.25rem 3rem; }
  header { border-bottom: 2px solid var(--ink); padding-bottom: 0.75rem; margin-bottom: 1rem; position: relative; }
  h1 { margin: 0; font-size: 1.6rem; } h2 { margin: 0 0 0.5rem; font-size: 1.15rem; } h3 { margin: 1rem 0 0.25rem; font-size: 1rem; }
  .scope { margin: 0.25rem 0 0; font-weight: 600; } .generated { margin: 0; font-size: 0.85rem; opacity: 0.7; }
  header button { position: absolute; right: 0; top: 0; padding: 0.6rem 1rem; font: inherit; font-weight: 700; color: #fff; background: var(--ink); border: 0; cursor: pointer; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; vertical-align: top; padding: 0.3rem 0.5rem; border-bottom: 1px solid var(--rule); }
  table.summary { width: auto; margin-bottom: 1.5rem; } table.summary th { font-weight: 500; }
  table.summary td { font-weight: 700; text-align: right; }
  table.facts { width: auto; margin-bottom: 0.5rem; } table.facts th { font-weight: 500; opacity: 0.8; padding-left: 0; }
  .pack { margin-bottom: 1.5rem; }
  .inspection { border: 1px solid var(--rule); border-left: 6px solid var(--rule); padding: 0.75rem 1rem 0.5rem; margin-bottom: 0.75rem; break-inside: avoid; }
  .inspection.status-differences_found { border-left-color: var(--magenta); }
  .inspection.status-ok { border-left-color: var(--pass); }
  .inspection.status-alignment_failed { border-left-color: var(--warn); }
  td.status-differences_found { color: var(--magenta); font-weight: 700; } td.status-ok { color: var(--pass); font-weight: 700; }
  tr.confirmed td { font-weight: 600; } tr.dismissed td { opacity: 0.55; } tr.invalid td { color: var(--magenta); font-weight: 600; } tr.check td { color: var(--warn); }
  td.closeup img { display: block; max-width: 14rem; max-height: 6rem; border: 1px solid var(--rule); }
  .raw-reading { margin: 0.25rem 0; }
  .signature { display: flex; gap: 2rem; flex-wrap: wrap; margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid var(--ink); }
  @media print {
    @page { margin: 15mm; }
    .no-print { display: none !important; }
    main { max-width: none; padding: 0; }
    a { color: inherit; text-decoration: none; }
  }
"""
