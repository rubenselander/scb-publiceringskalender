import json

from scb_extract import __main__ as cli


def test_cli_archives_input_and_replays_recorded_source_selection(
    tmp_path, monkeypatch
):
    calendar = tmp_path / "calendar.json"
    calendar.write_text('[{"product_code":"AM0201"}]', encoding="utf-8")
    observed = []

    def execute(context, sources, output):
        observed.append((context.offline, sources, context.calendar_path.read_bytes()))
        return {"complete": True, "sources": {}}

    monkeypatch.setattr(cli, "run_sources", execute)
    raw = tmp_path / "raw"
    assert (
        cli.main(
            [
                "run",
                "--source",
                "products",
                "--raw-dir",
                str(raw),
                "--calendar-path",
                str(calendar),
                "--output-dir",
                str(tmp_path / "first"),
            ]
        )
        == 0
    )
    calendar.write_text("[]", encoding="utf-8")
    assert (
        cli.main(
            ["replay", "--raw-dir", str(raw), "--output-dir", str(tmp_path / "second")]
        )
        == 0
    )
    assert observed[0][0] is False and observed[1][0] is True
    assert observed[0][1:] == observed[1][1:]
    assert json.loads((raw / "run.json").read_text())["sources"] == ["products"]


def test_cli_failure_exit_code_and_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli,
        "run_sources",
        lambda *args: {"complete": False, "sources": {"hvd": {"complete": False}}},
    )
    raw = tmp_path / "raw"
    assert (
        cli.main(
            [
                "run",
                "--source",
                "hvd",
                "--raw-dir",
                str(raw),
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )
        == 1
    )
    assert not json.loads((raw / "result.json").read_text())["complete"]
