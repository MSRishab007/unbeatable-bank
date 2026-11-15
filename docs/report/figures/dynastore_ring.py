"""Draws figures/dynastore-ring.pdf: a consistent hashing ring with three nodes.
Eight virtual nodes per node are drawn for legibility; DynaStore uses 64."""
import hashlib, math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
NODES = {"dynastore-0": "#1f6feb", "dynastore-1": "#b35900", "dynastore-2": "#2e8b57"}
V = 8
def h(s): return int(hashlib.md5(s.encode()).hexdigest(), 16) / 2**128
ring = sorted((h(f"{n}#{i}"), n) for n in NODES for i in range(V))
key = "rl:pay:customer:42:minute"
kp = h(key)
start = next(i for i, (p, _) in enumerate(ring) if p >= kp) if kp <= ring[-1][0] else 0
pref, seen, steps = [], set(), 0
i = start
while len(pref) < 3:
    p, n = ring[i % len(ring)]
    if n not in seen: seen.add(n); pref.append((p, n))
    i += 1

def xy(p, r=1.0):
    a = math.pi / 2 - 2 * math.pi * p      # clockwise from the top
    return r * math.cos(a), r * math.sin(a)

fig, ax = plt.subplots(figsize=(6.4, 4.2))
ax.add_patch(plt.Circle((0, 0), 1, fill=False, lw=1.4, color="#3d4a5c"))
for p, n in ring:
    x, y = xy(p); ax.plot(x, y, "o", ms=7, color=NODES[n], mec="white", mew=0.8, zorder=3)
for k, (p, n) in enumerate(pref, 1):
    x, y = xy(p); ax.plot(x, y, "o", ms=13, mfc="none", mec="#1a1f29", mew=1.4, zorder=4)
    tx, ty = xy(p, 1.3); ax.text(tx, ty, f"replica {k}", ha="center", va="center", fontsize=8.5, weight="bold")
x, y = xy(kp); ax.plot(x, y, "D", ms=8, color="#c9a227", mec="#1a1f29", zorder=5)
tx, ty = (0.3, -0.5); ax.annotate("hash(key)", (x, y), (tx, ty), ha="center", fontsize=8.5,
                                   arrowprops=dict(arrowstyle="->", color="#1a1f29", lw=1))
end = pref[-1][0] if pref[-1][0] >= kp else pref[-1][0] + 1
ts = [kp + (end - kp) * t / 60 for t in range(61)]
ax.plot(*zip(*[xy(t, 1.08) for t in ts]), color="#1a1f29", lw=1.2)
ax.annotate("", xy(ts[-1], 1.08), xy(ts[-3], 1.08), arrowprops=dict(arrowstyle="->", color="#1a1f29", lw=1.2))
ax.text(-0.1, 0.22, "MD5 hash ring", ha="center", va="bottom", fontsize=10, weight="bold")
ax.text(-0.1, 0.12, f"{V} virtual nodes per node\ndrawn (64 in DynaStore)\nN = 3", ha="center", va="top", fontsize=8.5, color="#3d4a5c")
for j, (n, c) in enumerate(NODES.items()):
    ax.plot(1.75, 0.45 - j * 0.25, "o", ms=8, color=c); ax.text(1.88, 0.45 - j * 0.25, n, va="center")
ax.plot(1.75, -0.3, "D", ms=7, color="#c9a227", mec="#1a1f29"); ax.text(1.88, -0.3, "position of the key", va="center")
ax.plot(1.75, -0.55, "o", ms=11, mfc="none", mec="#1a1f29", mew=1.4); ax.text(1.88, -0.55, "preference list", va="center")
ax.set_xlim(-1.45, 3.1); ax.set_ylim(-1.35, 1.35); ax.set_aspect("equal"); ax.axis("off")
fig.savefig("dynastore-ring.pdf", bbox_inches="tight"); print(key, [n for _, n in pref])
