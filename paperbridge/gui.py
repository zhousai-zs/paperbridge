"""Minimal native desktop interface; no HTTP server or embedded browser."""
from pathlib import Path
import os
import queue
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .desktop import BatchRunner, Options, load_output, save_output, unique_files, validate_output
from .environment import state_dir


class DesktopApp:
    def __init__(self, root, output=None, runner=None):
        self.root = root
        self.runner = runner or BatchRunner()
        self.files = []
        self.results = []
        self.busy = False
        self.closing = False
        self.advanced_open = False
        self.details_open = False
        self.current = 0
        self.batch_error = ''
        root.title('PaperBridge')
        root.minsize(660, 450)
        root.geometry('720x520')
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        root.option_add('*Font', ('Microsoft YaHei UI', 10))
        style.configure('Title.TLabel', font=('Segoe UI', 20, 'bold'))
        style.configure('Muted.TLabel', foreground='#64748b')
        style.configure('Status.TLabel', font=('Microsoft YaHei UI', 10))
        frame = ttk.Frame(root, padding=(24, 18))
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        heading = ttk.Frame(frame)
        heading.grid(row=0, column=0, sticky='ew', pady=(0, 4))
        ttk.Label(heading, text='PaperBridge', font=('Segoe UI', 20, 'bold')).pack(side='left')
        ttk.Label(heading, text=f'  {__version__}', style='Muted.TLabel').pack(side='left', pady=(8, 0))
        ttk.Label(frame, text='PDF 转 Markdown · 在本机处理，保留图片、公式与原页',
                  style='Muted.TLabel').grid(row=1, column=0, sticky='w', pady=(0, 16))

        file_frame = ttk.Frame(frame)
        file_frame.grid(row=2, column=0, sticky='nsew')
        file_frame.columnconfigure(0, weight=1)
        file_frame.rowconfigure(1, weight=1)
        bar = ttk.Frame(file_frame)
        bar.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 7))
        self.choose = ttk.Button(bar, text='选择 PDF…', command=self.choose_files)
        self.choose.pack(side='left')
        self.count = ttk.Label(bar, text='尚未选择文件', style='Muted.TLabel')
        self.count.pack(side='left', padx=12)
        self.clear = ttk.Button(bar, text='清空', command=self.clear_files, width=6)
        self.clear.pack(side='right')
        self.remove = ttk.Button(bar, text='移除', command=self.remove_files, width=6)
        self.remove.pack(side='right', padx=5)
        self.tree = ttk.Treeview(file_frame, columns=('name', 'status'), show='headings', height=4,
                                 selectmode='extended')
        self.tree.heading('name', text='PDF 文件')
        self.tree.heading('status', text='状态')
        self.tree.column('name', width=460, minwidth=260)
        self.tree.column('status', width=95, minwidth=85, stretch=False)
        self.tree.grid(row=1, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(file_frame, orient='vertical', command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<Delete>', lambda event: self.remove_files())

        location = ttk.Frame(frame)
        location.grid(row=3, column=0, sticky='ew', pady=(16, 6))
        location.columnconfigure(1, weight=1)
        ttk.Label(location, text='输出文件夹').grid(row=0, column=0, padx=(0, 10))
        initial, notice = load_output()
        self.output = tk.StringVar(root, str(output or initial))
        self.output_entry = ttk.Entry(location, textvariable=self.output)
        self.output_entry.grid(row=0, column=1, sticky='ew')
        self.browse = ttk.Button(location, text='浏览…', command=self.choose_output, width=7)
        self.browse.grid(row=0, column=2, padx=(8, 0))
        self.advanced_button = ttk.Button(frame, text='▸ 更多选项', command=self.toggle_advanced)
        self.advanced_button.grid(row=4, column=0, sticky='w', pady=(2, 6))
        self.advanced = ttk.Frame(frame, padding=(0, 4, 0, 8))
        self.tier = tk.StringVar(root, '标准（推荐）')
        self.pages = tk.StringVar(root, 'all')
        self.ocr = tk.StringVar(root, '自动')
        self.formula_device = tk.StringVar(root, '自动 GPU（推荐）')
        self.chunk = tk.StringVar(root, '20')
        self.timeout = tk.StringVar(root, '1800')
        self.option_controls = []
        specs = [('转换模式', self.tier, ['标准（推荐）', '快速']), ('公式加速', self.formula_device,
                 ['自动 GPU（推荐）', '仅 CPU', '强制 CUDA']), ('页码', self.pages, None),
                 ('OCR', self.ocr, ['自动', '优先文本', '强制 OCR']),
                 ('每段页数', self.chunk, None), ('超时（秒）', self.timeout, None)]
        for i, (label, variable, values) in enumerate(specs):
            row, col = divmod(i, 3)
            cell = ttk.Frame(self.advanced)
            cell.grid(row=row, column=col, sticky='ew', padx=(0, 14), pady=4)
            ttk.Label(cell, text=label).pack(anchor='w')
            control = (ttk.Combobox(cell, textvariable=variable, values=values, state='readonly', width=17)
                       if values else ttk.Entry(cell, textvariable=variable, width=19))
            control.pack(fill='x', pady=(3, 0))
            self.option_controls.append(control)
            self.advanced.columnconfigure(col, weight=1)
        ttk.Label(self.advanced, text='页码示例：all 或 1-3,5。快速模式的公式需特别核对。',
                  style='Muted.TLabel').grid(row=2, column=0, columnspan=3, sticky='w', pady=(4, 0))

        actions = ttk.Frame(frame)
        actions.grid(row=6, column=0, sticky='ew', pady=(8, 14))
        self.start_button = ttk.Button(actions, text='开始转换', command=self.start_or_stop, width=14)
        self.start_button.pack(side='left')
        self.retry = ttk.Button(actions, text='重试失败项', command=self.retry_failed)
        self.open_button = ttk.Button(actions, text='打开输出文件夹', command=self.open_output)
        self.open_button.pack(side='right')
        self.progress = ttk.Progressbar(frame, mode='determinate', maximum=100)
        self.progress.grid(row=7, column=0, sticky='ew')
        self.status = tk.StringVar(root, '选择 PDF 和输出文件夹后，即可开始。')
        self.phase = tk.StringVar(root, notice or '输出文件夹会记住上次选择。')
        ttk.Label(frame, textvariable=self.status, style='Status.TLabel', wraplength=650).grid(
            row=8, column=0, sticky='w', pady=(9, 0))
        ttk.Label(frame, textvariable=self.phase, style='Muted.TLabel', wraplength=650).grid(
            row=9, column=0, sticky='w', pady=(3, 0))
        self.details_button = ttk.Button(frame, text='▸ 错误详情', command=self.toggle_details)
        self.details = tk.Text(frame, height=6, wrap='word', relief='solid', borderwidth=1, state='disabled')
        self.root.after(100, self.poll)

    def add_files(self, paths):
        if self.busy:
            return
        self.files = unique_files([*self.files, *paths])
        self.refresh_files()

    def refresh_files(self):
        self.tree.delete(*self.tree.get_children())
        for i, path in enumerate(self.files):
            self.tree.insert('', 'end', iid=str(i), values=(path.name, '等待'))
        self.count.configure(text=f'已选择 {len(self.files)} 个文件' if self.files else '尚未选择文件')

    def choose_files(self):
        self.add_files(filedialog.askopenfilenames(parent=self.root, title='选择 PDF 文件',
                                                  filetypes=[('PDF 文件', '*.pdf')]))

    def remove_files(self):
        if not self.busy:
            selected = set(self.tree.selection())
            self.files = [path for i, path in enumerate(self.files) if str(i) not in selected]
            self.refresh_files()

    def clear_files(self):
        if not self.busy:
            self.files.clear()
            self.refresh_files()

    def choose_output(self):
        selected = filedialog.askdirectory(parent=self.root, title='选择输出文件夹',
                                           initialdir=self.output.get() if Path(self.output.get()).is_dir() else None)
        if selected:
            try:
                path = validate_output(selected)
                save_output(path)
                self.output.set(str(path))
                self.phase.set('输出文件夹已记住。')
            except (OSError, ValueError) as exc:
                messagebox.showerror('无法选择该目录', str(exc), parent=self.root)

    def toggle_advanced(self):
        self.advanced_open = not self.advanced_open
        if self.advanced_open:
            self.advanced.grid(row=5, column=0, sticky='ew')
        else:
            self.advanced.grid_remove()
        self.advanced_button.configure(text=('▾' if self.advanced_open else '▸') + ' 更多选项')
        self.resize()

    def resize(self):
        self.root.geometry(f'720x{520 + (180 if self.advanced_open else 0) + (170 if self.details_open else 0)}')

    def read_options(self):
        try:
            options = Options(tier='standard' if self.tier.get().startswith('标准') else 'basic',
                              pages=self.pages.get().strip() or 'all',
                              ocr={'自动': 'auto', '优先文本': 'txt', '强制 OCR': 'ocr'}[self.ocr.get()],
                              formula_device={'自动 GPU（推荐）': 'auto', '仅 CPU': 'cpu',
                                              '强制 CUDA': 'cuda'}[self.formula_device.get()],
                              chunk_size=int(self.chunk.get()), timeout=float(self.timeout.get()))
            options.validate()
            return options
        except (ValueError, KeyError) as exc:
            raise ValueError('请检查更多选项：页码格式、分段页数与超时。') from exc

    def set_busy(self, busy):
        self.busy = busy
        for control in (self.choose, self.clear, self.remove, self.output_entry, self.browse,
                        self.advanced_button, *self.option_controls):
            state = 'disabled' if busy else ('readonly' if isinstance(control, ttk.Combobox) else 'normal')
            control.configure(state=state)
        self.start_button.configure(text='停止' if busy else '开始转换', state='normal')

    def start_or_stop(self):
        if self.busy:
            self.runner.stop()
            self.start_button.configure(text='正在停止…', state='disabled')
            self.phase.set('正在停止当前处理；已完成的检查点会保留。')
            return
        self.start(self.files)

    def start(self, files):
        if not files:
            messagebox.showinfo('请选择 PDF', '请先添加一份或多份 PDF。', parent=self.root)
            return
        try:
            output = validate_output(self.output.get())
            options = self.read_options()
            save_output(output)
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法开始转换', str(exc), parent=self.root)
            return
        self.output.set(str(output))
        self.results = []
        self.batch_error = ''
        self.retry.pack_forget()
        self.details_button.grid_remove()
        self.details.grid_remove()
        self.details_open = False
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.configure(state='disabled')
        self.resize()
        self.set_busy(True)
        self.runner.start(files, output, options)

    def retry_failed(self):
        self.start([Path(item['source']) for item in self.results if item['status'] in {'failed', 'stopped'}])

    def update_row(self, source, status):
        for i, path in enumerate(self.files):
            if str(path) == source:
                self.tree.set(str(i), 'status', status)
                self.tree.see(str(i))

    def handle_event(self, event):
        kind = event['kind']
        if kind == 'started':
            self.progress.stop()
            self.progress.configure(mode='indeterminate', value=0)
            self.progress.start(20)
            self.status.set(f"第 {event['index']} / {event['total']} 个文件 · {Path(event['source']).name}")
            self.phase.set('正在准备文件与模型…')
            self.update_row(event['source'], '处理中')
        elif kind == 'progress':
            stage = event.get('stage')
            if stage == 'parsing':
                completed, total = event.get('completed_pages', 0), event.get('total_pages', 0)
                pages = event.get('current_pages', [])
                if completed and total and completed < total:
                    self.progress.stop()
                    self.progress.configure(mode='determinate', value=100 * completed / total)
                current = f"第 {pages[0]}–{pages[-1]} 页" if pages else '当前分段'
                step_names = {'loading_engine': '正在加载解析引擎', 'inference': '正在识别',
                              'layout': '正在分析版式', 'visual_recognition': '正在识别图表',
                              'formula_recognition': '正在识别公式', 'text_detection': '正在检测文字',
                              'text_recognition': '正在识别文字', 'saving_assets': '正在保存正文与图片',
                              'checking_assets': '正在校验图片'}
                step = step_names.get(event.get('worker_step'), '正在识别')
                detail = f" {event['step_completed']}/{event['step_total']}" if event.get('step_total') else ''
                elapsed = f" · 已用 {event['elapsed_seconds']:.0f} 秒" if 'elapsed_seconds' in event else ''
                self.phase.set(f'{step}{detail} · {current} · 已完成 {completed} / {total} 页{elapsed}')
            elif stage == 'retrying':
                self.phase.set(f"当前分段未通过，正在重试（第 {event.get('attempt', 2)} / {event.get('max_attempts', 2)} 次）…")
            elif stage in {'assembling', 'rendering', 'validating'}:
                self.progress.stop()
                self.progress.configure(mode='indeterminate')
                self.progress.start(20)
                self.phase.set({'assembling': '正在组装 Markdown 与核对素材…',
                                'rendering': '正在生成原页图…', 'validating': '正在校验图片、页码与检查点…'}[stage])
        elif kind == 'result':
            self.results.append(event)
            status = {'complete': '完成', 'needs_review': '需复核', 'failed': '失败', 'stopped': '已停止'}[event['status']]
            self.update_row(event['source'], status)
            if event['status'] in {'failed', 'stopped'}:
                self.details.configure(state='normal')
                self.details.insert('end', f"{Path(event['source']).name}\n{event.get('error', '已停止，检查点可复用。')}\n"
                                    f"{event.get('details', '')}\n运行记录：{event.get('log', '')}\n\n")
                self.details.configure(state='disabled')
                self.details_button.grid(row=10, column=0, sticky='w', pady=(5, 0))
        elif kind == 'error':
            self.batch_error = event['error']
            self.details.configure(state='normal')
            self.details.insert('end', event['error'] + '\n')
            self.details.configure(state='disabled')
            self.details_button.grid(row=10, column=0, sticky='w')
            self.phase.set(event['error'])
        elif kind == 'done':
            self.progress.stop()
            self.progress.configure(mode='determinate', value=0 if event['stopped'] else 100)
            self.set_busy(False)
            counts = {key: sum(r['status'] == key for r in self.results)
                      for key in ('complete', 'needs_review', 'failed', 'stopped')}
            self.status.set(f"{'已停止' if event['stopped'] else '处理结束'} · 完成 {counts['complete']} · "
                            f"需复核 {counts['needs_review']} · 失败 {counts['failed']} · 停止 {counts['stopped']}")
            self.phase.set('点击“打开输出文件夹”查看结果；需复核项请结合原页与报告核对。')
            if self.batch_error:
                self.status.set('运行失败，请查看错误详情。')
                self.phase.set(self.batch_error)
                self.progress.configure(value=0)
            if counts['failed'] or counts['stopped']:
                self.retry.configure(text='重试未完成项')
                self.retry.pack(side='left', padx=10)
            if self.closing:
                self.root.destroy()

    def poll(self):
        try:
            while True:
                self.handle_event(self.runner.events.get_nowait())
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(100, self.poll)

    def toggle_details(self):
        self.details_open = not self.details_open
        if self.details_open:
            self.details.grid(row=11, column=0, sticky='ew', pady=(5, 0))
        else:
            self.details.grid_remove()
        self.details_button.configure(text=('▾' if self.details_open else '▸') + ' 错误详情')
        self.resize()

    def open_output(self):
        path = Path(self.output.get())
        if not path.is_dir():
            messagebox.showinfo('目录尚未创建', '请选择有效输出目录，或完成首次转换后再打开。', parent=self.root)
            return
        try:
            if os.name == 'nt':
                os.startfile(str(path))
            else:
                subprocess.Popen(['xdg-open', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            messagebox.showinfo('输出路径', f'请在文件资源管理器中打开：\n{path}', parent=self.root)

    def close(self):
        if self.busy:
            if not messagebox.askyesno('停止并退出？', '当前仍在转换。停止后保留完整检查点，下次可继续。', parent=self.root):
                return
            self.closing = True
            self.start_or_stop()
        else:
            self.root.destroy()


def launch(output=None):
    if os.name == 'nt':
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
    root = tk.Tk()
    try:
        DesktopApp(root, output)
        root.mainloop()
        return 0
    except Exception as exc:
        import traceback
        folder = state_dir() / 'logs'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'desktop-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        messagebox.showerror('PaperBridge 无法启动', f'{exc}\n运行记录：{folder}', parent=root)
        return 1
