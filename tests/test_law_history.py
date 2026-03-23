from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from law_history import (
    EliMetadata,
    build_year_rows,
    load_law_sources_from_markdown,
    normalize_law_title,
    parse_eli_metadata,
    trace_to_edge,
)


class NormalizeLawTitleTests(unittest.TestCase):
    def test_handles_old_and_new_faerdselslov_titles(self) -> None:
        self.assertEqual(
            normalize_law_title("Bekendtgørelse af færdselsloven"),
            normalize_law_title("Bekendtgørelse af Færdselslov"),
        )

    def test_reduces_loven_to_lov(self) -> None:
        self.assertEqual(normalize_law_title("Helligdagsloven"), "helligdagslov")


class ParseEliMetadataTests(unittest.TestCase):
    def test_parses_core_rdfa_fields(self) -> None:
        items = [
            {
                "content": "2019-03-10",
                "about": "https://retsinformation.dk/eli/lta/2019/239",
                "property": "eli:date_document",
            },
            {
                "resource": "http://www.retsinformation.dk/eli/resource/authority/type_document#LBKH",
                "about": "https://retsinformation.dk/eli/lta/2019/239",
                "property": "eli:type_document",
            },
            {
                "resource": "https://retsinformation.dk/eli/lta/2019/1022",
                "about": "https://retsinformation.dk/eli/lta/2019/239",
                "property": "eli:consolidated_by",
            },
            {
                "resource": "https://retsinformation.dk/eli/lta/2019/174",
                "about": "https://retsinformation.dk/eli/lta/2019/239",
                "property": "eli:changed_by",
            },
            {
                "resource": "http://www.retsinformation.dk/eli/resource/authority/relevant_for#INDOC",
                "about": "https://retsinformation.dk/eli/lta/2019/239",
                "property": "eli:relevant_for",
            },
            {
                "content": "Bekendtgørelse af udlændingeloven",
                "about": "https://retsinformation.dk/eli/lta/2019/239/dan",
                "property": "eli:title",
            },
            {
                "content": "Udlændingeloven",
                "about": "https://retsinformation.dk/eli/lta/2019/239/dan",
                "property": "eli:title_alternative",
            },
            {
                "content": "LBK nr 239 af 10/03/2019",
                "about": "https://retsinformation.dk/eli/lta/2019/239/dan",
                "property": "eli:title_short",
            },
        ]

        metadata = parse_eli_metadata(items, "https://retsinformation.dk/eli/lta/2019/239")

        self.assertEqual(metadata.document_type_code, "LBKH")
        self.assertEqual(metadata.date_document, date(2019, 3, 10))
        self.assertEqual(metadata.relevant_for_code, "INDOC")
        self.assertEqual(metadata.changed_by, ("https://retsinformation.dk/eli/lta/2019/174",))
        self.assertEqual(
            metadata.consolidated_by,
            ("https://retsinformation.dk/eli/lta/2019/1022",),
        )
        self.assertEqual(metadata.family_key, "udlændingelov")


class LoadLawSourcesTests(unittest.TestCase):
    def test_reads_multiple_seed_urls_from_same_line(self) -> None:
        markdown = """# Datagrundlag

Brug:
1. Færdselsloven <https://www.retsinformation.dk/eli/lta/1986/58> en senere version <https://www.retsinformation.dk/eli/lta/2021/1710>
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "underlying-data.md"
            path.write_text(markdown, encoding="utf-8")
            sources = load_law_sources_from_markdown(path)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].name, "Færdselsloven")
        self.assertEqual(
            sources[0].seed_urls,
            (
                "https://www.retsinformation.dk/eli/lta/1986/58",
                "https://www.retsinformation.dk/eli/lta/2021/1710",
            ),
        )


class BuildYearRowsTests(unittest.TestCase):
    def test_zero_fills_missing_years(self) -> None:
        rows = build_year_rows(
            [
                {"name": "Lov A", "counts_by_year": {2020: 2, 2022: 1}},
                {"name": "Lov B", "counts_by_year": {2021: 3}},
            ]
        )

        expected = [
            {"Lov": "Lov A", "År": 2020, "Ændringer": 2},
            {"Lov": "Lov A", "År": 2021, "Ændringer": 0},
            {"Lov": "Lov A", "År": 2022, "Ændringer": 1},
            {"Lov": "Lov B", "År": 2020, "Ændringer": 0},
            {"Lov": "Lov B", "År": 2021, "Ændringer": 3},
            {"Lov": "Lov B", "År": 2022, "Ændringer": 0},
        ]

        self.assertEqual(rows, expected)


class TraceToEdgeTests(unittest.TestCase):
    def test_previous_direction_only_uses_consolidates(self) -> None:
        meta = EliMetadata(
            url="https://www.retsinformation.dk/eli/lta/2021/1710",
            document_type_code="LBKH",
            title="Bekendtgørelse af færdselsloven",
            title_alternative="Færdselsloven",
            title_short="LBK nr 1710 af 13/08/2021",
            date_document=date(2021, 8, 13),
            date_publication=date(2021, 8, 24),
            date_no_longer_in_force=None,
            relevant_for_code="INDOC",
            changed_by=(),
            consolidated_by=("https://www.retsinformation.dk/eli/lta/2023/168",),
            consolidates=("https://www.retsinformation.dk/eli/lta/2018/1324",),
            basis_for=("https://www.retsinformation.dk/eli/lta/2013/463",),
        )

        with patch("law_history.find_adjacent_version", return_value=None) as mocked:
            result = trace_to_edge(
                meta,
                family_key=meta.family_key,
                relevant_for_code=meta.relevant_for_code,
                direction="previous",
            )

        self.assertEqual(result, meta)
        self.assertEqual(
            mocked.call_args.kwargs["relation_urls"],
            ("https://www.retsinformation.dk/eli/lta/2018/1324",),
        )


if __name__ == "__main__":
    unittest.main()
