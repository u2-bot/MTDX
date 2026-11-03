"""The MTDX window: pick the dump once, convert, play. Later starts go straight to Play."""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__, install, winfit

TITLE = f'MTDX {__version__}'
SAVES = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'AppData', 'LocalLow', 'RTDX', 'save')
SETTINGS = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'AppData', 'LocalLow', 'MTDX', 'Pegasus')


class App:
    def __init__(self, root_dir):
        self.paths = install.Paths(root_dir)
        self.q = queue.Queue()
        self.worker = None
        self.win = tk.Tk()
        self.win.title(f'{TITLE} - Pokemon Mystery Dungeon: Rescue Team DX for Windows')
        winfit.fit(self.win, 720, 560, 560, 420, centre=True)
        self.win.protocol('WM_DELETE_WINDOW', self.on_close)
        pad = {'padx': 12, 'pady': 6}
        self.head = ttk.Label(self.win, text='', font=('Segoe UI', 12, 'bold'))
        self.head.pack(anchor='w', **pad)
        self.info = ttk.Label(self.win, text='', wraplength=680, justify='left')
        self.info.pack(anchor='w', fill='x', **pad)
        # packed before the log: the buttons and the progress always keep their place, however short the window is
        btns = ttk.Frame(self.win)
        btns.pack(side='bottom', fill='x', **pad)
        self.convert_btn = ttk.Button(btns, text='Convert', command=self.start_convert)
        self.convert_btn.pack(side='left')
        self.play_btn = ttk.Button(btns, text='Play', command=self.play)
        self.play_btn.pack(side='left', padx=(8, 0))
        ttk.Button(btns, text='Saves folder', command=lambda: self.open_folder(SAVES)).pack(side='right')
        ttk.Button(btns, text='Settings folder', command=lambda: self.open_folder(SETTINGS)).pack(side='right', padx=(0, 8))
        prog = ttk.Frame(self.win)
        prog.pack(side='bottom', fill='x', padx=12)
        self.bar = ttk.Progressbar(prog, mode='determinate', maximum=1000)
        self.bar.pack(fill='x')
        self.stage = ttk.Label(prog, text='', foreground='#555')
        self.stage.pack(anchor='w')
        box = ttk.LabelFrame(self.win, text='Your game dump: the folder that holds "romfs" and "exefs"')
        box.pack(fill='x', **pad)
        row = ttk.Frame(box)
        row.pack(fill='x', padx=8, pady=6)
        cfg = install.load_config(self.paths)
        self.dump_var = tk.StringVar(value=cfg.get('dump', ''))
        self.dump_entry = ttk.Entry(row, textvariable=self.dump_var)
        self.dump_entry.pack(side='left', fill='x', expand=True)
        self.browse = ttk.Button(row, text='Browse...', command=self.pick)
        self.browse.pack(side='left', padx=(8, 0))
        self.dump_var.trace_add('write', lambda *_: self.refresh_buttons())
        self.log_box = ScrolledText(self.win, height=10, wrap='word', state='disabled', font=('Consolas', 9))
        self.log_box.pack(fill='both', expand=True, **pad)
        self.state = None
        self.show_status(auto=True)
        self.win.after(100, self.poll)

    # --- state -------------------------------------------------------------------------------------------------------
    def show_status(self, auto=False):
        try:
            st = install.status(self.paths)
        except Exception as e:                 # a damaged release folder: say so, the user can still convert
            st = {'state': 'convert', 'reason': f'{type(e).__name__}: {e}'}
        self.state = st['state']
        cfg = install.load_config(self.paths)
        if st['state'] == 'ready':
            self.head.config(text='Ready to play')
            self.info.config(text='Press Play (or start RTDX.exe). Your dump is only read; keep it - a later MTDX update may '
                                  'need to convert again.' + (f'\nConverted {cfg["converted"]} from {cfg.get("dump")}.'
                                                              if cfg.get('converted') else ''))
            self.bar['value'] = 1000
            self.stage.config(text='')
        elif st['state'] == 'refresh':
            self.head.config(text='The game data needs an update')
            self.info.config(text=f'Since the last conversion: {st["reason"]}. MTDX makes only those files again from your '
                                  f'dump.')
        else:
            self.head.config(text='Convert your dump first')
            self.info.config(text='Pick your own dump of Pokemon Mystery Dungeon: Rescue Team DX with its version 1.0.2 update '
                                  '(the folder that holds '
                                  '"romfs" and "exefs"), then press Convert. It takes a few minutes; you can close MTDX '
                                  'and start it again, it goes on where it stopped.' +
                                  (f' ({st["reason"]})' if st['reason'] not in ('', 'not converted yet') else ''))
        self.refresh_buttons()
        if auto and st['state'] == 'refresh' and cfg.get('dump') and os.path.isdir(cfg['dump']):
            self.log('updating the game data from the dump of the last conversion')
            self.start_convert()

    def refresh_buttons(self):
        busy = self.worker is not None and self.worker.is_alive()
        self.convert_btn.config(state='disabled' if busy or not self.dump_var.get().strip() else 'normal',
                                text='Convert again' if self.state == 'ready' else 'Convert')
        self.play_btn.config(state='normal' if self.state == 'ready' and not busy else 'disabled')
        self.browse.config(state='disabled' if busy else 'normal')
        self.dump_entry.config(state='disabled' if busy else 'normal')

    # --- actions -----------------------------------------------------------------------------------------------------
    def pick(self):
        d = filedialog.askdirectory(title='The folder of your Rescue Team DX dump (it holds romfs and exefs)',
                                    initialdir=self.dump_var.get() or None)
        if d:
            try:
                d = install.find_dump(d)
            except install.DumpError as e:
                messagebox.showwarning(TITLE, str(e))
                self.log(str(e))
            self.dump_var.set(os.path.normpath(d))

    def start_convert(self):
        dump = self.dump_var.get().strip()
        if not dump or (self.worker and self.worker.is_alive()):
            return
        self.bar['value'] = 0
        self.head.config(text='Converting ...')
        self.info.config(text='MTDX makes the game data from your dump. You can close this window: the next start goes on '
                              'where it stopped.')

        def run():
            try:
                install.convert(self.paths, dump, log=lambda s: self.q.put(('log', s)),
                                progress=lambda s, d, t: self.q.put(('progress', s, d, t)))
                self.q.put(('done', None))
            except (install.DumpError, install.ConvertError) as e:
                self.q.put(('done', str(e)))
            except Exception as e:
                import traceback
                self.q.put(('done', f'{type(e).__name__}: {e}\n\n{traceback.format_exc()[-1500:]}'))
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()
        self.refresh_buttons()

    def play(self):
        from .__main__ import play
        try:
            play(self.paths.root)
        except OSError as e:
            messagebox.showerror(TITLE, f'RTDX.exe could not be started: {e}')
            return
        self.win.after(1500, self.win.destroy)

    def open_folder(self, path):
        os.makedirs(path, exist_ok=True)
        subprocess.Popen(['explorer', os.path.normpath(path)])

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(TITLE, 'The conversion is still running. Stop it? (The next start goes on where it '
                                              'stopped.)'):
                return
            os._exit(0)
        self.win.destroy()

    # --- messages from the worker ------------------------------------------------------------------------------------
    STAGES = {'native data': (0, 20), 'bundles': (20, 930), 'player files': (930, 990)}

    def poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                if m[0] == 'log':
                    self.log(m[1])
                elif m[0] == 'progress':
                    _, stage, done, total = m
                    a, b = self.STAGES.get(stage, (990, 1000))
                    self.bar['value'] = a + (b - a) * done / max(total, 1)
                    self.stage.config(text=f'{stage}: {done} / {total}')
                elif m[0] == 'done':
                    err = m[1]
                    self.worker = None
                    self.show_status()
                    if err:
                        self.log(err)
                        self.head.config(text='The conversion stopped')
                        messagebox.showerror(TITLE, err)
                    else:
                        self.bar['value'] = 1000
                        self.stage.config(text='done')
                    self.refresh_buttons()
        except queue.Empty:
            pass
        self.win.after(100, self.poll)

    def log(self, s):
        self.log_box.config(state='normal')
        self.log_box.insert('end', s.rstrip() + '\n')
        self.log_box.see('end')
        self.log_box.config(state='disabled')


def main(root):
    app = App(root)
    app.win.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main(os.getcwd()))
