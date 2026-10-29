import os
os.environ.setdefault('OMP_NUM_THREADS','2')
import sys
from pathlib import Path

if __name__=='__main__':
    try:
        import ctypes
        if os.name=='nt':
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('MangaTranslatorStudio.Desktop')
        import cv2
        cv2.setNumThreads(1)
        from studio.app import main
        main(sys.argv[1] if len(sys.argv)>1 else None)
    except Exception:
        import traceback
        from studio.storage import USER_ROOT
        error=traceback.format_exc();(USER_ROOT/'startup_error.log').write_text(error,encoding='utf-8')
        import tkinter as tk
        from tkinter import messagebox
        root=tk.Tk();root.withdraw();messagebox.showerror('简单漫画翻译器启动失败',f'请查看日志：{USER_ROOT / "startup_error.log"}\n\n{error[-1200:]}');root.destroy()
