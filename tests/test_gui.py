from __future__ import annotations

import json
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from common import IntakeTestCase

try:
    import source_intake_gui as gui
except Exception as exc:  # pragma: no cover - 环境缺 Tk 时跳过
    gui = None
    GUI_IMPORT_ERROR = exc


class GuiSmokeTests(IntakeTestCase):
    def setUp(self):
        super().setUp()
        if gui is None:
            self.skipTest(f"无法导入 GUI 模块：{GUI_IMPORT_ERROR}")
        try:
            self.tk = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"当前环境无法初始化 Tk：{exc}")
        self.tk.withdraw()
        self.message_patches = [
            mock.patch.object(gui.messagebox, "showinfo", return_value=None),
            mock.patch.object(gui.messagebox, "showwarning", return_value=None),
            mock.patch.object(gui.messagebox, "showerror", return_value=None),
            mock.patch.object(gui.messagebox, "askyesno", return_value=True),
        ]
        self.message_mocks = [patcher.start() for patcher in self.message_patches]
        self.clipboard_payload = ("empty", None, None)

        def reader():
            return self.clipboard_payload

        self.settings_path = self.tmp / "settings.json"
        self.app = gui.SourceIntakeApp(
            self.tk,
            settings_path=self.settings_path,
            clipboard_reader=reader,
        )
        self.tk.update_idletasks()

    def tearDown(self):
        try:
            if getattr(self, "tk", None) is not None and self.tk.winfo_exists():
                self.tk.destroy()
        except tk.TclError:
            pass
        for patcher in getattr(self, "message_patches", []):
            patcher.stop()
        super().tearDown()

    def test_tk_initializes_with_chinese_ui_and_controls(self):
        self.assertEqual(self.tk.title(), gui.APP_TITLE)
        self.assertEqual(self.app.conflict_var.get(), "skip")
        self.assertEqual(self.app.profile_var.get(), "hisui")
        self.assertEqual(self.app.route_vars["csp"].get(), "frontend/csp")
        self.assertEqual(self.app.route_vars["cls"].get(), "backend")
        for widget in (
            self.app.collect_button,
            self.app.files_button,
            self.app.paste_button,
            self.app.preview_button,
            self.app.import_button,
        ):
            self.assertTrue(widget.winfo_exists())
        self.assertEqual(len(self.app.tree.get_children()), 0)

    def test_collect_clipboard_edit_preview_and_import(self):
        code = "function clipboardCode() { return 1; }\r\n"
        self.clipboard_payload = ("text", code, None)
        self.app.set_root_directory(str(self.root))
        self.app.req_id_var.set("BOS-GUI-1")
        self.app.req_title_var.set("GUI 烟测")
        self.app.on_collect_clipboard()
        self.assertEqual(len(self.app.source_entries), 1)
        self.assertFalse(self.root.exists())
        entry = self.app.source_entries[0]
        self.app._apply_entry_edit(entry, "clipboard.js", "")
        plan = self.app.run_preview()
        self.assertIsNotNone(plan)
        self.assertFalse(self.root.exists())
        self.assertEqual(plan["items"][0]["status"], "ready")
        self.assertEqual(
            plan["items"][0]["destination"],
            "frontend/scripts/dhcnewpro/dhcemc/clipboard.js",
        )
        result = self.app.run_import()
        self.assertIsNotNone(result)
        self.assertTrue(result["ok"], result)
        target = self.root / "frontend/scripts/dhcnewpro/dhcemc/clipboard.js"
        self.assertEqual(target.read_bytes(), code.encode("utf-8"))
        self.assertEqual(self.app.tree.item(entry.uid, "values")[4], "已写入")
        settings_text = self.settings_path.read_text(encoding="utf-8")
        settings = json.loads(settings_text)
        self.assertTrue(settings["recent_roots"])
        self.assertTrue(settings["projects"])
        self.assertNotIn("clipboardCode", settings_text)

    def test_pending_source_preview_import_does_not_create_root(self):
        self.app.set_root_directory(str(self.root))
        self.app.add_text("function unnamedGui() { return 1; }\n")
        plan = self.app.run_preview()
        self.assertIsNotNone(plan)
        self.assertEqual(plan["items"][0]["status"], "pending")
        self.assertFalse(self.root.exists())
        result = self.app.run_import()
        self.assertIsNone(result)
        self.assertFalse(self.root.exists())

    def test_directory_dialog_cancel_never_imports(self):
        self.app.set_root_directory(str(self.root))
        self.app.add_text("function cancelTest() { return 1; }\n", filename="cancel.js")
        with mock.patch.object(gui.filedialog, "askdirectory", return_value=""):
            self.app.on_browse_directory()
        self.assertEqual(self.app.root_dir_var.get(), str(self.root))
        self.assertFalse(self.root.exists())
        self.assertIsNone(self.app._preview_state)

    def test_edit_delete_clear_and_backup_replace_flow(self):
        old_content = b"function oldGui() { return 0; }\r\n"
        new_content = b"function newGui() { return 1; }\r\n"
        target = self.root / "frontend/scripts/dhcnewpro/dhcemc/gui.js"
        target.parent.mkdir(parents=True)
        target.write_bytes(old_content)
        source = self.write_source("gui.js", new_content)
        self.app.set_root_directory(str(self.root))
        self.app.add_paths([source])
        entry = self.app.source_entries[0]
        self.app._apply_entry_edit(entry, "", "frontend/scripts/dhcnewpro/dhcemc/gui.js")
        plan = self.app.run_preview()
        self.assertIsNotNone(plan)
        self.assertEqual(plan["items"][0]["status"], "exists")
        self.app.conflict_var.set("backup-replace")
        result = self.app.run_import()
        self.assertIsNotNone(result)
        self.assertTrue(result["ok"], result)
        self.assertEqual(target.read_bytes(), new_content)
        self.assertEqual(self.app.tree.item(entry.uid, "values")[4], "已备份替换")

        self.app.add_text("function second() { return 2; }\n", filename="second.js")
        self.assertEqual(len(self.app.source_entries), 2)
        self.app.tree.selection_set(entry.uid)
        self.app.on_delete_selected()
        self.assertEqual(len(self.app.source_entries), 1)
        self.app.on_clear_sources()
        self.assertEqual(len(self.app.source_entries), 0)
        self.assertEqual(len(self.app.tree.get_children()), 0)

    def test_project_rules_are_remembered_across_app_instances(self):
        self.app.set_root_directory(str(self.root))
        self.app.profile_var.set("flat")
        self.app.on_profile_changed()
        self.assertEqual(self.app.route_vars["csp"].get(), "")
        self.app.route_vars["js"].set("custom/js")
        self.app.add_text("function routed() { return 1; }\n", filename="routed.js")
        plan = self.app.run_preview()
        self.assertIsNotNone(plan)
        self.assertEqual(plan["items"][0]["destination"], "custom/js/routed.js")
        self.app.on_close()

        tk2 = tk.Tk()
        tk2.withdraw()
        try:
            app2 = gui.SourceIntakeApp(
                tk2,
                settings_path=self.settings_path,
                clipboard_reader=lambda: ("empty", None, None),
            )
            app2.set_root_directory(str(self.root), load_saved=True)
            self.assertEqual(app2.profile_var.get(), "flat")
            self.assertEqual(app2.route_vars["js"].get(), "custom/js")
            app2.on_close()
        finally:
            try:
                if tk2.winfo_exists():
                    tk2.destroy()
            except tk.TclError:
                pass
        self.tk = None


if __name__ == "__main__":
    unittest.main()
