"""Ask AquaRevive - a chatbot that answers only from the project's own data.

Two layers, both free:
  * AI mode (when a local Ollama model is running, default qwen2.5:7b): the model
    holds the conversation and calls TOOLS that fetch the real numbers (lake
    status, change events, history, rain, priority, accuracy). Its reply is then
    CHECKED: every number in it must come from the tool results or the question,
    otherwise the grounded answer below is used instead.
  * Grounded mode (always available, no model): find the lake (name, alias or a
    close spelling) and the topic (keywords), and reply in 2-3 plain sentences
    built from the real measurements.
Both add a link to the page that shows the details.
"""

import difflib
import json
import os
import re
import time

import numpy as np

from . import lake_analytics as la

ALIASES = {"halasuru": "ulsoor", "ulsur": "ulsoor", "bellanduru": "bellandur",
           "hesarghatta": "hesaraghatta", "lal bagh": "lalbagh", "muthanallur": "muthanalluru",
           "gunjur": "gunjuru"}
TOPICS = [   # first match wins
    ("method", ["how do you", "how does it work", "how is it measured", "method"]),
    ("accuracy", ["accura", "reliable", "trust", "correct"]),
    ("events", ["drain", "sluice", "desilt", "weed", "sewage", "happened", "event", "suddenly", "removed"]),
    ("rain", ["rain", "monsoon", "drought"]),
    ("priority", ["priority", "rank", "worst", "urgent", "attention", "top"]),
    ("history", ["trend", "history", "years", "since", "past", "before", "shrink", "decline"]),
    ("overview", ["how many", "all lakes", "overview", "summary", "which lakes", "below normal"]),
    ("status", ["how is", "status", "now", "current", "condition", "water", "level", "health"]),
]
EXAMPLES = ("Try: 'How is Bellandur?', 'Was Hebbal drained?', 'Bellandur over the years', "
            "'Which lakes need attention?', 'Rain at Ulsoor in 2022', 'How accurate is this?'")


def _when(d):
    from datetime import date
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d.day} {d.strftime('%b %Y')}"


# ---------------------------------------------------------------- understand
def find_lakes(text):
    t = text.lower()
    for alias, name in ALIASES.items():
        t = t.replace(alias, name)
    words = re.findall(r"[a-z]{4,}", t)
    found = []
    for lake in la._load_lakes():
        key = re.sub(r"\b(lake|kere|tank)\b", "", (lake["name"] or "").lower()).strip()
        if not key:
            continue
        if key in t or any(difflib.SequenceMatcher(None, w, key).ratio() >= 0.85 for w in words):
            found.append((t.find(key.split()[0]) if key.split()[0] in t else 999, lake))
    return [lake for _, lake in sorted(found, key=lambda x: x[0])]


def find_topic(text):
    t = text.lower()
    return next((topic for topic, words in TOPICS if any(w in t for w in words)), None)


def find_year(text):
    m = re.search(r"\b(20(1[5-9]|2[0-9]))\b", text)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- answers
def status(lake):
    a = la.anomaly(lake["id"])
    if a.get("observed_water_ha") is None:
        return f"{lake['name']} has no clear satellite measurement yet."
    text = f"{lake['name']} had {a['observed_water_ha']:.1f} ha of open water on {_when(a['date'])}."
    if a.get("deficit_fraction") is not None:
        d = a["deficit_fraction"]
        text += (f" That is {abs(d) * 100:.0f}% {'below' if d > 0 else 'above'} its usual "
                 f"{a['expected_water_ha']:.0f} ha for this season.")
    if a.get("interpretation"):
        text += " Weeds or plants are covering part of the lake."
    rank = next((i for i in la.rank()["items"] if i["lake_id"] == lake["id"]), None)
    if rank and rank["rank"] <= 5:
        text += f" It is #{rank['rank']} on the restoration priority list."
    return text


def events(lake, year=None):
    from . import events as ev
    res = ev.lake_events(lake)
    if not res.get("available"):
        return f"There are not enough clear images of {lake['name']} yet to check for sudden changes."
    found = [e for e in res["events"] if not year or str(year) in (e["start"][:4], e["end"][:4])]
    if not found:
        return (f"No sudden change that rain can't explain was found at {lake['name']}"
                + (f" in {year}." if year else "."))
    e = found[0]
    what = {"water_removed": "it was most likely drained or water was let out",
            "plants_covered_water": "weeds or plants spread over the water",
            "plants_cleared": "weeds were most likely cleared",
            "inflow_without_rain": "water flowed in without rain (sewage or treated water)"}[e["type"]]
    text = (f"Between {_when(e['start'])} and {_when(e['end'])}, {lake['name']} "
            f"{'lost' if e['change_ha'] < 0 else 'gained'} {abs(e['change_ha']):.1f} ha of water with only "
            f"{e['rain_mm']:.0f} mm of rain, so {what}.")
    if len(found) > 1:
        text += f" {len(found) - 1} other such change(s) were found" + (f" in {year}." if year else ".")
    return text


def history(lake, year=None):
    rows = la.lake_rows(lake["id"])
    if not rows:
        return f"{lake['name']} has no clear measurements yet."
    if year:
        yr = [r for r in rows if r["_date"].year == year]
        if not yr:
            return f"There is no clear image of {lake['name']} in {year}."
        lo, hi = min(yr, key=lambda r: r["_water"]), max(yr, key=lambda r: r["_water"])
        return (f"In {year}, {lake['name']} had between {lo['_water']:.1f} ha ({_when(lo['_date'])}) and "
                f"{hi['_water']:.1f} ha ({_when(hi['_date'])}) of open water.")
    cp = la.changepoint(lake["id"])
    if cp.get("change_point_year"):
        return (f"Before the monsoon, {lake['name']} used to hold about {cp['before_mean']:.0f} ha of open water; "
                f"since {cp['change_point_year']} it holds about {cp['after_mean']:.0f} ha.")
    levels = la.yearly_levels(lake["id"])
    if levels:
        first, last = list(levels.items())[0], list(levels.items())[-1]
        return (f"No big lasting change at {lake['name']}: about {first[1]:.0f} ha in {first[0]} and "
                f"{last[1]:.0f} ha in {last[0]} before the monsoon.")
    return f"{lake['name']} has {len(rows)} clear measurements since {rows[0]['_date'].year}."


def rain(lake, year=None):
    from . import rainfall
    try:
        calendar, mm = rainfall.lake_rain(lake)
    except rainfall.RainfallUnavailable:
        return "Rainfall data is not loaded yet."
    totals = {}
    for d, v in zip(calendar, mm):
        if np.isfinite(v):
            totals[d.year] = totals.get(d.year, 0.0) + float(v)
    full = [v for y, v in totals.items() if y < calendar[-1].year]
    avg = sum(full) / len(full)
    year = year or calendar[-1].year - 1
    if year not in totals:
        return f"No rainfall data for {year}."
    return (f"About {totals[year]:.0f} mm of rain fell around {lake['name']} in {year}; "
            f"an average year brings {avg:.0f} mm.")


def accuracy():
    from . import accuracy as acc, events as ev
    m = acc.measurement_accuracy()["by_reference"]
    v = ev.validation()
    return (f"Our water measurements closely match independent satellite data (correlation "
            f"{m['jrc']['pearson_r']:.2f} with JRC and {m['s1']['pearson_r']:.2f} with radar). "
            f"The change detector found {v['detected']} of {v['documented']} documented drainings or de-weedings; "
            "the misses were too small to see at those lakes.")


def priority():
    top = [i["name"] for i in la.rank()["items"][:5]]
    return "The lakes that need attention first are: " + ", ".join(top) + "."


def overview():
    s = la.summary()
    below = [i["name"] for i in la.rank()["items"]
             if i["status"] in ("persistent_degradation", "below_seasonal_normal")]
    return (f"AquaRevive watches {s['lakes_total']} well-known Bengaluru lakes. {len(below)} are clearly below "
            f"their usual water level for the season: " + ", ".join(below[:6]) + ("…" if len(below) > 6 else "."))


METHOD = ("Every few days the Sentinel-2 satellite photographs Bengaluru. For each lake we count the pixels that "
          "look like water inside its outline, skip cloudy days, and compare with the same season in other "
          "years. If water changes in a way the rain can't explain, we flag it.")


# ---------------------------------------------------------------- main
def grounded_answer(message, context=None):
    """Rule-based answer. -> {answer, lake_ids, topic, link}; context = {lake_ids, topic}."""
    message = (message or "").strip()[:300]
    context = context or {}
    if re.match(r"^\s*(hi|hello|hey|namaste|namaskara)\b", message, re.I):
        return {"answer": "Hello! Ask me about any well-known Bengaluru lake. " + EXAMPLES,
                "lake_ids": [], "topic": None, "link": None}
    lakes = find_lakes(message)
    topic = find_topic(message)
    year = find_year(message)
    # follow-up ("what about 2021?", "and the rain?") -> same lake, same topic
    follow_up = (not lakes and context.get("lake_ids") and topic not in ("priority", "overview", "accuracy", "method")
                 and (year or re.match(r"^\s*(and|what about|how about|then|also)\b", message, re.I)))
    if follow_up:                                    # "what about 2023?" -> same lake, same topic
        lakes = [la._lake_by_id(i) for i in context["lake_ids"] if la._lake_by_id(i)]
        topic = topic or context.get("topic")
    if topic == "method":
        reply = METHOD
    elif topic == "accuracy":
        reply = accuracy()
    elif topic == "priority" and not lakes:
        reply = priority()
    elif not lakes:
        if topic in ("overview", "events", "status"):
            reply = overview()
        else:
            return {"answer": "I can only answer questions about Bengaluru's lakes. " + EXAMPLES,
                    "lake_ids": [], "topic": None, "link": None}
    else:
        topic = topic if topic in ("events", "history", "rain") else ("history" if year else "status")
        fn = {"status": status, "events": lambda l: events(l, year),
              "history": lambda l: history(l, year), "rain": lambda l: rain(l, year)}[topic]
        reply = " ".join(fn(lake) for lake in lakes[:2])
    tab = {"events": "events", "history": "trends", "rain": "trends", "accuracy": "accuracy"}.get(topic)
    link = {"lake_id": lakes[0]["id"], "tab": tab} if lakes else None
    return {"answer": reply, "lake_ids": [l["id"] for l in lakes], "topic": topic, "link": link}


# ---------------------------------------------------------------- AI mode (local, free)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.getenv("CHAT_MODEL", "qwen2.5:7b")
MAX_TOOL_ROUNDS = 3
SYSTEM = (
    "You are 'Ask AquaRevive', a friendly assistant about Bengaluru's lakes, built on satellite "
    "measurements (Sentinel-2) and rainfall data. Always call a tool to get facts before answering "
    "anything about a lake, rain, priority, accuracy or how the system works - never answer such "
    "questions from memory. Use ONLY numbers, dates and names that appear in the tool results. "
    "If the tools do not answer the question, say you don't have that data. You may explain "
    "general terms (e.g. what a sluice gate or desilting is) in plain words without numbers. "
    "Keep answers short (2-5 sentences), clear for a non-expert, no markdown headings. "
    "'ha' means hectares. An 'event' means a change in water that the rain cannot explain."
)


def _lake_arg(name):
    lakes = find_lakes(name or "")
    return lakes[0] if lakes else None


def _need_lake(fn):
    def run(args):
        lake = _lake_arg(args.get("lake"))
        if lake is None:
            return f"No lake called '{args.get('lake')}' in AquaRevive. Known lakes: " + list_lakes({})
        year = args.get("year")
        return fn(lake, int(year)) if year and fn is not status else fn(lake)
    return run


def list_lakes(_args):
    return ", ".join(sorted(l["name"] for l in la._load_lakes() if l.get("name")))


TOOLS = {
    "lake_status": (_need_lake(status), "Current open water of one lake vs its normal for the season, "
                    "weeds, and priority rank.", {"lake": "lake name"}),
    "lake_events": (_need_lake(events), "Sudden changes in a lake's water that rain cannot explain "
                    "(drained, weeds spread or cleared, sewage inflow). Optional year.",
                    {"lake": "lake name", "year": "optional year, e.g. 2026"}),
    "lake_history": (_need_lake(history), "How a lake's water changed over the years, or within one year.",
                     {"lake": "lake name", "year": "optional year"}),
    "lake_rain": (_need_lake(rain), "Rainfall around a lake in a year vs an average year.",
                  {"lake": "lake name", "year": "optional year"}),
    "priority_list": (lambda a: priority(), "The lakes that need restoration first.", {}),
    "city_overview": (lambda a: overview(), "How many lakes are watched and which are below normal.", {}),
    "accuracy": (lambda a: accuracy(), "How accurate the measurements and the change detector are.", {}),
    "how_it_works": (lambda a: METHOD, "How AquaRevive measures water and detects changes.", {}),
    "list_lakes": (list_lakes, "Names of all lakes AquaRevive follows.", {}),
}


def _tool_specs():
    specs = []
    for name, (_, desc, params) in TOOLS.items():
        specs.append({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object",
                           "properties": {k: {"type": "string", "description": v} for k, v in params.items()},
                           "required": [k for k in params if k == "lake"]}}})
    return specs


_alive = {"at": 0.0, "ok": False}


def model_available():
    """Is Ollama running with the model? (checked at most every 30 s)"""
    if time.time() - _alive["at"] < 30:
        return _alive["ok"]
    import httpx
    try:
        tags = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=2).json()
        ok = any(m["name"].startswith(MODEL) for m in tags.get("models", []))
    except Exception:                                  # noqa: BLE001 - not installed / not running
        ok = False
    _alive.update(at=time.time(), ok=ok)
    return ok


NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")


def _numbers(text):
    return {float(n.replace(",", "")) for n in NUM.findall(text)}


def numbers_ok(reply, evidence):
    """Every number in the reply must appear in the evidence (allowing rounding)."""
    allowed = _numbers(evidence)
    for n in _numbers(reply):
        if n in allowed or (n.is_integer() and n <= 10):
            continue
        if any(abs(n - a) <= 0.51 and round(a) == n for a in allowed) or \
                any(abs(n - a) < 0.051 for a in allowed):
            continue
        return False
    return True


HINT_TOOL = {"status": "lake_status", "events": "lake_events", "history": "lake_history", "rain": "lake_rain",
             "priority": "priority_list", "overview": "city_overview", "accuracy": "accuracy",
             "method": "how_it_works"}


def _hints(message, context):
    """The rule-based understanding steers the model: which lakes, which tool."""
    parts = []
    lakes = find_lakes(message)
    if not lakes and context and context.get("lake_ids"):
        names = [la._lake_by_id(i)["name"] for i in context["lake_ids"] if la._lake_by_id(i)]
        if names:
            parts.append("The user was just talking about " + " and ".join(names) + ".")
    topic = find_topic(message)
    if topic in HINT_TOOL:
        parts.append(f"The tool '{HINT_TOOL[topic]}' probably answers this.")
    return (" (" + " ".join(parts) + ")") if parts else ""


def ai_answer(message, history, context=None):
    """Tool-calling conversation with the local model. -> (reply, evidence, lakes) or None."""
    import httpx
    msgs = [{"role": "system", "content": SYSTEM}]
    msgs += [{"role": h["role"], "content": h["content"][:600]} for h in (history or [])[-6:]
             if h.get("role") in ("user", "assistant")]
    # small models follow a reminder in the latest turn better than the system prompt alone
    msgs.append({"role": "user", "content": message + _hints(message, context) + REMINDER})
    # earlier turns were themselves verified, so their numbers are allowed too
    evidence, lakes = [message] + [h["content"] for h in (history or [])[-6:]], []
    used_tools = retried = False
    for _ in range(MAX_TOOL_ROUNDS + 1):
        r = httpx.post(f"{OLLAMA_URL}/api/chat", timeout=120, json={
            "model": MODEL, "messages": msgs, "tools": _tool_specs(), "stream": False,
            "options": {"temperature": 0.2}})
        r.raise_for_status()
        msg = r.json()["message"]
        calls = msg.get("tool_calls") or []
        if not calls and not used_tools and not retried and needs_data(message):
            retried = True                       # it answered from memory: ask once more, firmly
            msgs.append(msg)
            msgs.append({"role": "user", "content": "Do not answer from memory. Call the right tool "
                         "first, then answer using only its result."})
            continue
        if not calls:
            return (msg.get("content") or "").strip(), "\n".join(evidence), lakes, used_tools
        msgs.append(msg)
        used_tools = True
        for call in calls[:4]:
            name = call["function"]["name"]
            args = call["function"].get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            fn = TOOLS.get(name, (None,))[0]
            result = fn(args) if fn else f"Unknown tool {name}."
            if args.get("lake") and _lake_arg(args["lake"]):
                lakes.append(_lake_arg(args["lake"]))
            evidence.append(result)
            msgs.append({"role": "tool", "content": result, "tool_name": name})
    return None


REMINDER = ("\n\n(Look up any lake, rain, priority or accuracy facts with the tools before answering; "
            "earlier answers may be outdated. Never invent numbers or lake names.)")


def needs_data(message):
    """Does the question need AquaRevive data (a lake, or a data topic)?"""
    return bool(find_lakes(message)) or find_topic(message) in (
        "status", "history", "rain", "priority", "overview", "accuracy") or bool(
        re.search(r"which lake|worst|best|compare|how much|how many", message, re.I))


DOMAIN = re.compile(r"lake|kere|tank|water|rain|monsoon|weed|hyacinth|desilt|sluice|sewage|drain|encroach|"
                    r"flood|wetland|satellite|sentinel|bengaluru|bangalore|buffer|hectare|pollut|restor|"
                    r"ecolog|bird|fish|catchment|aquarevive|accura|data|measure|trust", re.I)


def answer(message, context=None, history=None):
    """Main entry: AI mode when available and verified, otherwise grounded mode."""
    base = grounded_answer(message, context)
    if not model_available() or not (message or "").strip():
        return {**base, "mode": "grounded"}
    # accuracy and method answers must not be "spun" (e.g. "high accuracy" for 1 of 3): keep the
    # carefully worded grounded text for them
    if base["topic"] in ("accuracy", "method") and not base["lake_ids"]:
        return {**base, "mode": "grounded"}
    try:
        out = ai_answer(message.strip()[:300], history, context)
    except Exception:                                  # noqa: BLE001 - never fail the user
        out = None
    if not out or not out[0]:
        return {**base, "mode": "grounded"}
    reply, evidence, lakes, used_tools = out
    if not used_tools:
        # no data fetched: fine for a general explanation ("what is desilting?"), but a data
        # question must use the grounded answer, and off-topic questions are declined
        if needs_data(message) or base["lake_ids"] or _numbers(reply) - _numbers(message):
            return {**base, "mode": "grounded"}
        if not DOMAIN.search(message):
            return {"answer": "I can only answer questions about Bengaluru's lakes. " + EXAMPLES,
                    "lake_ids": [], "topic": None, "link": None, "mode": "grounded"}
    if not numbers_ok(reply, evidence):
        return {**base, "mode": "grounded (AI reply had a number not in the data)"}
    lakes = list({l["id"]: l for l in lakes}.values()) or [la._lake_by_id(i) for i in base["lake_ids"]]
    link = base["link"] if base["link"] else ({"lake_id": lakes[0]["id"], "tab": None} if lakes else None)
    return {"answer": reply, "lake_ids": [l["id"] for l in lakes], "topic": base["topic"],
            "link": link, "mode": "ai", "model": MODEL}
