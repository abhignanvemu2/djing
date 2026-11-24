"""Exercise the HTTP entry points with a private database and real audio fixtures."""
import io
import json
import os
import tempfile
import time
import unittest
import wave
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

import numpy as np

import app


class Client:
    def __init__(self):
        self.cookie = ""

    def request(self, endpoint, body=None, filename=None):
        handler = app.Handler.__new__(app.Handler)
        handler.request_version = "HTTP/1.1"
        handler.command = "GET" if body is None else "POST"
        handler.path = "/api/" + endpoint
        handler.requestline = handler.command + " " + handler.path
        buf = body if filename else json.dumps(body).encode() if body is not None else b""
        handler.headers = {"Cookie": self.cookie, "X-MixFlow": "1", "Content-Length": str(len(buf))}
        if filename:
            handler.headers["X-Filename"] = filename
        handler.rfile = io.BytesIO(buf)
        handler.wfile = io.BytesIO()
        handler.log_request = lambda *a: None
        handler.handle_call()
        header, payload = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        first, rest = header.split(b"\r\n", 1)
        fields = BytesParser().parsebytes(rest)
        if fields.get("Set-Cookie"):
            self.cookie = fields["Set-Cookie"].split(";")[0]
        return int(first.split()[1]), json.loads(payload) if fields["Content-Type"] == "application/json" else payload

    def ok(self,*args):
        status,value=self.request(*args)
        assert status in (200,201), value
        return value


class StudioWorkflow(unittest.TestCase):
    def test_real_audio_private_projects_and_versions(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app,"DATA",Path(tmp)), patch.object(app,"AUDIO",Path(tmp)/"audio"), patch.dict(os.environ,{"MIXFLOW_MODEL":"","MIXFLOW_CLAP_MODEL":""}):
            app.AUDIO.mkdir()
            app.init()
            client, stranger = Client(), Client()
            for n, user in enumerate([client,stranger]):
                user.ok("register",{"email":f"dj{n}@example.test","password":"practice-set"})
            base="project/"+client.ok("new",{"name":"Rehearsal"})["id"]
            self.assertEqual(stranger.request(base)[0],404)
            self.assertEqual(client.request(base+"/upload",b"invalid","broken.mp3")[0],422)
            t=np.arange(16000*16)/16000
            for n in range(6):
                samples=(.2*np.sin(2*np.pi*(220+n*11)*t)+.5*np.sin(2*np.pi*80*t)*np.exp(-40*(t%(60/(120+n)))))*(n+1)/6
                buf=io.BytesIO()
                with wave.open(buf,"wb") as w:
                    w.setparams((1,2,16000,0,"NONE",""))
                    w.writeframes((samples*32000).astype("<i2").tobytes())
                client.ok(base+"/upload",buf.getvalue(),f"Practice {n}.wav")
            client.ok(base+"/analyze",{})
            for _ in range(200):
                snapshot=client.ok(base)
                if all(t["status"]=="ready" for t in snapshot["tracks"]): break
                time.sleep(.1)
            self.assertTrue(all(t["status"]=="ready" for t in snapshot["tracks"]))
            self.assertTrue(any(t["bpm"] for t in snapshot["tracks"]))
            self.assertTrue(all(0<=t["energy"]<=100 for t in snapshot["tracks"]))
            track=snapshot["tracks"][0]
            self.assertEqual(stranger.request("audio/"+track["id"])[0],404)
            self.assertTrue(client.ok("audio/"+track["id"]).startswith(b"RIFF"))
            state=snapshot["state"]
            state["prefs"]["minutes"]=1
            rows=client.ok(base+"/generate",{"prefs":state["prefs"]})["rows"]
            self.assertEqual({r["stage"] for r in rows},set(app.STAGES))
            self.assertEqual(len(rows),len({r["id"] for r in rows}))
            state["rows"]=rows
            state["versions"]=[{"name":"Practice","rows":rows,"prefs":state["prefs"]}]
            client.ok(base+"/save",{"state":state,"rev":0})
            self.assertEqual(client.request(base+"/save",{"state":state,"rev":0})[0],409)
            plan=client.ok(base)
            self.assertTrue(0<plan["total"]<=61)
            self.assertEqual(plan["plan"][0]["time"],0)
            alternative=client.ok(base+"/generate",{"stage":"Opening"})["rows"]
            self.assertEqual([r for r in rows if r["stage"]!="Opening"],[r for r in alternative if r["stage"]!="Opening"])
            copy="project/"+client.ok(base+"/duplicate",{})["id"]
            client.ok(base+"/remove",{"id":track["id"],"rev":1})
            client.ok(base+"/delete",{"rev":2})
            self.assertEqual(len(client.ok(copy)["tracks"]),6)
            client.ok(copy+"/delete",{"rev":0})
            self.assertEqual(list(app.AUDIO.iterdir()),[])
            client.ok("logout",{})
            self.assertEqual(client.request("studio")[0],401)


if __name__=="__main__":
    unittest.main()
