import sys
import os
import subprocess
import shutil
import urllib.request
import json
import ssl
from PyQt6.QtCore import Qt, QSize, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QFont
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QPushButton, QLabel, QMessageBox, QHBoxLayout, QDialog, QTextEdit, QCheckBox)

class UpdateCheckerThread(QThread):
    update_checked = pyqtSignal(str, str)

    def __init__(self, repo_owner, repo_name, current_version):
        super().__init__()
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.current_version = current_version

    def run(self):
        url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/releases/latest"
        try:
            req = urllib.request.Request(
                url, 
                headers={"User-Agent": "Decky-Sunshine-Res-Helper-Updater"}
            )
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=4, context=ctx) as response:
                data = json.loads(response.read().decode())
                latest_tag = data.get("tag_name", "").strip()
                html_url = data.get("html_url", "").strip()
                if latest_tag and latest_tag != self.current_version:
                    self.update_checked.emit(latest_tag, html_url)
        except Exception:
            pass


class InstallerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Decky Sunshine Res-Helper Installer")
        self.setFixedWidth(420)
        # This will be replaced during the GitHub Action build
        self.current_app_version = "DEV"
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(15)
        
        title_label = QLabel("Decky Sunshine Res-Helper")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(title_label)
        
        rc_label = QLabel("Release Candidate 4")
        rc_label.setStyleSheet("color: #aaaaaa; font-style: italic;")
        rc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(rc_label)
        
        desc_label = QLabel("Install, Update, or Uninstall the Res-Helper service.\nRequires root privileges.")
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setWordWrap(True)
        self.layout.addWidget(desc_label)
        
        self.status_label = QLabel("Status: Checking...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(self.status_label)
        
        self.layout.addSpacing(10)
        
        self.adv_btn = QPushButton("⚙️ Advanced Options")
        self.adv_btn.setCheckable(True)
        self.adv_btn.setStyleSheet("text-align: left; padding: 5px;")
        
        self.adv_widget = QWidget()
        adv_layout = QVBoxLayout(self.adv_widget)
        adv_layout.setContentsMargins(10, 0, 0, 0)
        
        self.config_path = os.path.expanduser("~/.local/share/decky-sunshine-res-helper/config.conf")
        self.default_config = {"enable_hdr": True, "native_res": True, "force_composite": False}
        self.saved_config = self.default_config.copy()
        
        if os.path.exists(self.config_path):
            try:
                import configparser
                parser = configparser.ConfigParser()
                parser.read(self.config_path)
                if "Settings" in parser:
                    self.saved_config["enable_hdr"] = parser.getboolean("Settings", "enable_hdr", fallback=self.saved_config["enable_hdr"])
                    self.saved_config["native_res"] = parser.getboolean("Settings", "native_res", fallback=self.saved_config["native_res"])
                    self.saved_config["force_composite"] = parser.getboolean("Settings", "force_composite", fallback=self.saved_config["force_composite"])
            except Exception:
                pass
                
        self.cb_hdr = QCheckBox("Enable HDR Support")
        self.cb_hdr.setChecked(self.saved_config["enable_hdr"])
        adv_layout.addWidget(self.cb_hdr)
        
        self.cb_native = QCheckBox("Set Maximum Game Resolution to Native")
        self.cb_native.setChecked(self.saved_config["native_res"])
        adv_layout.addWidget(self.cb_native)
        
        self.cb_composite = QCheckBox("Force Composite (Fixes Black Screen)")
        self.cb_composite.setChecked(self.saved_config["force_composite"])
        adv_layout.addWidget(self.cb_composite)
        
        self.save_cfg_btn = QPushButton("Save Configuration")
        self.save_cfg_btn.setMinimumHeight(35)
        self.save_cfg_btn.setStyleSheet("""
            QPushButton { background-color: #2a82da; color: white; padding: 5px; }
            QPushButton:disabled { background-color: #555555; color: #aaaaaa; }
        """)
        self.save_cfg_btn.setEnabled(False)
        self.save_cfg_btn.clicked.connect(self._save_config_standalone)
        adv_layout.addWidget(self.save_cfg_btn)
        
        self.cb_hdr.toggled.connect(self._on_config_changed)
        self.cb_native.toggled.connect(self._on_config_changed)
        self.cb_composite.toggled.connect(self._on_config_changed)
        
        self.layout.addWidget(self.adv_btn)
        self.layout.addWidget(self.adv_widget)
        self.adv_widget.setVisible(False)
        
        def toggle_adv(checked):
            self.adv_widget.setVisible(checked)
            
        self.adv_btn.toggled.connect(toggle_adv)
        
        self.layout.addSpacing(10)
        
        self.install_btn = QPushButton("Install / Update")
        self.install_btn.setMinimumHeight(40)
        self.install_btn.clicked.connect(self.install)
        self.layout.addWidget(self.install_btn)
        
        self.uninstall_btn = QPushButton("Uninstall")
        self.uninstall_btn.setMinimumHeight(40)
        self.uninstall_btn.clicked.connect(self.uninstall)
        self.layout.addWidget(self.uninstall_btn)
        
        self.commands_btn = QPushButton("Show Sunshine Commands")
        self.commands_btn.setMinimumHeight(40)
        self.commands_btn.clicked.connect(self.show_commands)
        self.layout.addWidget(self.commands_btn)
        
        self.exit_btn = QPushButton("Exit")
        self.exit_btn.setMinimumHeight(40)
        self.exit_btn.clicked.connect(self.close)
        self.layout.addWidget(self.exit_btn)
        
        self.update_label = QLabel()
        self.update_label.setStyleSheet("color: #4da6ff; font-weight: bold;")
        self.update_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.update_label.hide()
        self.layout.addWidget(self.update_label)
        
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(2000)
        self.update_status()
        
        self.start_update_check("MisterAnderson91", "decky-sunshine-res-helper")
        
    def update_status(self):
        service_file = "/etc/systemd/system/decky-sunshine-res-helper.service"
        installed = os.path.exists(service_file)
        
        running = False
        if installed:
            try:
                # AppImages inject LD_LIBRARY_PATH which can break system binaries like systemctl.
                # Clear it out from the environment before running.
                env = os.environ.copy()
                env.pop("LD_LIBRARY_PATH", None)
                env.pop("APPDIR", None)
                result = subprocess.run(["systemctl", "is-active", "decky-sunshine-res-helper.service"], 
                                      capture_output=True, text=True, env=env)
                if result.stdout.strip() == "active":
                    running = True
            except Exception as e:
                print(f"Failed to check service status: {e}")
                
        version_file = os.path.expanduser("~/.local/share/decky-sunshine-res-helper/version.txt")
        installed_version = "unknown"
        if os.path.exists(version_file):
            try:
                with open(version_file, "r") as f:
                    installed_version = f.read().strip()
            except Exception:
                pass
                
        if not installed:
            status_text = "Status: Not Installed"
            color = "#ff4c4c"
        elif installed_version != self.current_app_version and self.current_app_version != "DEV":
            status_text = f"Status: Update Required (Installed: {installed_version})"
            color = "#ffa500"
        elif not running:
            status_text = "Status: Installed (Not Running)"
            color = "#ffa500"
        else:
            status_text = "Status: Installed and Running"
            color = "#4cff4c"
            
        self.status_label.setText(status_text)
        self.status_label.setStyleSheet(f"font-weight: bold; color: {color};")

    def start_update_check(self, owner, repo):
        self.update_thread = UpdateCheckerThread(owner, repo, self.current_app_version)
        self.update_thread.update_checked.connect(self.display_update_notification)
        self.update_thread.start()

    def display_update_notification(self, latest_version, url):
        self.release_url = url
        self.update_label.setText(f"🚀 Update v{latest_version} available!")
        self.update_label.mousePressEvent = lambda event: self.open_url(self.release_url)
        self.update_label.show()

    def open_url(self, url):
        env = os.environ.copy()
        for k in ["LD_LIBRARY_PATH", "APPDIR", "APPIMAGE"]:
            env.pop(k, None)
        try:
            subprocess.Popen(["xdg-open", url], env=env)
        except Exception:
            pass
        
    def show_commands(self):
        home = os.path.expanduser("~")
        do_cmd = 'sh -c "echo --connect,--width,\\${SUNSHINE_CLIENT_WIDTH},--height,\\${SUNSHINE_CLIENT_HEIGHT},--refresh-rate,\\${SUNSHINE_CLIENT_FPS},--hdr,\\${SUNSHINE_CLIENT_HDR} > /root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.in && cat /root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.out"'
        undo_cmd = 'sh -c "echo --disconnect > /root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.in && cat /root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.out"'
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Sunshine Configuration Commands")
        dialog.setFixedSize(650, 300)
        layout = QVBoxLayout(dialog)
        
        info = QLabel("These commands are added automatically by the installer. They are provided here in case they weren't added automatically (or if you need to copy them manually):")
        info.setWordWrap(True)
        layout.addWidget(info)
        
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(f"--- Do Command ---\n{do_cmd}\n\n--- Undo Command ---\n{undo_cmd}")
        layout.addWidget(text_edit)
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.exec()
        
    def run_script(self, script_name):
        import tempfile
        
        # When running from an AppImage (or PyInstaller one-dir), the script is alongside the executable
        base_path = os.path.dirname(os.path.abspath(__file__))
        if getattr(sys, 'frozen', False):
            base_path = os.path.dirname(sys.executable)
            
        script_path = os.path.join(base_path, script_name)
        
        if not os.path.exists(script_path):
            QMessageBox.critical(self, "Error", f"Could not find {script_name} at {script_path}")
            return False
            
        # AppImage uses FUSE mounts that root (pkexec) is not allowed to read by default.
        # To bypass this, we copy the needed files to a temporary directory in /tmp.
        try:
            temp_dir = tempfile.mkdtemp(prefix="decky_res_helper_")
            os.chmod(temp_dir, 0o755)
            
            for item in ["install.sh", "uninstall.sh", "src"]:
                src_item = os.path.join(base_path, item)
                dest_item = os.path.join(temp_dir, item)
                if os.path.exists(src_item):
                    if os.path.isdir(src_item):
                        shutil.copytree(src_item, dest_item)
                    else:
                        shutil.copy2(src_item, dest_item)
            
            temp_script_path = os.path.join(temp_dir, script_name)
            
            # We construct a bash command to run the script via sudo, and then wait for user input so the window doesn't immediately close
            bash_cmd = f"sudo bash {temp_script_path} '{self.current_app_version}'; echo ''; echo 'Press Enter to close this window...'; read"
            
            # Try to use konsole (SteamOS default), fallback to xterm if not available
            if shutil.which("konsole"):
                cmd = ["konsole", "-e", "bash", "-c", bash_cmd]
            elif shutil.which("xterm"):
                cmd = ["xterm", "-e", "bash", "-c", bash_cmd]
            else:
                QMessageBox.critical(self, "Error", "Could not find a terminal emulator (konsole or xterm).")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return False
                
            # Strip PyInstaller/AppImage environment variables so the system terminal doesn't crash 
            # trying to load bundled libraries.
            env = os.environ.copy()
            env.pop("LD_LIBRARY_PATH", None)
            env.pop("APPDIR", None)
            env.pop("APPIMAGE", None)
                
            # Block the GUI while the terminal is open, so we don't clean up the temp directory too early
            subprocess.run(cmd, cwd=temp_dir, env=env)
            
            shutil.rmtree(temp_dir, ignore_errors=True)
            
            # Immediately update the status once the terminal is closed
            self.update_status()
            
            return True
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to execute command:\n{str(e)}")
            return False

    def _save_config(self):
        import configparser
        parser = configparser.ConfigParser()
        parser["Settings"] = {
            "enable_hdr": str(self.cb_hdr.isChecked()),
            "native_res": str(self.cb_native.isChecked()),
            "force_composite": str(self.cb_composite.isChecked())
        }
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as f:
            parser.write(f)
            
    def _on_config_changed(self):
        changed = False
        def update_cb(cb, key):
            is_changed = cb.isChecked() != self.saved_config[key]
            font = cb.font()
            font.setBold(is_changed)
            cb.setFont(font)
            if is_changed:
                cb.setStyleSheet("color: #4da6ff;")
            else:
                cb.setStyleSheet("")
            return is_changed

        if update_cb(self.cb_hdr, "enable_hdr"): changed = True
        if update_cb(self.cb_native, "native_res"): changed = True
        if update_cb(self.cb_composite, "force_composite"): changed = True
        
        self.save_cfg_btn.setEnabled(changed)
        
    def _save_config_standalone(self):
        self._save_config()
        self.saved_config = {
            "enable_hdr": self.cb_hdr.isChecked(),
            "native_res": self.cb_native.isChecked(),
            "force_composite": self.cb_composite.isChecked()
        }
        self._on_config_changed()
        QMessageBox.information(self, "Configuration Saved", "Advanced options have been saved instantly.\\nThey will apply on your next Moonlight connection.")

    def install(self):
        self._save_config()
        self.run_script("install.sh")

    def uninstall(self):
        self.run_script("uninstall.sh")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Set a dark theme to match SteamOS aesthetic
    app.setStyle("Fusion")
    
    window = InstallerApp()
    window.show()
    sys.exit(app.exec())
