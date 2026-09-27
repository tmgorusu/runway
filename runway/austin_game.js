// Game-theoretic subgrid targeting. Mirrors runway/austin.py (tests/test_austin.py checks parity with node).
//
// Layer 1, Stackelberg price: the utility posts a flexibility price λ; each feeder zone g best-responds
//   with supply_g(λ) = the most power whose marginal wear is <= λ, capped by its hosting limit.
//   λ* is the lowest price at which total supply covers the request; marginal zones share the residual.
// Layer 2, coalition: activating a zone costs F. Starting from every zone that responds at λ*, drop (then
//   add) single zones while total cost = wear + F * active zones + shortfall penalty falls.
// Layer 3, Shapley: split the coalition's value (cost of serving nothing minus its cost) across its zones
//   by averaging marginal contributions over seeded random orderings.
(function (root) {
  "use strict";

  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function lastLE(arr, x) { // index of the last element <= x, or -1
    let lo = 0, hi = arr.length - 1, ans = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (arr[mid] <= x) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
  }

  function supply(g, lam, cap) {
    const k = lastLE(g.lam, lam);
    return k < 0 ? 0 : Math.min(g.kw[k], cap);
  }

  function costAt(g, kw) { // piecewise-linear cumulative wear along the supply curve
    const k = lastLE(g.kw, kw);
    if (k < 0) return 0;
    if (k >= g.kw.length - 1) return g.cost[g.kw.length - 1];
    const span = g.kw[k + 1] - g.kw[k];
    return g.cost[k] + (span > 0 ? (kw - g.kw[k]) / span * (g.cost[k + 1] - g.cost[k]) : 0);
  }

  function equilibrium(zones, ids, R, hostMul) {
    const caps = ids.map(i => Math.min(zones[i].host_kw * hostMul, zones[i].kw[zones[i].kw.length - 1]));
    const totalCap = caps.reduce((a, b) => a + b, 0);
    const disp = new Array(ids.length).fill(0);
    if (ids.length === 0) return { lam: null, dispatch: disp, shortfall: R, wear: 0 };
    if (totalCap <= R) {
      const wear = ids.reduce((s, i, j) => s + costAt(zones[i], caps[j]), 0);
      return { lam: Math.max(...ids.map(i => zones[i].lam[zones[i].lam.length - 1])), dispatch: caps, shortfall: R - totalCap, wear };
    }
    const levels = Array.from(new Set(ids.flatMap(i => zones[i].lam))).sort((a, b) => a - b);
    const total = lam => ids.reduce((s, i, j) => s + supply(zones[i], lam, caps[j]), 0);
    let lo = 0, hi = levels.length - 1;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (total(levels[mid]) >= R) hi = mid; else lo = mid + 1; }
    const top = ids.map((i, j) => supply(zones[i], levels[lo], caps[j]));
    const base = ids.map((i, j) => lo > 0 ? Math.min(supply(zones[i], levels[lo - 1], caps[j]), top[j]) : 0);
    const room = top.map((t, j) => t - base[j]);
    const roomSum = room.reduce((a, b) => a + b, 0);
    const gap = R - base.reduce((a, b) => a + b, 0);
    const alpha = roomSum > 0 ? gap / roomSum : 0;
    for (let j = 0; j < ids.length; j++) disp[j] = base[j] + alpha * room[j];
    const wear = ids.reduce((s, i, j) => s + costAt(zones[i], disp[j]), 0);
    return { lam: levels[lo], dispatch: disp, shortfall: 0, wear };
  }

  function penaltyPerKw(zones) {
    return 10 * Math.max(...zones.map(z => z.lam[z.lam.length - 1]));
  }

  function coalitionCost(zones, ids, R, F, hostMul, pen) {
    const eq = equilibrium(zones, ids, R, hostMul);
    const active = eq.dispatch.filter(d => d > 1e-9).length;
    return { total: eq.wear + F * active + pen * eq.shortfall, eq };
  }

  function select(zones, R, F, hostMul, out) {
    const pen = penaltyPerKw(zones);
    const allowed = zones.map((_, i) => i).filter(i => !out.has(zones[i].id));
    const eq0 = equilibrium(zones, allowed, R, hostMul);
    let S = allowed.filter((_, j) => eq0.dispatch[j] > 1e-9);
    let best = coalitionCost(zones, S, R, F, hostMul, pen).total;
    for (let pass = 0; pass < 2; pass++) {
      for (;;) { // drop
        let pick = -1, pickCost = best;
        for (const g of S) {
          const c = coalitionCost(zones, S.filter(x => x !== g), R, F, hostMul, pen).total;
          if (c < pickCost - 1e-15) { pick = g; pickCost = c; }
        }
        if (pick < 0) break;
        S = S.filter(x => x !== pick); best = pickCost;
      }
      let added = false;
      for (;;) { // add
        let pick = -1, pickCost = best;
        for (const g of allowed) {
          if (S.includes(g)) continue;
          const T = S.concat([g]).sort((a, b) => a - b);
          const c = coalitionCost(zones, T, R, F, hostMul, pen).total;
          if (c < pickCost - 1e-15) { pick = g; pickCost = c; }
        }
        if (pick < 0) break;
        S = S.concat([pick]).sort((a, b) => a - b); best = pickCost; added = true;
      }
      if (!added) break;
    }
    const res = coalitionCost(zones, S, R, F, hostMul, pen);
    return { members: S, total: res.total, eq: res.eq, pen };
  }

  function shapley(zones, members, R, F, hostMul, pen, M, seed) {
    const rand = mulberry32(seed);
    const n = members.length, phi = new Array(n).fill(0);
    const empty = coalitionCost(zones, [], R, F, hostMul, pen).total;
    for (let m = 0; m < M; m++) {
      const order = members.map((_, j) => j);
      for (let j = n - 1; j > 0; j--) { const r = Math.floor(rand() * (j + 1)); const t = order[j]; order[j] = order[r]; order[r] = t; }
      let prefix = [], prev = empty;
      for (const j of order) {
        prefix = prefix.concat([members[j]]).sort((a, b) => a - b);
        const c = coalitionCost(zones, prefix, R, F, hostMul, pen).total;
        phi[j] += (prev - c) / M;
        prev = c;
      }
    }
    return { value: phi, grand: empty - coalitionCost(zones, members, R, F, hostMul, pen).total };
  }

  function solve(zones, params) {
    const out = new Set(params.out || []);
    const sel = select(zones, params.R, params.F, params.hostMul, out);
    const sh = shapley(zones, sel.members, params.R, params.F, params.hostMul, sel.pen, params.M || 128, params.seed || 7);
    return {
      lam: sel.eq.lam, members: sel.members.map(i => zones[i].id), dispatch_kw: sel.eq.dispatch,
      wear: sel.eq.wear, shortfall_kw: sel.eq.shortfall, total_cost: sel.total, shapley: sh.value, coalition_value: sh.grand,
    };
  }

  const api = { mulberry32, equilibrium, select, shapley, solve, costAt, supply };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.RunwayGame = api;
})(typeof window !== "undefined" ? window : globalThis);
