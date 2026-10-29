"""Download pinned public OCR assets from RapidAI's model repository."""
import hashlib,os,time
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]/'assets/models'
FILES=[
 ('en_rec.onnx','onnx/PP-OCRv4/rec/en_PP-OCRv4_rec_mobile.onnx','e8770c967605983d1570cdf5352041dfb68fa0c21664f49f47b155abd3e0e318'),
 ('en_dict.txt','paddle/PP-OCRv4/rec/en_PP-OCRv4_rec_mobile/en_dict.txt','5662df9d2d03f0e8ca0d3b0649d6acbab904b6a14b3d3521463c71c37c668ce3')]

def download():
 ROOT.mkdir(parents=True,exist_ok=True)
 for name,path,expected in FILES:
  dst=ROOT/name
  if dst.exists() and hashlib.sha256(dst.read_bytes()).hexdigest()==expected:
   print('Ready:',name);continue
  url='https://modelscope.cn/api/v1/models/RapidAI/RapidOCR/repo'
  params={'Revision':'master','FilePath':path,'download_time':int(time.time())}
  try:response=requests.get(url,params=params,timeout=(15,90))
  except requests.RequestException:
   session=requests.Session();session.trust_env=False
   response=session.get(url,params=params,timeout=(15,90))
  response.raise_for_status();data=response.content
  if hashlib.sha256(data).hexdigest()!=expected:raise RuntimeError('OCR asset hash mismatch: '+name)
  temporary=dst.with_suffix(dst.suffix+'.download');temporary.write_bytes(data);os.replace(temporary,dst)
  print('Downloaded:',name)

if __name__=='__main__':download()
