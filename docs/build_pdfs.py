"""
Render the presentation to PDF with an installed Google Chrome (Playwright).

    python docs/build_pdfs.py    -> docs/presentation.pdf

The slides are plain HTML (docs/presentation/slides.html) so the Arabic text is shaped by the
browser and the file stays editable. The written report is maintained as docs/report.docx
(exported to docs/report.pdf from Word).
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

DOCS = Path(__file__).resolve().parent


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page()
        page.goto((DOCS / "presentation" / "slides.html").as_uri())
        page.wait_for_load_state("networkidle")
        page.evaluate("document.fonts.ready")
        page.pdf(path=str(DOCS / "presentation.pdf"), width="1280px", height="720px",
                 print_background=True, margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        browser.close()
    print("->", DOCS / "presentation.pdf")


if __name__ == "__main__":
    main()
