# -*- coding: utf-8 -*-
"""اختبارات سكربت قياس دقة استخراج الفواتير — لا تحتاج Tesseract."""
import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import fitz
from PIL import Image

from tools import benchmark_invoices as bm


def _write_truth(path, headers, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(rows)


def _image(path, color):
    Image.new("RGB", (64, 64), color).save(path)


def _pdf(path, colors):
    doc = fitz.open()
    try:
        for color in colors:
            page = doc.new_page(width=100, height=100)
            page.draw_rect(page.rect, color=color, fill=color)
        doc.save(path)
    finally:
        doc.close()


class CompareFieldTests(unittest.TestCase):
    def test_numbers_and_dates(self):
        self.assertTrue(bm.compare_field("ht", "1 234,50", 1234.5))
        self.assertTrue(bm.compare_field("ht", "1 234,50", 1234.51))
        self.assertFalse(bm.compare_field("ht", "1 234,50", 1234.52))
        self.assertTrue(bm.compare_field("tva_rate", "20%", 20))
        self.assertTrue(bm.compare_field("date", "2026-02-03", "2026-02-03"))
        self.assertFalse(bm.compare_field("date", "2026-02-03", "2026-02-04"))

    def test_identifiers_and_names(self):
        self.assertTrue(bm.compare_field("supplier_nif", "12 345 678", "12345678"))
        self.assertTrue(bm.compare_field("supplier_nif", "", ""))
        self.assertFalse(bm.compare_field("supplier_nif", "", "N/A"))
        self.assertTrue(bm.compare_field("supplier_rc", "RC 123/2020", "rc123/2020"))
        self.assertTrue(bm.compare_field("invoice_number", "INV 001", "inv001"))
        self.assertTrue(bm.compare_field("supplier", "SOCIÉTÉ ALPHA", "societe alpha"))
        self.assertTrue(bm.compare_field("client", "شركة الأمل", "شركه الامل"))
        self.assertFalse(bm.compare_field("client", "شركة الأمل", "شركة النور"))

    def test_blank_and_zero_items(self):
        self.assertTrue(bm.compare_field("supplier", "", ""))
        self.assertTrue(bm.compare_field("supplier", "", None))
        self.assertFalse(bm.compare_field("supplier", "", "ACME"))
        self.assertTrue(bm.compare_field("items_count", "", 0))
        self.assertTrue(bm.compare_field("items_count", "", None))
        self.assertFalse(bm.compare_field("items_count", "2", None))

    def test_expected_values_and_skip(self):
        blank = bm.parse_expected("supplier", "", 1)
        skip = bm.parse_expected("supplier", "SKIP", 1)
        amount = bm.parse_expected("ht", "1 234,50", 1)
        self.assertFalse(blank["skip"])
        self.assertEqual(blank["value"], None)
        self.assertTrue(skip["skip"])
        self.assertAlmostEqual(amount["value"], 1234.5)
        with self.assertRaises(bm.BenchmarkError):
            bm.parse_expected("ht", "abc", 2)
        with self.assertRaises(bm.BenchmarkError):
            bm.parse_expected("date", "03/02/2026", 3)


class TruthAndJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        _image(self.dir / "one.png", (10, 200, 10))
        (self.dir / "sub").mkdir()
        _pdf(self.dir / "sub" / "two.pdf", [(1, 0, 0), (0, 0, 1)])

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_truth_creates_page_rows(self):
        target = self.dir / "truth.csv"
        _path, count = bm.init_truth(str(self.dir), str(target))
        self.assertEqual(count, 3)
        with open(target, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([r["file"] for r in rows].count("sub/two.pdf"), 2)
        self.assertEqual(sorted(r["pages"] for r in rows if r["file"].endswith(".pdf")), ["1", "2"])
        self.assertTrue(all(not r["invoice_number"] for r in rows))
        with self.assertRaises(bm.BenchmarkError):
            bm.init_truth(str(self.dir), str(target))
        _path, count = bm.init_truth(str(self.dir), str(target), force=True)
        self.assertEqual(count, 3)

    def test_build_jobs_expands_pdf_pages_and_reports_missing(self):
        truth = self.dir / "truth.csv"
        _write_truth(
            truth,
            ["file", "pages", "invoice_number"],
            [["one.png", "", "A"], ["sub/two.pdf", "1-2", "B"], ["missing.png", "", "C"]],
        )
        rows, has_pages, headers = bm.read_truth(str(truth))
        jobs, missing, unlabeled, uncovered, total = bm.build_jobs(str(self.dir), rows)
        self.assertEqual(total, 2)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[1]["pages"], [0, 1])
        self.assertEqual(jobs[1]["pages_label"], "1,2")
        self.assertEqual(len(missing), 1)
        self.assertEqual(unlabeled, [])
        self.assertEqual(uncovered, [])
        self.assertEqual(headers, ["invoice_number"])

    def test_multi_page_pdf_requires_pages_or_one_row_per_page(self):
        truth = self.dir / "truth.csv"
        _write_truth(truth, ["file", "invoice_number"], [["sub/two.pdf", "B"]])
        rows, has_pages, _headers = bm.read_truth(str(truth))
        with self.assertRaises(bm.BenchmarkError):
            bm.build_jobs(str(self.dir), rows)

    def test_duplicate_or_overlapping_page_rows_rejected(self):
        truth = self.dir / "truth.csv"
        _write_truth(
            truth,
            ["file", "pages", "invoice_number"],
            [["sub/two.pdf", "1", "A"], ["sub/two.pdf", "1", "B"]],
        )
        rows, has_pages, _headers = bm.read_truth(str(truth))
        with self.assertRaises(bm.BenchmarkError):
            bm.build_jobs(str(self.dir), rows)
        _write_truth(
            truth,
            ["file", "pages", "invoice_number"],
            [["sub/two.pdf", "1-2", "A"], ["sub/two.pdf", "2", "B"]],
        )
        rows, has_pages, _headers = bm.read_truth(str(truth))
        with self.assertRaises(bm.BenchmarkError):
            bm.build_jobs(str(self.dir), rows)

    def test_invalid_pages_and_empty_field_headers_rejected(self):
        truth = self.dir / "truth.csv"
        _write_truth(truth, ["file", "pages", "invoice_number"], [["sub/two.pdf", "9", "A"]])
        rows, has_pages, _headers = bm.read_truth(str(truth))
        with self.assertRaises(bm.BenchmarkError):
            bm.build_jobs(str(self.dir), rows)
        _write_truth(truth, ["file", "pages"], [["one.png", ""]])
        with self.assertRaises(bm.BenchmarkError):
            bm.read_truth(str(truth))


class RunBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        _image(self.dir / "good.png", (10, 200, 10))
        _image(self.dir / "bad.png", (200, 10, 10))
        _pdf(self.dir / "doc.pdf", [(1, 0, 0), (0, 0, 1)])

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def fake_extract(images):
        if len(images) > 1:
            return {
                "fields": {"invoice_number": "PDF-1", "supplier": "PDF CO"},
                "items": [], "warnings": [], "score": 100,
                "text": "PDF",
            }
        color = images[0].getpixel((0, 0))
        if color[0] > 150:
            return {
                "fields": {"invoice_number": "BAD-1", "supplier": "ACME"},
                "items": [], "warnings": [("error", "invoice_number", "رقم غير متوقع")],
                "score": 50, "text": "BAD",
            }
        return {
            "fields": {"invoice_number": "GOOD-1", "supplier": "ACME"},
            "items": [], "warnings": [], "score": 100, "text": "GOOD",
        }

    def _truth(self, headers=None):
        truth = self.dir / "truth.csv"
        _write_truth(
            truth,
            headers or ["file", "pages", "invoice_number", "supplier"],
            [
                ["good.png", "", "GOOD-1", "ACME"],
                ["bad.png", "", "GOOD-1", "ACME"],
                ["doc.pdf", "1-2", "PDF-1", "PDF CO"],
            ],
        )
        return truth

    def test_run_writes_reports_and_combines_pdf_pages(self):
        truth = self._truth()
        out = self.dir / "reports" / "run"
        report = bm.run_benchmark(
            str(self.dir), str(truth), out=str(out),
            extractor=self.fake_extract,
            extractor_meta={"engine": "fake", "lang": None, "tesseract": None, "tessdata": None},
        )
        s = report["summary"]
        self.assertEqual(s["invoices_total"], 3)
        self.assertEqual(s["invoices_scored"], 3)
        self.assertEqual(s["invoices_error"], 0)
        self.assertEqual(s["full_invoice"], {"correct": 2, "total": 3, "accuracy": 66.67})
        self.assertEqual(s["overall"]["correct"], 5)
        self.assertEqual(s["overall"]["total"], 6)
        self.assertEqual(s["warnings"]["incorrect_flagged"], 1)
        self.assertEqual(s["warnings"]["incorrect_rate"], 100.0)
        self.assertEqual(s["warnings"]["false_alarm_rate"], 0.0)
        pdf = next(inv for inv in report["invoices"] if inv["file"] == "doc.pdf")
        self.assertTrue(pdf["full_correct"])
        self.assertEqual(pdf["pages"], "1,2")
        for suffix in (".csv", ".json", ".md"):
            self.assertTrue(os.path.isfile(str(out) + suffix))
        with open(str(out) + ".json", encoding="utf-8") as fh:
            loaded = json.load(fh)
        self.assertEqual(loaded["summary"]["full_invoice"]["correct"], 2)
        with open(str(out) + ".md", encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("تقرير قياس دقة استخراج الفواتير", md)
        self.assertIn("bad.png", md)

    def test_limit_and_blank_truth_field(self):
        truth = self._truth(["file", "pages", "invoice_number", "tva"])
        _write_truth(
            truth,
            ["file", "pages", "invoice_number", "tva"],
            [["good.png", "", "GOOD-1", ""], ["bad.png", "", "GOOD-1", ""]],
        )
        out = self.dir / "limited"
        report = bm.run_benchmark(
            str(self.dir), str(truth), out=str(out), limit=1,
            extractor=self.fake_extract,
            extractor_meta={"engine": "fake", "lang": None, "tesseract": None, "tessdata": None},
        )
        self.assertEqual(len(report["invoices"]), 1)
        self.assertTrue(report["meta"]["limited"])
        self.assertEqual(report["summary"]["jobs_total"], 2)
        comparisons = {c["field"]: c for c in report["invoices"][0]["fields"]}
        self.assertTrue(comparisons["tva"]["correct"])
        self.assertFalse(report["invoices"][0]["full_correct"])

    def test_cli_init_truth(self):
        target = self.dir / "cli_truth.csv"
        code = bm.main(["--dir", str(self.dir), "--truth", str(target), "--init-truth"])
        self.assertEqual(code, 0)
        self.assertTrue(target.is_file())
        with open(target, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
