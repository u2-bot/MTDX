"""The MTDX window: pick the dump once, convert, play. Later starts go straight to Play. At every start MTDX first looks for
a new version of itself (update.py) and installs it (unless the player unticked that), then brings the game data up to date."""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__, install, update, winfit

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
        urow = ttk.Frame(self.win)
        urow.pack(fill='x', padx=12, pady=(0, 2))
        self.update_auto = tk.BooleanVar(value=update.enabled(self.paths))
        ttk.Checkbutton(urow, text='Update MTDX automatically', variable=self.update_auto,
                        command=self.toggle_update_auto).pack(side='left')
        self.update_btn = ttk.Button(urow, text='Check for updates', command=lambda: self.update_check(auto=False))
        self.update_btn.pack(side='right')
        self.update_note = ttk.Label(urow, text='', foreground='#555', wraplength=330, justify='left')
        self.update_note.pack(side='left', fill='x', expand=True, padx=(12, 8))
        self.log_box = ScrolledText(self.win, height=10, wrap='word', state='disabled', font=('Consolas', 9))
        self.log_box.pack(fill='both', expand=True, **pad)
        self.state = None
        self.worker_kind = None                # 'convert' | 'update' while self.worker runs
        self.checking = False
        self.show_status()
        try:
            if update.just_updated(self.paths):
                self.log(f'MTDX updated itself to {__version__} (build {update.current_build(self.paths)}).')
        except OSError:
            pass
        # an update of MTDX itself comes first (it may bring a new player), then the game data is brought up to date
        self.refresh_pending = True
        if self.update_auto.get():
            self.win.after(300, lambda: self.update_check(auto=True))
        else:
            self.update_note.config(text=f'MTDX {__version__}')
            self.win.after(300, self.refresh_once)
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

    def refresh_once(self):
        """the start-up refresh of the game data (show_status auto), once, after the update check"""
        if self.refresh_pending:
            self.refresh_pending = False
            if not (self.worker and self.worker.is_alive()):
                self.show_status(auto=True)

    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def refresh_buttons(self):
        busy = self.busy()
        self.convert_btn.config(state='disabled' if busy or not self.dump_var.get().strip() else 'normal',
                                text='Convert again' if self.state == 'ready' else 'Convert')
        self.play_btn.config(state='normal' if self.state == 'ready' and not busy else 'disabled')
        self.browse.config(state='disabled' if busy else 'normal')
        self.dump_entry.config(state='disabled' if busy else 'normal')
        self.update_btn.config(state='disabled' if busy or self.checking else 'normal')

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
        self.worker_kind = 'convert'
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
        if self.busy():
            text = ('MTDX is downloading its update. Stop it? (The next start goes on with the download.)'
                    if self.worker_kind == 'update' else
                    'The conversion is still running. Stop it? (The next start goes on where it stopped.)')
            if not messagebox.askyesno(TITLE, text):
                return
            os._exit(0)
        self.win.destroy()

    # --- MTDX's own updates (update.py) ------------------------------------------------------------------------------
    def toggle_update_auto(self):
        update.set_enabled(self.paths, self.update_auto.get())
        if self.update_auto.get():
            self.update_check(auto=True)

    def update_check(self, auto=False):
        """reads the update information in a thread; auto = install a new version at once (the start-up check)"""
        if self.checking or self.busy():
            return
        self.checking = True
        self.update_note.config(text='Looking for a new MTDX ...')
        self.refresh_buttons()

        def work():
            try:
                self.q.put(('upd_plan', update.check(self.paths), auto))
            except Exception as e:      # offline, GitHub down, a damaged feed: say it, play on
                self.q.put(('upd_error', update.describe(e), auto))
        threading.Thread(target=work, daemon=True).start()

    def update_found(self, r, auto):
        e = r['entry']
        if not e:
            self.update_note.config(text=f'MTDX {__version__} is up to date')
            if not auto:
                self.log(f'MTDX {__version__} is the newest version')
            return self.refresh_once()
        name = update.summary(e)
        page = e.get('page', '')
        if not update.can_self_update():
            self.update_note.config(text=f'MTDX {e.get("version", "")} is out (this copy runs from its source: update it '
                                         f'with git)')
            return self.refresh_once()
        failed = update.self_update_failed(self.paths, e['build'])
        if failed and auto:
            self.update_note.config(text=f'MTDX {e.get("version", "")} is out, but installing it did not work last time')
            self.log(f'{name} is out, but the last try to install it did not finish ({failed}). Download it'
                     f'{" from " + page if page else ""} and unzip it over this folder; "Check for updates" tries again.')
            return self.refresh_once()
        if self.busy():                 # a conversion was started meanwhile
            self.update_note.config(text=f'MTDX {e.get("version", "")} is out: it is installed at the next start')
            return
        if update.game_running(self.paths):
            self.update_note.config(text=f'MTDX {e.get("version", "")} is out: close the game, then start MTDX again')
            return self.refresh_once()
        if update.other_instances(self.paths):
            self.update_note.config(text=f'MTDX {e.get("version", "")} is out: close the other MTDX window, then start '
                                         f'MTDX again')
            return self.refresh_once()
        if not auto and not messagebox.askyesno(
                TITLE, f'{name} is out{" (" + e["notes"] + ")" if e.get("notes") else ""}.\n\nInstall it now? MTDX '
                       f'downloads it, closes, copies it in and starts again. Your game data, saves and settings stay.'):
            self.update_note.config(text=f'MTDX {e.get("version", "")} is out (not installed)')
            return self.refresh_once()
        self.log(f'{name} is out{": " + page if page else ""}. Installing it; MTDX starts again by itself.')
        if e.get('notes'):
            self.log(e['notes'])
        self.update_apply(e)

    def update_apply(self, entry):
        self.head.config(text=f'Updating MTDX to {entry.get("version", "")} ...')
        self.info.config(text=f'MTDX downloads its new version ({update.size_text(entry["asset"]["size"])}), copies it in '
                              f'and starts again by itself. Your game data, saves and settings stay.')
        self.update_note.config(text=f'Downloading MTDX {entry.get("version", "")} ...')
        self.bar['value'] = 0

        def run():
            try:
                cmd = update.prepare_self_update(self.paths, entry, log=lambda s: self.q.put(('log', s)),
                                                 progress=lambda d, t: self.q.put(('progress', 'Downloading MTDX', d, t)))
                self.q.put(('upd_restart', cmd))
            except Exception as e:
                self.q.put(('upd_failed', update.describe(e)))
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker_kind = 'update'
        self.worker.start()
        self.refresh_buttons()

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
                    if stage.startswith('Downloading'):
                        self.bar['value'] = 1000 * done / max(total, 1)
                        self.stage.config(text=f'{stage}: {done / 2 ** 20:.1f} / {total / 2 ** 20:.1f} MiB')
                        continue
                    a, b = self.STAGES.get(stage, (990, 1000))
                    self.bar['value'] = a + (b - a) * done / max(total, 1)
                    self.stage.config(text=f'{stage}: {done} / {total}')
                elif m[0] == 'upd_plan':
                    self.checking = False
                    self.refresh_buttons()
                    self.update_found(m[1], m[2])
                elif m[0] == 'upd_error':
                    self.checking = False
                    self.refresh_buttons()
                    self.update_note.config(text='Updates could not be checked: ' + m[1].splitlines()[0][:160])
                    if not m[2]:
                        self.log('Updates could not be checked: ' + m[1])
                    self.refresh_once()
                elif m[0] == 'upd_restart':
                    self.log('Restarting MTDX to finish its update ...')
                    self.update_note.config(text='Restarting MTDX ...')
                    self.win.update_idletasks()
                    update.run_self_update(m[1])
                    os._exit(0)
                elif m[0] == 'upd_failed':
                    self.worker = None
                    self.worker_kind = None
                    self.log('PROBLEM (update): ' + m[1])
                    self.update_note.config(text='The update could not be installed - see the log; MTDX is as it was')
                    self.stage.config(text='')
                    self.show_status()
                    self.refresh_once()
                elif m[0] == 'done':
                    err = m[1]
                    self.worker = None
                    self.worker_kind = None
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
