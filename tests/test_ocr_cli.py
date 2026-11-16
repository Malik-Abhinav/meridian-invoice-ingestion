"""CLI output and explicit input failures."""

from pathlib import Path

import pytest

from meridian import ocr_cli
from meridian.evaluation_fixtures import FixtureManifest
from meridian.ocr import OcrDocument, OcrPage, PreprocessingConfig


def test_cli_one_scan_writes_json_and_text(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-test")
    result = OcrDocument(
        source_sha256="a" * 64, engine_version="test", configuration=PreprocessingConfig(),
        pages=(OcrPage(page_number=1, text="invoice\n123", words=(), image_width=100,
                       image_height=100, deskew_angle_degrees=0),),
        normalized_text="invoice 123",
    )
    monkeypatch.setattr(ocr_cli, "extract_ocr", lambda *_args: result)
    destination = tmp_path / "result"
    assert ocr_cli.main(["--input", str(source), "--output-dir", str(destination)]) == 0
    assert (destination / "scan.txt").read_text() == "invoice\n123\n"
    assert OcrDocument.model_validate_json((destination / "scan.ocr.json").read_text()) == result


def test_cli_rejects_bad_input_and_configuration(tmp_path: Path) -> None:
    source = tmp_path / "bad.pdf"
    source.write_bytes(b"invalid")
    with pytest.raises(SystemExit) as exc:
        ocr_cli.main(["--input", str(source), "--output-dir", str(tmp_path), "--dpi", "200"])
    assert exc.value.code == 1
    with pytest.raises(SystemExit):
        ocr_cli.main(["--input", str(source), "--output-dir", str(tmp_path), "--dpi", "20"])


def test_fixture_batch_records_failures_and_exits_nonzero(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1] / "output/evaluation"
    manifest = FixtureManifest.model_validate_json((root / "manifest.json").read_text())
    one = manifest.model_copy(update={"entries": manifest.entries[:1]})
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(one.model_dump_json())
    result = ocr_cli.run_fixtures(manifest_path, tmp_path / "ocr", PreprocessingConfig())
    assert result.failures == 1
    assert result.invoices[0].status == "error"
    assert (tmp_path / "ocr/report.json").is_file()
    assert (tmp_path / "ocr/fixture_a_clean_01.evaluation.json").is_file()
