"""Write seven review scans and JSON metadata from the three existing PDFs."""

from pathlib import Path

from meridian.scanned_invoices import ScanProfile, scan_invoice
from generate_pdf_samples import OUTPUT as SOURCE_DIR, SUPPLIERS, sample_invoice


OUTPUT = Path(__file__).resolve().parents[1] / "output" / "scans"
MASTER_SEED = 2026
DPI = 150
SAMPLES = (
    (0, ScanProfile.CLEAN),
    (0, ScanProfile.MILD_BLUR),
    (1, ScanProfile.MODERATE_BLUR),
    (1, ScanProfile.ROTATION),
    (2, ScanProfile.LOW_CONTRAST),
    (2, ScanProfile.JPEG_ARTIFACTS),
    (2, ScanProfile.COMBINED),
)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for supplier_index, profile in SAMPLES:
        source_filename, code, name, layout = SUPPLIERS[supplier_index]
        invoice = sample_invoice(code, name, layout)
        source_pdf = (SOURCE_DIR / source_filename).read_bytes()
        result = scan_invoice(source_pdf, invoice, master_seed=MASTER_SEED, profile=profile, dpi=DPI)
        stem = f"{code.lower()}_{profile.value}"
        (OUTPUT / f"{stem}.pdf").write_bytes(result.pdf_bytes)
        (OUTPUT / f"{stem}.json").write_text(result.metadata.model_dump_json(indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
