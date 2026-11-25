"""GTK4 view only: the guarded minecraft-pack CLI owns all pack operations."""

import argparse
import json
import os
import re
import shutil
from pathlib import Path

import gi

from .i18n import SUPPORTED_LOCALES, load_translator
from .settings import add_pack_dir_option, add_validation_options

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

APP_ID = "io.github.kawaiilang.MinecraftPackManager"
SIDES = ("client", "both", "server")


def resolve_cli_command(command_dir: str | Path | None) -> str:
    if command_dir:
        candidate = Path(command_dir) / "minecraft-pack"
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("minecraft-pack")
    if found:
        return found
    return "minecraft-pack"


class ManagerWindow(Gtk.ApplicationWindow):
    def __init__(
        self,
        app,
        pack_dir,
        command_dir=None,
        autoload=True,
        *,
        java_version=None,
        policy=None,
        lang=None,
    ):
        self.i18n = load_translator(explicit=lang)
        super().__init__(application=app, title=self.i18n.tr("window.title"))
        self.set_default_size(1060, 760)
        self.command_dir = Path(command_dir) if command_dir is not None else None
        self.validation_args = []
        if java_version is not None:
            self.validation_args.extend(["--java-version", java_version])
        if policy is not None:
            self.validation_args.extend(["--policy", str(policy)])
        self.busy = False
        self.loaded = False
        self.selected = None
        self.rows = []
        self.process = None
        self.connect("close-request", self.on_close)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for edge in ("top", "bottom", "start", "end"):
            getattr(root, "set_margin_" + edge)(18)
        self.set_child(root)
        title = Gtk.Label(label=self.i18n.tr("window.title"), xalign=0)
        title.add_css_class("title-1")
        root.append(title)
        root.append(
            Gtk.Label(
                label=self.i18n.tr("window.subtitle"),
                xalign=0,
                wrap=True,
            )
        )
        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.append(self.controls)
        source = Gtk.Box(spacing=8)
        self.controls.append(source)
        source.append(Gtk.Label(label=self.i18n.tr("source.label")))
        self.pack_dir = Gtk.Entry(text=str(pack_dir), hexpand=True)
        self.pack_dir.set_placeholder_text(self.i18n.tr("source.placeholder"))
        source.append(self.pack_dir)
        self.reload_button = self.button(
            source, self.i18n.tr("button.reload"), lambda: self.refresh()
        )
        java_hint = (
            self.i18n.tr("java.target", version=java_version)
            if java_version
            else self.i18n.tr("java.missing_hint")
        )
        self.policy = Gtk.Label(label=java_hint, xalign=0, wrap=True)
        root.append(self.policy)
        self.summary = Gtk.Label(xalign=0, wrap=True, selectable=True)
        root.append(self.summary)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-selected", self.on_select)
        scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=200)
        scroll.set_child(self.listbox)
        root.append(scroll)

        self.actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.controls.append(self.actions)
        install = Gtk.Box(spacing=8)
        self.actions.append(install)
        self.project = Gtk.Entry(
            placeholder_text=self.i18n.tr("input.project_placeholder"), hexpand=True
        )
        self.version = Gtk.Entry(
            placeholder_text=self.i18n.tr("input.version_placeholder"), hexpand=True
        )
        install.append(self.project)
        install.append(self.version)
        self.install_button = self.button(
            install, self.i18n.tr("button.install"), self.install
        )
        self.install_button.add_css_class("suggested-action")
        commands = Gtk.Box(spacing=8)
        self.actions.append(commands)
        self.update_button = self.button(
            commands,
            self.i18n.tr("button.update_selected"),
            lambda: self.run_selected("update"),
        )
        self.update_all_button = self.button(
            commands, self.i18n.tr("button.update_all"), self.update_all
        )
        self.remove_button = self.button(
            commands, self.i18n.tr("button.remove"), self.remove
        )
        self.remove_button.add_css_class("destructive-action")
        self.validate_button = self.button(
            commands, self.i18n.tr("button.validate"), lambda: self.run("validate")
        )
        self.side = Gtk.DropDown.new_from_strings(
            [self.i18n.side_label(side) for side in SIDES]
        )
        commands.append(self.side)
        self.side_button = self.button(
            commands, self.i18n.tr("button.change_side"), self.change_side
        )
        self.status = Gtk.Label(
            label=self.i18n.tr("status.initial"), xalign=0, wrap=True
        )
        root.append(self.status)
        self.spinner = Gtk.Spinner(halign=Gtk.Align.START)
        root.append(self.spinner)
        self.log = Gtk.TextView(
            editable=False,
            cursor_visible=False,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )
        details = Gtk.ScrolledWindow(min_content_height=150)
        details.set_child(self.log)
        root.append(details)
        self.pack_dir.connect("changed", self.source_changed)
        self.set_busy(False)
        if autoload:
            self.refresh()

    @staticmethod
    def button(box, label, callback):
        button = Gtk.Button(label=label)
        button.connect("clicked", lambda *_: callback())
        box.append(button)
        return button

    def source_changed(self, *_):
        self.loaded = False
        self.clear_rows()
        self.summary.set_text(self.i18n.tr("summary.changed"))
        self.set_busy(False)

    def clear_rows(self):
        self.selected = None
        self.rows = []
        while self.listbox.get_first_child() is not None:
            self.listbox.remove(self.listbox.get_first_child())

    def set_busy(self, busy):
        self.busy = busy
        self.side.set_visible(True)
        self.side_button.set_visible(True)
        self.controls.set_sensitive(not busy)
        self.listbox.set_sensitive(not busy)
        self.actions.set_sensitive(self.loaded)
        for button in (
            self.update_button,
            self.remove_button,
            self.side_button,
            self.side,
        ):
            button.set_sensitive(self.selected is not None)
        self.spinner.set_spinning(busy)

    def on_close(self, *_):
        if self.busy:
            self.status.set_text(self.i18n.tr("status.busy_close"))
        return self.busy

    def on_select(self, _box, row):
        self.selected = row.item if row is not None else None
        if self.selected:
            self.side.set_selected(SIDES.index(self.selected["side"]))
        self.set_busy(self.busy)

    def refresh(self, keep_result=False):
        if self.busy:
            return
        self.loaded = False
        self.clear_rows()
        self.run("list", "--json", keep_result=keep_result)

    def install(self):
        project = self.project.get_text().strip()
        version = self.version.get_text().strip()
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", project):
            self.status.set_text(self.i18n.tr("status.invalid_project"))
            return
        if version and not re.fullmatch(r"[A-Za-z0-9]+", version):
            self.status.set_text(self.i18n.tr("status.invalid_version"))
            return
        self.run("install", project, *([version] if version else []))

    def run_selected(self, operation):
        if self.selected:
            self.run(operation, self.selected["id"])

    def confirm(self, text, callback):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=text,
        )

        def answer(window, response):
            window.destroy()
            if response == Gtk.ResponseType.OK:
                callback()

        dialog.connect("response", answer)
        dialog.present()

    def update_all(self):
        self.confirm(
            self.i18n.tr("confirm.update_all"),
            lambda: self.run("update"),
        )

    def remove(self):
        if self.selected:
            item = self.selected.copy()
            self.confirm(
                self.i18n.tr("confirm.remove", name=item["name"]),
                lambda: self.run("remove", item["id"]),
            )

    def change_side(self):
        if self.selected:
            self.run("side", self.selected["id"], SIDES[self.side.get_selected()])

    def render(self, data):
        self.clear_rows()
        for item in data["entries"]:
            row = Gtk.ListBoxRow()
            row.item = item
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for edge in ("top", "bottom", "start", "end"):
                getattr(box, "set_margin_" + edge)(8)
            pin = self.i18n.tr("row.pinned_suffix") if item["pinned"] else ""
            destination = self.i18n.side_label(item["side"])
            box.append(
                Gtk.Label(
                    label=self.i18n.tr(
                        "row.title", name=item["name"], side=destination, pin=pin
                    ),
                    xalign=0,
                )
            )
            detail = Gtk.Label(
                label=self.i18n.tr(
                    "row.detail",
                    category=item["category"],
                    id=item["id"],
                    version=item["version"],
                    filename=item["filename"],
                ),
                xalign=0,
                wrap=True,
            )
            detail.add_css_class("dim-label")
            box.append(detail)
            row.set_child(box)
            self.listbox.append(row)
            self.rows.append(item)
        self.summary.set_text(
            self.i18n.tr(
                "summary.loaded",
                name=data["name"],
                minecraft=data["minecraft"],
                fabric=data["fabric"],
                count=len(self.rows),
            )
        )
        self.loaded = True

    def run(self, operation, *args, keep_result=False):
        if self.busy:
            return
        self.set_busy(True)
        if not keep_result:
            self.status.set_text(
                self.i18n.tr("status.running", operation=operation)
            )
            self.log.get_buffer().set_text("")
        argv = [
            resolve_cli_command(self.command_dir),
            "--pack-dir",
            self.pack_dir.get_text(),
            *self.validation_args,
            operation,
            *args,
        ]
        try:
            self.process = Gio.Subprocess.new(
                argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            )
            self.process.communicate_utf8_async(
                None, None, self.finished, (operation, keep_result)
            )
        except GLib.Error as error:
            self.status.set_text(
                self.i18n.tr("status.launch_failed", error=error.message)
            )
            self.set_busy(False)

    def finished(self, process, result, context):
        operation, keep_result = context
        success = False
        try:
            _ok, stdout, stderr = process.communicate_utf8_finish(result)
            success = process.get_successful()
            output = "\n".join(part for part in (stdout, stderr) if part)
            output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
            if operation == "list" and success:
                self.render(json.loads(stdout))
                if not keep_result:
                    self.status.set_text(self.i18n.tr("status.list_loaded"))
            else:
                self.log.get_buffer().set_text(output or self.i18n.tr("log.empty"))
                if success:
                    self.status.set_text(
                        self.i18n.tr(
                            "status.validate_succeeded"
                            if operation == "validate"
                            else "status.update_succeeded"
                        )
                    )
                else:
                    self.status.set_text(self.i18n.tr("status.operation_failed"))
        except (GLib.Error, ValueError, KeyError, TypeError) as error:
            success = False
            self.status.set_text(self.i18n.tr("status.parse_failed", error=error))
        self.process = None
        self.set_busy(False)
        if success and operation not in ("list", "validate"):
            self.refresh(keep_result=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="minecraft-pack-manager",
        description="GTK4 front-end for minecraft-pack.",
    )
    add_pack_dir_option(parser)
    add_validation_options(parser)
    parser.add_argument(
        "--lang",
        choices=SUPPORTED_LOCALES,
        default=None,
        help="UI language override (default: detect from system locale)",
    )
    args = parser.parse_args(argv)
    app = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect(
        "activate",
        lambda application: ManagerWindow(
            application,
            args.pack_dir,
            os.environ.get("MINECRAFT_PACK_BIN"),
            java_version=args.java_version,
            policy=args.policy,
            lang=args.lang,
        ).present(),
    )
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())
