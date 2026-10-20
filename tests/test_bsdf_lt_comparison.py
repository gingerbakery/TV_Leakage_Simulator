"""Comparator tests use fabricated temporary numbers, never LT validation data."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from compare_bsdf_lt_csv import compare, read_map, scores


def write_fixture(path):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['u_mm', 'v_mm', 'flux_lm'])
        for row in range(20):
            for col in range(20):
                writer.writerow([col-9.5, row-9.5, 1+row+col])


class BsdfComparisonTests(unittest.TestCase):
    def test_shape_agreement_cannot_hide_total_flux_mismatch(self):
        result = scores([1., 2.], [2., 4.])
        self.assertEqual(result['flux_error_percent'], -50.)
        self.assertAlmostEqual(result['distribution_overlap_percent'], 100.)
        self.assertAlmostEqual(result['correlation'], 1.)
        self.assertFalse(result['pass'])

    def test_equal_totals_do_not_hide_displaced_distribution(self):
        result = scores([1., 0.], [0., 1.])
        self.assertEqual(result['flux_error_percent'], 0.)
        self.assertEqual(result['distribution_overlap_percent'], 0.)
        self.assertAlmostEqual(result['correlation'], -1.)
        self.assertFalse(result['pass'])

    def test_map_rejects_duplicate_or_missing_bin(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'fixture.csv'
            write_fixture(path)
            self.assertEqual(len(read_map(path)), 400)
            with path.open('a', encoding='utf-8') as stream:
                stream.write('-9.5,-9.5,1\n')
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                read_map(path)
            path.write_text('u_mm,v_mm,flux_lm\n-9.5,-9.5,1\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '400 bins'):
                read_map(path)

    def test_incomplete_cases_stay_partial_and_summary_has_no_raw_mesh(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            write_fixture(path/'lambertian-aoi0-sim.csv')
            write_fixture(path/'lambertian-aoi0.csv')
            provenance = {'engine': 'LightTools', 'version': 'UNIT-TEST-FIXTURE-NOT-AN-LT-RUN',
                          'shift_type': 'No Shift', 'anisotropic': False, 'smoothing': False,
                          'source_power_lm': 1, 'rays': 100000}
            (path/'provenance.json').write_text(json.dumps(provenance), encoding='utf-8')
            report = compare(path, path)
            self.assertEqual(report['status'], 'partial')
            self.assertTrue(report['cases'][0]['pass'])
            self.assertEqual(sum(row['status'] == 'missing' for row in report['cases']), 5)
            self.assertNotIn('flux_lm', json.dumps(report))
            provenance['smoothing'] = True
            (path/'provenance.json').write_text(json.dumps(provenance), encoding='utf-8')
            with self.assertRaises(ValueError):
                compare(path, path)


if __name__ == '__main__':
    unittest.main()
