"""Open the window under Xvfb, fill it, run a job, screenshot before and after."""
import sys, threading, time, subprocess
sys.path.insert(0, '/root/landscan')
import landscan.gui as gui

# Monkeypatch messagebox so nothing blocks
from tkinter import messagebox
messagebox.askyesno = lambda *a, **k: False
messagebox.showerror = lambda *a, **k: print("ERROR BOX:", a)
messagebox.showwarning = lambda *a, **k: print("WARN BOX:", a)

orig_run = gui.run_gui
import customtkinter as ctk
real_mainloop = ctk.CTk.mainloop
def fake_mainloop(self):
    def script():
        time.sleep(1.5)
        subprocess.run(['import', '-window', 'root', '/root/landscan/tests/out/gui-1.png'])
        # find widgets by walking
        def walk(w):
            yield w
            for c in w.winfo_children(): yield from walk(c)
        entries = [w for w in walk(self) if isinstance(w, ctk.CTkEntry)]
        buttons = [w for w in walk(self) if isinstance(w, ctk.CTkButton)]
        self.after(0, lambda: entries[0].delete(0, 'end'))
        self.after(10, lambda: entries[0].insert(0, '/root/landscan/tests/synthetic_tiles'))
        self.after(20, lambda: entries[1].insert(0, '/root/landscan/tests/out'))
        self.after(30, lambda: entries[2].insert(0, 'gui_run'))
        start = [b for b in buttons if b.cget('text') == 'START PROCESSING'][0]
        self.after(300, lambda: start.invoke())
        time.sleep(4)
        subprocess.run(['import', '-window', 'root', '/root/landscan/tests/out/gui-2.png'])
        time.sleep(6)
        subprocess.run(['import', '-window', 'root', '/root/landscan/tests/out/gui-3.png'])
        self.after(0, self.destroy)
    threading.Thread(target=script, daemon=True).start()
    real_mainloop(self)
ctk.CTk.mainloop = fake_mainloop
gui.run_gui()
