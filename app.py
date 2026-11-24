import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from analysis import inspect_file, analyze
from curator import DEFAULT, STAGES, generate, settings, timeline

ROOT=Path(__file__).resolve().parent
DATA=Path(os.getenv("MIXFLOW_DATA",ROOT/"data")).resolve()
DATA.mkdir(parents=True,exist_ok=True)
AUDIO=DATA/"audio"
AUDIO.mkdir(exist_ok=True)
POOL=ThreadPoolExecutor(max_workers=int(os.getenv("MIXFLOW_WORKERS","2")))


@contextmanager
def db():
    con=sqlite3.connect(DATA/"mixflow.sqlite",timeout=20)
    con.row_factory=sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


def uid():
    return uuid.uuid4().hex


def process(tid):
    with db() as c:
        row=c.execute("select * from tracks where id=?",(tid,)).fetchone()
    if not row:
        return
    path=AUDIO/tid
    meta=json.loads(row["meta"])
    try:
        meta.update(analyze(path,meta))
        if os.getenv("MIXFLOW_CLAP_MODEL"):
            try:
                from classifier import classify
                for k,v in classify(path,meta["duration"]).items():
                    if not meta.get(k):
                        meta[k]=v
                        meta[k+"Source"]="CLAP estimate"
            except Exception:
                meta["classNote"]="CLAP classification unavailable. Review genre, mood and vocals."
        status="ready"
    except Exception:
        status="failed"
        meta["error"]="Audio analysis failed. Try re-encoding the track and importing it again."
    with db() as c:
        c.execute("update tracks set status=?,meta=? where id=?",(status,json.dumps(meta),tid))


def init():
    with db() as c:
        c.executescript("""
        pragma journal_mode=WAL;
        create table if not exists users(id text primary key,email text unique,salt blob,hash blob);
        create table if not exists sessions(token text primary key,owner text,expires real);
        create table if not exists projects(id text primary key,owner text,state text,rev integer,updated real);
        create table if not exists tracks(id text primary key,owner text,meta text,status text);
        create table if not exists members(project text,track text,primary key(project,track));
        """)
        jobs=list(c.execute("select id from tracks where status='analyzing'"))
    for row in jobs:
        POOL.submit(process,row[0])


def tracks(c,pid):
    overrides=json.loads(c.execute("select state from projects where id=?",(pid,)).fetchone()[0]).get("overrides",{})
    return [{"id":r["id"],"status":r["status"],**json.loads(r["meta"]),**overrides.get(r["id"],{})} for r in c.execute(
        "select tracks.* from tracks join members on track=id where project=? order by members.rowid",(pid,))]


class HTTPError(Exception):
    def __init__(self,status,message):
        self.status=status
        super().__init__(message)


class Handler(BaseHTTPRequestHandler):
    def send(self,code,data,ctype="application/json",headers=None):
        body=json.dumps(data,allow_nan=False).encode() if ctype=="application/json" else data
        self.send_response(code)
        self.send_header("Content-Type",ctype)
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        for k,v in (headers or {}).items():
            self.send_header(k,v)
        self.end_headers()
        self.wfile.write(body)

    def args(self):
        size=int(self.headers.get("Content-Length",0))
        if not 0<size<=2_000_000:
            raise ValueError("Invalid request size.")
        return json.loads(self.rfile.read(size))

    def cookie(self):
        jar=SimpleCookie()
        jar.load(self.headers.get("Cookie",""))
        return hashlib.sha256(jar["mixflow"].value.encode()).hexdigest() if "mixflow" in jar else ""

    def route(self):
        parts=urlsplit(self.path).path.strip("/").split("/")
        get=self.command=="GET"
        if parts[0]!="api":
            files={"":("index.html","text/html; charset=utf-8"),"app.js":("app.js","text/javascript"),"style.css":("style.css","text/css")}
            if len(parts)!=1 or parts[0] not in files or not get:
                raise HTTPError(404,"Page unavailable.")
            name,ctype=files[parts[0]]
            return self.send(200,(ROOT/"static"/name).read_bytes(),ctype)
        if not get and self.headers.get("X-MixFlow")!="1":
            raise HTTPError(403,"Invalid request origin.")
        action=parts[1] if len(parts)>1 else ""
        with db() as c:
            if action in ("login","register") and not get:
                p=self.args()
                email=p["email"].strip().lower()
                pw=p["password"].encode()
                if "@" not in email or len(email)>254 or not 8<=len(pw)<=256:
                    raise ValueError("Enter a valid email and a password of 8–256 bytes.")
                row=c.execute("select * from users where email=?",(email,)).fetchone()
                salt=row["salt"] if row else secrets.token_bytes(16)
                digest=hashlib.scrypt(pw,salt=salt,n=16384,r=8,p=1)
                if action=="register":
                    if row:
                        raise ValueError("An account already exists for this email.")
                    owner=uid()
                    c.execute("insert into users values(?,?,?,?)",(owner,email,salt,digest))
                else:
                    if not row or not hmac.compare_digest(row["hash"],digest):
                        raise HTTPError(401,"Check your email and password.")
                    owner=row["id"]
                token=secrets.token_urlsafe(32)
                c.execute("insert into sessions values(?,?,?)",(hashlib.sha256(token.encode()).hexdigest(),owner,time.time()+2592000))
                c.commit()
                return self.send(200,{},headers={"Set-Cookie":"mixflow="+token+"; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"+("; Secure" if os.getenv("MIXFLOW_HTTPS")=="1" else "")})
            user=c.execute("select owner,email from sessions join users on users.id=owner where token=? and expires>?",(self.cookie(),time.time())).fetchone()
            if not user:
                raise HTTPError(401,"Sign in to open your studio.")
            owner=user["owner"]
            if action=="logout":
                if get: raise HTTPError(405,"Use POST.")
                c.execute("delete from sessions where token=?",(self.cookie(),))
                c.commit()
                return self.send(200,{})
            if action=="studio" and get:
                projects=[{"id":r["id"],"updated":r["updated"],**{k:s[k] for k in ("name","prefs")},"count":len(s["rows"])} for r in c.execute("select * from projects where owner=? order by updated desc",(owner,)) if (s:=json.loads(r["state"]))]
                return self.send(200,dict(email=user["email"],projects=projects,model=os.getenv("MIXFLOW_MODEL"),classifier=bool(os.getenv("MIXFLOW_CLAP_MODEL"))))
            if action=="new" and not get:
                p=self.args()
                state=dict(name=p.get("name","Untitled set")[:120] or "Untitled set",prefs=DEFAULT,rows=[],versions=[],step=0,manual=False)
                pid=uid()
                c.execute("insert into projects values(?,?,?,0,?)",(pid,owner,json.dumps(state),time.time()))
                c.commit()
                return self.send(201,{"id":pid})
            if action=="audio" and get:
                tid=parts[2]
                row=c.execute("select meta from tracks where owner=? and id=?",(owner,tid)).fetchone()
                if not row: raise HTTPError(404,"Track unavailable.")
                path=AUDIO/tid
                size=path.stat().st_size
                start,end=0,size-1
                rng=self.headers.get("Range","")
                if rng:
                    import re
                    m=re.fullmatch(r"bytes=(\d*)-(\d*)",rng)
                    if not m or not any(m.groups()): raise HTTPError(416,"Invalid range.")
                    a,b=m.groups()
                    start=int(a) if a else max(0,size-int(b))
                    end=min(size-1,int(b)) if a and b else size-1
                    if start>end: raise HTTPError(416,"Invalid range.")
                self.send_response(206 if rng else 200)
                ext=json.loads(row["meta"])["filename"].rsplit(".",1)[-1].lower()
                self.send_header("Content-Type",{"mp3":"audio/mpeg","aif":"audio/aiff","aiff":"audio/aiff","wav":"audio/wav","flac":"audio/flac"}.get(ext,"application/octet-stream"))
                self.send_header("Accept-Ranges","bytes")
                self.send_header("Cache-Control","private, no-store")
                if rng: self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length",str(end-start+1))
                self.end_headers()
                with path.open("rb") as f:
                    f.seek(start)
                    left=end-start+1
                    while left:
                        block=f.read(min(left,65536))
                        if not block: break
                        self.wfile.write(block)
                        left-=len(block)
                return
            if action!="project" or len(parts)<3: raise HTTPError(404,"Unknown endpoint.")
            pid=parts[2]
            row=c.execute("select * from projects where owner=? and id=?",(owner,pid)).fetchone()
            if not row: raise HTTPError(404,"Project unavailable.")
            state=json.loads(row["state"])
            op=parts[3] if len(parts)>3 else ""
            if get and not op:
                library=tracks(c,pid)
                plan,total=timeline(state["rows"],library,state["prefs"])
                return self.send(200,dict(state=state,rev=row["rev"],tracks=library,plan=plan,total=total))
            if get: raise HTTPError(405,"Use POST.")
            if op=="upload":
                size=int(self.headers.get("Content-Length",0))
                if not 0<size<=250*1024**2: raise ValueError("Choose a file smaller than 250 MB.")
                tid=uid()
                path=AUDIO/tid
                filename=Path(unquote(self.headers.get("X-Filename","track"))).name[:250]
                try:
                    with path.open("xb") as f:
                        remaining=size
                        while remaining:
                            chunk=self.rfile.read(min(remaining,1024*1024))
                            if not chunk: raise ValueError("Upload interrupted.")
                            f.write(chunk)
                            remaining-=len(chunk)
                    meta=inspect_file(path,filename)
                    meta.update(filename=filename,size=size)
                    c.execute("insert into tracks values(?,?,?,'imported')",(tid,owner,json.dumps(meta)))
                    c.execute("insert into members values(?,?)",(pid,tid))
                    c.commit()
                except Exception:
                    path.unlink(missing_ok=True)
                    raise
                return self.send(201,{"id":tid})
            p=self.args()
            if op=="analyze":
                jobs=[t["id"] for t in tracks(c,pid) if t["status"] in ("imported","failed")]
                for tid in jobs:
                    if c.execute("update tracks set status='analyzing' where id=? and status in ('imported','failed')",(tid,)).rowcount:
                        c.commit()
                        POOL.submit(process,tid)
                return self.send(200,{})
            if op=="generate":
                result=generate(tracks(c,pid),settings(p.get("prefs",state["prefs"])),state["rows"],p.get("request",""),p.get("stage"))
                return self.send(200,{"rows":result})
            if op=="duplicate":
                new=uid()
                state["name"]+=" (copy)"
                c.execute("insert into projects values(?,?,?,0,?)",(new,owner,json.dumps(state),time.time()))
                c.execute("insert into members select ?,track from members where project=?",(new,pid))
                c.commit()
                return self.send(201,{"id":new})
            if p.get("rev")!=row["rev"]: raise HTTPError(409,"This project changed elsewhere. Reload to see the latest version.")
            if op in ("delete","remove"):
                c.execute("begin immediate")
                if c.execute("select rev from projects where id=?",(pid,)).fetchone()[0]!=p["rev"]: raise HTTPError(409,"Reload the project before editing.")
                tids=[r[0] for r in c.execute("select track from members where project=?",(pid,))]
                if op=="remove":
                    tid=p["id"]
                    if tid not in tids: raise ValueError("Track unavailable.")
                    tids=[tid]
                    c.execute("delete from members where project=? and track=?",(pid,tid))
                    for version in [state,*state["versions"]]:
                        version["rows"]=[r for r in version["rows"] if r["id"]!=tid]
                    c.execute("update projects set state=?,rev=rev+1 where id=?",(json.dumps(state),pid))
                else:
                    c.execute("delete from members where project=?",(pid,))
                    c.execute("delete from projects where id=?",(pid,))
                unused=[tid for tid in tids if not c.execute("select 1 from members where track=?",(tid,)).fetchone()]
                for tid in unused: c.execute("delete from tracks where id=?",(tid,))
                c.commit()
                for tid in unused: (AUDIO/tid).unlink(missing_ok=True)
                return self.send(200,{})
            if op=="save":
                s=p["state"]
                settings(s["prefs"])
                ids={t["id"] for t in tracks(c,pid)}
                for version in [s,*s["versions"]]:
                    rows=version["rows"]
                    if len({r["id"] for r in rows})!=len(rows) or any(r["id"] not in ids or r["stage"] not in STAGES for r in rows):
                        raise ValueError("Tracklist contains unavailable or duplicate tracks.")
                if not c.execute("update projects set state=?,rev=rev+1,updated=? where id=? and rev=?",(json.dumps(s),time.time(),pid,p["rev"])).rowcount:
                    raise HTTPError(409,"Reload the project before editing.")
                c.commit()
                return self.send(200,{"rev":p["rev"]+1})
            raise HTTPError(404,"Unknown action.")

    def handle_call(self):
        try: self.route()
        except HTTPError as e: self.send(e.status,{"error":str(e)})
        except (BrokenPipeError,ConnectionResetError): pass
        except (ValueError,KeyError,TypeError,IndexError) as e: self.send(400,{"error":str(e)})
        except (subprocess.SubprocessError,OSError): self.send(422,{"error":"Could not read the audio. Check its format and FFmpeg installation."})
        except Exception:
            import traceback
            traceback.print_exc()
            self.send(500,{"error":"The server could not complete this operation."})

    do_GET=handle_call
    do_POST=handle_call


if __name__=="__main__":
    init()
    ThreadingHTTPServer((os.getenv("MIXFLOW_HOST","127.0.0.1"),int(os.getenv("PORT","8000"))),Handler).serve_forever()
