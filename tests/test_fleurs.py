from pathlib import Path

from collectors.fleurs import load_fleurs_metadata


def test_fleurs_literal_quotes_do_not_shift_columns(tmp_path: Path) -> None:
    metadata_path = tmp_path / "train.tsv"
    metadata_path.write_text(
        '423\tclip.wav\t"Teks dengan "kutipan" literal."\tteks dengan kutipan literal\twords |\t117120\tFEMALE\n',
        encoding="utf-8",
    )

    rows = load_fleurs_metadata(metadata_path, "train")

    assert rows[0]["num_samples"] == "117120"
    assert rows[0]["gender"] == "FEMALE"
