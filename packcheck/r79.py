"""The R79 form, "Validation des matériaux imprimés à la réception", filled in for one pack.

The company checks every delivery of printed packaging on this paper form: a header (date,
label code, product code, product name), first delivery or new version, 12 points to tick
(trademark legible, ingredients exact...), accepted or refused, date and initials. This
module fills that form from ONE scan: the front and back photos of one pack.

How the 12 points are decided - honestly. PackCheck cannot read "is the trademark
legible?" by itself. What it CAN say is whether the pack matches the APPROVED artwork, and
every one of the 12 points is part of that artwork. So:

    ticked (yes)   both faces match the approved artwork, and every difference the
                   pipeline flagged was looked at by a person and dismissed
    crossed (no)   a person CONFIRMED a difference and said which point it breaks
                   (the desk screen asks, next to the Confirm button)
    left blank     not decided yet: a face is missing, could not be compared, or a
                   difference is still waiting for a person. The form says what is missing.

"Présence du code UPC" also uses the barcode read in the photos when the artwork has one
registered: a different code read is a cross. Not reading it is NOT a cross - the
comparison still saw the barcode printed in its place - the comment says it was not read.

fill_form() decides (plain data, easy to test); render_form() draws the page. Text comes
from web/strings.json like every other page, in French by default: it is the company's form.
"""

from html import escape

from .report import STYLE as REPORT_STYLE
from .report import text

# The 12 points of the paper form, in its order. The key is stored with a decision
# (storage.decisions.checklist_item); the words are in strings.json under "r79.item.<key>".
CHECKLIST = [
    "trademark",       # La marque de commerce lisible
    "product_name",    # Le nom du produit est lisible
    "description",     # Description lisible
    "company",         # Nom de la compagnie et adresse lisible
    "upc",             # Présence du code UPC
    "nutrition",       # Tableau de valeur nutritive exacte
    "ingredients",     # Liste d'ingrédients exacte
    "net_weight",      # Mention du poids net en gramme et/ou once
    "net_weight_same", # Les déclarations de poids net sont identiques
    "made_in_canada",  # Logo « Fabriqué au Canada »
    "registration",    # Numéro d'enregistrement
    "languages",       # Le tout est bilingue, trilingue ou multilingue
]


def fill_form(inspections, products, earlier_inspections=0):
    """Everything the form shows, for the inspections of ONE pack (storage.list_inspections
    with that pack_id). `products` maps product_id -> Product (for the codes and the name);
    `earlier_inspections` counts inspections of the same artwork BEFORE this pack: zero
    means this is its first delivery ("Première réception").

    Texts are returned as (strings.json key, values) pairs, so the same form can be drawn
    in any language.
    """
    by_face = {}
    for inspection in inspections:
        product = products.get(inspection["product_id"])
        by_face[product.face if product else inspection["product_id"]] = inspection
    first = inspections[0]
    product = products.get(first["product_id"])

    form = {
        "pack_id": first.get("pack_id"),
        "date": first["created_at"][:10],
        "label_code": (product.label_code if product else None) or "",
        "product_code": (product.pack if product else None) or first["product_id"],
        "product_name": _pack_name(product, first),
        "first_reception": earlier_inspections == 0,
        "inspector": first.get("inspector") or "",
        "initials": _initials(first.get("inspector") or ""),
        "faces": [(face, by_face[face]) for face in ("front", "back") if face in by_face],
        "missing": [],     # why the form cannot be concluded yet
        "remarks": [],     # what does not fit a single point
    }

    # Is the comparison complete? Both faces, both compared, every difference decided.
    for face in ("front", "back"):
        if face not in by_face:
            form["missing"].append(("r79.missing_face", {"face": face}))
        elif by_face[face]["status"] == "alignment_failed":
            form["missing"].append(("r79.face_not_compared", {"face": face}))
    undecided = sum(difference["decision"] is None for inspection in inspections for difference in inspection["differences"])
    if undecided:
        form["missing"].append(("r79.undecided", {"count": undecided}))
    complete = not form["missing"]

    # Confirmed differences, sorted onto the point the worker chose (or into the remarks).
    failing = {key: [] for key in CHECKLIST}
    for face, inspection in by_face.items():
        for difference in inspection["differences"]:
            decision = difference["decision"]
            if not decision or decision["decision"] != "confirm":
                continue
            comment = ("r79.difference_comment", {"face": face, "number": difference["number"], "note": decision.get("note") or ""})
            if decision.get("checklist_item") in failing:
                failing[decision["checklist_item"]].append(comment)
            else:
                form["remarks"].append(comment)

    items = []
    for key in CHECKLIST:
        state = False if failing[key] else (True if complete else None)
        items.append({"key": key, "state": state, "comments": list(failing[key])})

    # The barcode: compare what the photos read with what the artwork says, when it says one.
    expected = product.barcode if product else None
    if expected:
        read = sorted({code for inspection in inspections for code in (inspection.get("codes_read") or [])})
        upc = next(item for item in items if item["key"] == "upc")
        if expected in read:
            upc["comments"].append(("r79.upc_read", {"code": expected}))
        elif read:
            upc["state"] = False
            upc["comments"].append(("r79.upc_wrong", {"read": ", ".join(read), "expected": expected}))
        else:
            upc["comments"].append(("r79.upc_not_read", {}))

    # Dates and lots, when the pack has them: only a CONFIDENT wrong reading counts against it.
    for face, inspection in by_face.items():
        for reading in inspection["readings"]:
            if reading["name"] and reading["valid"] is False:
                form["remarks"].append(("r79.reading_invalid", {"face": face, "reason": reading["reason"] or reading["name"]}))
    readings_invalid = any(key == "r79.reading_invalid" for key, _ in form["remarks"])

    form["items"] = items
    confirmed_anywhere = any(item["state"] is False for item in items) or any(key == "r79.difference_comment" for key, _ in form["remarks"])
    if confirmed_anywhere or readings_invalid:
        form["verdict"] = "refused"
    elif complete and all(item["state"] is True for item in items):
        form["verdict"] = "accepted"
    else:
        form["verdict"] = "pending"
    return form


def _pack_name(product, inspection):
    name = product.name if product else inspection["product_name"]
    face = product.face if product else None
    return name.removesuffix(f" - {face}") if face else name


def _initials(name):
    return "".join(word[0] for word in name.split() if word).upper()


# ------------------------------------------------------------------- drawing

def render_form(form, language="fr", runs_url="/runs", print_on_load=False):
    """The filled form as a printable page, followed by an annex with the evidence."""
    def t(key, **values):
        return escape(text(language, key, **values))

    def say(pair):
        key, values = pair
        if "face" in values:
            values = {**values, "face": text(language, f"r79.face.{values['face']}")}
        return escape(text(language, key, **values))

    def box(ticked):
        return "&#9745;" if ticked else "&#9744;"   # ☑ / ☐: the same tick as the 12 points

    rows = []
    for item in form["items"]:
        mark = {True: "&#9745;", False: "&#9746;", None: "&#9744;"}[item["state"]]   # ☑ / ☒ / ☐
        css = {True: "yes", False: "no", None: "open"}[item["state"]]
        comments = "<br>".join(say(comment) for comment in item["comments"])
        if item["state"] is False and not comments:
            comments = t("r79.not_conform")
        rows.append(f"<tr class='{css}'><td class='mark'>{mark}</td><td>{t('r79.item.' + item['key'])}</td><td>{comments}</td></tr>")

    pending = ""
    if form["verdict"] == "pending":
        reasons = "".join(f"<li>{say(reason)}</li>" for reason in form["missing"]) or f"<li>{t('r79.points_open')}</li>"
        pending = f"<div class='pending'><b>{t('r79.pending_title')}</b><ul>{reasons}</ul></div>"
    remarks = ""
    if form["remarks"]:
        remarks = f"<p class='remarks'><b>{t('r79.remarks')}:</b> " + "; ".join(say(remark) for remark in form["remarks"]) + "</p>"

    parts = [
        "<!DOCTYPE html>",
        f'<html lang="{escape(language)}"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>R79 {escape(form['product_code'])} {escape(form['date'])}</title>",
        f"<style>{REPORT_STYLE}{STYLE}</style></head><body><main>",
        "<nav class='no-print'>"
        f"<button onclick='window.print()'>{t('r79.print')}</button>"
        f"<a href='?lang={'en' if language == 'fr' else 'fr'}'>{'English' if language == 'fr' else 'Français'}</a>"
        f"<a href='/history'>{t('r79.history')}</a><a href='/'>{t('r79.home')}</a></nav>",
        f"<h1 class='form-title'>{t('r79.title')}</h1>",
        "<table class='head'><tr>"
        f"<th>{t('r79.date')}</th><th>{t('r79.label_code')}</th><th>{t('r79.product_code')}</th><th>{t('r79.product_name')}</th></tr><tr>"
        f"<td>{escape(form['date'])}</td><td>{escape(form['label_code'])}</td><td>{escape(form['product_code'])}</td><td>{escape(form['product_name'])}</td></tr></table>",
        f"<p class='indicate'>{t('r79.indicate')}</p>",
        f"<p class='option'>{box(form['first_reception'])} <b>{t('r79.first_reception')}</b></p>",
        f"<p class='option'>{box(False)} <b>{t('r79.new_version')}</b></p>",
        f"<p class='instructions'>{t('r79.instructions')}</p>",
        f"<table class='checklist'><thead><tr><th></th><th>{t('r79.check')}</th><th>{t('r79.comments')}</th></tr></thead><tbody>",
        *rows,
        "</tbody></table>",
        remarks,
        pending,
        f"<p class='indicate'>{t('r79.accept_or_refuse')}</p>",
        f"<p class='option'>{box(form['verdict'] == 'accepted')} {t('r79.accepted')}</p>",
        f"<p class='option'>{box(form['verdict'] == 'refused')} {t('r79.refused')}</p>",
        f"<p class='instructions'><b>{t('r79.attach')}</b></p>",
        "<div class='sign'>"
        f"<div>{t('r79.signed_date')}: <span class='line'>{escape(form['date'])}</span></div>"
        f"<div>{t('r79.initials')}: <span class='line'>{escape(form['initials'])}</span><br><small>{t('r79.technician')}</small></div></div>",
        f"<p class='filled-by'>{t('r79.filled_by')}</p>",
        _annex(form, t, say, runs_url),
        "</main>",
    ]
    if print_on_load:
        parts.append("<script>window.addEventListener('load', () => window.print());</script>")
    parts.append("</body></html>")
    return "\n".join(part for part in parts if part)


def _annex(form, t, say, runs_url):
    """Page 2: the evidence behind every tick - the comparison images and each difference."""
    parts = [f"<section class='annex'><h2>{t('r79.annex')}</h2>"]
    for face_name, inspection in form["faces"]:
        face = say(("r79.face_heading", {"face": face_name, "name": inspection["product_name"]}))
        face = face[:1].upper() + face[1:]   # a heading: "Recto : ...", not "recto : ..."
        parts.append(f"<article class='inspection status-{escape(inspection['status'])}'><h3>{face}</h3>")
        parts.append(f"<p>{t('report.status.' + inspection['status'])}"
                     + (f" - {escape(inspection['message'])}" if inspection["status"] == "alignment_failed" else "") + "</p>")
        if inspection["status"] != "alignment_failed":
            parts.append(f"<img class='comparison' src='{runs_url}/{escape(inspection['run'])}/comparison.jpg' alt=''>")
        if inspection["differences"]:
            parts.append(f"<table class='differences'><thead><tr><th>#</th><th>{t('report.kind')}</th><th>{t('report.decision')}</th>"
                         f"<th>{t('r79.point')}</th><th>{t('report.note')}</th><th></th></tr></thead><tbody>")
            for difference in inspection["differences"]:
                decision = difference["decision"]
                decided = t("differences.undecided") if not decision else t("differences.confirmed" if decision["decision"] == "confirm" else "differences.dismissed")
                point = t("r79.item." + decision["checklist_item"]) if decision and decision.get("checklist_item") else ""
                note = escape(decision["note"]) if decision and decision.get("note") else ""
                kind = t("differences.content" if difference["kind"] == "content" else "differences.colour")
                closeup = f"<img src='{runs_url}/{escape(inspection['run'])}/{escape(difference['closeup'])}' alt=''>" if difference.get("closeup") else ""
                parts.append(f"<tr><td>{difference['number']}</td><td>{kind}</td><td>{decided}</td><td>{point}</td><td>{note}</td><td class='closeup'>{closeup}</td></tr>")
            parts.append("</tbody></table>")
        parts.append("</article>")
    parts.append("</section>")
    return "".join(parts)


STYLE = """
  nav { display: flex; gap: 0.75rem; align-items: center; margin-bottom: 1rem; }
  nav button { padding: 0.6rem 1rem; font: inherit; font-weight: 700; color: #fff; background: var(--ink); border: 0; cursor: pointer; }
  nav a { color: var(--ink); }
  .form-title { text-align: center; font-size: 1.35rem; margin: 0.5rem 0 1rem; }
  table.head th, table.head td { border: 1px solid var(--ink); text-align: center; }
  table.head td { height: 2.2rem; font-weight: 600; }
  .indicate { margin: 1rem 0 0.25rem; font-weight: 600; }
  .option { margin: 0.2rem 0 0.2rem 1.5rem; }
  .instructions { font-size: 0.9rem; margin: 0.75rem 0; }
  table.checklist th, table.checklist td { border: 1px solid var(--ink); }
  table.checklist td.mark { width: 2rem; text-align: center; font-size: 1.2rem; }
  table.checklist td:nth-child(2) { width: 45%; }
  tr.no td { color: var(--magenta); font-weight: 600; }
  tr.open td.mark { color: var(--warn); }
  .pending { margin: 0.75rem 0; padding: 0.5rem 0.75rem; border-left: 5px solid var(--warn); background: #FFF8E6; }
  .pending ul { margin: 0.25rem 0 0; }
  .remarks { margin: 0.75rem 0; }
  .sign { display: flex; justify-content: flex-end; gap: 3rem; margin-top: 1.5rem; }
  .sign .line { display: inline-block; min-width: 10rem; border-bottom: 1px solid var(--ink); }
  .filled-by { margin-top: 1.5rem; font-size: 0.8rem; opacity: 0.75; }
  .annex { break-before: page; margin-top: 2rem; }
  .annex h2 { font-size: 1.2rem; border-bottom: 2px solid var(--ink); }
  img.comparison { display: block; max-width: 100%; border: 1px solid var(--rule); margin: 0.5rem 0; }
"""
