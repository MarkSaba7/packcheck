"""The web server: a JSON API plus the pages (phone, desk, zone editor, report).

    uvicorn packcheck.server:app --host 0.0.0.0 --port 8000

Then open http://<this computer's IP>:8000 on a phone that is on the same Wi-Fi, or
http://<ip>:8000/desk on a computer screen next to the packs.

Pages and API are kept separate on purpose: every page only calls the API, so a future
native app could use exactly the same endpoints.

Pages
    GET  /                                the phone page
    GET  /desk                            desk screen: shows a QR code, lists what the paired phone checks
    GET  /products/{id}/zones             zone editor for one product
    GET  /report?day=YYYY-MM-DD           printable report (or ?work_order=..., &lang=fr)

API
    GET  /api/me                          {login_required, user}
    POST /api/login  {name, pin}          sets the login cookie
    POST /api/logout
    GET  /api/products                    products that can be checked (id, name, pack, face, barcode)
    GET  /api/products/{id}               one product with its zones
    GET  /api/products/{id}/reference.png the artwork (for the zone editor)
    PUT  /api/products/{id}/zones         save the zone list
    POST /api/products/lookup  photo      find products by the barcode / QR code in the photo
    POST /api/inspections  form           photo, product_id?, work_order?, inspector?, pack_id?, session_id?
                                          product_id omitted (or "auto"): identified FROM the photo (identify.py);
                                          if that is ambiguous, returns {status: identify_ambiguous|identify_failed,
                                          message, candidates} instead of running the inspection - resubmit the
                                          SAME photo with product_id set to what the worker picked.
    GET  /api/inspections                 ?day= ?work_order= ?session= ?pack_id= ?after_id=
    POST /api/differences/{id}/decisions  {decision: confirm|dismiss, note?}
    POST /api/sessions                    {session_id}: a new desk session
    GET  /api/sessions/{id}               does it exist?
    GET  /api/server-url                  {url}: a LAN address a phone can actually reach (see local_network_ip)
    GET  /api/qr.png?text=...             a QR code image
    GET  /runs/<run>/<file>               result images written by the pipeline
    GET  /static/<file>                   strings.json and other page assets

Login: until a user is created (python -m packcheck.cli add-user NAME --pin 1234) anyone
can use the tool. Once users exist, inspections, decisions and zone edits need a login;
the login is a random ticket kept in a cookie and looked up in the database (no signing
library needed: the server simply remembers which tickets it handed out).
"""

import shutil
import socket
import uuid
from pathlib import Path

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import storage
from .codes import decode_codes, make_qr_png
from .identify import identify_product
from .pipeline import inspect_photo, new_run_dir
from .product import list_products, save_zones
from .report import render_report

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_DIR = ROOT / "products"
RUNS_DIR = ROOT / "runs"
DATABASE_PATH = ROOT / "packcheck.sqlite"
WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_PHOTO_BYTES = 25 * 1024 * 1024
LOGIN_COOKIE = "packcheck_login"

RUNS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="PackCheck")
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
def phone_page():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/desk")
def desk_page():
    return FileResponse(WEB_DIR / "desk.html")


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
    zones = [{"name": zone.name, "kind": zone.kind, "rect": list(zone.rect), "expect": zone.expect} for zone in product.zones]
    return {**product_summary(product), "zones": zones, "compare_settings": product.compare_settings,
            "has_golden_photo": product.golden_path is not None}


@app.get("/api/products/{product_id}/reference.png")
def get_reference(product_id: str):
    return FileResponse(find_product(product_id).reference_path)


class ZonesBody(BaseModel):
    zones: list


@app.put("/api/products/{product_id}/zones")
def put_zones(product_id: str, body: ZonesBody, user=Depends(require_user)):
    product = find_product(product_id)
    try:
        saved = save_zones(product.json_path, body.zones)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"zones": [{"name": zone.name, "kind": zone.kind, "rect": list(zone.rect), "expect": zone.expect} for zone in saved.zones]}


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


class DecisionBody(BaseModel):
    decision: str      # "confirm" | "dismiss"
    note: str = None
    decided_by: str = None


@app.post("/api/differences/{difference_id}/decisions")
def post_decision(difference_id: int, body: DecisionBody, user=Depends(require_user), connection=Depends(database)):
    try:
        return storage.save_decision(connection, difference_id, body.decision, body.note, user or body.decided_by)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No difference with id {difference_id}")


# ----------------------------------------------------------------- sessions

@app.post("/api/sessions")
def create_session(connection=Depends(database)):
    return {"session_id": storage.create_session(connection)}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str, connection=Depends(database)):
    if not storage.session_exists(connection, session_id):
        raise HTTPException(status_code=404, detail="Unknown session.")
    return {"session_id": session_id}


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
