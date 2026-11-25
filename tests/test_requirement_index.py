import json
import unittest
from pathlib import Path
from unittest.mock import patch
from common import IntakeTestCase, make_dir_link
import requirement_index as index


class RequirementIndexTests(IntakeTestCase):
    def package(self, folder, req_id='REQ-1', title='新增字段'):
        path = self.root / 'requirements' / folder
        path.mkdir(parents=True)
        (path / 'requirement.json').write_text(json.dumps({'requirement': {'id': req_id, 'title': title, 'description': '描述'}, 'attachments': []}), encoding='utf-8')
        (path / '修改说明.md').write_text('补充', encoding='utf-8')
        return path

    def test_duplicate_ids_and_query_return_distinct_packages(self):
        self.package('first'); self.package('second')
        result = index.list_requirements(self.root, query='req-1')
        self.assertTrue(result['ok'])
        self.assertEqual(result['matches'], 2)
        self.assertEqual(len({x['directory'] for x in result['items']}), 2)
        self.assertTrue(all(x['prompt_present'] for x in result['items']))
        self.assertEqual(index.list_requirements(self.root, '不存在')['items'], [])

    def test_manual_package_and_corrupt_metadata_are_visible_for_recovery(self):
        package = self.package('broken')
        (package / 'requirement.json').write_text('{broken', encoding='utf-8')
        manual = self.root / 'requirements/manual'
        manual.mkdir()
        result = index.list_requirements(self.root)
        self.assertEqual(result['matches'], 2)
        self.assertTrue(all(x['warnings'] for x in result['items']))

    def test_links_to_external_packages_and_metadata_are_not_followed(self):
        self.package('ordinary')
        outside = self.tmp / 'external'; outside.mkdir()
        (outside / 'requirement.json').write_text('{"id":"DO-NOT-READ"}', encoding='utf-8')
        if not make_dir_link(outside, self.root / 'requirements/linked'):
            self.skipTest('directory links unavailable')
        result = index.list_requirements(self.root)
        self.assertEqual(result['matches'], 1)
        self.assertNotIn('DO-NOT-READ', json.dumps(result))

    def test_missing_root_errors_and_empty_existing_root_stays_unchanged(self):
        self.assertFalse(index.list_requirements(self.root)['ok'])
        self.assertFalse(self.root.exists())
        self.root.mkdir()
        self.assertEqual(index.list_requirements(self.root)['items'], [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_scan_and_response_limits_are_explicit(self):
        for i in range(4): self.package(str(i))
        limited = index.list_requirements(self.root, limit=2)
        self.assertEqual(len(limited['items']), 2)
        self.assertTrue(limited['truncated'])
        with patch.object(index, 'MAX_SCAN_ENTRIES', 2):
            result = index.list_requirements(self.root)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['matches'], 2)

    def test_requirements_container_cannot_be_a_junction(self):
        self.root.mkdir(); outside = self.tmp / 'external'; outside.mkdir()
        if not make_dir_link(outside, self.root / 'requirements'):
            self.skipTest('directory links unavailable')
        self.assertFalse(index.list_requirements(self.root)['ok'])
