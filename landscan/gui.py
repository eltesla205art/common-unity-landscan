"""Desktop window for Common Unity LandScan (CustomTkinter)."""

from __future__ import annotations

import json
import os
import platform
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

from . import APP_NAME, ORG, SITE, VERSION
from .pipeline import Cancelled, JobSettings, Pipeline, StepError, VisSettings
from .tools import check_environment

PREFS = Path.home() / ".common-unity-landscan.json"

# Palette — matches barnarium.com
NIGHT, NIGHT2, NIGHT3 = "#0B0F0C", "#10150F", "#171E16"
BONE, BONE2, INK2, INK3 = "#EFEADB", "#E5DFCC", "#B9B7A6", "#8C8C7D"
AMBER, AMBER_HI, OLIVE = "#E0A03A", "#F5C463", "#8FBE3A"
RED = "#D8735A"


def _load_prefs() -> dict:
    try:
        return json.loads(PREFS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_prefs(d: dict):
    try:
        PREFS.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _open_path(p: str):
    try:
        if platform.system() == "Windows":
            os.startfile(p)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception as e:  # noqa: BLE001
        messagebox.showinfo(APP_NAME, f"Open this folder yourself:\n{p}\n\n({e})")


def run_gui() -> int:
    try:
        import customtkinter as ctk
    except ImportError:
        print("The desktop window needs the 'customtkinter' package: pip install customtkinter\n"
              "Or run from the command line: landscan <folder-of-tiles>")
        return 1

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    app = ctk.CTk()
    app.title(f"{APP_NAME} {VERSION}")
    app.geometry("1180x760")
    app.minsize(980, 640)
    app.configure(fg_color=NIGHT)
    try:
        icon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "assets" / "icon.ico"
        if icon.exists() and platform.system() == "Windows":
            app.iconbitmap(str(icon))
    except Exception:  # noqa: BLE001
        pass

    prefs = _load_prefs()
    state = {"thread": None, "cancel": threading.Event(), "q": queue.Queue(), "out": None, "running": False}

    def font(size=13, weight="normal", family=None):
        return ctk.CTkFont(family=family or ("Segoe UI" if platform.system() == "Windows" else None), size=size, weight=weight)

    mono = ctk.CTkFont(family="Consolas" if platform.system() == "Windows" else "Menlo", size=11)

    # ── header ────────────────────────────────────────────────────────────
    header = ctk.CTkFrame(app, fg_color=NIGHT2, corner_radius=0, height=74)
    header.pack(fill="x"); header.pack_propagate(False)
    ctk.CTkLabel(header, text="COMMON UNITY  ·  LANDSCAN", font=font(11, "bold"), text_color=OLIVE).place(x=24, y=10)
    ctk.CTkLabel(header, text="See the ground before we buy it", font=ctk.CTkFont(family="Georgia", size=22), text_color=BONE).place(x=24, y=30)
    ctk.CTkButton(header, text="How to read the results ↗", width=190, fg_color="transparent", border_width=1, border_color=INK3, text_color=INK2,
                  hover_color=NIGHT3, command=lambda: webbrowser.open(SITE)).pack(side="right", padx=24, pady=16)

    body = ctk.CTkFrame(app, fg_color=NIGHT, corner_radius=0)
    body.pack(fill="both", expand=True, padx=20, pady=16)
    body.grid_columnconfigure(0, weight=0, minsize=430)
    body.grid_columnconfigure(1, weight=1)
    body.grid_rowconfigure(0, weight=1)

    # ── left: inputs ──────────────────────────────────────────────────────
    left = ctk.CTkScrollableFrame(body, fg_color=NIGHT2, corner_radius=6, width=430)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

    def section(title):
        ctk.CTkLabel(left, text=title.upper(), font=font(11, "bold"), text_color=OLIVE).pack(anchor="w", padx=18, pady=(16, 4))

    def row_path(label, key, hint, must_exist=True):
        ctk.CTkLabel(left, text=label, font=font(12), text_color=INK2).pack(anchor="w", padx=18)
        fr = ctk.CTkFrame(left, fg_color="transparent"); fr.pack(fill="x", padx=18, pady=(2, 6))
        var = ctk.StringVar(value=prefs.get(key, ""))
        ent = ctk.CTkEntry(fr, textvariable=var, placeholder_text=hint, fg_color=NIGHT3, border_color=NIGHT3, text_color=BONE)
        ent.pack(side="left", fill="x", expand=True)

        def browse():
            d = filedialog.askdirectory(title=label, initialdir=var.get() or str(Path.home()))
            if d:
                var.set(d)
        ctk.CTkButton(fr, text="Browse", width=76, fg_color=NIGHT3, hover_color=NIGHT, text_color=BONE, command=browse).pack(side="left", padx=(6, 0))
        return var

    section("1 · Your LiDAR tiles")
    v_input = row_path("Folder with the .laz / .las tiles", "input", "e.g. C:\\LiDAR\\parcel-tiles")
    ctk.CTkLabel(left, text="Free tiles: apps.nationalmap.gov → Elevation Source Data (3DEP) → Lidar Point Cloud (LPC)", font=font(11), text_color=INK3, wraplength=380, justify="left").pack(anchor="w", padx=18)

    section("2 · Where to put the results")
    v_output = row_path("Output folder (a new project folder is created inside it)", "output", "leave empty: next to the tiles", must_exist=False)
    fr = ctk.CTkFrame(left, fg_color="transparent"); fr.pack(fill="x", padx=18, pady=(0, 4))
    ctk.CTkLabel(fr, text="Project name", font=font(12), text_color=INK2, width=110, anchor="w").pack(side="left")
    v_name = ctk.StringVar(value="")
    ctk.CTkEntry(fr, textvariable=v_name, placeholder_text="LandScan_<date>", fg_color=NIGHT3, border_color=NIGHT3, text_color=BONE).pack(side="left", fill="x", expand=True)

    section("3 · Terrain")
    fr = ctk.CTkFrame(left, fg_color="transparent"); fr.pack(fill="x", padx=18)
    ctk.CTkLabel(fr, text="Cell size (metres)", font=font(12), text_color=INK2, width=140, anchor="w").pack(side="left")
    v_res = ctk.StringVar(value=str(prefs.get("resolution", 1.0)))
    ctk.CTkOptionMenu(fr, values=["0.5", "1.0", "2.0", "5.0"], variable=v_res, width=90, fg_color=NIGHT3, button_color=NIGHT3, button_hover_color=NIGHT, text_color=BONE).pack(side="left")
    ctk.CTkLabel(fr, text="1 m is right for most parcels", font=font(11), text_color=INK3).pack(side="left", padx=10)
    v_existing = ctk.BooleanVar(value=True)
    ctk.CTkCheckBox(left, text="Trust ground classes already in the tiles (USGS tiles have them)", variable=v_existing, font=font(12), text_color=INK2,
                    fg_color=AMBER, hover_color=AMBER_HI, checkmark_color=NIGHT).pack(anchor="w", padx=18, pady=(8, 2))

    section("4 · Views to render")
    vis_vars = {}
    for key, label, tip in [("hillshade", "Hillshade", "the familiar relief picture"),
                            ("slrm", "Local relief model", "ditches, platforms, plough lines"),
                            ("svf", "Sky view factor", "hollows and enclosures"),
                            ("local_dominance", "Local dominance", "low mounds and banks")]:
        var = ctk.BooleanVar(value=True); vis_vars[key] = var
        ctk.CTkCheckBox(left, text=f"{label}  —  {tip}", variable=var, font=font(12), text_color=INK2, fg_color=AMBER, hover_color=AMBER_HI, checkmark_color=NIGHT).pack(anchor="w", padx=18, pady=2)

    # Advanced
    adv_open = ctk.BooleanVar(value=False)
    adv_frame = ctk.CTkFrame(left, fg_color="transparent")

    def toggle_adv():
        adv_open.set(not adv_open.get())
        adv_btn.configure(text=("▾  Advanced settings" if adv_open.get() else "▸  Advanced settings"))
        if adv_open.get():
            adv_frame.pack(fill="x", padx=18, pady=(4, 8), after=adv_btn)
        else:
            adv_frame.pack_forget()
    adv_btn = ctk.CTkButton(left, text="▸  Advanced settings", fg_color="transparent", hover_color=NIGHT3, text_color=INK2, anchor="w", command=toggle_adv)
    adv_btn.pack(anchor="w", padx=10, pady=(12, 0))

    adv = {}

    def adv_row(label, key, default, width=70):
        fr = ctk.CTkFrame(adv_frame, fg_color="transparent"); fr.pack(fill="x", pady=2)
        ctk.CTkLabel(fr, text=label, font=font(12), text_color=INK2, width=250, anchor="w").pack(side="left")
        var = ctk.StringVar(value=str(prefs.get(key, default))); adv[key] = var
        ctk.CTkEntry(fr, textvariable=var, width=width, fg_color=NIGHT3, border_color=NIGHT3, text_color=BONE).pack(side="left")

    adv_row("Hillshade sun azimuth (°)", "hs_azimuth", 315)
    adv_row("Hillshade sun elevation (°)", "hs_altitude", 35)
    adv_row("Local relief radius (m)", "slrm_radius_m", 20)
    adv_row("Sky view factor: directions", "svf_directions", 16)
    adv_row("Sky view factor: radius (m)", "svf_radius_m", 10)
    adv_row("Local dominance: min radius (m)", "ld_min_m", 10)
    adv_row("Local dominance: max radius (m)", "ld_max_m", 20)
    adv_row("Ground filter: max window (m)", "ground_max_window_m", 20)
    adv_row("Ground filter: threshold (m)", "ground_threshold_m", 0.5)
    ctk.CTkLabel(adv_frame, text="LAStools folder (optional — used for tiles without ground classes)", font=font(12), text_color=INK2).pack(anchor="w", pady=(8, 0))
    fr = ctk.CTkFrame(adv_frame, fg_color="transparent"); fr.pack(fill="x")
    v_lastools = ctk.StringVar(value=prefs.get("lastools", ""))
    ctk.CTkEntry(fr, textvariable=v_lastools, placeholder_text="C:\\LAStools\\bin", fg_color=NIGHT3, border_color=NIGHT3, text_color=BONE).pack(side="left", fill="x", expand=True)
    ctk.CTkButton(fr, text="Browse", width=76, fg_color=NIGHT3, hover_color=NIGHT, text_color=BONE,
                  command=lambda: v_lastools.set(filedialog.askdirectory(title="LAStools bin folder") or v_lastools.get())).pack(side="left", padx=(6, 0))
    v_keep = ctk.BooleanVar(value=False)
    ctk.CTkCheckBox(adv_frame, text="Keep intermediate files", variable=v_keep, font=font(12), text_color=INK2, fg_color=AMBER, hover_color=AMBER_HI, checkmark_color=NIGHT).pack(anchor="w", pady=(8, 0))

    # ── right: tools, progress, log ───────────────────────────────────────
    right = ctk.CTkFrame(body, fg_color="transparent")
    right.grid(row=0, column=1, sticky="nsew")
    right.grid_rowconfigure(2, weight=1)
    right.grid_columnconfigure(0, weight=1)

    tools = ctk.CTkFrame(right, fg_color=NIGHT2, corner_radius=6)
    tools.grid(row=0, column=0, sticky="ew", pady=(0, 12))
    tools_head = ctk.CTkFrame(tools, fg_color="transparent"); tools_head.pack(fill="x", padx=18, pady=(12, 2))
    ctk.CTkLabel(tools_head, text="TOOL CHECK", font=font(11, "bold"), text_color=OLIVE).pack(side="left")
    tools_status = ctk.CTkLabel(tools_head, text="", font=font(12), text_color=INK2); tools_status.pack(side="left", padx=12)
    tools_box = ctk.CTkTextbox(tools, height=118, fg_color=NIGHT3, text_color=BONE2, font=mono, wrap="none")
    tools_box.pack(fill="x", padx=18, pady=(2, 12))

    def do_check():
        rep = check_environment(v_lastools.get() or None)
        tools_box.configure(state="normal"); tools_box.delete("1.0", "end")
        tools_box.insert("end", "\n".join(rep.lines())); tools_box.configure(state="disabled")
        tools_status.configure(text=("Ready to run" if rep.ready else "Something required is missing"), text_color=(OLIVE if rep.ready else RED))
        return rep
    ctk.CTkButton(tools_head, text="Re-check", width=80, fg_color=NIGHT3, hover_color=NIGHT, text_color=BONE, command=do_check).pack(side="right")

    prog = ctk.CTkFrame(right, fg_color=NIGHT2, corner_radius=6)
    prog.grid(row=1, column=0, sticky="ew", pady=(0, 12))
    step_lbl = ctk.CTkLabel(prog, text="Choose a folder of tiles, then press Start.", font=font(13), text_color=BONE, anchor="w")
    step_lbl.pack(fill="x", padx=18, pady=(12, 4))
    bar = ctk.CTkProgressBar(prog, height=14, progress_color=AMBER, fg_color=NIGHT3); bar.set(0)
    bar.pack(fill="x", padx=18)
    eta_lbl = ctk.CTkLabel(prog, text="", font=font(11), text_color=INK3, anchor="w"); eta_lbl.pack(fill="x", padx=18, pady=(2, 8))
    btns = ctk.CTkFrame(prog, fg_color="transparent"); btns.pack(fill="x", padx=18, pady=(0, 12))
    start_btn = ctk.CTkButton(btns, text="START PROCESSING", width=190, height=40, font=font(13, "bold"), fg_color=AMBER_HI, hover_color=AMBER, text_color=NIGHT)
    start_btn.pack(side="left")
    cancel_btn = ctk.CTkButton(btns, text="Cancel", width=90, height=40, fg_color="transparent", border_width=1, border_color=INK3, text_color=INK2, hover_color=NIGHT3, state="disabled")
    cancel_btn.pack(side="left", padx=8)
    open_btn = ctk.CTkButton(btns, text="Open output folder", width=150, height=40, fg_color=NIGHT3, hover_color=NIGHT, text_color=BONE, state="disabled")
    open_btn.pack(side="right")
    report_btn = ctk.CTkButton(btns, text="Open report", width=110, height=40, fg_color=NIGHT3, hover_color=NIGHT, text_color=BONE, state="disabled")
    report_btn.pack(side="right", padx=8)

    logf = ctk.CTkFrame(right, fg_color=NIGHT2, corner_radius=6)
    logf.grid(row=2, column=0, sticky="nsew")
    ctk.CTkLabel(logf, text="LOG", font=font(11, "bold"), text_color=OLIVE).pack(anchor="w", padx=18, pady=(12, 2))
    log_box = ctk.CTkTextbox(logf, fg_color=NIGHT3, text_color=BONE2, font=mono, wrap="word")
    log_box.pack(fill="both", expand=True, padx=18, pady=(2, 14))
    log_box.configure(state="disabled")

    def log(msg: str):
        state["q"].put(("log", msg))

    def progress(pct, msg, eta=None):
        state["q"].put(("prog", (pct, msg, eta)))

    def pump():
        try:
            while True:
                kind, payload = state["q"].get_nowait()
                if kind == "log":
                    log_box.configure(state="normal"); log_box.insert("end", payload + "\n"); log_box.see("end"); log_box.configure(state="disabled")
                elif kind == "prog":
                    pct, msg, eta = payload
                    bar.set(pct / 100); step_lbl.configure(text=f"{pct:.0f}%  ·  {msg}")
                    eta_lbl.configure(text=(f"about {eta / 60:.0f} min left" if eta and eta > 90 else (f"about {eta:.0f} s left" if eta else "")))
                elif kind == "done":
                    ok, info = payload
                    finish(ok, info)
        except queue.Empty:
            pass
        app.after(120, pump)

    def set_running(r: bool):
        state["running"] = r
        start_btn.configure(state=("disabled" if r else "normal"))
        cancel_btn.configure(state=("normal" if r else "disabled"))

    def finish(ok, info):
        set_running(False)
        if ok:
            state["out"] = str(info)
            open_btn.configure(state="normal"); report_btn.configure(state="normal")
            step_lbl.configure(text="Done. The terrain model, four views and the report are in the output folder.")
            eta_lbl.configure(text=str(info))
            bar.set(1)
            if messagebox.askyesno(APP_NAME, "Processing finished.\n\nOpen the report now?"):
                _open_path(str(Path(info) / "report.html"))
        elif info == "cancelled":
            step_lbl.configure(text="Cancelled."); eta_lbl.configure(text="")
        else:
            step_lbl.configure(text="Stopped — see the message.", text_color=RED)
            messagebox.showerror(APP_NAME, str(info))
            step_lbl.configure(text_color=BONE)

    def settings_from_form() -> JobSettings:
        inp = v_input.get().strip()
        if not inp or not Path(inp).is_dir():
            raise StepError("Choose the folder that holds your .laz / .las tiles (step 1).")
        out = v_output.get().strip()
        if out and not Path(out).is_dir():
            raise StepError("The output folder doesn't exist. Pick an existing folder, or leave it empty to save next to the tiles.")

        def num(key, default):
            try:
                return float(adv[key].get())
            except ValueError:
                raise StepError(f"'{adv[key].get()}' is not a number (Advanced settings → {key.replace('_', ' ')}).")
        try:
            res = float(v_res.get())
        except ValueError:
            raise StepError("Cell size must be a number.")
        vis = VisSettings(hillshade=vis_vars["hillshade"].get(), slrm=vis_vars["slrm"].get(), svf=vis_vars["svf"].get(), local_dominance=vis_vars["local_dominance"].get(),
                          hs_azimuth=num("hs_azimuth", 315), hs_altitude=num("hs_altitude", 35), slrm_radius_m=num("slrm_radius_m", 20),
                          svf_directions=int(num("svf_directions", 16)), svf_radius_m=num("svf_radius_m", 10), ld_min_m=num("ld_min_m", 10), ld_max_m=num("ld_max_m", 20))
        return JobSettings(input_dir=inp, output_root=out, project_name=v_name.get().strip(), resolution=res, lastools_path=v_lastools.get().strip() or None,
                           use_existing_classes=v_existing.get(), ground_max_window_m=num("ground_max_window_m", 20), ground_threshold_m=num("ground_threshold_m", 0.5),
                           vis=vis, keep_intermediate=v_keep.get())

    def start():
        try:
            s = settings_from_form()
        except StepError as e:
            messagebox.showwarning(APP_NAME, str(e)); return
        rep = do_check()
        if not rep.ready:
            messagebox.showerror(APP_NAME, "A required component is missing — see the tool check. Reinstall the app to fix it."); return
        _save_prefs({**prefs, "input": s.input_dir, "output": s.output_root, "resolution": s.resolution, "lastools": s.lastools_path or "",
                     **{k: v.get() for k, v in adv.items()}})
        log_box.configure(state="normal"); log_box.delete("1.0", "end"); log_box.configure(state="disabled")
        open_btn.configure(state="disabled"); report_btn.configure(state="disabled")
        bar.set(0); eta_lbl.configure(text="")
        state["cancel"] = threading.Event()
        set_running(True)

        def work():
            try:
                out = Pipeline(s, progress=progress, log=log, cancel=state["cancel"]).run()
                state["q"].put(("done", (True, out)))
            except Cancelled:
                state["q"].put(("done", (False, "cancelled")))
            except StepError as e:
                state["q"].put(("done", (False, str(e))))
            except MemoryError:
                state["q"].put(("done", (False, "The computer ran out of memory. Try a larger cell size (2 m) or fewer tiles at a time.")))
            except Exception as e:  # noqa: BLE001
                import traceback
                log(traceback.format_exc())
                state["q"].put(("done", (False, f"Unexpected error: {e}\n\nThe log has the details; send it to the initiative if it keeps happening.")))
        state["thread"] = threading.Thread(target=work, daemon=True); state["thread"].start()

    def cancel():
        state["cancel"].set(); cancel_btn.configure(state="disabled"); step_lbl.configure(text="Cancelling after the current tile…")

    start_btn.configure(command=start)
    cancel_btn.configure(command=cancel)
    open_btn.configure(command=lambda: state["out"] and _open_path(state["out"]))
    report_btn.configure(command=lambda: state["out"] and _open_path(str(Path(state["out"]) / "report.html")))

    def on_close():
        if state["running"] and not messagebox.askyesno(APP_NAME, "A job is still running. Quit anyway?"):
            return
        state["cancel"].set(); app.destroy()
    app.protocol("WM_DELETE_WINDOW", on_close)

    ctk.CTkLabel(app, text=f"{ORG} · {APP_NAME} {VERSION} · terrain algorithms from the Relief Visualization Toolbox (ZRC SAZU) · workflow after LiDARch (M. Carrero-Pazos)",
                 font=font(10), text_color=INK3).pack(pady=(0, 8))

    do_check()
    pump()
    app.mainloop()
    return 0
