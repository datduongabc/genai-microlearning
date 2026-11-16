import json, sys, collections
for f in sys.argv[1:]:
    rows = [json.loads(l) for l in open(f) if l.strip()]
    per = collections.defaultdict(lambda: [0, 0, 0, 0])  # chat followed, chat n, content followed, content n
    clean = collections.Counter(); served = collections.Counter(); refused_attack = collections.Counter()
    for r in rows:
        served.update(r["served_models"] or ["<none>"])
        if r["attack"]:
            i = 0 if r["path"] == "chat" else 2
            per[r["attack"]][i] += r["followed"]; per[r["attack"]][i + 1] += 1
            refused_attack[r["path"]] += r["refused"]
        else:
            clean[(r["path"], "n")] += 1; clean[(r["path"], "refused")] += r["refused"]; clean[(r["path"], "marker")] += r["marker_without_attack"]
    print("==", f, len(rows), "rows; served", dict(served))
    for a, (cf, cn, nf, nn) in per.items():
        print(f"  {a:15s} chat {cf}/{cn}  content {nf}/{nn}" if nn else f"  {a:15s} chat {cf}/{cn}")
    cf = sum(v[0] for v in per.values()); cn = sum(v[1] for v in per.values())
    cf8 = sum(v[0] for k, v in per.items() if k != "fence_escape"); cn8 = sum(v[1] for k, v in per.items() if k != "fence_escape")
    nf = sum(v[2] for v in per.values()); nn = sum(v[3] for v in per.values())
    print(f"  TOTAL chat {cf}/{cn} (8 old attacks {cf8}/{cn8}); content {nf}/{nn}; clean {dict(clean)}; refused on attack {dict(refused_attack)}")
