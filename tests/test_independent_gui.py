import importlib
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from unittest import TestCase, main
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill' / 'source-intake' / 'scripts'))
gui=importlib.import_module('source_intake_gui')

class GUIAcceptance(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='source-intake-gui-independent-')
        self.base=Path(self.tmp.name)
        self.root=self.base/'手动输入 新建项目'
        self.tk=tk.Tk()
        self.tk.withdraw()
        self.app=gui.SourceIntakeApp(self.tk,settings_path=self.base/'config/settings.json',
                                    clipboard_reader=lambda:('none',None,None))
        self.dialog_patches=[patch.object(gui.messagebox,name,return_value=True)
                             for name in ['showinfo','showwarning','showerror','askyesno']]
        self.mocks=[p.start() for p in self.dialog_patches]

    def tearDown(self):
        for p in self.dialog_patches:p.stop()
        try:self.tk.destroy()
        except tk.TclError:pass
        self.tmp.cleanup()

    def test_manual_directory_entry_imports_after_one_preview(self):
        self.app.root_dir_var.set(str(self.root))
        self.app.req_id_var.set('GUI-QA-1')
        self.app.add_text('var direct=1;',filename='manual.js')
        plan=self.app.run_preview()
        self.assertIsNotNone(plan)
        self.assertFalse(self.root.exists())
        result=self.app.run_import()
        self.assertIsNotNone(result, 'Manual root entry required a redundant second preview')
        self.assertTrue((self.root/'frontend/scripts/dhcnewpro/dhcemc/manual.js').is_file())

    def test_picker_directory_import_and_saved_rules(self):
        self.app.set_root_directory(str(self.root))
        self.app.route_vars['js'].set('assets/js')
        self.app.req_id_var.set('GUI-QA-2')
        self.app.add_text('var selected=1;',filename='picked.js')
        self.app.run_preview()
        result=self.app.run_import()
        self.assertTrue(result and result.get('ok'))
        self.assertTrue((self.root/'assets/js/picked.js').is_file())
        stored=gui.SettingsStore(self.base/'config/settings.json')
        self.assertEqual(stored.project_for(str(self.root))['routes']['js'],'assets/js')

    def test_cancel_directory_picker_has_no_side_effects(self):
        with patch.object(gui.filedialog,'askdirectory',return_value=''):
            self.app.on_browse_directory()
        self.assertEqual(self.app.root_dir_var.get(),'')
        self.assertFalse(self.root.exists())
        self.assertFalse(self.app.source_entries)

    def test_preview_then_source_file_changed_is_refused(self):
        self.app.set_root_directory(str(self.root))
        src=self.base/'changing.js'
        src.write_text('var a=1;',encoding='utf-8')
        self.app.add_paths([str(src)])
        self.app.run_preview()
        src.write_text('var a=2;',encoding='utf-8')
        self.assertIsNone(self.app.run_import())
        self.assertFalse(self.root.exists())

    def test_open_target_rejects_a_file(self):
        file=self.base/'not-a-directory.js'
        file.write_text('var a=1;',encoding='utf-8')
        self.app.root_dir_var.set(str(file))
        with patch.object(gui.os,'startfile',create=True) as open_file:
            self.app.on_open_target()
        open_file.assert_not_called()

if __name__=='__main__':main(verbosity=2)
