"""Turn a page of an artwork PDF into a PNG that can be used as a reference image.

    python scripts/render_artwork.py private/artwork.pdf --out private/artwork_page1.png
    python scripts/render_artwork.py private/artwork.pdf --page 2 --dpi 200 --out private/back.png

(Illustrator .ai files are usually PDF inside: try them as they are.)

This gives you the WHOLE artwork sheet: every face of the box unfolded, plus cut lines,
bleed and printer's marks. A reference image must be ONE face only, so afterwards crop the
face you want in any image editor, exactly along the fold/cut lines, and save it as
products/<product_id>/reference.png.

Colours: artwork is built for a press (CMYK / Pantone). The colours in this PNG are an
on-screen approximation, never an exact match for a correct printed pack. That is fine for
checking text and layout. It is the reason colour should later be judged against a photo
of a known-good pack instead (milestone 5).
"""

import argparse

import pypdfium2 as pdfium


def main():
    parser = argparse.ArgumentParser(description="Render one page of a PDF to a PNG.")
    parser.add_argument("pdf", help="path to the artwork PDF (keep it in private/, which git ignores)")
    parser.add_argument("--page", type=int, default=1, help="page number, starting at 1")
    parser.add_argument("--dpi", type=int, default=150, help="resolution; 150 is plenty for a whole face")
    parser.add_argument("--out", required=True, help="where to write the PNG")
    args = parser.parse_args()

    document = pdfium.PdfDocument(args.pdf)
    page = document[args.page - 1]
    image = page.render(scale=args.dpi / 72).to_pil()   # PDF units are 1/72 inch
    image.save(args.out)
    print(f"Wrote {args.out} ({image.width} x {image.height} px)")


if __name__ == "__main__":
    main()
