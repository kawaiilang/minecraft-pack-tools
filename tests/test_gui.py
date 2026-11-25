"""Exercise actual GTK callbacks and async Gio subprocesses with a local fake CLI."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from minecraft_pack import gui as ui


class GuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ui.Gtk.Application(
            application_id="io.github.kawaiilang.MinecraftPackManager.Test",
            flags=ui.Gio.ApplicationFlags.NON_UNIQUE,
        )
        cls.app.register(None)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.calls = self.root / "calls.jsonl"
        fake = (
            f"#!{sys.executable}\n"
            + """import json, pathlib, sys, time
args = sys.argv[1:]
root = pathlib.Path(__file__).parent
with (root / 'calls.jsonl').open('a') as calls:
    calls.write(json.dumps([pathlib.Path(sys.argv[0]).name, *args]) + '\\n')
operation = args[2]
if operation == 'list':
    print(json.dumps({'name': 'Fixture', 'minecraft': '26.3', 'fabric': '0.19.5', 'entries': [
        {'id': 'example', 'name': 'Example', 'side': 'both', 'pinned': False, 'version': 'v1', 'filename': 'example.jar', 'category': 'mods'}]}))
else:
    time.sleep(0.1)
    if 'broken' in args:
        print('incompatible release; original retained', file=sys.stderr)
        sys.exit(7)
    print('fixture operation complete')
"""
        )
        executable = self.root / "minecraft-pack"
        executable.write_text(fake)
        executable.chmod(0o755)
        self.window = ui.ManagerWindow(
            self.app, self.root / "pack with spaces", self.root
        )
        self.wait_idle()

    def tearDown(self):
        self.window.destroy()
        self.temp.cleanup()

    def wait_idle(self):
        deadline = time.monotonic() + 8
        while self.window.busy:
            while ui.GLib.MainContext.default().iteration(False):
                pass
            if time.monotonic() > deadline:
                self.fail("GUI subprocess remained busy")
            time.sleep(0.005)

    def calls_list(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_routing_and_callbacks(self):
        self.assertEqual(len(self.window.rows), 1)
        self.window.listbox.select_row(self.window.listbox.get_row_at_index(0))
        self.assertEqual(self.calls_list()[-1][0], "minecraft-pack")
        self.assertTrue(self.window.side_button.get_visible())
        self.window.listbox.select_row(self.window.listbox.get_row_at_index(0))
        self.window.side.set_selected(ui.SIDES.index("server"))
        self.window.change_side()
        self.wait_idle()
        self.assertIn(
            [
                "minecraft-pack",
                "--pack-dir",
                str(self.root / "pack with spaces"),
                "side",
                "example",
                "server",
            ],
            self.calls_list(),
        )
        self.window.project.set_text("example")
        self.window.version.set_text("v2")
        self.window.install()
        self.assertTrue(self.window.on_close())
        self.window.run("validate")  # Ignored while busy.
        self.wait_idle()
        self.assertIn(
            [
                "minecraft-pack",
                "--pack-dir",
                str(self.root / "pack with spaces"),
                "install",
                "example",
                "v2",
            ],
            self.calls_list(),
        )
        self.assertNotIn("validate", [call[3] for call in self.calls_list()])
        self.window.listbox.select_row(self.window.listbox.get_row_at_index(0))
        self.window.run_selected("update")
        self.wait_idle()
        self.window.run("validate")
        self.wait_idle()
        self.assertEqual(self.window.status.get_text(), "検証に成功しました。")
        self.window.listbox.select_row(self.window.listbox.get_row_at_index(0))
        self.window.confirm = lambda _text, callback: callback()
        self.window.remove()
        self.wait_idle()
        self.assertIn("remove", [call[3] for call in self.calls_list()])

    def test_failure_and_pack_dir_change(self):
        self.window.project.set_text("broken")
        self.window.install()
        self.wait_idle()
        self.assertIn("失敗", self.window.status.get_text())
        buffer = self.window.log.get_buffer()
        self.assertIn(
            "original retained",
            buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True),
        )
        self.assertFalse(self.window.busy)
        self.window.pack_dir.set_text(str(self.root / "another pack"))
        self.assertFalse(self.window.loaded)
        self.assertFalse(self.window.actions.get_sensitive())


if __name__ == "__main__":
    unittest.main()
