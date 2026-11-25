"""Independent black-box checks authored by Codex, separate from implementation tests."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(os.environ.get('SOURCE_INTAKE_TOOL', str(Path(__file__).resolve().parents[1] / 'skill' / 'source-intake' / 'scripts' / 'source_intake.py')))


class IntakeAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='source-intake-independent-')
        self.base = Path(self.tmp.name)
        self.root = self.base / '目标 项目 中文'
        self.seq = 0

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, sources, **changes):
        result = {
            'root': str(self.root), 'project_name': '独立验收项目',
            'profile': 'hisui', 'sources': sources,
            'requirement': {'system': 'BOS', 'id': 'BOS-QA-20261125',
                            'title': '导入验收需求', 'description': '原始需求第一版\n增加字段',
                            'url': 'http://intranet.invalid/requirements/QA', 'updated_at': '2026-11-25'},
        }
        result.update(changes)
        return result

    def cli(self, command, request=None, *extra):
        args = [sys.executable, '-X', 'utf8', str(TOOL), command]
        if request is not None:
            self.seq += 1
            req_path = self.base / f'request-{self.seq}.json'
            req_path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
            args += ['--manifest', str(req_path)]
        args += list(extra)
        proc = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                              timeout=40, env={**os.environ, 'PYTHONUTF8': '1'})
        try:
            data = json.loads(proc.stdout)
        except Exception as exc:
            self.fail(f'CLI did not produce JSON: rc={proc.returncode}, stdout={proc.stdout!r}, stderr={proc.stderr!r}: {exc}')
        if isinstance(data, dict):
            error_type = (data.get('error') or {}).get('type')
            self.assertNotIn(error_type, ['NameError','AttributeError','UnboundLocalError','RecursionError','KeyError','IndexError','NotImplementedError'], data)
        return proc.returncode, data

    def apply_ok(self, req, *extra):
        code, result = self.cli('apply', req, *extra)
        self.assertEqual(code, 0, result)
        return result

    def manifests(self):
        return list((self.root / '.source-intake' / 'batches').glob('*/manifest.json'))

    def assert_snapshot_contains(self, payload):
        copies = [p for p in (self.root / '.source-intake').rglob('*') if p.is_file()]
        self.assertTrue(any(p.read_bytes() == payload for p in copies), 'Original/backup bytes not retained')

    def test_plan_does_not_create_target(self):
        req = self.request([{'text': 'var a = 1;\r\n', 'filename': 'sample.js'}])
        code, plan = self.cli('plan', req)
        self.assertEqual(code, 0, plan)
        self.assertFalse(self.root.exists())

    def test_real_files_preserve_bom_crlf_and_gb18030(self):
        payloads = {'bom.js': b'\xef\xbb\xbfvar x = 1;\r\n',
                    'legacy.js': '// 中文注释\r\nvar y = 2;\r\n'.encode('gb18030')}
        sources = []
        for name, payload in payloads.items():
            src = self.base / name
            src.write_bytes(payload)
            sources.append({'path': str(src)})
        self.apply_ok(self.request(sources))
        for name, payload in payloads.items():
            self.assertEqual((self.root / 'frontend/scripts/dhcnewpro/dhcemc' / name).read_bytes(), payload)
            self.assert_snapshot_contains(payload)

    def test_cls_and_csp_from_generic_text_files(self):
        cls = self.base / 'class-source.txt'
        cls.write_text('Class Sample.BL.Demo Extends %RegisteredObject\n{\n}\n', encoding='utf-8')
        csp = self.base / '已粘贴的文本.txt'
        csp.write_text('<!-- doc.sample.demo.csp 测试 -->\n<html><body>示例</body></html>\n', encoding='utf-8')
        self.apply_ok(self.request([{'path': str(cls)}, {'path': str(csp)}]))
        self.assertEqual((self.root / 'backend/Sample/BL/Demo.cls').read_bytes(), cls.read_bytes())
        self.assertEqual((self.root / 'frontend/csp/doc.sample.demo.csp').read_bytes(), csp.read_bytes())

    def test_fenced_paste_retains_raw_snapshot(self):
        text = '```javascript\nvar original = "&amp;";\n```'
        self.apply_ok(self.request([{'text': text, 'filename': 'fenced.js'}]))
        target = self.root / 'frontend/scripts/dhcnewpro/dhcemc/fenced.js'
        self.assertIn('var original = "&amp;";', target.read_text(encoding='utf-8'))
        self.assertNotIn('```', target.read_text(encoding='utf-8'))
        self.assert_snapshot_contains(text.encode('utf-8'))

    def test_unnamed_javascript_is_not_silently_named(self):
        req = self.request([{'text': 'function doThing() { return 1; }'}])
        code, result = self.cli('apply', req)
        self.assertNotEqual(code, 0, result)
        self.assertFalse(list(self.root.rglob('*.js')))

    def test_blocked_batch_does_not_import_only_the_resolved_part(self):
        req=self.request([{'text':'var known=1;','filename':'known.js'},
                          {'text':'function unnamed() { return 2; }'}])
        code,result=self.cli('apply',req)
        self.assertNotEqual(code,0,result)
        self.assertFalse((self.root/'frontend/scripts/dhcnewpro/dhcemc/known.js').exists())

    def test_requirements_are_snapshotted_per_batch(self):
        self.apply_ok(self.request([{'text': 'var one=1;', 'filename': 'one.js'}]))
        other = self.request([{'text': 'var two=2;', 'filename': 'two.js'}])
        other['requirement']['id'] = 'BOS-SECOND'
        other['requirement']['description'] = '第二条需求'
        self.apply_ok(other)
        manifests = self.manifests()
        self.assertEqual(len(manifests), 2)
        serialized = '\n'.join(p.read_text(encoding='utf-8-sig') for p in (self.root/'.source-intake').rglob('*.json'))
        self.assertIn('BOS-QA-20261125', serialized)
        self.assertIn('BOS-SECOND', serialized)
        self.assertIn('原始需求第一版', serialized)
        code, history = self.cli('history', None, '--root', str(self.root))
        self.assertEqual(code, 0, history)
        self.assertIn('BOS-SECOND', json.dumps(history, ensure_ascii=False))

    def test_default_conflict_skips_and_explicit_replace_backs_up(self):
        dest = self.root / 'frontend/scripts/dhcnewpro/dhcemc/conflict.js'
        dest.parent.mkdir(parents=True)
        old = b'var hospitalVersion = 1;\r\n'
        dest.write_bytes(old)
        req = self.request([{'text': 'var incomingVersion = 2;\n', 'filename': 'conflict.js'}])
        self.apply_ok(req)
        self.assertEqual(dest.read_bytes(), old)
        self.apply_ok(req, '--conflict', 'backup-replace')
        self.assertEqual(dest.read_text(encoding='utf-8'), 'var incomingVersion = 2;\n')
        self.assert_snapshot_contains(old)

    def test_relative_paths_reject_escape_reserved_metadata_and_device_names(self):
        for destination in ['../escape.js', '..\\escape.js', 'C:/escape.js',
                            '\\\\server\\share\\escape.js', '.source-intake/manifest.json',
                            'frontend/CON.js', 'frontend/stream:part.js']:
            with self.subTest(destination=destination):
                req = self.request([{'text': 'var p=1;', 'filename': 'p.js', 'destination': destination}])
                code, result = self.cli('apply', req)
                self.assertNotEqual(code, 0, result)
        self.assertFalse((self.base / 'escape.js').exists())

    def test_same_batch_case_insensitive_destination_collision(self):
        req = self.request([{'text': 'var a=1;', 'filename': 'Same.js'},
                            {'text': 'var b=2;', 'filename': 'same.JS'}])
        code, result = self.cli('apply', req)
        self.assertNotEqual(code, 0, result)
        self.assertFalse(list(self.root.rglob('*.js')))

    def test_custom_routes(self):
        self.apply_ok(self.request([{'text': 'var a=1;', 'filename': 'route.js'}], routes={'js': 'assets/js'}))
        self.assertTrue((self.root / 'assets/js/route.js').is_file())

    def test_unsafe_custom_route_is_rejected(self):
        code, result = self.cli('apply', self.request(
            [{'text': 'var route=1;', 'filename': 'route.js'}], routes={'js': '../outside'}))
        self.assertNotEqual(code, 0, result)
        self.assertFalse((self.base/'outside/route.js').exists())

    def make_directory_link(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
            return
        except OSError:
            if os.name != 'nt':
                self.skipTest('Directory symlinks unavailable')
        proc = subprocess.run([
            'powershell', '-NoProfile', '-Command',
            'New-Item -ItemType Junction -Path $env:SOURCE_INTAKE_QA_LINK -Target $env:SOURCE_INTAKE_QA_TARGET -ErrorAction Stop | Out-Null'
        ], capture_output=True, timeout=20, env={**os.environ,
            'SOURCE_INTAKE_QA_LINK':str(link), 'SOURCE_INTAKE_QA_TARGET':str(target)})
        if proc.returncode != 0:
            self.skipTest('Directory symlink/junction not available on this machine')

    def test_symlink_destination_cannot_escape_project(self):
        self.root.mkdir()
        outside = self.base/'external-data'
        outside.mkdir()
        self.make_directory_link(self.root/'linked',outside)
        code,result=self.cli('apply',self.request([
            {'text':'var escape=1;','filename':'escape.js','destination':'linked/escape.js'}]))
        self.assertNotEqual(code,0,result)
        self.assertFalse((outside/'escape.js').exists())

    def test_metadata_directory_link_cannot_escape_project(self):
        self.root.mkdir()
        outside = self.base/'external-metadata'
        outside.mkdir()
        self.make_directory_link(self.root/'.source-intake',outside)
        code,result=self.cli('apply',self.request([{'text':'var a=1;','filename':'a.js'}]))
        self.assertNotEqual(code,0,result)
        self.assertEqual(list(outside.iterdir()),[])

    def test_metadata_batches_link_cannot_escape_project(self):
        meta=self.root/'.source-intake'
        meta.mkdir(parents=True)
        outside=self.base/'external-batches'
        outside.mkdir()
        self.make_directory_link(meta/'batches',outside)
        code,result=self.cli('apply',self.request([{'text':'var a=1;','filename':'a.js'}]))
        self.assertNotEqual(code,0,result)
        self.assertEqual(list(outside.iterdir()),[])

    def test_preview_stale_destination_cannot_overwrite(self):
        dest = self.root / 'frontend/scripts/dhcnewpro/dhcemc/stale.js'
        dest.parent.mkdir(parents=True)
        dest.write_text('var version=1;', encoding='utf-8')
        req = self.request([{'text': 'var incoming=2;', 'filename': 'stale.js'}])
        code, plan = self.cli('plan', req)
        self.assertEqual(code, 0, plan)
        preview = self.base / 'preview.json'
        preview.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        dest.write_text('var version=3;', encoding='utf-8')
        code, result = self.cli('apply', req, '--conflict', 'backup-replace', '--expected-plan', str(preview))
        self.assertNotEqual(code, 0, result)
        self.assertEqual(dest.read_text(encoding='utf-8'), 'var version=3;')

    def test_preview_stale_source_cannot_import(self):
        src = self.base / 'changing.js'
        src.write_text('var a=1;', encoding='utf-8')
        req = self.request([{'path': str(src)}])
        code, plan = self.cli('plan', req)
        self.assertEqual(code, 0, plan)
        preview = self.base / 'preview.json'
        preview.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        src.write_text('var a=2;', encoding='utf-8')
        code, result = self.cli('apply', req, '--expected-plan', str(preview))
        self.assertNotEqual(code, 0, result)
        self.assertFalse((self.root / 'frontend/scripts/dhcnewpro/dhcemc/changing.js').exists())

    def test_hospital_eight_file_batch(self):
        original = Path(r'D:\粤北卒中中心')
        if not original.is_dir():
            self.skipTest('Optional local hospital source fixture is unavailable')
        files = sorted((original/'frontend/csp').glob('*.csp')) + sorted((original/'frontend/scripts/dhcnewpro/dhcemc').glob('*.js'))
        self.assertEqual(len(files), 8)
        expected_hashes = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
        self.apply_ok(self.request([{'path': str(f)} for f in files]))
        for f in files:
            imported = self.root / f.relative_to(original)
            self.assertEqual(imported.read_bytes(), f.read_bytes())
            self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(), expected_hashes[str(f)])
        self.assertEqual(len(list((self.root/'frontend').rglob('*.csp'))) + len(list((self.root/'frontend').rglob('*.js'))), 8)
        self.assertFalse(list((self.root/'frontend').rglob('*.css')), 'Referenced but missing CSS must not be generated')


if __name__ == '__main__':
    unittest.main(verbosity=2)
