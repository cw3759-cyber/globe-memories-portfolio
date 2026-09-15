"""Repeatable checks using a disposable copy and synthetic data only."""
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def main():
    checks = []
    def check(label, condition):
        if not condition: raise AssertionError(label)
        checks.append(label)
    with tempfile.TemporaryDirectory(prefix='globe-template-check-') as tmp:
        folder = Path(tmp)
        for name in ['app', 'static', 'demo']:
            shutil.copytree(ROOT/name, folder/name, ignore=shutil.ignore_patterns('__pycache__'))
        personal = folder/'data'
        personal.mkdir()
        sentinel = personal/'do-not-touch.txt'
        sentinel.write_text('synthetic personal directory sentinel')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = {k:v for k,v in os.environ.items() if not k.startswith(('BACKUP_', 'RESET_', 'RENDER'))}
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        def start():
            proc = subprocess.Popen([sys.executable,'-m','uvicorn','demo.app:app','--host','127.0.0.1','--port',str(port)], cwd=folder, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                if proc.poll() is not None: raise RuntimeError('Demo server exited before startup')
                try:
                    urllib.request.urlopen(base+'/demo/health',timeout=1).close()
                    return proc
                except urllib.error.URLError: time.sleep(.1)
            proc.terminate();proc.wait(timeout=10)
            raise RuntimeError('Demo server startup timed out')
        def stop(proc):
            proc.terminate();proc.wait(timeout=10)
        def client():
            return urllib.request.build_opener(NoRedirect(),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def request(opener,path,method='GET',data=None,headers=None):
            req=urllib.request.Request(base+path,method=method,data=data,headers=headers or {})
            try:r=opener.open(req,timeout=10)
            except urllib.error.HTTPError as e:r=e
            return r.status,r.headers,r.read()
        def form(opener,path,values):
            return request(opener,path,'POST',urllib.parse.urlencode(values).encode(),{'Content-Type':'application/x-www-form-urlencoded'})
        def jreq(opener,path,method,data):
            return request(opener,path,method,json.dumps(data).encode(),{'Content-Type':'application/json'})
        proc=start()
        try:
            anon=client(); c=client(); partner=client()
            status,headers,_=request(anon,'/')
            check('Root redirects to template',status==307 and headers['Location']=='/demo/')
            for path in ['/api/places','/api/auth/status','/login','/media/anything.jpg','/health']:
                check('Private route blocked: '+path,request(anon,path)[0]==404)
            check('Demo API requires login',request(anon,'/demo/api/places')[0]==401)
            check('Demo static script loads',request(anon,'/demo/static/app.js')[0]==200)
            check('Demo earth image loads',request(anon,'/demo/static/img/earth-blue-marble.jpg')[0]==200)
            check('Login works',form(c,'/demo/api/auth/login',{'username':'demo','password':'demo2026'})[0]==303)
            check('Second sample account works',form(partner,'/demo/api/auth/login',{'username':'case','password':'demo2026'})[0]==303)
            places=json.loads(request(c,'/demo/api/places')[2])
            check('Seven seeded memories',len(places)==7)
            check('Seven seeded images',sum(len(x['photos']) for x in places)==7)
            photo=places[0]['photos'][0]['url']
            check('Anonymous image access denied',request(anon,photo)[0]==401)
            check('Authenticated image access works',request(c,photo)[0]==200)
            other=next(x for x in places if x['author']['username']=='case') if 'username' in places[0]['author'] else next(x for x in places if x['author']['display_name']=='阿舟')
            check('Other author cannot be edited',jreq(c,f"/demo/api/places/{other['id']}",'PATCH',{'title':'forbidden'})[0]==403)
            check('Other author cannot be deleted',request(c,f"/demo/api/places/{other['id']}",'DELETE')[0]==403)
            code,head,_=form(anon,'/demo/api/auth/register',{'username':'new','display_name':'Sample','password':'sample-pass'})
            check('Template registration rejected',code==303 and 'err=' in head['Location'])
            raw=io.BytesIO();Image.new('RGB',(80,60),(120,160,200)).save(raw,'PNG')
            boundary='globe-template-check'
            fields={'title':'Synthetic verification memory','lat':'0','lng':'0','place_date':'2026-01-01','location_name':'','description':'Disposable test only'}
            body=b''
            for key,val in fields.items():
                body+=f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{val}\r\n'.encode()
            body+=f'--{boundary}\r\nContent-Disposition: form-data; name="photos"; filename="sample.png"\r\nContent-Type: image/png\r\n\r\n'.encode()+raw.getvalue()+f'\r\n--{boundary}--\r\n'.encode()
            status,_,content=request(c,'/demo/api/places','POST',body,{'Content-Type':f'multipart/form-data; boundary={boundary}'})
            created=json.loads(content)
            check('Memory and image save',status==200 and len(created['photos'])==1)
            check('Generated thumbnail loads',request(c,created['photos'][0]['thumb'])[0]==200)
            check('Own memory can be edited',jreq(c,f"/demo/api/places/{created['id']}",'PATCH',{'description':'Updated synthetic text'})[0]==200)
            status,_,content=jreq(c,'/demo/api/messages','POST',{'body':'Synthetic verification message','place_id':created['id']})
            msg=json.loads(content)
            check('Location-linked message saves',status==200 and msg['place_id']==created['id'])
            partner_messages=json.loads(request(partner,'/demo/api/messages')[2])
            check('Other member sees message',any(x['id']==msg['id'] for x in partner_messages['messages']))
            check('Other member has unread message',partner_messages['unread']>0)
            check('Own memory deletion works',request(c,f"/demo/api/places/{created['id']}",'DELETE')[0]==200)
            messages=json.loads(request(c,'/demo/api/messages')[2])['messages']
            check('Deletion preserves message without dangling place',next(x for x in messages if x['id']==msg['id'])['place_id'] is None)
            check('Logout works',request(c,'/demo/api/auth/logout','POST')[0]==303)
            check('Logout removes access',request(c,'/demo/api/places')[0]==401)
            check('Personal directory untouched',list(personal.iterdir())==[sentinel] and sentinel.read_text()=='synthetic personal directory sentinel')
        finally:stop(proc)
        proc=start()
        try:
            c=client();form(c,'/demo/api/auth/login',{'username':'demo','password':'demo2026'})
            check('Restart restores seven memories',len(json.loads(request(c,'/demo/api/places')[2]))==7)
            check('Restart restores two messages',len(json.loads(request(c,'/demo/api/messages')[2])['messages'])==2)
            check('Restart leaves personal directory untouched',list(personal.iterdir())==[sentinel])
        finally:stop(proc)
    print(json.dumps({'passed':len(checks),'checks':checks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
