"""Deterministic attack-round TP distributions used by Time to WS.

The legacy calculation divided required TP by an average TP return.  That is
fast, but lets fractional hits become usable immediately.  This module keeps
the combat engine deterministic while modelling complete attack rounds and
the chance that a round returns no TP.
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from math import comb

import numpy as np

from engine.numba_compat import njit


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _merge(target, source):
    for key, value in source.items():
        target[key] += value


def _proc_attempts(qa, ta, da, oa_counts):
    """Return the mutually-exclusive extra-swing distribution for one hand."""
    remaining = 1.0
    result = defaultdict(float)
    for chance, count in ((_clamp(qa), 3), (_clamp(ta), 2), (_clamp(da), 1), *oa_counts):
        chance = _clamp(chance)
        result[int(count)] += remaining * chance
        remaining *= 1.0 - chance
    result[0] += remaining
    return dict(result)


def _add_weapon(states, enabled, extras, field):
    if not enabled:
        return states
    result = defaultdict(float)
    for state, probability in states.items():
        for extra, proc_probability in extras.items():
            values = list(state)
            available = max(0, 8 - sum(values))
            values[field] += min(available, 1 + int(extra))
            result[tuple(values)] += probability * proc_probability
    return dict(result)


def _add_optional(states, chance, field, count=1):
    chance = _clamp(chance)
    result = defaultdict(float)
    for state, probability in states.items():
        available = max(0, 8 - sum(state))
        result[state] += probability * (1.0 - chance)
        if available:
            values = list(state)
            values[field] += min(available, int(count))
            result[tuple(values)] += probability * chance
        else:
            result[state] += probability * chance
    return dict(result)


def _add_zanshin(states, chance, oa2):
    chance = _clamp(chance)
    oa2 = _clamp(oa2)
    result = defaultdict(float)
    for state, probability in states.items():
        available = max(0, 8 - sum(state))
        result[state] += probability * (1.0 - chance)
        if not available:
            result[state] += probability * chance
            continue
        one = list(state)
        one[2] += 1
        result[tuple(one)] += probability * chance * (1.0 - oa2)
        two = list(state)
        two[2] += min(available, 2)
        result[tuple(two)] += probability * chance * oa2
    return dict(result)


def _binomial(attempts, hit_rate):
    hit_rate = _clamp(hit_rate)
    return {
        hits: comb(attempts, hits) * hit_rate ** hits * (1.0 - hit_rate) ** (attempts - hits)
        for hits in range(int(attempts) + 1)
    }


@njit(cache=True)
def _expected_rounds_kernel(tp_values, probabilities, remaining):
    """Evaluate the TP recurrence in compiled numeric code."""
    zero_probability = 0.0
    positive_count = 0
    for index in range(len(tp_values)):
        if tp_values[index] <= 0:
            zero_probability += probabilities[index]
        elif probabilities[index] > 0:
            positive_count += 1
    if zero_probability >= 1.0 - 1e-12:
        return np.inf

    values = np.zeros(remaining + 1, dtype=np.float64)
    positive_tp = np.empty(positive_count, dtype=np.int64)
    positive_probability = np.empty(positive_count, dtype=np.float64)
    position = 0
    for index in range(len(tp_values)):
        if tp_values[index] > 0 and probabilities[index] > 0:
            positive_tp[position] = tp_values[index]
            positive_probability[position] = probabilities[index]
            position += 1

    for needed in range(1, remaining + 1):
        total = 1.0
        for index in range(positive_count):
            total += positive_probability[index] * values[max(0, needed - positive_tp[index])]
        values[needed] = total / (1.0 - zero_probability)
    return values[remaining]


@lru_cache(maxsize=4096)
def _tp_distribution_cached(config):
    """Return ``((tp, probability), ...)`` for a canonical immutable config."""
    (
        dual_wield, qa, ta, da, main_oa3, main_oa2, sub_oa8, sub_oa7,
        sub_oa6, sub_oa5, sub_oa4, sub_oa3, sub_oa2, main_hit, sub_hit,
        kick_rate, daken_rate, daken_hit, zanshin_rate, zanshin_oa2,
        normal_tp, zanshin_tp, daken_tp,
    ) = config
    states = {(0, 0, 0, 0, 0): 1.0}  # main, sub, zanshin, kick, daken attempts
    main_extras = _proc_attempts(qa, ta, da, ((main_oa3, 2), (main_oa2, 1)))
    sub_extras = _proc_attempts(
        qa, ta, da,
        ((sub_oa8, 7), (sub_oa7, 6), (sub_oa6, 5), (sub_oa5, 4),
         (sub_oa4, 3), (sub_oa3, 2), (sub_oa2, 1)),
    )
    states = _add_weapon(states, True, main_extras, 0)
    states = _add_weapon(states, bool(dual_wield), sub_extras, 1)
    states = _add_zanshin(states, zanshin_rate, zanshin_oa2)
    states = _add_optional(states, kick_rate, 3)
    states = _add_optional(states, daken_rate, 4)

    result = defaultdict(float)
    for (main_attempts, sub_attempts, zanshin_attempts, kick_attempts, daken_attempts), probability in states.items():
        for main_hits, main_probability in _binomial(main_attempts, main_hit).items():
            for sub_hits, sub_probability in _binomial(sub_attempts, sub_hit).items():
                for zanshin_hits, zanshin_probability in _binomial(zanshin_attempts, main_hit).items():
                    for kick_hits, kick_probability in _binomial(kick_attempts, main_hit).items():
                        for daken_hits, daken_probability in _binomial(daken_attempts, daken_hit).items():
                            tp = (
                                (main_hits + sub_hits + kick_hits) * normal_tp
                                + zanshin_hits * zanshin_tp
                                + daken_hits * daken_tp
                            )
                            result[int(tp)] += (
                                probability * main_probability * sub_probability
                                * zanshin_probability * kick_probability * daken_probability
                            )
    total = sum(result.values())
    if total <= 0:
        return ((0, 1.0),)
    return tuple(sorted((tp, probability / total) for tp, probability in result.items()))


def tp_distribution(**values):
    """Return a cached discrete TP distribution for one attack round."""
    names = (
        "dual_wield", "qa", "ta", "da", "main_oa3", "main_oa2", "sub_oa8", "sub_oa7",
        "sub_oa6", "sub_oa5", "sub_oa4", "sub_oa3", "sub_oa2", "main_hit", "sub_hit",
        "kick_rate", "daken_rate", "daken_hit", "zanshin_rate", "zanshin_oa2",
        "normal_tp", "zanshin_tp", "daken_tp",
    )
    config = tuple(
        int(bool(values.get(name))) if name == "dual_wield"
        else int(values.get(name, 0)) if name.endswith("_tp")
        else round(float(values.get(name, 0.0)), 8)
        for name in names
    )
    return _tp_distribution_cached(config)


@lru_cache(maxsize=32768)
def _expected_rounds_cached(distribution, remaining):
    """Cache the compiled recurrence for repeated gear/TP configurations."""
    if remaining <= 0:
        return 0.0
    tp_values = np.asarray([int(tp) for tp, _ in distribution], dtype=np.int64)
    probabilities = np.asarray([float(probability) for _, probability in distribution], dtype=np.float64)
    return float(_expected_rounds_kernel(tp_values, probabilities, int(remaining)))


def expected_rounds(distribution, starting_tp, target_tp):
    """Expected complete rounds to reach target TP for an iid TP distribution."""
    remaining = max(0, int(round(float(target_tp) - float(starting_tp))))
    if remaining <= 0:
        return 0.0
    return _expected_rounds_cached(tuple(distribution), remaining)


def time_to_ws_breakdown(*, starting_tp, target_tp, round_seconds, regain_per_round=0.0, **config):
    """Calculate expected Time to WS and a compact inspectable breakdown.

    Regain remains an expected value, matching the legacy model: server tick
    phase cannot be inferred from a static gear set.  Iterate that expected
    gain against the exact hit-round result until the two agree.
    """
    distribution = tp_distribution(**config)
    rounds = expected_rounds(distribution, starting_tp, target_tp)
    for _ in range(8):
        adjusted_target = max(float(starting_tp), float(target_tp) - rounds * regain_per_round)
        next_rounds = expected_rounds(distribution, starting_tp, adjusted_target)
        if abs(next_rounds - rounds) < 1e-6:
            rounds = next_rounds
            break
        rounds = next_rounds
    average_tp = sum(tp * probability for tp, probability in distribution) + regain_per_round
    return {
        "expected_rounds": rounds,
        "time_to_ws": rounds * round_seconds if rounds != float("inf") else float("inf"),
        "average_tp_per_round": average_tp,
        "round_seconds": round_seconds,
        "regain_per_round": regain_per_round,
        "outcomes": distribution,
    }
