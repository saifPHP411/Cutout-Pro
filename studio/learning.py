"""Learn a ranking from consented seven-day retention measurements."""
import json
import math
import threading
import time
import uuid
from datetime import datetime

import numpy as np

from . import cache, config, ctx

LOCK = threading.RLock()
HORIZON = 7 * 86400
FIELDS = ("hook_strength", "emotional_impact", "entertainment", "context_clarity", "standalone_story",
          "ending_quality", "retention", "originality")
DEFAULT = {key: 1 / len(FIELDS) for key in FIELDS}
MAX_ROWS = 3000


def _file():
    return config.user_dir(ctx.uid()) / "learning.json"


def _read():
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"consent": False, "observations": {}, "versions": [], "active": None}


def _save(data):
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temp.replace(path)
    ctx.bump()


def status():
    with LOCK:
        data = _read()
        return {"consent": data["consent"], "observations": len(data["observations"]),
                "experiment": {k: v for k, v in (data.get("experiment") or {}).items() if k not in ("a", "b")} or None,
                "experiments": results(data) if data["consent"] else {}, "versions": list(reversed(data["versions"])), "active": data["active"] if data["consent"] else None}


def consent(enabled):
    with LOCK:
        data = _read()
        data["consent"] = enabled is True
        if not enabled:
            data["active"] = None
            data["experiment"] = None
        _save(data)
    return status()


def active():
    if not ctx.uid_or_none():
        return DEFAULT, "default"
    with LOCK:
        data = _read()
        if data["consent"] and data["active"]:
            row = next((v for v in data["versions"] if v["id"] == data["active"]), None)
            if row:
                return row["weights"], row["id"]
    return DEFAULT, "default"


def _timestamp(text):
    value = datetime.fromisoformat(str(text))
    if value.tzinfo is None:
        raise ValueError("Use a publication date and time with a time zone.")
    return value.timestamp()


def add(job, clip, body, provider=False):
    with LOCK:
        data = _read()
        if not data["consent"]:
            raise ValueError("Enable learning and data collection before saving performance.")
        if clip.get("status") != "done" or not all(k in (clip.get("ratings") or {}) for k in FIELDS):
            raise ValueError("Save results for a completed clip with component ratings.")
        published = _timestamp(body.get("published_at"))
        collected = _timestamp(body.get("observed_at"))
        now = time.time()
        if not job["created"] <= published <= collected <= now or (not provider and not HORIZON <= collected - published <= HORIZON + 86400):
            raise ValueError("Record a seven-day observation, measured between 7 and 8 days after publication.")
        source = str(body.get("evidence") or "").strip()[:300]
        target = float(body.get("retention", -1))
        views = body.get("views")
        interactions = body.get("interactions")
        if not source or not math.isfinite(target) or not 0 <= target <= (1000 if provider else 100) or type(views) is not int or views <= 0:
            raise ValueError("Enter the measured retention, views and source report.")
        if interactions is not None and (type(interactions) is not int or interactions < 0):
            raise ValueError("Engagement must be a nonnegative count.")
        key = f"{job['id']}:{clip['id']}"
        if key not in data["observations"] and len(data["observations"]) >= MAX_ROWS:
            raise ValueError("This account has reached the observation limit.")
        source_id = job.get("content_fingerprint") or cache.digest(job.get("source", {}).get("url") or job["id"])
        data["observations"][key] = {"source": source_id, "published": published, "observed": collected,
                                     "ratings": clip["ratings"], "score": clip.get("score"),
                                     "retention": target, "views": views, "interactions": interactions,
                                     "accepted": body.get("accepted") is True, "evidence": source, "provider": ("youtube" if provider is True else provider if isinstance(provider, str) else "manual"),
                                     "experiment": job.get("experiment"),
                                     "title": job.get("title", "")[:200], "length": clip["end"] - clip["start"],
                                     "hook_type": clip.get("category"), "captions": clip.get("style"),
                                     "framing": clip.get("layout")}
        _save(data)
    return status()



def experiment(enable, version=None):
    with LOCK:
        data = _read()
        if not data["consent"]:
            raise ValueError("Enable performance collection before running an experiment.")
        if enable:
            if data.get("experiment"):
                raise ValueError("End the current experiment before starting another.")
            tested = next((v for v in data["versions"] if v["id"] == version and v["eligible"]), None)
            if not tested or tested["id"] == data["active"]:
                raise ValueError("Choose an eligible challenger distinct from the active ranking.")
            weights, active_version = active()
            data["experiment"] = {"id": uuid.uuid4().hex, "control": active_version, "challenger": tested["id"],
                                  "a": weights, "b": tested["weights"], "created": time.time()}
        else:
            data["experiment"] = None
        _save(data)
    return status()


def assign(source_key):
    if not source_key:
        return None
    with LOCK:
        data = _read()
        trial = data.get("experiment") if data["consent"] else None
        if not trial:
            return None
        arm = "a" if int(cache.digest(trial["id"], source_key)[-1], 16) % 2 == 0 else "b"
        return {"id": trial["id"], "arm": arm, "version": trial["control" if arm == "a" else "challenger"],
                "weights": trial[arm]}


def results(data):
    from statistics import mean
    groups = {}
    for row in data["observations"].values():
        trial = row.get("experiment")
        if not trial:
            continue
        arms = groups.setdefault(trial["id"], {"a": {}, "b": {}})
        arms[trial["arm"]].setdefault(row["source"], []).append(row["retention"])
    return {tid: {k: {"sources": len(group), "retention": round(mean(mean(v) for v in group.values()), 2) if group else None}
                  for k, group in arms.items()} for tid, arms in groups.items()}


def _split(rows):
    ordered = sorted(rows, key=lambda row: row["published"])
    for i in range(int(0.65 * len(ordered)), int(0.8 * len(ordered)) + 1):
        cutoff = ordered[i]["published"]
        test = [r for r in ordered[i:] if r["published"] >= cutoff]
        held = {r["source"] for r in test}
        train = [r for r in ordered[:i] if r["published"] < cutoff and r["observed"] + (3 * 86400 if r.get("provider") in ("youtube", "windsor_youtube") else 0) < cutoff and r["source"] not in held]
        if len(train) >= 20 and len(test) >= 8 and len(held) >= 4 and len({r["source"] for r in train}) >= 8:
            return train, test
    raise ValueError("Need 20 earlier observations and an independent later set of 8 clips from 4 sources.")


def _samples(rows):
    x = np.array([[r["ratings"][k] / 100 for k in FIELDS] for r in rows])
    y = np.array([r["retention"] / 100 for r in rows])
    return x, y


def _accuracy(prediction, actual):
    a, b = np.triu_indices(len(actual), 1)
    true = np.sign(actual[a] - actual[b])
    predicted = np.sign(prediction[a] - prediction[b])
    usable = true != 0
    score = (predicted == true).astype(float) + 0.5 * (predicted == 0)
    return float(np.mean(score[usable])) if np.any(usable) else 0.5


def evaluate():
    with LOCK:
        data = _read()
        rows = list(data["observations"].values())
        cohorts = {}
        for row in rows:
            cohorts.setdefault(row.get("provider", "manual"), []).append(row)
        rows = max(cohorts.values(), key=len, default=[])
        if not data["consent"] or len(rows) < 28:
            raise ValueError("Enable learning and add at least 28 qualifying observations before comparing ranking.")
        train, test = _split(rows)
        x, y = _samples(train)
        xt, yt = _samples(test)
        centered = x - x.mean(axis=0)
        fitted = np.linalg.solve(centered.T @ centered + np.eye(len(FIELDS)) * 0.15, centered.T @ (y - y.mean()))
        fitted = np.maximum(0.025, fitted)
        fitted /= fitted.sum()
        baseline, version = active()
        previous = _accuracy(xt @ np.array([baseline[k] for k in FIELDS]), yt)
        candidate = _accuracy(xt @ fitted, yt)
        row = {"id": uuid.uuid4().hex[:12], "model": "ridge-v1", "baseline": version,
               "training": len(train), "cohort": train[0].get("provider", "manual"), "holdout": len(test), "previous": round(previous, 4),
               "candidate": round(candidate, 4), "eligible": candidate - previous >= 0.03,
               "weights": dict(zip(FIELDS, fitted.tolist())), "created": time.time()}
        # Keep the active weights even when newer comparisons fill the history.
        kept = data["versions"][-19:]
        if data["active"] and not any(v["id"] == data["active"] for v in kept):
            kept.insert(0, next(v for v in data["versions"] if v["id"] == data["active"]))
        data["versions"] = kept + [row]
        _save(data)
        return row


def select(version):
    with LOCK:
        data = _read()
        if not data["consent"]:
            raise ValueError("Enable learning to select ranking weights.")
        row = next((r for r in data["versions"] if r["id"] == version), None)
        if version != "default" and (not row or not row["eligible"]):
            raise ValueError("Choose Default or a ranking version that passed its holdout.")
        data["active"] = None if version == "default" else version
        _save(data)
    return status()
