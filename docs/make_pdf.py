"""Render the BRD and HLD artifact pages to print-quality PDFs.

The artifact runtime supplies mermaid at view time; a standalone file has to bring its
own, so a vendored bundle is inlined and initialised before printing. Edge in headless
mode does the rendering, which means the diagrams are real vector output rather than
screenshots.

Page numbers are stamped afterwards with reportlab: Chrome's own header/footer would put
a file:// path on every page, and CSS margin-box counters are not supported.

**On the vendored bundle.** ``mermaid.min.js`` is checked in beside this script rather
than fetched at build time. Two reasons, and the second is the real one:

* The build has to work on a machine with no route to a CDN, which is the normal
  condition inside a bank's network.
* This is 3.3 MB of third-party JavaScript being inlined into documents that go to a
  regulator. Its version is pinned and its SHA-256 is asserted on every run, so a
  substituted or truncated bundle stops the build instead of quietly rendering
  something else. ``--fetch`` re-downloads it and verifies the same digest.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
EDGE = pathlib.Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
MERMAID = HERE / "mermaid.min.js"

#: Pinned deliberately. A floating "latest" would change diagram rendering between two
#: builds of the same document, and the second one would be unexplainable.
MERMAID_VERSION = "10.9.1"
MERMAID_URL = (f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}"
               "/dist/mermaid.min.js")
MERMAID_SHA256 = "61b335a46df05a7ce1c98378f60e5f3e77a7fb608a1056997e8a649304a936d6"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_mermaid() -> None:
    """Download the pinned bundle and verify it before writing it to disk."""
    import urllib.request

    print(f"fetching mermaid {MERMAID_VERSION} …")
    with urllib.request.urlopen(MERMAID_URL, timeout=120) as resp:
        data = resp.read()
    got = _digest(data)
    if got != MERMAID_SHA256:
        # Written nowhere. A bundle that fails its digest is not saved for someone to
        # find later and assume is good.
        raise SystemExit(
            f"Refusing to save mermaid {MERMAID_VERSION}: expected SHA-256\n"
            f"  {MERMAID_SHA256}\nbut the download was\n  {got}\n"
            "Either the CDN served something else or the pin is stale.")
    MERMAID.write_bytes(data)
    print(f"wrote {MERMAID} ({len(data):,} bytes, digest verified)")


def require_mermaid() -> str:
    """Return the bundle's source, refusing anything that is not the pinned build."""
    if not MERMAID.exists():
        raise SystemExit(
            f"{MERMAID.name} is missing. It is vendored beside this script so the build "
            f"works without network access.\n"
            f"  Restore it with:  python make_pdf.py --fetch\n"
            f"  Or download {MERMAID_URL}")
    data = MERMAID.read_bytes()
    got = _digest(data)
    if got != MERMAID_SHA256:
        raise SystemExit(
            f"{MERMAID.name} does not match the pinned build.\n"
            f"  expected {MERMAID_SHA256}\n  found    {got}\n"
            f"This bundle is inlined into documents that leave the building, so the "
            f"build stops rather than rendering with an unknown version.\n"
            f"  Re-fetch with:  python make_pdf.py --fetch")
    return data.decode("utf-8")


PRINT_CSS = """
@page { size: A4; margin: 15mm 13mm 17mm; }

html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-size: 9.6pt; line-height: 1.5; background: #fff; }

/* The screen layout is a sticky sidebar beside the text. On paper the contents
   becomes a front matter page and the body runs full width. */
.wrap { display: block; max-width: none; padding: 0; }
/* A document opens with its title, not its index. The nav is moved after the masthead
   when the printable copy is assembled - see reorder_for_print. */
header.doc { break-after: page; border-bottom: none; padding-bottom: 0; }
nav.toc { position: static; max-height: none; padding: 0; margin: 0;
          break-after: page; }
nav.toc h2 { font-size: 9pt; margin-bottom: 12pt; }
nav.toc ol { display: block; columns: 2; column-gap: 26pt; }
nav.toc a { color: #101820; padding: 3pt 0; }
nav.toc a:hover { background: none; }

header.doc { padding: 0 0 14pt; }
header.doc h1 { font-size: 27pt; }
.standfirst { font-size: 11pt; }
.control { break-inside: avoid; }

main { padding-top: 0; }
/* Screen spacing is generous because scrolling is free. On paper the same values cost
   whole pages: section 2 missed fitting on its page by about ten points, which reads as
   a half-empty sheet rather than as breathing room. */
section { padding-top: 15pt; break-inside: auto; }
.diagram { margin: 10pt 0 15pt; }
.tw { margin: 11pt 0 15pt; }
.note { margin: 12pt 0; }
section > h2 { font-size: 15.5pt; break-after: avoid; }
/* break-after on the lede as well as the heading. Without it the break is merely
   disallowed between h2 and lede, so when the figure below them will not fit, the figure
   alone moves and the heading is stranded above half a blank page. With it the whole
   opening group travels together and the page instead ends after the previous section. */
section > h2 + .lede { break-before: avoid; break-after: avoid; }
h3, h4 { break-after: avoid; }
h3 + p, h4 + p, h3 + .tw, h4 + .tw { break-before: avoid; }
p, li { orphans: 3; widows: 3; }

/* A row split across a page break is unreadable in a requirements table; a repeated
   header row is what makes a long one usable. */
.tw { overflow: visible; box-shadow: none; break-inside: auto; }
table { font-size: 8.3pt; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
th { padding: 6pt 8pt; }
td { padding: 5.5pt 8pt; }
td.id, code, .mono { font-size: 7.8pt; }

.note, .diagram, .score { break-inside: avoid; box-shadow: none; }
.diagram { overflow: visible; padding: 9pt; }
/* A diagram is never split across a page, so one taller than the space remaining jumps
   to the next page and strands its heading above half a blank sheet. Capping the height
   means it always fits somewhere sensible; the SVG letterboxes rather than distorting,
   and the cost is a little scale on the tallest graph instead of a wasted page. */
.diagram svg { max-width: 100%; width: auto; height: auto; max-height: 142mm; }
.score { grid-template-columns: repeat(5, 1fr); }
.score .n { font-size: 19pt; }

footer { break-inside: avoid; margin-top: 26pt; }

/* Leave room for the stamped footer. */
body { padding-bottom: 4mm; }
"""

BOOT = """
<script>%(mermaid)s</script>
<script>
  // Light theme for print regardless of the viewer's OS preference.
  document.documentElement.setAttribute('data-theme', 'light');
  mermaid.initialize({
    startOnLoad: false, theme: 'base', securityLevel: 'loose',
    fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
    themeVariables: {
      primaryColor: '#E3EFEF', primaryTextColor: '#101820',
      primaryBorderColor: '#16565C', lineColor: '#5C7376',
      secondaryColor: '#F7F9F9', tertiaryColor: '#FFFFFF',
      fontSize: '15px'
    },
    // Diagram text is scaled by (page column width / natural SVG width), so a graph
    // that lays out wide arrives on the page unreadable however large its font is set.
    // Tight spacing keeps the natural width down; the graphs themselves are authored
    // to run vertically for the same reason.
    flowchart: { curve: 'basis', useMaxWidth: true, nodeSpacing: 30, rankSpacing: 38,
                 padding: 8 },
    sequence: { useMaxWidth: true, wrap: true, width: 150, boxMargin: 6 },
    state: { useMaxWidth: true }
  });
  window.__ready = false;
  mermaid.run({ querySelector: 'pre.mermaid' })
    .then(() => { window.__ready = true; document.title = document.title; })
    .catch((e) => { console.error(e); window.__ready = true; });
</script>
"""


def reorder_for_print(html: str) -> str:
    """Move the contents rail from beside the text to after the masthead.

    On screen it is a sticky sidebar; on paper a sidebar is meaningless, and a reader
    handed a document expects the title first and the index second.
    """
    nav = re.search(r'<nav class="toc">.*?</nav>', html, re.S)
    if not nav:
        return html
    html = html.replace(nav.group(0), "", 1)
    return html.replace("</header>", "</header>\n" + nav.group(0), 1)


def strip_screen_boot(html: str) -> str:
    """Remove the source file's own mermaid loader.

    The sources reference ``mermaid.min.js`` so they render when opened directly. The
    print copy inlines the bundle instead - a relative ``src`` is unreliable under
    headless file:// loading, and loading mermaid twice runs the diagrams twice.
    """
    return re.sub(r"<!-- mermaid-boot:.*?<!-- /mermaid-boot -->", "", html, flags=re.S)


def build_print_html(src: pathlib.Path, dst: pathlib.Path) -> str:
    raw = src.read_text(encoding="utf-8")
    title_m = re.search(r"<title>(.*?)</title>", raw, re.S)
    title = title_m.group(1).strip() if title_m else src.stem
    body = raw.replace(title_m.group(0), "") if title_m else raw

    body = strip_screen_boot(body)
    body = reorder_for_print(body)

    doc = (
        "<!doctype html><html lang=\"en\" data-theme=\"light\"><head>"
        "<meta charset=\"utf-8\">"
        f"<title>{title}</title>"
        f"{body[:body.index('</style>') + len('</style>')]}"
        f"<style>{PRINT_CSS}</style>"
        "</head><body>"
        f"{body[body.index('</style>') + len('</style>'):]}"
        + BOOT % {"mermaid": require_mermaid()}
        + "</body></html>"
    )
    dst.write_text(doc, encoding="utf-8")
    return title


def to_pdf(html: pathlib.Path, pdf: pathlib.Path) -> None:
    if pdf.exists():
        pdf.unlink()
    # --print-to-pdf-no-header is silently ignored by this Edge build, which stamped a
    # file:// URL and a date on every page. --no-pdf-header-footer is the one that works.
    # --run-all-compositor-stages-before-draw hangs this render; the virtual time budget
    # is what actually gives mermaid room to finish laying out before the snapshot.
    profile = HERE / "_edge_profile"
    proc = subprocess.run(
        [str(EDGE), "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check", "--disable-sync",
         "--virtual-time-budget=30000",
         "--no-pdf-header-footer",
         f"--print-to-pdf={pdf}", str(html)],
        capture_output=True, text=True, timeout=300, check=False)
    if not pdf.exists():
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        raise SystemExit(f"Edge produced no PDF for {html.name}\n" + "\n".join(tail))


def stamp(pdf: pathlib.Path, left: str) -> int:
    """Add a footer rule, document name and 'Page n of m' to every page."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    reader = PdfReader(str(pdf))
    total = len(reader.pages)
    writer = PdfWriter()
    w, h = A4

    for i, page in enumerate(reader.pages, start=1):
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        c.setStrokeColor(HexColor("#DCE5E5"))
        c.setLineWidth(0.5)
        c.line(37, 30, w - 37, 30)
        c.setFillColor(HexColor("#64777A"))
        c.setFont("Helvetica", 7.2)
        c.drawString(37, 20, left)
        c.drawRightString(w - 37, 20, f"Page {i} of {total}")
        c.save()
        buf.seek(0)
        page.merge_page(PdfReader(buf).pages[0])
        writer.add_page(page)

    writer.add_metadata({
        "/Title": left,
        "/Subject": "Fraud Risk Management & Early Warning Signals Platform",
        "/Creator": "Fraud360 documentation set",
    })
    with open(pdf, "wb") as fh:
        writer.write(fh)
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", action="store_true",
                    help="re-download the pinned mermaid bundle and verify its digest")
    args = ap.parse_args()

    if args.fetch:
        fetch_mermaid()

    if not EDGE.exists():
        raise SystemExit("Edge not found")
    # Verified up front so a bad bundle fails before Edge is launched twice.
    require_mermaid()

    jobs = [
        ("brd.html", "Fraud360-BRD-v1.9.pdf",
         "BRD v1.9  ·  Fraud360  ·  Business Requirements"),
        ("hld.html", "Fraud360-HLD-v1.8.pdf",
         "HLD v1.8  ·  Fraud360  ·  High-Level Design"),
        ("fsd.html", "Fraud360-FSD-v1.6.pdf",
         "FSD v1.6  ·  Fraud360  ·  Functional Specification"),
    ]
    for src_name, pdf_name, footer in jobs:
        src = HERE / src_name
        printable = HERE / f"_print_{src_name}"
        pdf = HERE / pdf_name
        title = build_print_html(src, printable)
        to_pdf(printable, pdf)
        pages = stamp(pdf, footer)
        size = pdf.stat().st_size / 1024
        print(f"{pdf_name:<28} {pages:>3} pages   {size:>7.0f} KB   {title[:40]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
