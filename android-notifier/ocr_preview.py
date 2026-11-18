"""Independent, read-only OCR preview. Never imports the answer/submission pipeline."""
import argparse
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog

from ocr_engine import file_capture, load_dependencies, recognize


def format_result(result):
    options = '\n\n'.join(f'{k}. {v}' for k, v in result['options'].items())
    warnings = '\n'.join('• ' + w for w in result['warnings']) or '文字结构已识别，请对照原图核对。'
    scan = f"\n向下采集 {result['scan']['screens']} 屏；请核对长图接缝。" if result.get('scan') else ''
    return (f"{result['notice']}{scan}\n\n题干\n{result['prompt'] or '（未读到）'}\n\n"
            f"选项\n{options or '（未读到）'}\n\n核对提示\n{warnings}\n\n"
            f"用时 {result.get('seconds', 0):.2f} 秒\n\n识别来源\n" +
            '\n'.join(f"[{i}] {'控件文字' if b['source']=='native' else 'OCR'} " +
                      (f"{b['confidence']:.0%} " if b['confidence'] is not None else '') + b['text']
                      for i, b in enumerate(result['blocks'], 1)))


class Preview:
    def __init__(self, parent=None, current=None, current_long=None):
        if parent is None:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            except (AttributeError, OSError):
                pass
        self.window = tk.Toplevel(parent) if parent else tk.Tk()
        self.window.title('图片题文字识别 · 仅预览')
        self.window.geometry('1100x740')
        self.current = current
        self.current_long = current_long
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.busy = False
        self.closed = False
        self.closing = False
        self.result = None
        self.image = None
        self.photo = None
        self.capture = None
        self.worker = None
        bar = ttk.Frame(self.window, padding=10)
        bar.pack(fill='x')
        self.file_button = ttk.Button(bar, text='导入题目图片', command=self.open_file)
        self.file_button.pack(side='left')
        self.current_button = ttk.Button(bar, text='识别当前题目', command=self.open_current)
        self.current_button.pack(side='left', padx=8)
        if current is None:
            self.current_button.state(['disabled'])
        self.long_button = ttk.Button(bar, text='滚动识别长题', command=self.open_long)
        self.long_button.pack(side='left', padx=8)
        if current_long is None:
            self.long_button.state(['disabled'])
        self.cancel_button = ttk.Button(bar, text='取消识别', command=self.cancel.set)
        self.cancel_button.pack(side='left')
        self.cancel_button.state(['disabled'])
        self.status = tk.StringVar(value='支持单题文字图片；请包含题干和 A、B…选项标签。')
        ttk.Label(self.window, textvariable=self.status, padding=(10, 0, 10, 8)).pack(anchor='w')
        panes = ttk.Panedwindow(self.window, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=10, pady=5)
        left, right = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(left, weight=1); panes.add(right, weight=1)
        self.layout_timer = self.window.after(50, lambda: panes.sashpos(0, max(250, panes.winfo_width() // 2)) if not self.closed else None)
        self.canvas = tk.Canvas(left, background='#e8e8e8', highlightthickness=0)
        image_scroll = ttk.Scrollbar(left, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=image_scroll.set)
        image_scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda _: self.draw())
        self.output = tk.Text(right, wrap='word', font=('Microsoft YaHei UI', 11), state='disabled')
        scroll = ttk.Scrollbar(right, command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y'); self.output.pack(fill='both', expand=True)
        ttk.Label(self.window, text='只读取与预览，不调用答题模型，不选择选项，不提交活动。关闭窗口后清除本次预览。',
                  padding=10).pack(anchor='w')
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.pump_timer = self.window.after(100, self.pump)

    def set_text(self, value):
        self.output.configure(state='normal'); self.output.delete('1.0', 'end')
        self.output.insert('1.0', value); self.output.configure(state='disabled')

    def start(self, producer):
        if self.busy:
            return
        self.busy = True; self.cancel.clear()
        self.result = None; self.image = None; self.capture = None
        self.set_text(''); self.canvas.delete('all')
        self.canvas.yview_moveto(0)
        self.file_button.state(['disabled']); self.current_button.state(['disabled'])
        self.long_button.state(['disabled'])
        self.cancel_button.state(['!disabled'])
        self.status.set('正在读取与识别，可随时取消…')
        def work():
            try:
                capture = producer()
                result, image = recognize(capture, self.cancel)
                self.events.put(('result', (capture, result, image)))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def open_file(self):
        path = filedialog.askopenfilename(parent=self.window, title='选择单道题目图片',
            filetypes=[('题目图片', '*.png *.jpg *.jpeg *.bmp *.webp')])
        if path:
            self.start(lambda: file_capture(path))

    def open_current(self):
        if self.current:
            self.start(lambda: self.current(self.cancel))

    def open_long(self):
        if self.current_long:
            def produce():
                capture = self.current_long(self.cancel, lambda message: self.events.put(('progress', message)))
                self.events.put(('progress', '正在识别题目图片，可随时取消…'))
                return capture
            self.start(produce)

    def pump(self):
        if self.closed:
            return
        while not self.events.empty():
            kind, value = self.events.get()
            if kind == 'progress':
                if not self.closing:
                    self.status.set(value)
                continue
            self.busy = False
            self.file_button.state(['!disabled'])
            if self.current:
                self.current_button.state(['!disabled'])
            if self.current_long:
                self.long_button.state(['!disabled'])
            self.cancel_button.state(['disabled'])
            if kind == 'result' and not self.cancel.is_set():
                self.capture, self.result, self.image = value
                self.set_text(format_result(self.result)); self.draw()
                self.status.set('识别完成 · 请核对原图' if self.result['status'] == 'recognized' else '识别完成 · 有内容需要核对')
            else:
                self.status.set('已取消识别' if self.cancel.is_set() else str(value))
        self.pump_timer = self.window.after(100, self.pump)

    def draw(self):
        if self.image is None:
            return
        load_dependencies()
        from PIL import ImageTk
        image = self.image.copy()
        height = self.image.height if self.capture and (self.capture.scan or self.capture.originals) else max(1, self.canvas.winfo_height())
        image.thumbnail((max(1, self.canvas.winfo_width()), height))
        self.photo = ImageTk.PhotoImage(image, master=self.window)
        self.canvas.delete('all'); self.canvas.create_image(0, 0, anchor='nw', image=self.photo)
        self.canvas.configure(scrollregion=(0, 0, image.width, image.height))
        scale = image.width / self.image.width
        for i, b in enumerate(self.result['blocks'], 1):
            x1, y1, x2, y2 = [v * scale for v in b['box']]
            color = '#18743c' if b['source'] == 'native' else '#ae5700'
            self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=2)
            self.canvas.create_text(x1, max(8, y1), text=str(i), anchor='sw', fill=color)

    def close(self):
        if self.closing or self.closed:
            return
        self.closing = True
        self.cancel.set()
        self.status.set('正在取消识别并清理临时图片…')
        def finish():
            if self.worker and self.worker.is_alive():
                self.window.after(100, finish)
                return
            self.closed = True
            for timer in (self.layout_timer, self.pump_timer):
                self.window.after_cancel(timer)
            self.result = self.image = self.capture = self.photo = None
            while not self.events.empty():
                self.events.get()
            self.window.destroy()
        finish()


def dialog(parent, current, current_long=None):
    return Preview(parent, current, current_long)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='图片文字识别预览，不操作课堂活动')
    parser.add_argument('--image', type=Path)
    parser.add_argument('--installation', type=Path, help='只读取已有正式安装的配对与关闭状态，不启动接收端或模型')
    parser.add_argument('--output', type=Path, help='将识别结果保存到本人指定的本地 JSON 文件')
    args = parser.parse_args()
    if args.output:
        if not args.image:
            parser.error('--output 需要 --image')
        result, _ = recognize(file_capture(args.image))
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        current = None
        current_long = None
        if args.installation:
            from preview_device import ReadOnlyDevice
            device = ReadOnlyDevice(args.installation)
            current, current_long = device.capture_question, device.capture_long
        preview = Preview(current=current, current_long=current_long)
        if current:
            preview.status.set('请从题干开头识别。图文混排单选题会按栏位读取文字和图片，附件会逐张打开原图。')
        if args.image:
            preview.start(lambda: file_capture(args.image))
        preview.window.mainloop()
