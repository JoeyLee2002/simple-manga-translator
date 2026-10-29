from __future__ import annotations

import copy
import json
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import settings
from .demo import create_demo
from .pipeline import run, text_issues
from .storage import APP_ROOT, USER_ROOT, Project
from .translate import Budget, Translator, completion
from .typeset import export_pdf, layout_issues, preview

STATUS = {'imported': '待识别', 'recognized': '待翻译', 'translated': '已翻译',
          'review': '待复核', 'ready': '可导出', 'error': '失败'}
ENGINES = {'DeepSeek': 'deepseek', '在线翻译': 'quick', '自定义API': 'compatible'}
OCR_MODES = {'英文OCR': 'english', '中英OCR': 'mixed', '云端视觉OCR': 'vision'}
ROLES = {'对白/旁白': 'dialogue', '拟声': 'sound_effect', '标识': 'graphic'}
CLEAR = {'自动清字': 'auto', '局部修补': 'inpaint', '白底清字': 'white', '保留底图': 'keep'}


def label(mapping, value):
    return next((k for k, v in mapping.items() if v == value), next(iter(mapping)))


class App:
    def __init__(self, root, project=None):
        self.root = root
        self.root.tk.call('tk','scaling',96/72)
        self.config, self.api_key, self.saved = settings.load()
        self.remember = bool(self.saved.get('remember'))
        self.project = None
        self.current = None
        self.selected = []
        self.region_rows = []
        self.messages = queue.Queue()
        self.stop = threading.Event()
        self.busy = False
        self.history = []
        self.preview_token = 0
        self.preview_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='preview')
        self.preview_future=None
        self.preview_image = None
        self.report = []
        self.tk_image = None
        self.zoom = None
        self.drag = None
        self.loading = False
        self.root.title('简单漫画翻译器')
        self.root.geometry(f'{min(1450,self.root.winfo_screenwidth()-60)}x{min(930,self.root.winfo_screenheight()-100)}+20+20')
        self.root.minsize(1120, 740)
        self.root.configure(bg='#eef2f6')
        self.build()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.bind('<Control-s>', lambda e: self.save_editor())
        self.root.bind('<Control-z>', lambda e: self.undo())
        self.root.bind('<Prior>', lambda e: self.navigate(-1))
        self.root.bind('<Next>', lambda e: self.navigate(1))
        self.root.after(100, self.poll)
        if project:
            self.open_project(project)
        elif self.saved.get('recent_project') and (Path(self.saved['recent_project'])/'project.json').exists():
            self.open_project(self.saved['recent_project'])
        else:
            self.use_demo()

    def build(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#eef2f6', foreground='#172b3a')
        style.configure('TButton', padding=(10, 6), background='#ffffff', borderwidth=1)
        style.configure('Primary.TButton', background='#0f766e', foreground='white')
        style.map('Primary.TButton', background=[('active', '#115e59')])
        style.configure('Treeview', rowheight=29, background='white', fieldbackground='white')
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'))
        header = tk.Frame(self.root, bg='#122b3a', height=76)
        header.pack(fill='x')
        tk.Label(header, text='简单漫画翻译器', font=('Microsoft YaHei UI', 22, 'bold'), fg='white', bg='#122b3a').pack(side='left', padx=(22, 14), pady=15)
        ttk.Button(header, text='引擎设置', command=self.settings_dialog).pack(side='right', padx=16)
        self.engine_badge = tk.Label(header, text='', fg='#99f6e4', bg='#122b3a', font=('Microsoft YaHei UI', 10))
        self.engine_badge.pack(side='right', padx=8)
        toolbar = ttk.Frame(self.root, padding=(16, 10))
        toolbar.pack(fill='x')
        buttons = [('新建项目', self.new_project), ('打开项目', self.open_project_dialog),
                   ('导入原稿', self.import_files), ('导入文件夹', self.import_folder),
                   ('导入已有校译', self.import_legacy), ('术语/背景', self.glossary_dialog)]
        for text, fn in buttons:
            ttk.Button(toolbar, text=text, command=fn).pack(side='left', padx=(0, 6))
        ttk.Button(toolbar, text='开始/继续翻译', style='Primary.TButton', command=self.start).pack(side='left', padx=8)
        ttk.Button(toolbar, text='停止', command=self.request_stop).pack(side='left')
        ttk.Button(toolbar, text='导出PDF', command=self.export).pack(side='right')
        main = ttk.Panedwindow(self.root, orient='horizontal')
        main.pack(fill='both', expand=True, padx=16, pady=(0, 8))
        left = ttk.Frame(main, padding=(0, 0, 10, 0));main.add(left, weight=0)
        self.project_label = ttk.Label(left, text='项目', font=('Microsoft YaHei UI', 11, 'bold'), wraplength=220)
        self.project_label.pack(anchor='w', pady=(2, 8))
        self.filter_var = tk.StringVar(value='全部页面')
        box = ttk.Combobox(left, textvariable=self.filter_var, values=['全部页面', '待复核/失败', '未完成'], state='readonly', width=25)
        box.pack(fill='x', pady=(0, 8));box.bind('<<ComboboxSelected>>', lambda e: self.refresh_tree())
        self.tree = ttk.Treeview(left, columns=('status',), show='tree headings', selectmode='browse', height=18)
        self.tree.heading('#0', text='章节 / 页面');self.tree.heading('status', text='状态')
        self.tree.column('#0', width=150, minwidth=90);self.tree.column('status', width=76, minwidth=60)
        self.tree.pack(fill='both', expand=True)
        for tag, color in [('ready','#0f766e'), ('review','#b45309'), ('error','#be123c')]:self.tree.tag_configure(tag, foreground=color)
        self.tree.bind('<<TreeviewSelect>>', self.tree_selected)
        left_foot=ttk.Frame(left);left_foot.pack(fill='x',side='bottom',before=self.tree)
        ttk.Button(left_foot, text='示例项目', command=self.use_demo).pack(fill='x')
        ttk.Button(left_foot, text='使用说明', command=self.show_help).pack(fill='x', pady=(6, 0))
        center = ttk.Frame(main);main.add(center, weight=1)
        nav = ttk.Frame(center, padding=(4, 0, 4, 8));nav.pack(fill='x')
        ttk.Button(nav, text='‹ 上页', command=lambda:self.navigate(-1)).pack(side='left')
        ttk.Button(nav, text='下页 ›', command=lambda:self.navigate(1)).pack(side='left', padx=5)
        self.page_label = ttk.Label(nav, text='');self.page_label.pack(side='left', padx=8)
        self.view_var = tk.StringVar(value='译文')
        for name in ['原图', '译文']:
            ttk.Radiobutton(nav, text=name, variable=self.view_var, value=name, command=self.draw_canvas).pack(side='right', padx=4)
        ttk.Button(nav, text='适应窗口', command=self.fit).pack(side='right', padx=5)
        cvframe = ttk.Frame(center);cvframe.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(cvframe, bg='#dce4ec', highlightthickness=0)
        sy = ttk.Scrollbar(cvframe, orient='vertical', command=self.canvas.yview)
        sx = ttk.Scrollbar(cvframe, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.pack(side='right', fill='y');sx.pack(side='bottom', fill='x');self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self.draw_canvas())
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.canvas.bind('<ButtonPress-1>', self.mouse_down)
        self.canvas.bind('<B1-Motion>', self.mouse_move)
        self.canvas.bind('<ButtonRelease-1>', self.mouse_up)
        self.canvas.bind('<ButtonPress-3>', self.add_start)
        self.canvas.bind('<B3-Motion>', self.add_move)
        self.canvas.bind('<ButtonRelease-3>', self.add_end)
        right = ttk.Frame(main, padding=(12, 0, 0, 0));main.add(right, weight=0)
        ttk.Label(right, text='译文与气泡', font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w', pady=(2, 5))
        self.region_list = tk.Listbox(right, height=3, width=32, selectmode='extended', exportselection=False,
                                     font=('Microsoft YaHei UI', 10), relief='flat', highlightthickness=1)
        self.region_list.pack(fill='x');self.region_list.bind('<<ListboxSelect>>', self.region_selected)
        actions = ttk.Frame(right);actions.pack(fill='x', pady=4)
        ttk.Button(actions, text='合并所选', command=self.merge).pack(side='left')
        ttk.Button(actions, text='撤销', command=self.undo).pack(side='left', padx=5)
        ttk.Button(actions, text='重译选区', command=self.retranslate).pack(side='left')
        ttk.Label(right, text='原文', foreground='#64748b').pack(anchor='w', pady=(5, 2))
        self.source_text = tk.Text(right, height=2, width=31, wrap='word', font=('Segoe UI', 10), relief='flat', padx=8, pady=6)
        self.source_text.pack(fill='x')
        ttk.Label(right, text='中文译文', foreground='#64748b').pack(anchor='w', pady=(7, 2))
        self.zh_text = tk.Text(right, height=3, width=29, wrap='word', font=('Microsoft YaHei UI', 12), relief='flat', padx=8, pady=7)
        self.zh_text.pack(fill='x')
        row = ttk.Frame(right);row.pack(fill='x', pady=6)
        self.role_var = tk.StringVar(value=next(iter(ROLES)))
        ttk.Combobox(row, textvariable=self.role_var, values=list(ROLES), state='readonly', width=16).pack(side='left')
        ttk.Label(row, text='字号').pack(side='left', padx=(8, 3))
        self.font_var = tk.StringVar(value='自动')
        ttk.Spinbox(row, values=('自动',)+tuple(range(12,101)), textvariable=self.font_var, width=4).pack(side='left')
        self.clear_var = tk.StringVar(value=next(iter(CLEAR)))
        ttk.Combobox(right, textvariable=self.clear_var, values=list(CLEAR), state='readonly').pack(fill='x')
        ttk.Button(right, text='保存并更新预览  Ctrl+S', style='Primary.TButton', command=self.save_editor).pack(fill='x')
        self.reviewed_var = tk.BooleanVar()
        ttk.Checkbutton(right, text='已复核', variable=self.reviewed_var, command=self.mark_reviewed).pack(anchor='w', pady=7)
        self.issues_text = tk.Text(right, height=2, width=31, wrap='word', font=('Microsoft YaHei UI',9), bg='#fff8ed', relief='flat', padx=7, pady=6, state='disabled')
        self.issues_text.pack(fill='both', expand=True)
        self.candidate_var = tk.StringVar()
        self.candidate_box = ttk.Combobox(right, textvariable=self.candidate_var, state='readonly', width=29)
        self.candidate_box.pack(fill='x', pady=(6, 4),side='bottom',before=self.issues_text)
        line = ttk.Frame(right);line.pack(fill='x',side='bottom',before=self.issues_text)
        ttk.Button(line, text='加入疑似漏字', command=self.accept_candidate).pack(side='left')
        ttk.Button(line, text='忽略候选', command=self.ignore_candidates).pack(side='right')
        foot = ttk.Frame(self.root, padding=(16, 6));foot.pack(fill='x',side='bottom',before=main)
        self.progress = ttk.Progressbar(foot, length=240);self.progress.pack(side='left')
        self.status = ttk.Label(foot, text='准备就绪');self.status.pack(side='left', padx=12)
        self.usage = ttk.Label(foot, text='', foreground='#64748b');self.usage.pack(side='right')
        self.log_var = tk.StringVar(value='')
        ttk.Label(self.root, textvariable=self.log_var, padding=(16, 4), foreground='#64748b', wraplength=1380).pack(fill='x',side='bottom',before=main)
        self.update_engine_badge()

    def emit(self, *values):self.messages.put(values)

    def task(self, fn, on_done=None):
        if self.busy:
            messagebox.showinfo('任务进行中', '请先停止或等待当前任务。');return
        self.save_editor(refresh=False)
        self.busy = True;self.stop.clear()
        self.source_text.configure(state='disabled');self.zh_text.configure(state='disabled')
        self.progress.configure(mode='indeterminate');self.progress.start(12)
        self.status.configure(text='正在执行…')
        def work():
            try:self.emit('done', fn(), on_done)
            except Exception as exc:self.emit('failure', str(exc))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            for _ in range(100):
                e = self.messages.get_nowait();kind=e[0]
                if kind=='preview':
                    _,token,pageid,image,rep=e
                    if self.current and token==self.preview_token and pageid==self.current['id']:
                        self.preview_image=image;self.report=rep
                        self.current['issues']=text_issues(self.current,self.project.manifest.get('glossary',{}))+layout_issues(self.current,rep)
                        if not self.busy:
                            errors=any(x['severity']=='error' for x in self.current['issues'])
                            if self.current.get('ocr_complete') or self.current['groups']:
                                self.current['status']='review' if errors or self.current['issues'] and not self.current.get('reviewed') else 'ready'
                            self.project.save_page(self.current)
                            self.refresh_tree()
                        self.show_issues();self.draw_canvas()
                elif kind=='preview_error':self.log_var.set(e[1])
                elif kind=='page':
                    self.refresh_tree()
                    if self.current and self.current['id']==e[1]:self.load_page(e[1], commit=False)
                elif kind=='progress':
                    _,n,total,elapsed,use=e;self.progress.stop();self.progress.configure(mode='determinate',maximum=max(1,total),value=n)
                    eta=(total-n)*elapsed/n if n else 0
                    self.status.configure(text=f'{n}/{total}页 · 已用{elapsed:.0f}秒 · 预计剩余{eta:.0f}秒')
                    amount=(use['prompt_tokens']*.3+use['output_tokens']*1.2)/1_000_000
                    text=f"请求 {use['requests']} · 缓存 {use['cache_hits']} · Token {use['prompt_tokens']+use['output_tokens']}"
                    if self.config['model']=='deepseek-flash':text+=f' · Flash峰时参考 ${amount:.4f}'
                    self.usage.configure(text=text)
                elif kind in {'import','export'}:self.status.configure(text=f'{e[1]}页 · {e[2]}')
                elif kind=='log':self.log_var.set(e[1])
                elif kind=='done':
                    self.busy=False;self.source_text.configure(state='normal');self.zh_text.configure(state='normal');self.progress.stop();self.status.configure(text='已停止，进度已保存' if self.stop.is_set() else '任务完成')
                    self.refresh_tree()
                    if e[2]:e[2](e[1])
                    elif isinstance(e[1],dict):self.log_var.set(json.dumps(e[1],ensure_ascii=False))
                elif kind=='failure':
                    self.busy=False;self.source_text.configure(state='normal');self.zh_text.configure(state='normal');self.progress.stop();self.status.configure(text='任务未完成，已有进度已保存');self.log_var.set(e[1]);self.refresh_tree()
                    messagebox.showerror('需要处理',e[1])
        except queue.Empty:pass
        self.root.after(100,self.poll)

    def update_engine_badge(self):
        name=label(ENGINES,self.config['engine'])
        self.engine_badge.configure(text=name)

    def save_settings(self):
        settings.save(self.config,self.api_key,self.remember,self.project.root if self.project else '')

    def open_project(self, path):
        if self.busy:return
        self.save_editor(refresh=False)
        try:self.project=Project(path)
        except Exception as exc:messagebox.showerror('无法打开',str(exc));return
        self.current=None;self.selected=[];self.history=[];self.zoom=None
        self.project_label.configure(text=self.project.manifest['name'])
        self.refresh_tree();self.save_settings()
        if self.project.ordered():self.load_page(self.project.ordered()[0]['id'],commit=False)

    def new_project(self):
        if self.busy:return
        parent=self.project.root.parent if self.project else Path.home()/'Documents'
        path=parent/('翻译项目_'+time.strftime('%Y%m%d_%H%M%S'))
        def create(values):
            target=Path(values[0]);Project(target,create=True,name=target.name);self.open_project(target)
        self.path_dialog('新建项目','项目目录',str(path),'创建项目',create,folder=True)

    def path_dialog(self,title,hint,initial,action,callback,multiple=False,folder=False):
        """A visible path entry is also usable when native file pickers are inconvenient."""
        if self.busy:return
        win=tk.Toplevel(self.root);win.title(title);win.transient(self.root)
        win.geometry(f'860x430+{self.root.winfo_rootx()+220}+{self.root.winfo_rooty()+180}')
        body=ttk.Frame(win,padding=22);body.pack(fill='both',expand=True)
        ttk.Label(body,text=title,font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
        ttk.Label(body,text=hint,wraplength=790).pack(anchor='w',pady=(10,12))
        text=tk.Text(body,height=7 if multiple else 3,wrap='word',font=('Microsoft YaHei UI',11),padx=10,pady=8)
        text.pack(fill='both',expand=True);text.insert('1.0',initial)
        buttons=ttk.Frame(body);buttons.pack(fill='x')
        def browse():
            if folder:
                value=filedialog.askdirectory(parent=win,title=title)
            elif title=='导出PDF':
                value=filedialog.asksaveasfilename(parent=win,title=title,defaultextension='.pdf',filetypes=[('PDF','*.pdf')])
            else:value=filedialog.askopenfilenames(parent=win,title=title,filetypes=[('漫画原稿','*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.pdf *.cbz *.zip'),('所有文件','*.*')])
            if value:
                text.delete('1.0','end');text.insert('1.0','\n'.join(value) if isinstance(value,(tuple,list)) else value)
        def apply():
            values=[v.strip().strip('"') for v in text.get('1.0','end-1c').splitlines() if v.strip()]
            if not values:messagebox.showerror('路径为空','请先输入或选择路径。',parent=win);return
            if not multiple and len(values)!=1:messagebox.showerror('路径无效','这里需要一个完整路径。',parent=win);return
            try:
                callback(values)
            except Exception as exc:messagebox.showerror('无法继续',str(exc),parent=win);return
            win.destroy()
        ttk.Button(buttons,text='浏览…',command=browse).pack(side='left')
        ttk.Button(buttons,text=action,style='Primary.TButton',command=apply).pack(side='right')
        ttk.Button(buttons,text='取消',command=win.destroy).pack(side='right',padx=8)
        self.active_path_dialog={'window':win,'text':text,'apply':apply}
        text.focus_set()

    def open_project_dialog(self):
        p=filedialog.askopenfilename(title='选择project.json',filetypes=[('翻译项目','project.json')])
        if p:self.open_project(Path(p).parent)

    def use_demo(self):
        if self.busy:return
        p=create_demo(USER_ROOT/'示例项目');self.open_project(p.root)

    def import_files(self):
        if not self.project:return
        def apply(paths):
            for p in paths:
                if not Path(p).exists():raise ValueError('路径不存在：'+p)
            self.task(lambda:self.project.import_paths(paths,self.emit,self.stop),self.import_done)
        self.path_dialog('导入漫画原稿','文件或文件夹路径，每行一个。','','开始导入',apply,multiple=True)

    def import_folder(self):
        p=filedialog.askdirectory(title='选择原稿目录')
        if p:self.task(lambda:self.project.import_paths([p],self.emit,self.stop),self.import_done)

    def import_legacy(self):
        p=filedialog.askdirectory(title='选择译稿目录')
        if p:self.task(lambda:self.project.import_legacy(p,self.emit,self.stop),self.import_done)

    def import_done(self,result):
        self.log_var.set(f'导入完成：{result}')
        if not self.current and self.project.ordered():self.load_page(self.project.ordered()[0]['id'],commit=False)

    def refresh_tree(self):
        if not self.project:return
        selected=self.current['id'] if self.current else None
        self.tree.delete(*self.tree.get_children());chapters={}
        for p in self.project.ordered():
            status=p.get('status','imported');mode=self.filter_var.get()
            if mode=='待复核/失败' and status not in {'review','error'}:continue
            if mode=='未完成' and status in {'ready','translated','review'}:continue
            chapter=p['chapter']
            if chapter not in chapters:chapters[chapter]=self.tree.insert('','end',text=chapter,open=True)
            self.tree.insert(chapters[chapter],'end',iid=p['id'],text=p['label'],values=(STATUS.get(status,status),),tags=(status,))
        if selected and self.tree.exists(selected):self.tree.selection_set(selected)

    def tree_selected(self,event=None):
        selected=self.tree.selection()
        if not selected or not self.project:return
        if any(p['id']==selected[0] for p in self.project.ordered()) and (not self.current or self.current['id']!=selected[0]):self.load_page(selected[0])

    def navigate(self,step):
        if not self.current or self.busy:return
        ps=self.project.ordered();i=next(i for i,p in enumerate(ps) if p['id']==self.current['id']);i=max(0,min(len(ps)-1,i+step));self.load_page(ps[i]['id'])

    def rows(self):
        d=self.current;consumed=set();rows=[]
        for ov in d.get('manual_bubbles',[]):
            gs=[g for g in d['groups'] if g['id'] in ov['ids']]
            if not gs:continue
            consumed.update(ov['ids']);b=ov.get('box') or self.union([g['box'] for g in gs])
            rows.append({'ids':ov['ids'],'box':b,'text':' '.join(g['text'] for g in gs),'zh':ov['zh'],'role':gs[0].get('role','dialogue'),'font_size':ov.get('max_font',0),'erase_mode':ov.get('erase_mode','auto')})
        for g in d['groups']:
            if g['id'] not in consumed:rows.append(dict(g,ids=[g['id']],box=g.get('layout_box',g['box'])))
        return sorted(rows,key=lambda x:min(x['ids']))

    @staticmethod
    def union(boxes):return [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]

    def load_page(self,pageid,commit=True):
        if commit:self.save_editor(refresh=False)
        self.current=self.project.load_page(pageid);self.selected=[];self.history=[];self.preview_image=None;self.report=[]
        self.page_label.configure(text=f'{self.current["chapter"]} · {self.current["label"]}')
        self.reviewed_var.set(bool(self.current.get('reviewed')))
        self.refresh_regions();self.show_issues();self.request_preview()
        if self.tree.exists(pageid):self.tree.selection_set(pageid);self.tree.see(pageid)

    def refresh_regions(self):
        self.region_rows=self.rows();self.loading=True;self.region_list.delete(0,'end')
        for r in self.region_rows:
            txt=r.get('zh') or r['text'];prefix='○' if r.get('role')=='dialogue' else '—'
            self.region_list.insert('end',f'{prefix} #{"+".join(map(str,r["ids"]))} {txt[:21]}')
        for i,r in enumerate(self.region_rows):
            if r['ids']==self.selected:self.region_list.selection_set(i)
        self.loading=False
        if self.selected:self.load_editor()
        else:
            for w in [self.source_text,self.zh_text]:w.delete('1.0','end')

    def region_selected(self,event=None):
        if self.loading:return
        self.save_editor(refresh=False)
        values=self.region_list.curselection()
        if len(values)==1:self.selected=list(self.region_rows[values[0]]['ids']);self.load_editor();self.draw_canvas()

    def load_editor(self):
        if not self.selected:return
        r=next((r for r in self.rows() if r['ids']==self.selected),None)
        if not r:return
        for widget,text in [(self.source_text,r['text']),(self.zh_text,r.get('zh',''))]:
            widget.configure(state='normal');widget.delete('1.0','end');widget.insert('1.0',text)
            if self.busy:widget.configure(state='disabled')
        self.role_var.set(label(ROLES,r.get('role','dialogue')));self.clear_var.set(label(CLEAR,r.get('erase_mode','auto')));self.font_var.set(str(r['font_size']) if r.get('font_size') else '自动')

    def save_editor(self,refresh=True):
        if not self.current or not self.selected or self.busy:return
        en=self.source_text.get('1.0','end-1c');zh=self.zh_text.get('1.0','end-1c')
        try:sz=max(0,min(100,int(self.font_var.get())))
        except ValueError:sz=0
        row=next((r for r in self.rows() if r['ids']==self.selected),None)
        if not row:return
        changed=(en!=row['text'] or zh!=row.get('zh','') or ROLES[self.role_var.get()]!=row.get('role') or sz!=int(row.get('font_size') or 0) or CLEAR[self.clear_var.get()]!=row.get('erase_mode','auto'))
        if changed:
            self.history.append(copy.deepcopy(self.current));self.history=self.history[-20:]
            gs=[g for g in self.current['groups'] if g['id'] in self.selected]
            for g in gs:g['role']=ROLES[self.role_var.get()];g['locked']=bool(zh.strip())
            if en!=row['text']:
                for i,g in enumerate(gs):g.setdefault('original_text',g['text']);g['text']=en if i==0 else ''
            if len(gs)==1 and not any(o['ids']==self.selected for o in self.current.get('manual_bubbles',[])):
                gs[0].update(zh=zh,font_size=sz,erase_mode=CLEAR[self.clear_var.get()])
            else:
                ov=next(o for o in self.current['manual_bubbles'] if o['ids']==self.selected)
                ov.update(zh=zh,max_font=sz or 23,erase_mode=CLEAR[self.clear_var.get()])
            self.current['revision']=self.current.get('revision',0)+1;self.current['reviewed']=False;self.reviewed_var.set(False)
            self.project.save_page(self.current)
        if refresh:self.refresh_regions();self.request_preview()

    def request_preview(self):
        if not self.current:return
        self.preview_token+=1;token=self.preview_token;d=copy.deepcopy(self.current)
        def work():
            try:
                image,rep=preview(d);self.emit('preview',token,d['id'],image,rep)
            except Exception as exc:self.emit('preview_error',str(exc))
        if self.preview_future:self.preview_future.cancel()
        self.preview_future=self.preview_pool.submit(work)

    def show_issues(self):
        if not self.current:return
        issues=self.current.get('issues',[]);text='\n'.join(('● ' if x['severity']=='error' else '△ ')+x['message'] for x in issues) or '暂无问题'
        self.issues_text.configure(state='normal');self.issues_text.delete('1.0','end');self.issues_text.insert('1.0',text);self.issues_text.configure(state='disabled')
        candidates=self.current.get('ocr_candidates',[]);self.candidate_box.configure(values=[f'{i+1}. {c["text"]}' for i,c in enumerate(candidates)])
        self.candidate_var.set(f'1. {candidates[0]["text"]}' if candidates else '没有待处理的漏字候选')

    def fit(self):self.zoom=None;self.draw_canvas()

    def draw_canvas(self):
        self.canvas.delete('all')
        if not self.current:return
        if self.view_var.get()=='原图' or self.preview_image is None:
            with Image.open(self.current['source']) as im:image=im.convert('RGB')
        else:image=self.preview_image
        W,H=self.current['size'];cw=max(100,self.canvas.winfo_width()-40);ch=max(100,self.canvas.winfo_height()-40)
        self.scale=self.zoom or min(cw/W,ch/H)
        width=max(1,round(W*self.scale));height=max(1,round(H*self.scale))
        image=image.resize((width,height),Image.Resampling.LANCZOS);self.tk_image=ImageTk.PhotoImage(image)
        self.canvas.create_image(20,20,image=self.tk_image,anchor='nw');self.canvas.configure(scrollregion=(0,0,width+40,height+40))
        for row in self.rows():
            b=row['box'];c='#f59e0b' if row.get('role')!='dialogue' else '#14b8a6';sel=row['ids']==self.selected
            a=[20+v*self.scale for v in b];self.canvas.create_rectangle(*a,outline=c if not sel else '#2563eb',width=2 if sel else 1,dash=() if sel else (3,3))
            if sel:self.canvas.create_rectangle(a[2]-5,a[3]-5,a[2]+5,a[3]+5,fill='#2563eb',outline='white')

    def wheel(self,event):
        if event.state&4:self.zoom=max(.1,min(4,(self.zoom or getattr(self,'scale',.6))*(1.12 if event.delta>0 else 1/1.12)));self.draw_canvas()
        else:self.canvas.yview_scroll(-1 if event.delta>0 else 1,'units')

    def point(self,e):return [(self.canvas.canvasx(e.x)-20)/self.scale,(self.canvas.canvasy(e.y)-20)/self.scale]

    def mouse_down(self,e):
        if not self.current or self.busy:return
        self.save_editor(refresh=False);x,y=self.point(e)
        matches=[r for r in self.rows() if r['box'][0]-5<=x<=r['box'][2]+5 and r['box'][1]-5<=y<=r['box'][3]+5]
        if not matches:return
        r=min(matches,key=lambda r:(r['box'][2]-r['box'][0])*(r['box'][3]-r['box'][1]));self.selected=r['ids'];b=r['box']
        self.drag={'start':[x,y],'box':b[:],'resize':abs(x-b[2])*self.scale<14 and abs(y-b[3])*self.scale<14}
        self.refresh_regions();self.load_editor();self.draw_canvas()

    def mouse_move(self,e):
        if not self.drag:return
        x,y=self.point(e);dx=x-self.drag['start'][0];dy=y-self.drag['start'][1];b=self.drag['box'];W,H=self.current['size']
        if self.drag['resize']:new=[b[0],b[1],max(b[0]+20,min(W,b[2]+dx)),max(b[1]+20,min(H,b[3]+dy))]
        else:
            dx=max(-b[0],min(W-b[2],dx));dy=max(-b[1],min(H-b[3],dy));new=[b[0]+dx,b[1]+dy,b[2]+dx,b[3]+dy]
        self.drag['new']=new;self.draw_canvas();a=[20+v*self.scale for v in new];self.canvas.create_rectangle(*a,outline='#2563eb',width=3)

    def mouse_up(self,e):
        if not self.drag:return
        new=self.drag.get('new');self.drag=None
        if new:
            self.history.append(copy.deepcopy(self.current));ov=next((o for o in self.current.get('manual_bubbles',[]) if o['ids']==self.selected),None)
            if ov:ov['box']=new;ov['free_layout']=True
            else:next(g for g in self.current['groups'] if g['id']==self.selected[0])['layout_box']=new
            self.current['reviewed']=False;self.project.save_page(self.current);self.refresh_regions();self.request_preview()

    def add_start(self,e):
        if self.current and not self.busy:self.drag_add=self.point(e)

    def add_move(self,e):
        if not hasattr(self,'drag_add'):return
        x,y=self.point(e);b=self.drag_add+[x,y];self.canvas.delete('newbox');self.canvas.create_rectangle(*[20+v*self.scale for v in b],outline='#2563eb',width=2,tags='newbox')

    def add_end(self,e):
        if not hasattr(self,'drag_add'):return
        x,y=self.point(e);a,b=self.drag_add;del self.drag_add;W,H=self.current['size'];box=[max(0,min(a,x)),max(0,min(b,y)),min(W,max(a,x)),min(H,max(b,y))]
        if box[2]-box[0]<15 or box[3]-box[1]<15:return
        self.history.append(copy.deepcopy(self.current));gid=max((g['id'] for g in self.current['groups']),default=0)+1
        self.current['groups'].append({'id':gid,'box':box,'text':'','zh':'','lines':[{'box':box,'text':'','confidence':1}], 'confidence':1,'role':'dialogue','erase_mode':'keep'})
        self.selected=[gid];self.project.save_page(self.current);self.refresh_regions();self.load_editor();self.request_preview()

    def merge(self):
        if self.busy or not self.current:return
        self.save_editor(refresh=False);indices=self.region_list.curselection()
        if len(indices)<2:messagebox.showinfo('合并气泡','按住Ctrl选择两个或更多文字区。');return
        rs=[self.region_rows[i] for i in indices];ids=[i for r in rs for i in r['ids']];self.history.append(copy.deepcopy(self.current))
        self.current['manual_bubbles']=[o for o in self.current.get('manual_bubbles',[]) if not set(o['ids'])&set(ids)]
        self.current['manual_bubbles'].append({'ids':ids,'zh':' '.join(r.get('zh','') for r in rs),'box':self.union([r['box'] for r in rs]),'free_layout':True})
        self.selected=ids;self.project.save_page(self.current);self.refresh_regions();self.load_editor();self.request_preview()

    def undo(self):
        if self.busy or not self.history:return
        self.current=self.history.pop();self.selected=[];self.project.save_page(self.current);self.refresh_regions();self.request_preview()

    def mark_reviewed(self):
        if not self.current or self.busy:return
        self.save_editor(refresh=False);self.current['reviewed']=self.reviewed_var.get()
        errors=any(i['severity']=='error' for i in self.current.get('issues',[]))
        self.current['status']='review' if errors or not self.current['reviewed'] and self.current.get('issues') else 'ready'
        self.project.save_page(self.current);self.refresh_tree()

    def accept_candidate(self):
        if not self.current or self.busy:return
        vals=self.current.get('ocr_candidates',[]);i=self.candidate_box.current()
        if not 0<=i<len(vals):return
        self.history.append(copy.deepcopy(self.current));item=vals.pop(i);gid=max((g['id'] for g in self.current['groups']),default=0)+1
        self.current['groups'].append(dict(item,id=gid,zh='',role='dialogue',lines=[copy.deepcopy(item)]));self.selected=[gid]
        self.project.save_page(self.current);self.refresh_regions();self.show_issues();self.request_preview()

    def ignore_candidates(self):
        if not self.current or self.busy:return
        self.history.append(copy.deepcopy(self.current));self.current['ignored_candidates']=self.current.get('ignored_candidates',[])+self.current.get('ocr_candidates',[]);self.current['ocr_candidates']=[]
        self.project.save_page(self.current);self.show_issues();self.request_preview()

    def start(self):
        if not self.project or not self.project.ordered():messagebox.showinfo('先导入原稿','请先新建项目并导入漫画图片、PDF或CBZ。');return
        cfg=dict(self.config,context=self.project.manifest.get('context',''))
        self.task(lambda:run(self.project,cfg,self.api_key,self.stop,self.emit),self.translation_done)

    def retranslate(self):
        if not self.current or not self.selected or self.busy:return
        self.save_editor(refresh=False);pageid=self.current['id'];ids=self.selected[:]
        row=next(r for r in self.rows() if r['ids']==ids);source=row['text']
        cfg=dict(self.config,context=self.project.manifest.get('context',''))
        def work():
            g={'id':1,'text':source,'zh':'','role':'dialogue'}
            budget=Budget(2);Translator(cfg,self.api_key,self.stop,budget,self.project.manifest.get('glossary',{})).translate([g],force=True)
            d=self.project.load_page(pageid);ov=next((o for o in d.get('manual_bubbles',[]) if o['ids']==ids),None)
            if ov:ov['zh']=g['zh']
            else:next(x for x in d['groups'] if x['id']==ids[0])['zh']=g['zh']
            d['reviewed']=False;d['status']='review';self.project.save_page(d)
            return pageid
        self.task(work,lambda p:self.load_page(p,commit=False))

    def translation_done(self,result):
        self.log_var.set(f"完成 {result['done']} 页，失败 {result['errors']} 页")
        if self.current:self.load_page(self.current['id'],commit=False)

    def request_stop(self):self.stop.set();self.status.configure(text='正在停止…')

    def export(self):
        if not self.project:return
        self.save_editor(refresh=False)
        dest=self.project.root/(self.project.manifest['name']+'_中文嵌字.pdf')
        self.path_dialog('导出PDF','保存路径',str(dest),'检查并导出',
                         lambda values:self.task(lambda:export_pdf(self.project,values[0],self.emit,self.stop),self.export_done))

    def export_done(self,result):
        win=tk.Toplevel(self.root);win.title('PDF导出完成');win.transient(self.root)
        win.geometry(f'850x330+{self.root.winfo_rootx()+230}+{self.root.winfo_rooty()+240}')
        body=ttk.Frame(win,padding=24);body.pack(fill='both',expand=True)
        ttk.Label(body,text=f"已导出 {result['pages']} 页 PDF",font=('Microsoft YaHei UI',17,'bold'),foreground='#0f766e').pack(anchor='w')
        text=tk.Text(body,height=3,wrap='word',font=('Microsoft YaHei UI',11));text.pack(fill='x');text.insert('1.0',result['path']);text.configure(state='disabled')
        ttk.Label(body,text=f"复核提醒：{result.get('warnings',0)} 条",foreground='#64748b').pack(anchor='w',pady=9)
        ttk.Button(body,text='关闭',command=win.destroy).pack(anchor='e')
        self.last_export_result=result

    def settings_dialog(self):
        if self.busy:return
        win=tk.Toplevel(self.root);win.title('引擎设置');win.geometry(f'680x660+{self.root.winfo_rootx()+380}+{self.root.winfo_rooty()+60}');win.transient(self.root)
        body=ttk.Frame(win,padding=22);body.pack(fill='both',expand=True)
        variables={}
        def field(name,key,value,values=None,secret=False):
            ttk.Label(body,text=name).pack(anchor='w',pady=(7,2));v=tk.StringVar(value=str(value));variables[key]=v
            if values:ttk.Combobox(body,textvariable=v,values=values,state='readonly').pack(fill='x')
            else:ttk.Entry(body,textvariable=v,show='●' if secret else '').pack(fill='x')
        field('翻译引擎','engine',label(ENGINES,self.config['engine']),list(ENGINES))
        field('API地址','endpoint',self.config['endpoint'])
        field('模型名称','model',self.config['model'])
        field('API密钥','key',self.api_key,secret=True)
        remember=tk.BooleanVar(value=self.remember);ttk.Checkbutton(body,text='记住密钥',variable=remember).pack(anchor='w',pady=5)
        field('文字识别','ocr_mode',label(OCR_MODES,self.config['ocr_mode']),list(OCR_MODES))
        line=ttk.Frame(body);line.pack(fill='x',pady=10)
        for title,key,values in [('原文','source_lang',['en','auto','ja','ko','vi','fr']),('目标','target_lang',['zh-CN','zh-TW','en','ja']),('并发','workers',['1','2','3','4','6','8'])]:
            ttk.Label(line,text=title).pack(side='left',padx=(0,4));v=tk.StringVar(value=str(self.config[key]));variables[key]=v;ttk.Combobox(line,textvariable=v,values=values,width=8).pack(side='left',padx=(0,12))
        limit=tk.StringVar(value=str(self.config['max_requests']));ttk.Label(body,text='请求上限').pack(anchor='w');ttk.Entry(body,textvariable=limit).pack(fill='x')
        secondary=tk.BooleanVar(value=self.config['secondary_ocr']);ttk.Checkbutton(body,text='二次识别查漏',variable=secondary).pack(anchor='w',pady=8)
        def apply():
            try:
                cfg=dict(self.config);cfg.update({k:v.get().strip() for k,v in variables.items() if k not in {'key','engine','ocr_mode'}})
                cfg.update(engine=ENGINES[variables['engine'].get()],ocr_mode=OCR_MODES[variables['ocr_mode'].get()],workers=int(variables['workers'].get()),max_requests=int(limit.get()),secondary_ocr=secondary.get())
                if not 1<=cfg['workers']<=8 or cfg['max_requests']<1:raise ValueError('并发需为1—8，请求次数至少为1。')
                self.config=cfg;self.api_key=variables['key'].get().strip();self.remember=remember.get();self.save_settings();self.update_engine_badge();win.destroy()
            except Exception as exc:messagebox.showerror('设置无效',str(exc),parent=win)
        ttk.Button(body,text='保存设置',style='Primary.TButton',command=apply).pack(fill='x',pady=8)

    def glossary_dialog(self):
        if not self.project or self.busy:return
        win=tk.Toplevel(self.root);win.title('术语与故事背景');win.geometry('650x650')
        ttk.Label(win,text='术语表，每行一条：原文=译名',padding=12).pack(anchor='w')
        text=tk.Text(win,height=14,font=('Microsoft YaHei UI',11));text.pack(fill='both',expand=True,padx=14)
        text.insert('1.0','\n'.join(f'{a}={b}' for a,b in self.project.manifest.get('glossary',{}).items()))
        ttk.Label(win,text='故事背景',padding=12).pack(anchor='w')
        context=tk.Text(win,height=6,font=('Microsoft YaHei UI',11));context.pack(fill='x',padx=14);context.insert('1.0',self.project.manifest.get('context',''))
        def save():
            try:
                terms={}
                for line in text.get('1.0','end-1c').splitlines():
                    if not line.strip():continue
                    a,b=line.split('=',1);a=a.strip();b=b.strip()
                    if not a or not b:raise ValueError('术语和译名都不能为空。')
                    terms[a]=b
                if len(terms)>200:raise ValueError('术语不能超过200条。')
                self.project.manifest['glossary']=terms;self.project.manifest['context']=context.get('1.0','end-1c')[:2000];self.project.save();win.destroy()
            except ValueError as exc:messagebox.showerror('格式错误',str(exc),parent=win)
        ttk.Button(win,text='保存',style='Primary.TButton',command=save).pack(fill='x',padx=14,pady=14)

    def show_help(self):
        win=tk.Toplevel(self.root);win.title('使用说明');win.geometry('850x780')
        text=tk.Text(win,wrap='word',font=('Microsoft YaHei UI',11),padx=20,pady=16);text.pack(fill='both',expand=True)
        p=APP_ROOT/'docs/使用说明.md';text.insert('1.0',p.read_text(encoding='utf-8') if p.exists() else '新建项目 → 导入 → 设置API → 翻译 → 复核 → 导出。');text.configure(state='disabled')

    def close(self):
        if self.busy:
            self.request_stop();self.log_var.set('停止后即可关闭。');return
        self.save_editor(refresh=False);self.save_settings();self.preview_pool.shutdown(wait=False,cancel_futures=True);self.root.destroy()


def main(project=None):
    if os.name=='nt':
        import ctypes
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:pass
    root=tk.Tk();app=App(root,project);root.mainloop();return app
