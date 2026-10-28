import shutil
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader


class PdfUnavailable(RuntimeError):
    pass


def to_pdf(docx: Path, out_dir: Path) -> Path:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if exe is None:
        raise PdfUnavailable("LibreOffice not found; run inside the container: docker compose run --rm pipeline jap …")
    # A throwaway LibreOffice profile per call, so parallel or crashed runs never share a lock.
    with tempfile.TemporaryDirectory() as lo_profile:
        subprocess.run(
            [exe, f"-env:UserInstallation=file://{lo_profile}", "--headless",
             "--convert-to", "pdf", "--outdir", str(out_dir), str(docx)],
            check=True, capture_output=True, timeout=120,
        )
    return out_dir / f"{docx.stem}.pdf"


def page_count(pdf: Path) -> int:
    return len(PdfReader(pdf).pages)
