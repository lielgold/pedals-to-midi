# /// script
# dependencies = [
#     "hidapi",
#     "mido",
#     "python-rtmidi",
# ]
# ///
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
import hid
import mido

CONFIG_FILE: str = "pedal_config.json"

# Common MIDI CC Options for Selection Dropdowns
CC_OPTIONS: dict[str, int] = {
	"CC 1 - Modulation Wheel": 1,
	"CC 11 - Expression": 11,
	"CC 64 - Sustain / Damper Pedal": 64,
	"CC 7 - Main Volume": 7,
	"CC 10 - Pan": 10,
	"CC 2 - Breath Controller": 2,
	"CC 74 - Brightness / Cutoff": 74,
	"Disabled (Send Nothing)": -1,
}

CC_LOOKUP: dict[int, str] = {val: key for key, val in CC_OPTIONS.items()}

DEFAULT_CONFIG: dict[str, int | str] = {
	"x_cc": "CC 11 - Expression",
	"y_cc": "CC 7 - Main Volume",
	"gas_cc": "CC 1 - Modulation Wheel",
	"brake_cc": "CC 64 - Sustain / Damper Pedal",
	"deadzone_x": 10,
	"deadzone_y": 10,
	"deadzone_z": 15,
}


class PedalMIDIApp:

	def __init__(self, root: tk.Tk) -> None:
		self.root: tk.Tk = root
		self.root.title("CH Pedal MIDI Bridge")
		self.root.geometry("520x620")
		self.root.resizable(False, False)

		# Hardware Parameters (CH Pro USB)
		self.vendor_id: int = 0x068E
		self.product_id: int = 0x00F2

		# Hardware byte limits
		self.center_val_z: int = 127
		self.pos_max: int = 254
		self.neg_min: int = 0

		# Operational State
		self.is_active: bool = False
		self.is_running: bool = False
		self.worker_thread: threading.Thread | None = None

		# Load Saved JSON Settings
		self.config: dict[str, int | str] = self._load_config()

		# Build UI Controls
		self._create_widgets()

		# Auto-start worker thread
		self.start_bridge()

	def _load_config(self) -> dict[str, int | str]:
		if os.path.exists(CONFIG_FILE):
			try:
				with open(CONFIG_FILE, "r", encoding="utf-8") as f:
					data = json.load(f)
					merged: dict[str, int | str] = DEFAULT_CONFIG.copy()
					merged.update(data)
					return merged
			except Exception as err:
				print(f"Error loading config, using defaults: {err}")
		return DEFAULT_CONFIG.copy()

	def _save_config(self) -> None:
		try:
			# Capture current UI states
			self.config["x_cc"] = self.x_combo.get()
			self.config["y_cc"] = self.y_combo.get()
			self.config["gas_cc"] = self.gas_combo.get()
			self.config["brake_cc"] = self.brake_combo.get()
			self.config["deadzone_x"] = int(self.spin_dz_x.get())
			self.config["deadzone_y"] = int(self.spin_dz_y.get())
			self.config["deadzone_z"] = int(self.spin_dz_z.get())

			with open(CONFIG_FILE, "w", encoding="utf-8") as f:
				json.dump(self.config, f, indent=4)
			self.log("Settings saved to pedal_config.json")
		except Exception as err:
			print(f"Failed to save config: {err}")

	def _create_widgets(self) -> None:
		# Top Toggle Button (State)
		toggle_frame = ttk.LabelFrame(self.root, text=" MIDI Bridge State ", padding=10)
		toggle_frame.pack(fill="x", padx=15, pady=8)

		self.btn_toggle = tk.Button(
			toggle_frame,
			text="ENABLE BRIDGE (OFF)",
			bg="#f44336",
			fg="white",
			font=("Segoe UI", 11, "bold"),
			height=2,
			command=self._toggle_active_state,
		)
		self.btn_toggle.pack(fill="x", expand=True)

		# Tabbed Interface Container
		self.notebook = ttk.Notebook(self.root)
		self.notebook.pack(fill="x", padx=15, pady=5)

		# --- Tab 1: Mappings & Live Bridge ---
		tab_bridge = ttk.Frame(self.notebook, padding=10)
		self.notebook.add(tab_bridge, text=" Mappings ")

		# X Axis (Left Toe Brake)
		ttk.Label(tab_bridge, text="X Axis (Left Toe Brake):").grid(
			row=0, column=0, sticky="w", pady=5
		)
		self.x_combo = ttk.Combobox(
			tab_bridge,
			values=list(CC_OPTIONS.keys()),
			state="readonly",
			width=28,
		)
		self.x_combo.set(str(self.config["x_cc"]))
		self.x_combo.grid(row=0, column=1, padx=10, pady=5)

		# Y Axis (Right Toe Brake)
		ttk.Label(tab_bridge, text="Y Axis (Right Toe Brake):").grid(
			row=1, column=0, sticky="w", pady=5
		)
		self.y_combo = ttk.Combobox(
			tab_bridge,
			values=list(CC_OPTIONS.keys()),
			state="readonly",
			width=28,
		)
		self.y_combo.set(str(self.config["y_cc"]))
		self.y_combo.grid(row=1, column=1, padx=10, pady=5)

		# Z Axis (+) (Rudder Slide Forward / Gas)
		ttk.Label(tab_bridge, text="Z Axis Positive (Gas Pedal):").grid(
			row=2, column=0, sticky="w", pady=5
		)
		self.gas_combo = ttk.Combobox(
			tab_bridge,
			values=list(CC_OPTIONS.keys()),
			state="readonly",
			width=28,
		)
		self.gas_combo.set(str(self.config["gas_cc"]))
		self.gas_combo.grid(row=2, column=1, padx=10, pady=5)

		# Z Axis (-) (Rudder Slide Backward / Brake)
		ttk.Label(tab_bridge, text="Z Axis Negative (Brake Pedal):").grid(
			row=3, column=0, sticky="w", pady=5
		)
		self.brake_combo = ttk.Combobox(
			tab_bridge,
			values=list(CC_OPTIONS.keys()),
			state="readonly",
			width=28,
		)
		self.brake_combo.set(str(self.config["brake_cc"]))
		self.brake_combo.grid(row=3, column=1, padx=10, pady=5)

		# --- Tab 2: Axis Deadzones ---
		tab_deadzones = ttk.Frame(self.notebook, padding=10)
		self.notebook.add(tab_deadzones, text=" Deadzone Calibration ")

		# Calibration Explanation Box
		info_frame = ttk.LabelFrame(
			tab_deadzones, text=" How Deadzones Work ", padding=8
		)
		info_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))

		info_text = (
			"• X & Y Axes (Toe Brakes): Resting hardware state is 0. Deadzone "
			"ignores small foot pressure until raw input exceeds the threshold.\n"
			"• Z Axis (Rudder Slide): Resting hardware state is 127 (center). "
			"Deadzone creates a +/- buffer zone around the center to prevent idle drift."
		)
		lbl_info = ttk.Label(
			info_frame, text=info_text, justify="left", wraplength=420
		)
		lbl_info.pack(anchor="w")

		# X Axis Deadzone
		ttk.Label(tab_deadzones, text="X Axis Deadzone (Left Brake):").grid(
			row=1, column=0, sticky="w", pady=5
		)
		self.spin_dz_x = ttk.Spinbox(tab_deadzones, from_=0, to=50, width=8)
		self.spin_dz_x.set(self.config["deadzone_x"])
		self.spin_dz_x.grid(row=1, column=1, padx=10, pady=5, sticky="w")

		# Y Axis Deadzone
		ttk.Label(tab_deadzones, text="Y Axis Deadzone (Right Brake):").grid(
			row=2, column=0, sticky="w", pady=5
		)
		self.spin_dz_y = ttk.Spinbox(tab_deadzones, from_=0, to=50, width=8)
		self.spin_dz_y.set(self.config["deadzone_y"])
		self.spin_dz_y.grid(row=2, column=1, padx=10, pady=5, sticky="w")

		# Z Axis Deadzone
		ttk.Label(tab_deadzones, text="Z Axis Deadzone (Center Slide):").grid(
			row=3, column=0, sticky="w", pady=5
		)
		self.spin_dz_z = ttk.Spinbox(tab_deadzones, from_=0, to=50, width=8)
		self.spin_dz_z.set(self.config["deadzone_z"])
		self.spin_dz_z.grid(row=3, column=1, padx=10, pady=5, sticky="w")

		# Frame 3: Status Indicators
		status_frame = ttk.LabelFrame(self.root, text=" Live Diagnostics ", padding=10)
		status_frame.pack(fill="x", padx=15, pady=5)

		self.lbl_status = ttk.Label(
			status_frame, text="Status: BYPASSED", font=("Segoe UI", 10, "bold")
		)
		self.lbl_status.pack(anchor="w", pady=2)

		self.lbl_raw = ttk.Label(
			status_frame, text="Raw Bytes -> X: 0 | Y: 0 | Z: 127"
		)
		self.lbl_raw.pack(anchor="w", pady=2)

		self.lbl_x_out = ttk.Label(status_frame, text="X Output: --")
		self.lbl_x_out.pack(anchor="w", pady=1)

		self.lbl_y_out = ttk.Label(status_frame, text="Y Output: --")
		self.lbl_y_out.pack(anchor="w", pady=1)

		self.lbl_gas_out = ttk.Label(status_frame, text="Gas Output (Z+): --")
		self.lbl_gas_out.pack(anchor="w", pady=1)

		self.lbl_brake_out = ttk.Label(status_frame, text="Brake Output (Z-): --")
		self.lbl_brake_out.pack(anchor="w", pady=1)

		# Frame 4: System Log Output
		log_frame = ttk.LabelFrame(self.root, text=" Log ", padding=5)
		log_frame.pack(fill="both", expand=True, padx=15, pady=8)

		self.log_text = tk.Text(log_frame, height=4, state="disabled", font=("Consolas", 9))
		self.log_text.pack(fill="both", expand=True)

		self.root.protocol("WM_DELETE_WINDOW", self.on_close)

	def _toggle_active_state(self) -> None:
		self.is_active = not self.is_active
		if self.is_active:
			self.btn_toggle.config(text="DISABLE BRIDGE (ACTIVE)", bg="#4CAF50")
			self.lbl_status.config(text="Status: ACTIVE")
			self.log("Bridge ENABLED.")
		else:
			self.btn_toggle.config(text="ENABLE BRIDGE (OFF)", bg="#f44336")
			self.lbl_status.config(text="Status: BYPASSED")
			self.log("Bridge DISABLED.")

	def log(self, message: str) -> None:
		self.log_text.config(state="normal")
		self.log_text.insert(tk.END, message + "\n")
		self.log_text.see(tk.END)
		self.log_text.config(state="disabled")

	def start_bridge(self) -> None:
		self.is_running = True
		self.worker_thread = threading.Thread(target=self._midi_worker, daemon=True)
		self.worker_thread.start()

	def _midi_worker(self) -> None:
		try:
			port_name: str = [
				name for name in mido.get_output_names() if "PedalMIDI" in name
			][0]
			outport = mido.open_output(port_name)
			self.log(f"Connected to {port_name}")
		except IndexError:
			self.log("ERROR: PedalMIDI port not found! Please start PedalMIDI.")
			return

		try:
			h = hid.device()
			h.open(self.vendor_id, self.product_id)
			h.set_nonblocking(True)
			self.log("Connected to CH Pro Pedals!")
		except Exception as err:
			self.log(f"Hardware Error: {err}")
			return

		last_x_sent: int = -1
		last_y_sent: int = -1
		last_gas_sent: int = -1
		last_brake_sent: int = -1
		was_active: bool = False

		while self.is_running:
			x_cc: int = CC_OPTIONS.get(self.x_combo.get(), -1)
			y_cc: int = CC_OPTIONS.get(self.y_combo.get(), -1)
			gas_cc: int = CC_OPTIONS.get(self.gas_combo.get(), -1)
			brake_cc: int = CC_OPTIONS.get(self.brake_combo.get(), -1)

			try:
				deadzone_x: int = int(self.spin_dz_x.get())
				deadzone_y: int = int(self.spin_dz_y.get())
				deadzone_z: int = int(self.spin_dz_z.get())
			except ValueError:
				deadzone_x, deadzone_y, deadzone_z = 10, 10, 15

			if not self.is_active:
				if (
					was_active
					or last_x_sent != -1
					or last_y_sent != -1
					or last_gas_sent != -1
					or last_brake_sent != -1
				):
					# Reset all mapped CCs when toggled OFF
					for cc in (x_cc, y_cc, gas_cc, brake_cc):
						if cc != -1:
							reset_val: int = 127 if cc == 11 else 0
							outport.send(
								mido.Message(
									"control_change", channel=0, control=cc, value=reset_val
								)
							)

					last_x_sent = -1
					last_y_sent = -1
					last_gas_sent = -1
					last_brake_sent = -1
					self.root.after(0, self.lbl_x_out.config, {"text": "X Output: Reset"})
					self.root.after(0, self.lbl_y_out.config, {"text": "Y Output: Reset"})
					self.root.after(0, self.lbl_gas_out.config, {"text": "Gas Output: Reset"})
					self.root.after(0, self.lbl_brake_out.config, {"text": "Brake Output: Reset"})

				was_active = False
				time.sleep(0.05)
				continue

			was_active = True

			data = h.read(64)
			if data and len(data) >= 3:
				raw_x: int = data[0]  # Left Toe Brake
				raw_y: int = data[1]  # Right Toe Brake
				raw_z: int = data[2]  # Rudder Slide

				x_val: int = 0
				y_val: int = 0
				gas_val: int = 0
				brake_val: int = 0

				# 1. Process X Axis (Positive Single-Direction: 0..255)
				if raw_x > deadzone_x:
					clamped_x: int = min(raw_x, self.pos_max)
					scaled_x: float = (clamped_x - deadzone_x) / float(
						self.pos_max - deadzone_x
					)
					x_val = int(scaled_x * 127)

				# 2. Process Y Axis (Positive Single-Direction: 0..255)
				if raw_y > deadzone_y:
					clamped_y: int = min(raw_y, self.pos_max)
					scaled_y: float = (clamped_y - deadzone_y) / float(
						self.pos_max - deadzone_y
					)
					y_val = int(scaled_y * 127)

				# 3. Process Z Axis (Dual-Direction from Center 127)
				pos_threshold: int = self.center_val_z + deadzone_z
				neg_threshold: int = self.center_val_z - deadzone_z

				if raw_z > pos_threshold:
					clamped_pos: int = min(raw_z, self.pos_max)
					scaled_pos: float = (clamped_pos - pos_threshold) / float(
						self.pos_max - pos_threshold
					)
					gas_val = int(scaled_pos * 127)
				elif raw_z < neg_threshold:
					clamped_neg: int = max(raw_z, self.neg_min)
					scaled_neg: float = (neg_threshold - clamped_neg) / float(
						neg_threshold - self.neg_min
					)
					if brake_cc == 64:
						brake_val = 127
					else:
						brake_val = int(scaled_neg * 127)

				# Dispatch MIDI Updates
				if x_cc != -1 and x_val != last_x_sent:
					outport.send(
						mido.Message("control_change", channel=0, control=x_cc, value=x_val)
					)
					last_x_sent = x_val

				if y_cc != -1 and y_val != last_y_sent:
					outport.send(
						mido.Message("control_change", channel=0, control=y_cc, value=y_val)
					)
					last_y_sent = y_val

				if gas_cc != -1 and gas_val != last_gas_sent:
					outport.send(
						mido.Message("control_change", channel=0, control=gas_cc, value=gas_val)
					)
					last_gas_sent = gas_val

				if brake_cc != -1 and brake_val != last_brake_sent:
					outport.send(
						mido.Message(
							"control_change", channel=0, control=brake_cc, value=brake_val
						)
					)
					last_brake_sent = brake_val

				# Update Diagnostic Labels
				self.root.after(
					0,
					self.lbl_raw.config,
					{"text": f"Raw Bytes -> X: {raw_x} | Y: {raw_y} | Z: {raw_z}"},
				)
				self.root.after(
					0,
					self.lbl_x_out.config,
					{"text": f"X Output ({self.x_combo.get()}): {x_val}"},
				)
				self.root.after(
					0,
					self.lbl_y_out.config,
					{"text": f"Y Output ({self.y_combo.get()}): {y_val}"},
				)
				self.root.after(
					0,
					self.lbl_gas_out.config,
					{"text": f"Gas Output ({self.gas_combo.get()}): {gas_val}"},
				)
				self.root.after(
					0,
					self.lbl_brake_out.config,
					{"text": f"Brake Output ({self.brake_combo.get()}): {brake_val}"},
				)

			time.sleep(0.005)

		h.close()

	def on_close(self) -> None:
		self._save_config()
		self.is_running = False
		self.root.destroy()


if __name__ == "__main__":
	root = tk.Tk()
	app = PedalMIDIApp(root)
	root.mainloop()