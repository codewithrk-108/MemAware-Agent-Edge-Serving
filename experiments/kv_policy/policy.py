"""Stay / swap / discard decision for a task's KV cache when its request ends in a tool call.

Cost models come from swap_vs_stay_vs_recompute/params.json (fitted under FIFO N4 on the 50-task requests):
  swap out  T_out = b + a_p*P + a_g*G              (policy_models.swap_out_split)
  swap in   T_in  = b + a_p*P + a_g*G              (policy_models.swap_in_split)
  recompute T_rc  = c + d*L + f_dec*L*n_dec + f_pre*L*n_pre + g*dec_ctx   (policy_models.recompute_contention)
P = resident tokens that were prefilled (contiguous in the pool), G = resident tokens that were generated one by one
(scattered between other requests' cells), L = P + G. Recompute predictions are floored at the no-contention
reference (recompute_solo_reference), and every prediction at 0, so the linear models cannot go negative.

Decision for a task holding L = P + G cells at a tool call:
  stay     keep the cache in its slot. Chosen only if all hold:
             - a spare slot exists (the server has more slots than the N running requests),
             - expected idle time E <= stay_max_idle_s, where E = estimated tool time + estimated queue wait before
               the task's next request is dispatched (both are running averages of what the run has observed;
               the trace's recorded tool delays are never read),
             - after keeping L, the pool still has room for the largest of the next N queued requests:
               capacity - reserved_others - L >= headroom + margin.
  otherwise evict, by whichever is predicted cheaper for the task:
  swap     T_out + T_in < T_rc and the host swap budget has room for L tokens
  discard  everything else (erase now, recompute on resume)
"""

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PARAMS = ROOT / "swap_vs_stay_vs_recompute" / "params.json"


class CostModel:
    def __init__(self, path: Path = PARAMS):
        p = json.loads(Path(path).read_text())
        m = p["policy_models"]
        self.out = m["swap_out_split"]["params"]
        self.inn = m["swap_in_split"]["params"]
        self.rc = m["recompute_contention"]["params"]
        self.solo = p["recompute_solo_reference"]["params"]
        self.source = str(path)

    def t_out(self, P: float, G: float) -> float:
        return max(0.0, self.out["b"] + self.out["a_p"] * P + self.out["a_g"] * G)

    def t_in(self, P: float, G: float) -> float:
        return max(0.0, self.inn["b"] + self.inn["a_p"] * P + self.inn["a_g"] * G)

    def t_rc_solo(self, L: float) -> float:
        return max(0.0, self.solo["c"] + self.solo["d"] * L + self.solo["e"] * L * L)

    def t_rc(self, L: float, n_dec: float, dec_ctx: float, n_pre: float = 0.0) -> float:
        r = self.rc
        pred = r["c"] + r["d"] * L + r["f_dec"] * L * n_dec + r["f_pre"] * L * n_pre + r["g"] * dec_ctx
        return max(pred, self.t_rc_solo(L))


@dataclass
class PolicyConfig:
    capacity: int = 32768
    stay_max_idle_s: float = 30.0
    margin_tokens: int = 512
    host_budget_tokens: int = 50_000      # ~5.5 GiB of /dev/shm at 112 KiB per token
    tool_prior_s: float = 1.3             # starting estimate of a tool wait (500-task trace mean)
    service_prior_s: float = 7.0          # starting estimate of one request's service time
    ema: float = 0.05                     # weight of each new observation in the running averages


class Policy:
    def __init__(self, cost: CostModel, cfg: PolicyConfig):
        self.cost, self.cfg = cost, cfg
        self.tool_mean, self.service_mean = cfg.tool_prior_s, cfg.service_prior_s

    def observe_tool(self, seconds: float):
        self.tool_mean += self.cfg.ema * (seconds - self.tool_mean)

    def observe_service(self, seconds: float):
        self.service_mean += self.cfg.ema * (seconds - self.service_mean)

    def expected_idle(self, queue_ahead: int, workers: int) -> float:
        return self.tool_mean + queue_ahead * self.service_mean / workers

    def decide(self, P: int, G: int, n_dec: int, dec_ctx: float, reserved_others: int, headroom: int,
               queue_ahead: int, workers: int, host_free_tokens: int, spare_slot: bool) -> dict:
        L = P + G
        t_out, t_in = self.cost.t_out(P, G), self.cost.t_in(P, G)
        t_rc = self.cost.t_rc(L, n_dec, dec_ctx)
        E = self.expected_idle(queue_ahead, workers)
        room = self.cfg.capacity - reserved_others - L
        stay_ok = spare_slot and E <= self.cfg.stay_max_idle_s and room >= headroom + self.cfg.margin_tokens
        swap_ok = t_out + t_in < t_rc and host_free_tokens >= L
        action = "stay" if stay_ok else ("swap" if swap_ok else "discard")
        return {"action": action, "P": P, "G": G, "L": L, "pred_t_out_ms": t_out, "pred_t_in_ms": t_in,
                "pred_t_swap_ms": t_out + t_in, "pred_t_rc_ms": t_rc, "expected_idle_s": E,
                "room_after_stay": room, "headroom": headroom, "spare_slot": spare_slot,
                "n_dec": n_dec, "dec_ctx": dec_ctx, "queue_ahead": queue_ahead,
                "tool_mean_s": self.tool_mean, "service_mean_s": self.service_mean}
