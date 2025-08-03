import os
import json
import time
import hashlib
import argparse
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlsplit, urlunsplit, parse_qsl, urlencode
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Logging setup
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
log_filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S.log")
log_filepath = os.path.join(LOG_DIR, log_filename)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(log_filepath), logging.StreamHandler()]
)

def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")

def short_hash(s):
    return hashlib.sha1(s.encode()).hexdigest()[:10]

def hostname(u):
    try:
        return urlparse(u).netloc.lower()
    except:
        return ""

def normalize_url(u):
    try:
        parts = urlsplit(u)
        qs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
              if not k.lower().startswith(("utm_", "fbclid", "ref"))]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment))
    except:
        return u

def client():
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"])

def call_chat(model, messages, temperature=0.2):
    logging.info(f"LLM: {model}")
    logging.debug(f"LLM INPUT:\n{json.dumps(messages, indent=2)}")
    c = client()
    resp = c.chat.completions.create(model=model, messages=messages, temperature=temperature)
    content = resp.choices[0].message.content
    logging.debug(f"LLM OUTPUT:\n{content}")
    return content

def parse_json_or_empty(content):
    try:
        return json.loads(content)
    except:
        return {}

def call_search(query, limit=10):
    logging.info(f"Search: '{query}' limit={limit}")
    sys = "Return JSON only as {\"results\":[{\"title\",\"url\",\"snippet\",\"source\",\"published_at\"}...]}. No prose. If nothing recent, return {\"results\":[]}."
    payload = {
        "task":"search",
        "query":query,
        "return":{"format":"json","fields":["title","url","snippet","source","published_at"]},
        "limit":limit
    }
    # Cost control model for search
    content = call_chat("openai/gpt-4o-mini-search-preview", [
        {"role":"system","content":sys},
        {"role":"user","content":json.dumps(payload)}
    ], temperature=0.0)
    data = parse_json_or_empty(content)
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return data["results"]
    if isinstance(data, list):
        return data
    return []

def plan_queries(topic, window, depth):
    logging.info(f"Plan: topic='{topic}' depth={depth}")
    sys = "Output strict JSON: {\"queries\":[],\"rationale\":\"\",\"expected_signals\":[\"\"]}. Keep queries focused and recent."
    prompt = {
        "goal":"Find the most important, novel, credible items on the topic.",
        "topic":topic,
        "time_window":window,
        "depth":depth,
        "constraints":{"max_results_this_step":8,"focus":"high-impact, credible sources, actionable insights","recent":"last 48-72 hours"},
        "output":["queries","rationale","expected_signals"]
    }
    out = call_chat("openrouter/horizon-alpha", [
        {"role":"system","content":sys},
        {"role":"user","content":json.dumps(prompt)}
    ], temperature=0.3)
    data = parse_json_or_empty(out) or {}
    qs = data.get("queries") if isinstance(data.get("queries"), list) else []
    if not qs:
        return {"queries":[f"{topic} {window}"],"rationale":"fallback","expected_signals":[]}
    return {"queries":qs[:3],"rationale":data.get("rationale",""),"expected_signals":data.get("expected_signals",[])}

def decide_next(topic, depth, window, items_preview, plan_rationale):
    logging.info(f"Decide: topic='{topic}' depth={depth}")
    sys = "Output strict JSON: {\"action\":\"deepen|stop\",\"reason\":\"\",\"next_focus\":[\"...\"]}"
    prompt = {
        "topic":topic,
        "depth":depth,
        "window":window,
        "recent_preview":items_preview,
        "plan_rationale":plan_rationale,
        "instruction":"If strong, novel, credible signals exist, stop. Otherwise deepen and propose 1-3 targeted queries."
    }
    out = call_chat("openrouter/horizon-beta", [
        {"role":"system","content":sys},
        {"role":"user","content":json.dumps(prompt)}
    ], temperature=0.2)
    data = parse_json_or_empty(out) or {}
    act = data.get("action","stop")
    if act not in ["deepen","stop"]:
        act = "stop"
    nf = data.get("next_focus",[])
    if not isinstance(nf, list):
        nf = []
    return {"action":act,"reason":data.get("reason",""),"next_focus":nf[:6]}

def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z",""))
    except:
        return None

def within_window(published_at, hours=48):
    t = parse_dt(published_at)
    if not t:
        return False
    return (datetime.utcnow() - t) <= timedelta(hours=hours)

def topic_whitelist():
    return {
        "investing": ["ft.com","reuters.com","bloomberg.com","wsj.com","ec.europa.eu"],
        "technical innovations": ["nature.com","science.org","arxiv.org","spectrum.ieee.org","ieee.org","openai.com","deepmind.google","mit.edu","mittechnologyreview.com","cell.com","aaas.org","sciencenews.org"],
        "ai law": ["ec.europa.eu","whitehouse.gov","gov.uk","europa.eu","nist.gov","ft.com","reuters.com"],
        "poland business": ["pulsbiznesu.pl","bankier.pl","forsal.pl","money.pl","pap.pl","rp.pl","wyborcza.biz","parkiet.com"],
        "ai advancements": ["nature.com","science.org","arxiv.org","openai.com","deepmind.google","anthropic.com","ft.com","reuters.com","bloomberg.com","wsj.com","mittechnologyreview.com","ieee.org","spectrum.ieee.org","economist.com","nytimes.com"],
        "ai news": ["reuters.com","bloomberg.com","ft.com","wsj.com","apnews.com","openai.com","deepmind.google","ai.meta.com","microsoft.com","abc.xyz","alphabet.com","investors.nvidia.com","ir.amazon.com"],
        "big tech": ["reuters.com","bloomberg.com","ft.com","wsj.com","sec.gov","microsoft.com","alphabet.com","apple.com","ir.amazon.com","about.fb.com","investors.nvidia.com"],
        "stock market - polish": ["pap.pl","pap.pl/biznes","parkiet.com","pulsbiznesu.pl","bankier.pl","forsal.pl","money.pl","rp.pl","wyborcza.biz","nbp.pl","gov.pl","ure.gov.pl","reuters.com","bloomberg.com"],
        "best picks to buy stocks": ["reuters.com","bloomberg.com","ft.com","wsj.com","marketwatch.com","seekingalpha.com","morningstar.com","barrons.com"]
    }

def filter_items(items, topic, window_hours=48):
    wl = topic_whitelist().get(topic.lower(), [])
    kept, dropped_old, dropped_src = [], [], []
    for i in items:
        src = (i.get("source") or hostname(i["url"])).lower()
        if wl and not any(d in src for d in wl):
            dropped_src.append(i)
            continue
        if not within_window(i.get("published_at"), hours=window_hours):
            dropped_old.append(i)
            continue
        kept.append(i)
    return kept, dropped_old, dropped_src

# Expanded source tiers
HIGH_TIER = set([
    "ft.com","reuters.com","bloomberg.com","wsj.com","apnews.com",
    "nytimes.com","theatlantic.com","economist.com",
    "nature.com","science.org","cell.com","aaas.org","sciencenews.org",
    "ieee.org","spectrum.ieee.org","acm.org",
    "mittechnologyreview.com","mit.edu",
    "openai.com","deepmind.google","anthropic.com","ai.googleblog.com","ai.meta.com","microsoft.com",
    "investors.nvidia.com","ir.amazon.com","abc.xyz","alphabet.com","apple.com","about.fb.com","amd.com","intel.com","tsmc.com",
    "sec.gov","ec.europa.eu","europa.eu","whitehouse.gov","nist.gov","gov.uk","parliament.uk","oecd.org",
    "pap.pl","parkiet.com","pulsbiznesu.pl","bankier.pl","forsal.pl","money.pl","rp.pl","wyborcza.biz","nbp.pl","ure.gov.pl"
])

MEDIUM_TIER = set([
    "axios.com","theinformation.com","semianalysis.com","wired.com","arstechnica.com","techcrunch.com",
    "theverge.com","cnbc.com","finance.yahoo.com","fortune.com","forbes.com","barrons.com","marketwatch.com",
    "quantamagazine.org","scientificamerican.com","venturebeat.com","zdnet.com","tomshardware.com","anandtech.com",
    "businesswire.com","prnewswire.com","seekingalpha.com","morningstar.com","moneyweek.com","kiplinger.com"
])

def source_tier(src):
    s = src.lower()
    if any(d in s for d in HIGH_TIER): return "high"
    if any(d in s for d in MEDIUM_TIER): return "medium"
    if "github.com" in s or "github" in s or "repositorystats" in s:
        return "low"
    return "low"

NOVELTY_TERMS = ["introducing","launch","launches","unveils","announces","announcement","benchmark","sota","state-of-the-art","general availability","released","open-sourced","release notes","breakthrough"]
RESEARCH_TERMS = ["paper","arxiv","preprint","nature","science","neurips","iclr","icml","jmlr","openreview","dataset","model card","system card"]

# Big-news recognizer terms
BIG_NEWS_TERMS = [
    "raises","funding","round","valuation","acquires","acquisition","merger","merges","m&a",
    "files 8-k","8-k","guidance","outlook","profit warning","buyback","dividend",
    "capex","capital expenditures","capital spending","infrastructure","data center","hyperscale",
    "regulator","regulatory","approval","ban","investigation","lawsuit","settlement","fine",
    "launch","introducing","release","general availability","ga","price cut","pricing"
]

def novelty_w(title, snippet):
    txt = f"{title} {snippet}".lower()
    score = 0.0
    if any(t in txt for t in NOVELTY_TERMS): score += 0.2
    if any(t in txt for t in RESEARCH_TERMS): score += 0.15
    return score

def big_news_w(title, snippet):
    txt = f"{title} {snippet}".lower()
    return 0.25 if any(t in txt for t in BIG_NEWS_TERMS) else 0.0

def reason_tags(item):
    tags = []
    # Triage age tag (7 days for triage)
    if not within_window(item.get("published_at"), hours=7*24):
        tags.append("old")
    # Hype tag for low/medium with novelty
    if item.get("source_quality") in ("low","medium") and novelty_w(item.get("title",""), item.get("snippet","")) > 0:
        tags.append("hype")
    if item.get("source_quality") == "high":
        tags.append("primary")
    # Uncertainty tag for weak sources or sensational claims
    title_snip = f"{item.get('title','')} {item.get('snippet','')}".lower()
    if item.get("source_quality") == "low" or "arc-agi" in title_snip or "surpasses human" in title_snip:
        tags.append("uncertain")
    # Evergreen tag for stock-pick listicles (handled later but tag here)
    if any(k in title_snip for k in ["best stocks","best picks","share tips","top picks","stocks to buy"]):
        tags.append("evergreen")
    return tags

def normalize_results(results, topic, depth, run_id):
    out = []
    for r in results:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        nurl = normalize_url(url)
        src = (r.get("source") or hostname(nurl)).lower()
        title = (r.get("title") or src).strip()
        pub = r.get("published_at","")
        snippet = (r.get("snippet") or "").strip()
        item_id = f"{run_id}-{short_hash(nurl)}"
        item = {
            "item_id": item_id,
            "title": title,
            "url": nurl,
            "source": src,
            "source_quality": source_tier(src),
            "published_at": pub,
            "snippet": snippet,
            "topic": topic,
            "depth": depth
        }
        item["tags"] = reason_tags(item)
        out.append(item)
    return out

def dedupe_by_url(items):
    seen = set()
    out = []
    for i in items:
        key = normalize_url(i["url"])
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    return out

# GitHub high-org release allowance
HIGH_ORGS = ["microsoft","openai","google","deepmind","anthropic","nvidia","meta","facebook","apple","amazon","aws","huggingface","alphabet"]
RELEASE_TERMS = ["release","v","tag","changelog","notes","launch","ga","general availability","releases/","release:"]

def allow_github_repo(item):
    s = (item.get("source") or "") + " " + (item.get("title") or "") + " " + (item.get("snippet") or "") + " " + item.get("url","")
    s = s.lower()
    if "github" not in s:
        return True
    org_hit = any(org in s for org in HIGH_ORGS)
    rel_hit = any(term in s for term in RELEASE_TERMS)
    return org_hit and rel_hit

def big_news_boost(i):
    b = big_news_w(i.get("title",""), i.get("snippet",""))
    if b <= 0:
        return 0.0
    # Base boost from terms
    boost = b
    # Very fresh
    if within_window(i.get("published_at"), hours=48):
        boost += 0.12
    # High-tier
    if i.get("source_quality") == "high":
        boost += 0.08
    return boost

def is_newsletter_like(item):
    s = (item.get("source") or "") + " " + (item.get("url") or "") + " " + (item.get("title") or "")
    s = s.lower()
    return ("newsletter" in s) or ("axios.com/newsletters" in s) or ("substack.com" in s)

def rank_basic(items):
    domain_w = {
        "reuters.com": 1.0, "bloomberg.com": 1.0, "ft.com": 0.9, "wsj.com": 0.9, "apnews.com": 0.7,
        "nytimes.com": 0.8, "theatlantic.com": 0.6, "economist.com": 0.7,
        "nature.com": 0.9, "science.org": 0.9, "cell.com": 0.7, "aaas.org": 0.5, "sciencenews.org": 0.5,
        "ieee.org": 0.6, "spectrum.ieee.org": 0.6, "acm.org": 0.6,
        "mittechnologyreview.com": 0.7, "mit.edu": 0.6,
        "openai.com": 0.5, "deepmind.google": 0.5, "anthropic.com": 0.5, "ai.googleblog.com": 0.4, "ai.meta.com": 0.4, "microsoft.com": 0.5,
        "investors.nvidia.com": 0.6, "ir.amazon.com": 0.6, "alphabet.com": 0.6, "abc.xyz": 0.6, "apple.com": 0.5, "about.fb.com": 0.5, "amd.com": 0.5, "intel.com": 0.5, "tsmc.com": 0.5,
        "sec.gov": 0.7, "ec.europa.eu": 0.7, "europa.eu": 0.6, "whitehouse.gov": 0.7, "nist.gov": 0.6, "gov.uk": 0.6, "parliament.uk": 0.6, "oecd.org": 0.5,
        "pap.pl": 0.6, "parkiet.com": 0.6, "pulsbiznesu.pl": 0.6, "bankier.pl": 0.5, "forsal.pl": 0.5, "money.pl": 0.5, "rp.pl": 0.5, "wyborcza.biz": 0.5, "nbp.pl": 0.6, "ure.gov.pl": 0.6
    }
    def recency_w(ts):
        t = parse_dt(ts)
        if not t:
            return 0.0
        age_h = (datetime.utcnow() - t).total_seconds()/3600
        if age_h <= 48: return 0.8
        if age_h <= 24*7: return 0.2
        return 0.0

    def score(i):
        d = (i.get("source") or hostname(i["url"])).lower()
        s = 1.0 + max([v for k,v in domain_w.items() if k in d] or [0.0])
        s += recency_w(i.get("published_at"))

        # Prefer confirmed high-tier items
        if i.get("depth",1) >= 2 and i.get("source_quality") == "high":
            s += 0.25

        # Big news boost (slightly stronger)
        s += big_news_boost(i) + 0.05  # extra nudge for strong signals

        # novelty
        nov = novelty_w(i.get("title",""), i.get("snippet",""))
        if i.get("source_quality") == "low":
            nov *= 0.4
            s -= 0.15
        s += nov

        # Uncertain penalty stronger
        if "uncertain" in (i.get("tags") or []):
            s -= 0.25

        # Newsletter penalty: larger if depth 1
        if is_newsletter_like(i):
            s -= 0.2 if i.get("depth",1) == 1 else 0.1

        # De-emphasize generic GitHub trending
        src_str = (i.get("source") or "") + " " + (i.get("title") or "")
        if ("github" in src_str.lower()) and i.get("source_quality") == "low":
            s -= 0.3
            if not allow_github_repo(i):
                s -= 0.5

        # Evergreen penalty for listicles
        if "evergreen" in (i.get("tags") or []):
            s -= 0.35  # slightly stronger

        return s

    # Stable sort by score, with deterministic tie-break preferring confirmed
    ranked = sorted(items, key=lambda x: (score(x), x.get("depth",1) >= 2, x.get("source_quality") == "high"), reverse=True)
    return ranked

def broad_queries(topic, window):
    return [
        f"{topic} latest news past 48 hours",
        f"{topic} announcement OR launch OR unveils past 3 days",
        f"{topic} breakthrough OR benchmark OR SOTA past week",
        f"{topic} report OR analysis OR deep dive past week",
    ]

DEFAULT_CONFIRM_PACK = [
    # Tier-1 finance/earnings/AI spend
    "site:reuters.com OR site:bloomberg.com AI capex OR AI spending OR earnings past 48 hours",
    # Investor pages and transcripts
    "site:microsoft.com/en-us/investor OR site:investors.nvidia.com OR site:ir.amazon.com OR site:alphabet.com OR site:abc.xyz past 48 hours transcript OR remarks AI OR capex",
    # Major labs releases
    "site:openai.com/blog OR site:deepmind.google OR site:ai.meta.com/blog past 7 days (introducing OR release OR model OR benchmark)"
]

POLAND_CONFIRM_PACK = [
    "site:pap.pl OR site:pap.pl/biznes OR site:parkiet.com OR site:pulsbiznesu.pl OR site:bankier.pl OR site:forsal.pl OR site:money.pl last 48 hours WIG20 OR Orlen OR PGE OR PKO OR Pekao OR Santander Polska OR KGHM",
    "site:nbp.pl last 48 hours komunikat OR konferencja OR oświadczenie",
    "site:gov.pl/web/klimat OR site:ure.gov.pl last 48 hours"
]

def topic_specific_hygiene(items, topic, drop_excess_evergreen=True, evergreen_cap=1):
    """
    Apply topic-specific hygiene at stage filtering time.
    - For 'best picks' style topics: tag evergreen listicles and require freshness or catalyst.
    - Optionally drop excess evergreen items beyond a small cap before ranking/merge.
    """
    tl = topic.lower()
    out = []
    evergreen_indices = []
    for idx, it in enumerate(items):
        title_snip = f"{it.get('title','')} {it.get('snippet','')}".lower()
        # Identify best-picks style topics
        is_best_picks_topic = ("best picks" in tl) or ("stocks" in tl and ("best" in tl or "picks" in tl))
        if is_best_picks_topic:
            fresh72 = within_window(it.get("published_at"), hours=72)
            catalyst = big_news_w(it.get("title",""), it.get("snippet","")) > 0
            # Tag evergreen if not fresh and no catalyst
            if not fresh72 and not catalyst:
                if "evergreen" not in it["tags"]:
                    it["tags"].append("evergreen")
            # Collect index if evergreen to optionally prune
            if "evergreen" in it["tags"]:
                evergreen_indices.append(idx)
        out.append(it)

    # Optionally drop excess evergreen items before merge
    if drop_excess_evergreen and evergreen_indices:
        # Keep only the earliest 'evergreen_cap' evergreen items, drop the rest
        to_keep = set(evergreen_indices[:max(0, evergreen_cap)])
        pruned = []
        for idx, it in enumerate(out):
            if "evergreen" in it["tags"] and idx not in to_keep:
                # Drop
                continue
            pruned.append(it)
        return pruned
    return out

def cap_newsletters(items, cap=1):
    out = []
    count = 0
    for it in items:
        if is_newsletter_like(it):
            if count >= cap:
                continue
            count += 1
        out.append(it)
    return out

def orchestrate(topics, window, max_depth, limit_total):
    logging.info(f"Orchestrate topics={topics} window='{window}' max_depth={max_depth} limit={limit_total}")
    run_id = time.strftime("%Y-%m-%d")
    log = []
    collected = []
    # Per-topic cap so one topic cannot exhaust budget
    topic_limit = max(3, limit_total // max(1, len(topics)))
    global_budget = limit_total

    for topic in topics:
        depth = 1
        topic_id = short_hash(topic + run_id)
        topic_items = []

        while depth <= max_depth:
            # Depth 1: broad scout; Depth >=2: planned queries
            if depth == 1:
                queries = broad_queries(topic, window)
                plan = {"queries": queries, "rationale": "broad-first", "expected_signals":[]}
            else:
                plan = plan_queries(topic, window, depth)
                queries = plan["queries"]

            # Execute searches
            step_raw = []
            for q in queries:
                step_raw.extend(call_search(q, limit=8) or [])
            step_items = normalize_results(step_raw, topic, depth, run_id)
            step_items = dedupe_by_url(step_items)

            # Depth 1: relaxed freshness (<=7 days), no whitelist
            if depth == 1:
                relaxed, old1 = [], []
                for it in step_items:
                    # Filter out extremely old
                    if within_window(it.get("published_at"), hours=7*24):
                        # Permit GitHub but will be de-emphasized later
                        relaxed.append(it)
                    else:
                        old1.append(it)
                filtered, dropped_old, dropped_src = relaxed, old1, []
                # Topic-specific hygiene
                filtered = topic_specific_hygiene(filtered, topic, drop_excess_evergreen=True, evergreen_cap=1)
                filtered = cap_newsletters(filtered)
            else:
                # Depth >=2: strict filter (whitelist + 48h)
                filtered, dropped_old, dropped_src = filter_items(step_items, topic, window_hours=48)

            preview = [{"title": i["title"], "source": i["source"], "url": i["url"]} for i in filtered[:6]]
            decision = decide_next(topic, depth, window, preview, plan["rationale"])
            logging.info(f"Topic='{topic}' depth={depth} kept={len(filtered)} drop_old={len(dropped_old)} drop_src={len(dropped_src)} decision={decision['action']}")

            log.append({
                "topic": topic,
                "topic_id": topic_id,
                "depth": depth,
                "plan": plan,
                "decision": decision,
                "found_raw": len(step_raw),
                "kept": len(filtered),
                "dropped_old": len(dropped_old),
                "dropped_src": len(dropped_src),
                "ts": now_iso()
            })

            # Merge filtered into topic_items, respecting per-topic cap
            for it in filtered:
                if len(topic_items) >= topic_limit:
                    break
                topic_items.append(it)

            # If asked to deepen and we still have slack for this topic, run confirm pass
            if decision["action"] == "deepen" and depth < max_depth and len(topic_items) < topic_limit:
                next_qs = decision.get("next_focus", []) or []
                # Merge with default confirm pack
                for q in DEFAULT_CONFIRM_PACK:
                    if q not in next_qs:
                        next_qs.append(q)
                # Add Poland confirm pack if topic is Polish market
                if "polish" in topic.lower():
                    for q in POLAND_CONFIRM_PACK:
                        if q not in next_qs:
                            next_qs.append(q)

                raw2 = []
                for q in next_qs:
                    raw2.extend(call_search(q, limit=8) or [])
                deeper = normalize_results(raw2, topic, depth+1, run_id)
                deeper = dedupe_by_url(deeper)
                deeper_f, d_old2, d_src2 = filter_items(deeper, topic, window_hours=48)
                logging.info(f"Deeper topic='{topic}' depth={depth+1} kept={len(deeper_f)} drop_old={len(d_old2)} drop_src={len(d_src2)}")

                # Prefer deeper items (confirmed): add and if exceed cap, keep best
                for it in deeper_f:
                    if len(topic_items) < topic_limit:
                        topic_items.append(it)
                    else:
                        pool = rank_basic(topic_items + [it])
                        topic_items = pool[:topic_limit]
                depth += 1
                # Continue planning if we still have room
                if len(topic_items) < topic_limit:
                    continue
                else:
                    break

            # Stop if we reached per-topic cap or decision is stop
            if len(topic_items) >= topic_limit or decision["action"] != "deepen":
                break
            # Otherwise continue inner while

        # Merge per-topic items to global collected
        collected.extend(topic_items)
        logging.info(f"Completed topic='{topic}' with {len(topic_items)} items (cap {topic_limit}).")

        # We keep processing all topics; do not stop early unless you prefer:
        # if len(collected) >= global_budget:
        #     logging.info("Global budget reached; stopping further topics.")
        #     break

    # Finalize
    collected = dedupe_by_url(collected)
    ranked = rank_basic(collected)[:limit_total]
    return {
        "run_id": run_id,
        "items": ranked,
        "log": log,
        "summary": {
            "topics": topics,
            "window": window,
            "max_depth": max_depth,
            "count": len(ranked),
            "topic_limit": topic_limit
        }
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topics", type=str, required=True, help="Comma-separated list of topics")
    parser.add_argument("--window", type=str, default="last 48 hours")
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    topics = [t.strip() for t in args.topics.split(",") if t.strip()]

    res = orchestrate(topics, args.window, args.max_depth, args.limit)

    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_filename = os.path.join(output_dir, f"{timestamp}_result.json")

    with open(output_filename, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    logging.info(f"Final output saved to {output_filename}")
    print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()