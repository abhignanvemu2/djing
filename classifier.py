"""Optional local CLAP inference on excerpts distributed across a track."""
import os
import subprocess
import threading

import numpy as np

_lock=threading.Lock()
_pipeline=None
GENRES="Afro House|Afro Tech|Organic House|Deep House|Melodic House|Melodic Techno|Progressive House|Tech House|House|Techno|Hard Techno|Industrial Techno|Peak-Time Techno|Hypnotic Techno|Minimal Techno|Trance|Drum & Bass".split("|")


def classify(path, duration):
    global _pipeline
    from transformers import pipeline
    with _lock:
        if _pipeline is None:
            _pipeline=pipeline("zero-shot-audio-classification",model=os.environ["MIXFLOW_CLAP_MODEL"])
        clips=[]
        for fraction in (.2,.5,.8):
            data=subprocess.check_output(["ffmpeg","-nostdin","-loglevel","error","-ss",str(max(0,duration*fraction-5)),
                "-i",str(path),"-t","10","-f","f32le","-ac","1","-ar","48000","-"],timeout=30)
            if data:
                clips.append(np.frombuffer(data,dtype="<f4"))
        result={}
        for field,labels in [("genre",GENRES),("mood",["atmospheric","hypnotic","emotional","aggressive","uplifting"]),
                             ("vocals",["instrumental music","music with singing or spoken vocals"])]:
            votes={}
            for clip in clips:
                for row in _pipeline(clip,candidate_labels=labels):
                    votes[row["label"]]=votes.get(row["label"],0)+row["score"]/len(clips)
            ordered=sorted(votes,key=votes.get,reverse=True)
            if ordered and votes[ordered[0]]-votes[ordered[1]]>.12:
                result[field]=ordered[0]
        return result
