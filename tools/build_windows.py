"""Build a portable Windows folder; never include user projects/settings/API keys."""
import os,subprocess,sys
from pathlib import Path

root=Path(__file__).resolve().parents[1]
args=[sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--windowed','--name','简单漫画翻译器',
      '--distpath',str(root/'发行版'),'--workpath',str(root/'build'),
      '--add-data',str(root/'assets')+';assets','--add-data',str(root/'docs')+';docs',
      '--collect-all','rapidocr_onnxruntime','--collect-binaries','onnxruntime','--collect-data','onnxruntime',
      '--hidden-import','PIL.ImageTk','--hidden-import','reportlab.pdfbase.ttfonts',
      '--exclude-module','torch','--exclude-module','tensorflow','--exclude-module','transformers',
      '--exclude-module','matplotlib','--exclude-module','pandas','--exclude-module','IPython',
      str(root/'run.py')]
with (root/'build.log').open('w',encoding='utf-8') as log:
    env=dict(os.environ)
    vendor=root/'vendor/cpu'
    if (vendor/'cv2/cv2.pyd').exists():
        env['PYTHONPATH']=str(vendor)+os.pathsep+env.get('PYTHONPATH','')
        args+=['--paths',str(vendor)]
    code=subprocess.call(args,cwd=root,stdout=log,stderr=subprocess.STDOUT,env=env)
if code:raise SystemExit('构建失败，请查看build.log')
import shutil
release=root/'发行版/简单漫画翻译器'
shutil.copy2(root/'README.md',release/'README.md')
shutil.copy2(root/'LICENSE',release/'LICENSE')
shutil.copy2(root/'THIRD_PARTY_NOTICES.md',release/'THIRD_PARTY_NOTICES.md')
shutil.copytree(root/'docs',release/'docs',dirs_exist_ok=True)
print(release)
