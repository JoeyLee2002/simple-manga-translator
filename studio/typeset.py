"""Bubble clearing and Chinese typesetting.
Original erasure boxes and editable placement frames are intentionally independent.
"""
from pathlib import Path
import json,io,re,math,copy,functools,os,threading
import cv2,numpy as np
from PIL import Image,ImageFont
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
import fitz
FONT_PATH=next((str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name) for name in ['msyhbd.ttc','msyh.ttc','simsun.ttc'] if (Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name).exists()),'')
if not FONT_PATH:raise RuntimeError('未找到中文字体，请安装微软雅黑或宋体。')
pdfmetrics.registerFont(TTFont('CN',FONT_PATH,subfontIndex=0))
_RENDER_LOCK=threading.RLock()
@functools.lru_cache(maxsize=128)
def font(size):return ImageFont.truetype(FONT_PATH,size)
def tw(s,size):return pdfmetrics.stringWidth(s,'CN',size)
def wrap(text,size,width):
 out=[]
 for para in text.split('\n'):
  cur=''
  for ch in para:
   if cur and tw(cur+ch,size)>width:
    if ch in '。，！？；：、）】》”’…' and len(cur)>1:out.append(cur[:-1]);cur=cur[-1]+ch
    else:out.append(cur);cur=ch
   else:cur+=ch
  if cur:out.append(cur)
 return out

def get_jobs(d):
 im=Image.open(d['source']).convert('RGB');arr=np.array(im);H,W=arr.shape[:2];gray=cv2.cvtColor(arr,cv2.COLOR_RGB2GRAY)
 n,labels,stats,_=cv2.connectedComponentsWithStats((gray>238).astype(np.uint8),8)
 edges=cv2.dilate(cv2.Canny(gray,40,100),np.ones((2,2),np.uint8));_,edge_labels,edge_stats,_=cv2.connectedComponentsWithStats((edges==0).astype(np.uint8),8)
 candidates={};jobs=[]
 for gg in d['groups']:
  g=copy.deepcopy(gg);t=g['zh'].strip()
  if not t or g.get('role') in ['graphic','sound_effect']:continue
  if not g.get('role') and (t.startswith('（') or 'manga.' in g['text'].lower()):continue
  b=[max(0,round(g['box'][0])),max(0,round(g['box'][1])),min(W,round(g['box'][2])),min(H,round(g['box'][3]))]
  x0,y0,x1,y1=b
  if x1<=x0 or y1<=y0:continue
  ls=labels[y0:y1,x0:x1];ids,count=np.unique(ls[ls>0],return_counts=True);cid=None
  for i in np.argsort(count)[::-1]:
   k=int(ids[i]);x,y,w,h,a=stats[k]
   if a>200 and a<W*H*.25 and x>1 and y>1 and x+w<W-1 and y+h<H-1 and count[i]/ls.size>.45:cid=k;break
  job={'zh':t,'lines':g['lines'],'box':g.get('layout_box',b),'ids':[g['id']],'cid':cid,'manual':bool(g.get('layout_box')),'free_layout':bool(g.get('layout_box')),'max_font':int(g.get('font_size') or max(23,round(W/784*23))),'erase_mode':g.get('erase_mode','auto')};jobs.append(job)
 # Explicit overrides fix OCR grouping and source reading order where needed.
 for ov in d.get('manual_bubbles',[]):
  selected=[j for j in jobs if set(j['ids'])&set(ov['ids'])]
  if not selected:continue
  jobs=[j for j in jobs if j not in selected]
  b=ov.get('box') or [min(j['box'][0] for j in selected),min(j['box'][1] for j in selected),max(j['box'][2] for j in selected),max(j['box'][3] for j in selected)]
  jobs.append({'box':b,'zh':ov['zh'],'ids':ov['ids'],'lines':[l for j in selected for l in j['lines']],'cid':selected[0]['cid'],'manual':True,'polygon':ov.get('polygon'),'erase_brown':ov.get('erase_brown'),'solid_rect':ov.get('solid_rect'),'max_font':ov.get('max_font',23),'free_layout':ov.get('free_layout',False),'max_lines':ov.get('max_lines'),'erase_mode':ov.get('erase_mode','auto')})
 # Merge only automatic regions sharing an enclosed white balloon.
 combined=[];candidates={}
 for j in jobs:
  cid=j['cid']
  if j.get('manual') or cid is None:combined.append(j);continue
  if cid not in candidates:candidates[cid]=j;combined.append(j);continue
  a=candidates[cid];x=a['box'];y=j['box'];
  if max(x[0]-y[2],y[0]-x[2],0)**2+max(x[1]-y[3],y[1]-x[3],0)**2>35**2:combined.append(j);continue
  a['zh']+=' '+j['zh'];a['lines']+=j['lines'];a['ids']+=j['ids'];a['box']=[min(x[0],y[0]),min(x[1],y[1]),max(x[2],y[2]),max(x[3],y[3])]
 jobs=combined
 # Merge intersecting OCR fragments, preserving all source erase boxes.
 change=True
 while change:
  change=False
  for i,a in enumerate(jobs):
   for j in range(i+1,len(jobs)):
    b=jobs[j]
    if a.get('manual') or b.get('manual'):continue
    x=a['box'];y=b['box']
    if min(x[2],y[2])+2>max(x[0],y[0]) and min(x[3],y[3])+2>max(x[1],y[1]):
     a['zh']+=' '+b['zh'];a['lines']+=b['lines'];a['ids']+=b['ids'];a['box']=[min(x[0],y[0]),min(x[1],y[1]),max(x[2],y[2]),max(x[3],y[3])];jobs.pop(j);change=True;break
   if change:break
 for j in jobs:
  b=j['box'];cid=j['cid'];known=np.zeros((H,W),np.uint8)
  for l in j['lines']:
   a,b0,c,e=[round(v) for v in l['box']];cv2.rectangle(known,(max(0,a-3),max(0,b0-3)),(min(W-1,c+3),min(H-1,e+3)),1,-1)
  filled=None
  if j.get('erase_mode')=='keep':j['mask']=None;j['clearing']='preserved'
  if j.get('erase_mode')=='white':
   for l in j['lines']:
    aa,bb,cc,ee=map(round,l['box']);arr[max(0,bb-2):min(H,ee+2),max(0,aa-2):min(W,cc+2)]=255
   j['mask']=None;j['clearing']='manual_white'
  if j.get('erase_brown'):
   ex0,ey0,ex1,ey1=j['erase_brown'];reg=arr[ey0:ey1,ex0:ex1];fl=reg.astype(np.float32);rr,gg,bb=fl[:,:,0],fl[:,:,1],fl[:,:,2];ink=((rr-gg>8)&(gg-bb>4)&(gg<190));nn,ll,ss,_=cv2.connectedComponentsWithStats((cv2.cvtColor(reg,cv2.COLOR_RGB2GRAY)<170).astype(np.uint8),8)
   for ki in range(1,nn):
    xx,yy,ww,hh,aa=ss[ki]
    if xx>1 and yy>1 and xx+ww<reg.shape[1]-1 and yy+hh<reg.shape[0]-1:ink|=(ll==ki)
   ink=cv2.dilate(ink.astype(np.uint8),np.ones((9,9),np.uint8));cream=(rr>=gg)&(gg>=bb)&(rr>185)&(gg>180)&(bb>150);color=np.median(reg[cream],axis=0) if np.any(cream) else np.array([246,236,211]);reg[ink>0]=color.astype(np.uint8);arr[ey0:ey1,ex0:ex1]=reg;j['clearing']='tinted_balloon';j['mask']=None
  if j.get('solid_rect'):
   aa,bb,cc,ee=j['solid_rect'];arr[bb:ee,aa:cc]=255;j['mask']=None;j['clearing']='manual_caption_strip'
  if j.get('polygon'):

   filled=np.zeros((H,W),np.uint8);cv2.fillPoly(filled,[np.array(j['polygon'],dtype=np.int32)],1);safe=cv2.erode(filled,np.ones((5,5),np.uint8));arr[safe>0]=255;j['clearing']='manual_balloon';j['mask']=safe
  if cid is not None and 'mask' not in j and j.get('erase_mode')!='inpaint':
   component=(labels==cid).astype(np.uint8);cont,_=cv2.findContours(component,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE);filled=np.zeros_like(component);cv2.drawContours(filled,cont,-1,1,-1)
   unexpected=int(np.sum((filled>0)&(known==0)&(gray<180)))
   # A balloon can be cleared completely only when it contains no unrelated art.
   if unexpected/max(1,np.sum(filled))<.055 and np.sum((filled>0)&(known>0))/max(1,np.sum(known))>.97:
    safe=cv2.erode(filled,np.ones((3,3),np.uint8));arr[safe>0]=255;j['clearing']='opaque_balloon';j['mask']=safe
  if 'mask' not in j and np.sum(known)>0 and j.get('erase_mode')!='inpaint':
   es=edge_labels[known>0];eid,ct=np.unique(es[es>0],return_counts=True)
   for ix in np.argsort(ct)[::-1][:3]:
    k=int(eid[ix]);ex,ey,ew,eh,ea=edge_stats[k]
    if ea<200 or ea>W*H*.20 or ex<=1 or ey<=1 or ex+ew>=W-1 or ey+eh>=H-1:continue
    comp=(edge_labels==k).astype(np.uint8);cc,_=cv2.findContours(comp,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE);fm=np.zeros_like(comp);cv2.drawContours(fm,cc,-1,1,-1)
    cov=np.sum((fm>0)&(known>0))/max(1,np.sum(known));extra=np.sum((fm>0)&(known==0)&(gray<150))/max(1,np.sum(fm))
    if cov>.80 and extra<.055:
     safe=cv2.erode(fm,np.ones((3,3),np.uint8));arr[safe>0]=255
     # Complete the few source glyph pixels cut off by edge detection, within their text rectangles.
     rest=(known>0)&(safe==0);arr[rest]=255
     j['mask']=safe;j['clearing']='edge_balloon';break
  if 'mask' not in j:
   yy,xx=np.where(known>0)
   if len(xx):x0=int(xx.min());x1=int(xx.max())+1;y0=int(yy.min());y1=int(yy.max())+1
   else:x0,y0,x1,y1=map(round,j['box'])
   pgray=gray[max(0,y0):min(H,y1),max(0,x0):min(W,x1)]
   if not pgray.size:pgray=np.array([255],np.uint8)
   white=np.mean(pgray>225);dark=np.mean(pgray<45)
   ink=(gray<115) if np.median(pgray)>100 else (gray>160)
   m=(ink & (known>0)).astype(np.uint8)*255;m=cv2.dilate(m,np.ones((5,5),np.uint8),iterations=1)
   yy,xx=np.where(known>0)
   if len(xx):x0=int(xx.min());x1=int(xx.max())+1;y0=int(yy.min());y1=int(yy.max())+1
   x0=max(0,x0-8);y0=max(0,y0-8);x1=min(W,x1+8);y1=min(H,y1+8)
   arr[y0:y1,x0:x1]=cv2.inpaint(arr[y0:y1,x0:x1],m[y0:y1,x0:x1],3,cv2.INPAINT_TELEA);j['clearing']='text_mask';j['mask']=None;j['light_text']=not(white>.55) and dark>.4
 return Image.fromarray(arr),jobs

def layout(im,jobs):
 W,H=im.size;occupied=[];report=[]
 for j in jobs:
  x0,y0,x1,y1=j['box'];cx=(x0+x1)/2;cy=(y0+y1)/2;text=re.sub('…{3,}','……',j['zh']);mask=None if j.get('free_layout') else j['mask'];others=[k['box'] for k in jobs if k is not j];choice=None;used_fallback=False
  centers=[(cx,cy)]
  if mask is not None:
   ds=cv2.distanceTransform(mask,cv2.DIST_L2,5);_,_,_,loc=cv2.minMaxLoc(ds);bx,by=loc
   if abs(bx-cx)<80 and abs(by-cy)<80:centers.append((bx,by))
  for size in range(j.get('max_font',23),11,-1):
   opts=[]
   for count in range(2,min(81,max(11,int((x1-x0)/12)+1))):
    width=min(x1-x0+80,count*size);ls=wrap(text,size,width)
    if j.get('max_lines') and len(ls)>j['max_lines']:continue
    w=max([tw(l,size) for l in ls],default=1);h=len(ls)*size*1.22
    for tx,ty in centers:
     a=tx-w/2-2;b=ty-h/2-2;c=tx+w/2+2;e=ty+h/2+2
     if a<0 or b<0 or c>W or e>H:continue
     if any(max(0,min(c,r[2])-max(a,r[0]))*max(0,min(e,r[3])-max(b,r[1]))>2 for r in occupied+others):continue
     if mask is not None:
      roi=mask[math.floor(b):math.ceil(e),math.floor(a):math.ceil(c)]
      if not roi.size or np.mean(roi)<.998:continue
     elif a<x0-12 or b<y0-12 or c>x1+12 or e>y1+12:continue
     ratio=max(.8,min(1.8,(x1-x0)/max(1,y1-y0)))
     orphan=3 if len(ls)>1 and len(re.sub(r'[\s。，！？；：、…]','',ls[-1]))<=1 else 0
     opts.append((abs(w/max(1,h)-ratio)+orphan+.003*abs(tx-cx)+.003*abs(ty-cy),ls,a+2,b+2,w,h))
   if opts:
    _,ls,a,b,w,h=min(opts,key=lambda v:v[0]);choice=(size,ls,a,b,w,h);break
  if choice is None:
   used_fallback=True
   # Keep an explicit repair ticket rather than silently claiming a polished layout.
   for size in range(14,7,-1):
    ls=wrap(text,size,max(20,x1-x0-2));h=len(ls)*size*1.22
    if h<=y1-y0:break
   w=x1-x0;choice=(size,ls,x0,cy-h/2,w,h)
  sz,ls,a,b,w,h=choice;occupied.append((a,b,a+w,b+h));j.update({'font_size':sz,'output_lines':ls,'text_rect':[a,b,a+w,b+h]});report.append({'ids':j['ids'],'font_size':sz,'rect':j['text_rect'],'clearing':j['clearing'],'text':j['zh'],'needs_repair':sz<12 or used_fallback})
 return jobs,report

def draw(c,d):
 im,jobs=get_jobs(d);jobs,rep=layout(im,jobs);W,H=im.size;s=.75;c.setPageSize((W*s,H*s));buf=io.BytesIO();im.save(buf,'JPEG',quality=97,subsampling=0);buf.seek(0);c.drawImage(ImageReader(buf),0,0,width=W*s,height=H*s)
 for j in jobs:
  sz=j['font_size'];a,b,e,f=j['text_rect'];c.setFont('CN',sz*s);c.setFillColorRGB(1,1,1) if j.get('light_text') else c.setFillColorRGB(.03,.03,.03)
  yy=b
  for line in j['output_lines']:
   # Embedded vector text stays sharp at any PDF zoom level.
   asc=pdfmetrics.getAscent('CN')/1000*sz;desc=pdfmetrics.getDescent('CN')/1000*sz
   baseline=H-yy-(sz*1.22+(asc+desc))/2;c.drawCentredString((a+e)/2*s,baseline*s,line);yy+=sz*1.22
 c.showPage();return rep


def preview(d, max_edge=1900):
    # Use exactly the same vector renderer for the screen and exported PDF.
    with _RENDER_LOCK:
        buf=io.BytesIO();c=canvas.Canvas(buf);report=draw(c,d);c.save()
        with fitz.open(stream=buf.getvalue(),filetype='pdf') as doc:
            scale=min(4/3,max_edge/max(doc[0].rect.width,doc[0].rect.height))
            pix=doc[0].get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
            im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
    return im,report


def layout_issues(d, report):
    issues=[];covered={i for r in report for i in r['ids']};W,H=d['size']
    for g in d['groups']:
        if g.get('role')!='dialogue':continue
        if not g.get('zh','').strip():issues.append({'severity':'error','kind':'missing_translation','ids':[g['id']],'message':'正文尚未翻译'})
        elif g['id'] not in covered:issues.append({'severity':'error','kind':'missing_embedding','ids':[g['id']],'message':'译文没有嵌入页面'})
    for i,r in enumerate(report):
        if r['needs_repair']:issues.append({'severity':'error','kind':'layout','ids':r['ids'],'message':'文字无法安全装入气泡，请缩短译文或调整文字框'})
        x=r['rect']
        if x[0]<0 or x[1]<0 or x[2]>W or x[3]>H:issues.append({'severity':'error','kind':'bounds','ids':r['ids'],'message':'文字越出页面'})
        if r['clearing']=='text_mask':issues.append({'severity':'warning','kind':'background','ids':r['ids'],'message':'复杂底图已局部清字，请检查是否有原文残影或画面损伤'})
        for b in report[i+1:]:
            y=b['rect'];area=max(0,min(x[2],y[2])-max(x[0],y[0]))*max(0,min(x[3],y[3])-max(x[1],y[1]))
            if area>2:issues.append({'severity':'error','kind':'overlap','ids':r['ids']+b['ids'],'message':'两个文字区相互重叠'})
    return issues


def export_pdf(project, destination, emit=lambda *a:None, stop=None, page_ids=None):
    import collections,csv,tempfile
    target=Path(destination);target.parent.mkdir(parents=True,exist_ok=True)
    descriptors=[p for p in project.ordered() if page_ids is None or p['id'] in page_ids]
    if not descriptors:raise ValueError('项目没有可导出的页面。')
    tmp=target.with_name(target.stem+'.partial.pdf');reports=[]
    try:
        with _RENDER_LOCK:
            c=canvas.Canvas(str(tmp));c.setTitle(project.manifest['name'])
            for n,meta in enumerate(descriptors,1):
                if stop and stop.is_set():raise RuntimeError('已停止导出，原有PDF未改变。')
                d=project.load_page(meta['id']);rep=draw(c,d)
                from .pipeline import text_issues
                issues=text_issues(d,project.manifest.get('glossary',{}))+layout_issues(d,rep)
                if not d.get('ocr_complete') and not d['groups']:
                    issues.append({'severity':'error','kind':'unprocessed','ids':[],'message':'页面尚未识别，不能作为已翻译页面导出'})
                reports.append({'page':n,'chapter':d['chapter'],'label':d['label'],'regions':rep,'issues':issues})
                emit('export',n,f'{d["chapter"]} / {d["label"]}')
            c.save()
        # Fail closed on missing translations/overflow; never silently deliver a broken layout.
        bad=[(r['page'],x['message']) for r in reports for x in r['issues'] if x['severity']=='error']
        from .storage import atomic_json
        report_path=target.with_suffix('.检查报告.json');atomic_json(report_path,{'pages':reports,'errors':bad})
        if bad:raise RuntimeError(f'导出被阻止：{len(bad)}处正文或排版错误。首处：第{bad[0][0]}页，{bad[0][1]}。请在“待复核”中修正。')
        with fitz.open(tmp) as doc:
            assert len(doc)==len(reports)
            toc=[];last=None
            for i,(page,r) in enumerate(zip(doc,reports),1):
                page.get_pixmap(matrix=fitz.Matrix(.5,.5),alpha=False)
                expected=collections.Counter(re.findall('[\u3400-\u9fff]',''.join(x['text'] for x in r['regions'])))
                actual=collections.Counter(re.findall('[\u3400-\u9fff]',page.get_text()))
                if expected-actual:raise RuntimeError(f'第{i}页PDF中文提取校验失败。')
                if r['chapter']!=last:toc.append([1,r['chapter'],i]);last=r['chapter']
            doc.set_toc(toc)
            clean=target.with_name(target.stem+'.verified.pdf');doc.save(clean,garbage=4,deflate=True)
        os.replace(clean,target);tmp.unlink(missing_ok=True)
        with target.with_suffix('.译文.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f);w.writerow(['页码','章节','原页名','文字区','中文'])
            for r in reports:
                for g in r['regions']:w.writerow([r['page'],r['chapter'],r['label'],','.join(map(str,g['ids'])),g['text']])
        return {'path':str(target),'pages':len(reports),'warnings':sum(x['severity']=='warning' for r in reports for x in r['issues'])}
    finally:
        if tmp.exists():tmp.unlink()
