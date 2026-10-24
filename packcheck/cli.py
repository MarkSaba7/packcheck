"""Command line entry point.

    python -m packcheck.cli demo
    python -m packcheck.cli inspect --product products/demo-dog-food-front/product.json --photo products/demo-dog-food-front/photo_bad.jpg
    python -m packcheck.cli add-user "Sam" --pin 1234        # from then on the pages ask for a login
    python -m packcheck.cli add-user "Sam" --pin 5678        # same name again = change Sam's PIN (signs Sam out everywhere)
    python -m packcheck.cli users
    python -m packcheck.cli remove-user "Sam"                # Sam can no longer sign in, and is signed out everywhere
"""

import argparse
import getpass
from pathlib import Path

from . import storage
from .demo import write_demo_product
from .pipeline import inspect_photo, new_run_dir
from .product import load_product

ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = ROOT / "packcheck.sqlite"


def main():
    parser = argparse.ArgumentParser(prog="packcheck")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("demo", help="(re)create the synthetic demo product in products/")

    inspect = commands.add_parser("inspect", help="compare one photo with one product")
    inspect.add_argument("--product", required=True, help="path to a product.json")
    inspect.add_argument("--photo", required=True, help="path to the photo")
    inspect.add_argument("--out", help="result folder (default: a new folder in runs/)")

    add_user = commands.add_parser("add-user", help="create a user, or change an existing user's PIN (once one exists, the pages require a login)")
    add_user.add_argument("name")
    add_user.add_argument("--pin", help="at least 4 characters; asked for if omitted")

    commands.add_parser("users", help="list the users")

    remove_user = commands.add_parser("remove-user", help="delete a user and sign them out everywhere")
    remove_user.add_argument("name")

    args = parser.parse_args()

    if args.command == "demo":
        folder = write_demo_product(ROOT / "products")
        print(f"Demo product written to {folder}")
        return

    if args.command == "add-user":
        pin = args.pin or getpass.getpass("PIN: ")
        with storage.connect(DATABASE_PATH) as connection:
            storage.add_user(connection, args.name, pin)
        print(f"User '{args.name}' saved. The pages now require a login.")
        return

    if args.command == "remove-user":
        with storage.connect(DATABASE_PATH) as connection:
            try:
                removed = storage.remove_user(connection, args.name)
            except ValueError as error:
                raise SystemExit(f"Not removed: {error}")
        if not removed:
            raise SystemExit(f"No user called '{args.name}'. See: python -m packcheck.cli users")
        print(f"User '{args.name}' removed and signed out everywhere.")
        return

    if args.command == "users":
        with storage.connect(DATABASE_PATH) as connection:
            for name in storage.list_users(connection):
                print(name)
        return

    product = load_product(args.product)
    out_dir = Path(args.out) if args.out else new_run_dir(ROOT / "runs", product.product_id)
    result = inspect_photo(args.photo, product, out_dir)

    print(f"{result['status']}: {result['message']}")
    for warning in result["warnings"]:
        print(f"  warning: {warning}")
    for difference in result["differences"]:
        print(f"  #{difference['number']}  {difference['kind']:8s} box {difference['rect_px']}  mean delta E {difference['mean_delta_e']}")
    if result["variable_zones"]:
        print(f"readings: {result['readings_status']}")
        for zone in result["variable_zones"]:
            print(f"  zone {zone['name']}: read {zone['text']!r}")
            for reading in zone["readings"]:
                verdict = {True: "valid", False: "INVALID", None: "check by hand"}[reading["valid"]]
                print(f"    {reading['name']:14s} {str(reading['text']):14s} {verdict}  {reading['reason']}")
    print(f"Results in {out_dir}")


if __name__ == "__main__":
    main()
