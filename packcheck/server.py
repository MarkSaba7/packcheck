"""The web server: a JSON API plus the pages (home, desk, phone, artworks, reports).

    uvicorn packcheck.server:app --host 0.0.0.0 --port 8000

Then open http://<this computer's IP>:8000 on the computer next to the packs. The flow:

    home  --"Start scanning"-->  desk (QR code)  <--scanned by--  phone (front + back photos)
          --"Add an artwork"-->  artwork page (proof PDF -> front + back products)
    desk / history  --> one R79 form per scanned pack, to view or print

Pages and API are kept separate on purpose: every page only calls the API, so a future
native app could use exactly the same endpoints.

Pages
    GET  /                                home: Start scanning / Add an artwork
    GET  /desk                            the computer's scanning screen: QR code, results, next scan / report
    GET  /phone?session=...               the phone's page, opened from the desk's QR code: front + back, send
    GET  /history                         the packs scanned today / this week / this month
    GET  /artworks/new                    add an artwork from a printer's proof (draw front and back)
    GET  /products/{id}/zones             zone editor for one product
    GET  /report/pack/{pack_id}           the R79 form for one scanned pack (&print=1 opens the print dialog)
    GET  /report?day=YYYY-MM-DD           printable summary of a day (or ?work_order=..., &lang=fr)

API
    GET  /api/me                          {login_required, user}
    POST /api/login  {name, pin}          sets the login cookie
    POST /api/logout
    GET  /api/products                    products that can be checked (id, name, pack, face, barcode)
    GET  /api/products/{id}               one product with its zones
    GET  /api/products/{id}/reference.png the artwork (for the zone editor)
    GET  /api/products/{id}/markup.png    the proof markup mask, if the product came from a proof
    POST /api/proofs  file                a proof PDF / scan -> {proof_id, width, height}; kept in private/proofs/
    GET  /api/proofs/{id}/sheet.png       the rendered sheet
    POST /api/proofs/{id}/products        {pack, name, barcode?, label_code?, faces: [{face, rect}], allow_similar?}
                                          -> the front and back products (see proof.py); 409 if already known
    POST /api/codes  photo                the barcodes / QR codes readable in a photo
    POST /api/photos/check  photo         {ok, problems}: blurry / too dark / too bright (photo_quality.py).
                                          Only a warning for the phone page; /api/packs/inspect never uses it.
    PUT  /api/products/{id}/zones         save the zone list
    POST /api/products/lookup  photo      find products by the barcode / QR code in the photo
    POST /api/inspections  form           photo, product_id?, work_order?, inspector?, pack_id?, session_id?
                                          product_id omitted (or "auto"): identified FROM the photo (identify.py);
                                          if that is ambiguous, returns {status: identify_ambiguous|identify_failed,
                                          message, candidates} instead of running the inspection - resubmit the
                                          SAME photo with product_id set to what the worker picked.
    POST /api/packs/inspect  form         front, back, session_id?, inspector?, pack?: ONE pack, both faces.
                                          The pack is found from both photos (identify.identify_pack); if that
                                          is unclear: {status: identify_ambiguous|identify_failed, candidates},
                                          and the phone resends the same photos with pack set.
    GET  /api/packs?range=day|week|month  scanned packs with their R79 verdict, newest first
    GET  /api/packs/{pack_id}             one scanned pack: verdict, what is still missing, faces
    GET  /api/checklist                   the keys of the R79's 12 points (r79.CHECKLIST)
    GET  /api/inspections                 ?day= ?work_order= ?session= ?pack_id= ?after_id=
    POST /api/differences/{id}/decisions  {decision: confirm|dismiss, note?, checklist_item?}
    POST /api/differences/{id}/ignore-spot {note?}: dismiss it AND add an ignore zone there to its artwork
    POST /api/sessions                    {session_id}: a new desk session
    GET  /api/sessions/{id}               {session_id, connected, checking}: the desk polls it
    POST /api/sessions/{id}/connect       the phone says it opened the session's link
    GET  /api/server-url                  {url}: a LAN address a phone can actually reach (see local_network_ip)
    GET  /api/qr.png?text=...             a QR code image
    GET  /runs/<run>/<file>               result images written by the pipeline
    GET  /static/<file>                   strings.json and other page assets

Login: until a user is created (python -m packcheck.cli add-user NAME --pin 1234) anyone
can use the tool. Once users exist, inspections, decisions and zone edits need a login;
the login is a random ticket kept in a cookie and looked up in the database (no signing
library needed: the server simply remembers which tickets it handed out).
"""

import re
import shutil
import socket
import uuid
from datetime import date, timedelta
from pathlib import Path

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import proof, r79, storage
from .codes import decode_codes, make_qr_png
from .identify import identify_pack, identify_product, packs_with_front_and_back
from .photo_quality import check_photo
from .pipeline import inspect_photo, new_run_dir
from .product import list_products, save_zones
from .report import render_report

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_DIR = ROOT / "products"
RUNS_DIR = ROOT / "runs"
DATABASE_PATH = ROOT / "packcheck.sqlite"
PROOFS_DIR = ROOT / "private" / "proofs"   # uploaded proofs are company artwork: private/ is ignored by git
WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_PHOTO_BYTES = 25 * 1024 * 1024
MAX_PROOF_BYTES = 50 * 1024 * 1024
LOGIN_COOKIE = "packcheck_login"

RUNS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="PackCheck")


@app.middleware("http")
async def always_revalidate(request: Request, call_next):
    """Make browsers ask "has this changed?" before reusing a page, stylesheet or script.
    (The pages also link packcheck.css and common.js with "?v=2" - see common.js.)

    Why: without this header a browser may keep using its cached copy for days without
    asking. After an update that means the NEW page with the OLD stylesheet - a broken mix
    (it happened: the redesign showed up unstyled). "no-cache" does not mean "never cache":
    the browser keeps its copy and the server answers "304 not modified" in a few bytes when
    nothing changed. Inspection images in /runs/ never change once written, so they keep the
    default."""
    response = await call_next(request)
    if not request.url.path.startswith("/runs/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
app.mount("/runs", StaticFiles(directory=RUNS_DIR), name="runs")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ------------------------------------------------------------------ helpers

def database():
    """One database connection per request, closed afterwards (FastAPI dependency)."""
    connection = storage.connect(app.state.database_path if hasattr(app.state, "database_path") else DATABASE_PATH)
    try:
        yield connection
    finally:
        connection.close()


def products_dir():
    return app.state.products_dir if hasattr(app.state, "products_dir") else PRODUCTS_DIR


def runs_dir():
    return app.state.runs_dir if hasattr(app.state, "runs_dir") else RUNS_DIR


def proofs_dir():
    return app.state.proofs_dir if hasattr(app.state, "proofs_dir") else PROOFS_DIR


def proof_folder(proof_id):
    # The id is one the server made up (hex). Checking its shape means a crafted id such as
    # "../../products" can never point outside private/proofs/.
    if not re.fullmatch(r"[0-9a-f]{12}", proof_id) or not (proofs_dir() / proof_id).is_dir():
        raise HTTPException(status_code=404, detail="Unknown proof.")
    return proofs_dir() / proof_id


def current_user(request: Request, connection=Depends(database)):
    return storage.user_for_token(connection, request.cookies.get(LOGIN_COOKIE))


def require_user(request: Request, connection=Depends(database)):
    """The user name, or 401 when logins are required and nobody is signed in."""
    user = storage.user_for_token(connection, request.cookies.get(LOGIN_COOKIE))
    if user is None and storage.login_required(connection):
        raise HTTPException(status_code=401, detail="Please sign in first.")
    return user


def find_product(product_id):
    # Look the product up in our own list. We never build a file path from what the client
    # sent, so a made-up product_id cannot make us read files outside products/.
    for product in list_products(products_dir()):
        if product.product_id == product_id:
            return product
    raise HTTPException(status_code=404, detail=f"Unknown product: {product_id}")


def zone_entry(zone):
    """A Zone as it is written in product.json and sent to the pages."""
    return {"name": zone.name, "kind": zone.kind, "rect": list(zone.rect), "expect": zone.expect}


def product_summary(product):
    return {"product_id": product.product_id, "name": product.name, "pack": product.pack,
            "face": product.face, "barcode": product.barcode}


async def read_upload(upload):
    photo_bytes = await upload.read()
    if len(photo_bytes) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="The photo is larger than 25 MB.")
    return photo_bytes


# -------------------------------------------------------------------- pages

@app.get("/")
def home_page():
    return FileResponse(WEB_DIR / "home.html")


@app.get("/desk")
def desk_page():
    return FileResponse(WEB_DIR / "desk.html")


@app.get("/phone")
def phone_page():
    return FileResponse(WEB_DIR / "phone.html")


@app.get("/history")
def history_page():
    return FileResponse(WEB_DIR / "history.html")


@app.get("/artworks/new")
def new_artwork_page():
    return FileResponse(WEB_DIR / "artwork.html")


@app.get("/report/pack/{pack_id}", response_class=HTMLResponse)
def pack_report_page(pack_id: str, lang: str = "fr", print: bool = False, connection=Depends(database)):
    form = pack_form(connection, pack_id)
    return r79.render_form(form, language=lang, print_on_load=print)


@app.get("/products/{product_id}/zones")
def zones_page(product_id: str):
    find_product(product_id)
    return FileResponse(WEB_DIR / "zones.html")


@app.get("/report", response_class=HTMLResponse)
def report_page(day: str = None, work_order: str = None, lang: str = "en", connection=Depends(database)):
    if not day and not work_order:
        raise HTTPException(status_code=400, detail="Give ?day=YYYY-MM-DD or ?work_order=...")
    inspections = storage.list_inspections(connection, day=day, work_order=work_order)
    return render_report(inspections, day=day, work_order=work_order, language=lang)


# -------------------------------------------------------------------- login

class LoginBody(BaseModel):
    name: str
    pin: str


@app.get("/api/me")
def who_am_i(request: Request, connection=Depends(database)):
    return {"login_required": storage.login_required(connection),
            "user": storage.user_for_token(connection, request.cookies.get(LOGIN_COOKIE))}


@app.post("/api/login")
def log_in(body: LoginBody, connection=Depends(database)):
    token = storage.login(connection, body.name, body.pin)
    if token is None:
        raise HTTPException(status_code=401, detail="Name or PIN is wrong.")
    response = JSONResponse({"user": body.name.strip()})
    response.set_cookie(LOGIN_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


@app.post("/api/logout")
def log_out(request: Request, connection=Depends(database)):
    storage.logout(connection, request.cookies.get(LOGIN_COOKIE))
    response = JSONResponse({"user": None})
    response.delete_cookie(LOGIN_COOKIE)
    return response


# ----------------------------------------------------------------- products

@app.get("/api/products")
def get_products():
    return [product_summary(product) for product in list_products(products_dir())]


@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    product = find_product(product_id)
    zones = [zone_entry(zone) for zone in product.zones]
    return {**product_summary(product), "zones": zones, "compare_settings": product.compare_settings,
            "has_golden_photo": product.golden_path is not None, "has_markup": product.markup_path is not None}


@app.get("/api/products/{product_id}/reference.png")
def get_reference(product_id: str):
    return FileResponse(find_product(product_id).reference_path)


@app.get("/api/products/{product_id}/markup.png")
def get_markup(product_id: str):
    product = find_product(product_id)
    if product.markup_path is None:
        raise HTTPException(status_code=404, detail="This product has no markup mask.")
    return FileResponse(product.markup_path)


# ------------------------------------------------------------------- proofs

@app.post("/api/proofs")
async def upload_proof(file: UploadFile = File(...), user=Depends(require_user)):
    """Keep the proof and its rendered sheet in private/proofs/<id>/, so the page can show the
    sheet and the person can draw the faces on it."""
    file_bytes = await file.read()
    if len(file_bytes) > MAX_PROOF_BYTES:
        raise HTTPException(status_code=413, detail="The proof is larger than 50 MB.")
    try:
        sheet = proof.load_sheet(file_bytes)
    except Exception as error:   # pdfium raises its own error types for broken PDFs
        raise HTTPException(status_code=400, detail=f"Could not read that proof: {error}")
    proof_id = uuid.uuid4().hex[:12]
    folder = proofs_dir() / proof_id
    folder.mkdir(parents=True)
    (folder / ("proof.pdf" if file_bytes[:5] == b"%PDF-" else "proof.img")).write_bytes(file_bytes)
    (folder / "name.txt").write_text(Path(file.filename or "proof").name, encoding="utf-8")
    cv2.imwrite(str(folder / "sheet.png"), sheet)
    return {"proof_id": proof_id, "width": sheet.shape[1], "height": sheet.shape[0]}


@app.get("/api/proofs/{proof_id}/sheet.png")
def get_proof_sheet(proof_id: str):
    return FileResponse(proof_folder(proof_id) / "sheet.png")


class ProofFace(BaseModel):
    face: str           # "front", "back"...
    rect: list          # [x, y, w, h] as fractions of the sheet, drawn along the trim line


class ProofProductsBody(BaseModel):
    pack: str                   # the product code, groups the faces: "80520S"
    name: str                   # "Hagen Tropican Lifetime 2 mm"
    barcode: str | None = None       # typed, or read from a photo of the barcode (POST /api/codes)
    label_code: str | None = None    # "#Code étiquette" on the R79, if the company uses one
    faces: list[ProofFace]
    allow_similar: bool = False # a person checked: it only LOOKS like an existing artwork


@app.post("/api/proofs/{proof_id}/products")
def create_proof_products(proof_id: str, body: ProofProductsBody, user=Depends(require_user)):
    folder = proof_folder(proof_id)
    sheet = cv2.imread(str(folder / "sheet.png"), cv2.IMREAD_COLOR)
    source = (folder / "name.txt").read_text(encoding="utf-8")
    try:
        product_ids = proof.create_products(sheet, products_dir(), body.pack, body.name, [face.model_dump() for face in body.faces],
                                            source=source, barcode=body.barcode, label_code=body.label_code,
                                            allow_similar=body.allow_similar)
    except proof.DuplicateArtwork as error:
        raise HTTPException(status_code=409, detail={"message": str(error), "can_override": error.can_override})
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"product_ids": product_ids}


@app.post("/api/codes")
async def read_codes(photo: UploadFile = File(...)):
    """The barcodes in a photo - used by the artwork page's "scan the barcode" button."""
    image = cv2.imdecode(np.frombuffer(await read_upload(photo), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="That file is not a photo the server can read.")
    return {"codes": decode_codes(image)}


@app.post("/api/photos/check")
async def check_one_photo(photo: UploadFile = File(...)):
    """Is this photo blurry, too dark or too bright? The phone asks right after each photo,
    so a bad one is retaken on the spot. Only a warning: the limits are not tuned on real
    photos yet, so the worker may still send it (see photo_quality.py)."""
    image = cv2.imdecode(np.frombuffer(await read_upload(photo), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="That file is not a photo the server can read.")
    problems = check_photo(image)
    return {"ok": not problems, "problems": problems}


class ZonesBody(BaseModel):
    zones: list


@app.put("/api/products/{product_id}/zones")
def put_zones(product_id: str, body: ZonesBody, user=Depends(require_user)):
    product = find_product(product_id)
    try:
        saved = save_zones(product.json_path, body.zones)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"zones": [zone_entry(zone) for zone in saved.zones]}


@app.post("/api/products/lookup")
async def lookup_by_code(photo: UploadFile = File(...)):
    """Read the barcode / QR code in the photo and return the products it points to."""
    photo_bytes = await read_upload(photo)
    image = cv2.imdecode(np.frombuffer(photo_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="That file is not a photo the server can read.")
    codes = decode_codes(image)
    products = [product_summary(product) for product in list_products(products_dir()) if product.barcode in codes]
    return {"codes": codes, "products": products}


# -------------------------------------------------------------- inspections

@app.post("/api/inspections")
async def create_inspection(
    photo: UploadFile = File(...),
    product_id: str = Form(None),   # omitted, empty, or "auto": work it out from the photo (see below)
    work_order: str = Form(None),
    inspector: str = Form(None),
    pack_id: str = Form(None),
    session_id: str = Form(None),
    user=Depends(require_user),
    connection=Depends(database),
):
    if session_id and not storage.session_exists(connection, session_id):
        raise HTTPException(status_code=404, detail=f"Unknown session: {session_id}")

    if product_id and product_id != "auto":
        product = find_product(product_id)
        photo_bytes = await read_upload(photo)
    else:
        # No product chosen: work out which one this is FROM the photo, the same
        # never-guess rule as everywhere else in this project - an unclear photo asks a
        # person instead of silently picking the wrong one. See identify.py.
        photo_bytes = await read_upload(photo)
        image = cv2.imdecode(np.frombuffer(photo_bytes, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(status_code=400, detail="That file is not a photo the server can read. Use a JPEG or PNG.")
        identified = identify_product(image, list_products(products_dir()))
        if identified.product_id is None:
            return {
                "status": "identify_ambiguous" if identified.candidates else "identify_failed",
                "message": identified.message,
                "candidates": [{"product_id": c.product_id, "name": c.product_name} for c in identified.candidates],
            }
        product = find_product(identified.product_id)
        product_id = product.product_id

    run_dir = new_run_dir(runs_dir(), product_id)
    photo_path = run_dir / "photo.jpg"   # OpenCV reads by content, so the extension is only a label
    photo_path.write_bytes(photo_bytes)

    try:
        result = inspect_photo(photo_path, product, run_dir)
    except ValueError:                   # the upload was not a readable image
        shutil.rmtree(run_dir)           # nothing worth keeping from this run
        raise HTTPException(status_code=400, detail="That file is not a photo the server can read. Use a JPEG or PNG.")

    storage.save_inspection(connection, result, run_dir.name, work_order=work_order,
                            inspector=user or inspector, pack_id=pack_id, session_id=session_id)
    result["run"] = run_dir.name         # lets the page build URLs like /runs/<run>/comparison.jpg
    result["inspector"] = user or inspector
    return result


@app.get("/api/inspections")
def get_inspections(day: str = None, work_order: str = None, session: str = None, pack_id: str = None,
                    after_id: int = None, connection=Depends(database)):
    if not any((day, work_order, session, pack_id)):
        raise HTTPException(status_code=400, detail="Filter by day, work_order, session or pack_id.")
    return storage.list_inspections(connection, day=day, work_order=work_order, session_id=session,
                                    pack_id=pack_id, after_id=after_id)


# -------------------------------------------------------------------- packs

@app.post("/api/packs/inspect")
async def inspect_pack(
    front: UploadFile = File(...),
    back: UploadFile = File(...),
    session_id: str = Form(None),
    inspector: str = Form(None),
    pack: str = Form(None),         # set when the worker picked the pack after an unclear match
    user=Depends(require_user),
    connection=Depends(database),
):
    """One scan = one physical pack: the front photo and the back photo, checked together."""
    if session_id and not storage.session_exists(connection, session_id):
        raise HTTPException(status_code=404, detail=f"Unknown session: {session_id}")
    photos = [await read_upload(front), await read_upload(back)]
    images = [cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR) for photo in photos]
    if any(image is None for image in images):
        raise HTTPException(status_code=400, detail="A file is not a photo the server can read. Use a JPEG or PNG.")

    # Checking two photos takes several seconds. It runs in a worker thread so the server
    # keeps answering the desk screen meanwhile - which is how the desk can show "checking".
    if session_id:
        storage.set_session_checking(connection, session_id, True)
    try:
        return await run_in_threadpool(_inspect_pack, connection, photos, images, pack, session_id, user or inspector)
    finally:
        if session_id:
            storage.set_session_checking(connection, session_id, False)


def _inspect_pack(connection, photos, images, chosen_pack, session_id, inspector):
    products = list_products(products_dir())
    swapped = False
    if chosen_pack:
        packs = packs_with_front_and_back(products)
        if chosen_pack not in packs:
            raise HTTPException(status_code=404, detail=f"Unknown artwork: {chosen_pack}")
        front_product, back_product = packs[chosen_pack]
    else:
        found = identify_pack(images[0], images[1], products)
        if found.pack is None:
            return {"status": "identify_ambiguous" if found.candidates else "identify_failed", "message": found.message,
                    "candidates": [{"pack": candidate.pack, "name": candidate.name} for candidate in found.candidates]}
        front_product, back_product, swapped = found.pack.front, found.pack.back, found.swapped
    if swapped:
        photos, images = photos[::-1], images[::-1]

    pack_id = new_pack_id()
    faces = []
    for product, photo_bytes, image in ((front_product, photos[0], images[0]), (back_product, photos[1], images[1])):
        run_dir = new_run_dir(runs_dir(), product.product_id)
        photo_path = run_dir / "photo.jpg"
        photo_path.write_bytes(photo_bytes)
        result = inspect_photo(photo_path, product, run_dir)
        result["codes_read"] = decode_codes(image)   # for the R79's "Présence du code UPC"
        if swapped:
            result["warnings"].append("The front and back photos were the other way round; PackCheck swapped them.")
        storage.save_inspection(connection, result, run_dir.name, inspector=inspector, pack_id=pack_id, session_id=session_id)
        result["run"] = run_dir.name
        result["face"] = product.face
        faces.append(result)
    return {"status": "inspected", "pack_id": pack_id, "name": front_product.name.removesuffix(" - front"), "faces": faces}


def pack_form(connection, pack_id):
    """The filled R79 for one pack (see r79.fill_form)."""
    inspections = storage.list_inspections(connection, pack_id=pack_id)
    if not inspections:
        raise HTTPException(status_code=404, detail="Unknown scan.")
    products = {product.product_id: product for product in list_products(products_dir())}
    first = products.get(inspections[0]["product_id"])
    same_artwork = [p.product_id for p in products.values() if first and p.pack and p.pack == first.pack] or [inspections[0]["product_id"]]
    earlier = storage.count_earlier_inspections(connection, same_artwork, inspections[0]["id"])
    return r79.fill_form(inspections, products, earlier)


def pack_summary(connection, pack_id):
    form = pack_form(connection, pack_id)
    return {
        "pack_id": pack_id,
        "created_at": form["faces"][0][1]["created_at"] if form["faces"] else None,
        "product_name": form["product_name"],
        "product_code": form["product_code"],
        "inspector": form["inspector"],
        "verdict": form["verdict"],                 # accepted | refused | pending
        "missing": [[key, values] for key, values in form["missing"]],
        "faces": [{"face": face, "status": inspection["status"], "differences": len(inspection["differences"])}
                  for face, inspection in form["faces"]],
    }


HISTORY_DAYS = {"day": 0, "week": 6, "month": 29}   # "this week" = today and the 6 days before


@app.get("/api/packs")
def get_packs(range: str = "day", connection=Depends(database)):
    if range not in HISTORY_DAYS:
        raise HTTPException(status_code=400, detail="range must be day, week or month")
    since = (date.today() - timedelta(days=HISTORY_DAYS[range])).isoformat()
    return [pack_summary(connection, pack_id) for pack_id in storage.list_pack_ids(connection, since)]


@app.get("/api/packs/{pack_id}")
def get_pack(pack_id: str, connection=Depends(database)):
    return pack_summary(connection, pack_id)


@app.get("/api/checklist")
def get_checklist():
    return r79.CHECKLIST


class DecisionBody(BaseModel):
    decision: str      # "confirm" | "dismiss"
    # "str | None", not "str = None": the pages send null for an empty field, and pydantic
    # refuses null for a plain str (422) - which made Dismiss fail when no R79 point was chosen.
    note: str | None = None
    decided_by: str | None = None
    checklist_item: str | None = None   # which R79 point a confirmed difference breaks


@app.post("/api/differences/{difference_id}/decisions")
def post_decision(difference_id: int, body: DecisionBody, user=Depends(require_user), connection=Depends(database)):
    if body.checklist_item and body.checklist_item not in r79.CHECKLIST:
        raise HTTPException(status_code=400, detail=f"Unknown checklist point: {body.checklist_item}")
    try:
        return storage.save_decision(connection, difference_id, body.decision, body.note, user or body.decided_by, body.checklist_item)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No difference with id {difference_id}")


# How much bigger than the flagged box the new ignore zone is, on each side, as a fraction of
# the face. The next photo of the same artwork never lands on exactly the same pixels, so a
# zone the exact size of today's box would let tomorrow's box stick out of it by a pixel.
IGNORE_SPOT_MARGIN = 0.01


class IgnoreSpotBody(BaseModel):
    note: str | None = None


@app.post("/api/differences/{difference_id}/ignore-spot")
def ignore_spot(difference_id: int, body: IgnoreSpotBody = None, user=Depends(require_user), connection=Depends(database)):
    """"Always ignore this spot": dismiss this difference AND add an ignore zone over it to the
    artwork it was found on, so later scans of that artwork are not asked about it again.

    Why: on real proofs most false alarms come back on every scan in the same place (the
    approval ticks and signatures on the proof). Dismissing them one scan at a time would make
    nobody look at the list. The zone is an ordinary "ignore" zone, so it shows up in the zone
    editor (/products/<id>/zones), where a person can move or delete it.
    The price: a real defect in that exact spot will not be flagged either. The desk asks
    for confirmation and says so."""
    difference = storage.get_difference(connection, difference_id)
    if difference is None:
        raise HTTPException(status_code=404, detail=f"No difference with id {difference_id}")
    product = find_product(difference["product_id"])

    name = f"ignored spot {difference_id}"
    zones = [zone_entry(zone) for zone in product.zones]
    if all(zone["name"] != name for zone in zones):   # clicked twice: one zone is enough
        x, y, w, h = difference["rect_fraction"]
        left, top = max(0.0, x - IGNORE_SPOT_MARGIN), max(0.0, y - IGNORE_SPOT_MARGIN)
        right, bottom = min(1.0, x + w + IGNORE_SPOT_MARGIN), min(1.0, y + h + IGNORE_SPOT_MARGIN)
        zones.append({"name": name, "kind": "ignore", "rect": [round(left, 4), round(top, 4), round(right - left, 4), round(bottom - top, 4)]})
        save_zones(product.json_path, zones)

    note = (body.note if body else None) or "Always ignore this spot (ignore zone added to the artwork)"
    decision = storage.save_decision(connection, difference_id, "dismiss", note, user)
    return {"decision": decision, "zone": name, "product_id": product.product_id}


# ----------------------------------------------------------------- sessions

@app.post("/api/sessions")
def create_session(connection=Depends(database)):
    return {"session_id": storage.create_session(connection)}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str, connection=Depends(database)):
    session = storage.get_session(connection, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session.")
    return {"session_id": session_id, "connected": session["connected_at"] is not None, "checking": bool(session["checking"])}


@app.post("/api/sessions/{session_id}/connect")
def connect_session(session_id: str, connection=Depends(database)):
    """Called by the phone page as soon as it opens: the desk then says "phone connected"."""
    if not storage.session_exists(connection, session_id):
        raise HTTPException(status_code=404, detail="Unknown session. Scan the QR code on the computer again.")
    storage.mark_session_connected(connection, session_id)
    return {"session_id": session_id, "connected": True}


@app.get("/api/qr.png")
def qr_png(text: str):
    if not text or len(text) > 500:
        raise HTTPException(status_code=400, detail="text must be 1-500 characters")
    return Response(make_qr_png(text), media_type="image/png")


def new_pack_id():
    """Front and back photos of one pack share this; the phone page asks for one per pack."""
    return uuid.uuid4().hex[:12]


@app.post("/api/packs")
def create_pack():
    return {"pack_id": new_pack_id()}


def local_network_ip():
    """This machine's address on the LAN, or None if none could be found.

    Why this exists: a phone scanning a QR code needs an address IT can reach. If someone
    opened the desk or phone page as http://localhost:8000 (an easy thing to type, and what
    the README's quick start suggests when trying it out on the same computer), the browser's
    own `location.origin` is "localhost" - which on the PHONE means the phone itself, not
    this computer. Any QR/link built from that is unusable off this one machine.

    How: ask the operating system which network interface it would use to reach the outside
    world. This never actually sends anything - a UDP "connect" only sets a default
    destination in the kernel and looks up the routing table for it - so it works even with
    no internet access, as long as there is a LAN connection at all.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return None   # no network interface at all (e.g. a disconnected dev machine)
    finally:
        probe.close()


@app.get("/api/server-url")
def server_url(request: Request):
    """A URL phones on the same network can use to reach this server. Prefers the LAN
    address found above; falls back to whatever host the browser itself used (better than
    nothing if this machine truly has no other network path)."""
    host = local_network_ip() or request.url.hostname
    port = request.url.port
    return {"url": f"{request.url.scheme}://{host}" + (f":{port}" if port else "")}
