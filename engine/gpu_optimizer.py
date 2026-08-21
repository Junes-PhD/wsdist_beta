"""Batch numeric backend for the experimental GPU optimizer.

The existing optimizer is intentionally object-oriented: every candidate is a
Python gearset and player.  That representation cannot be sent to CUDA
efficiently.  This module is the numeric boundary for the aggressive rewrite:
the CPU-side planner emits a dense ``float64`` row per candidate, and this
module evaluates thousands of rows in one launch.

The row layout is kept explicit instead of using a structured NumPy dtype so
the same buffer can be passed directly to a CUDA kernel.  Column constants are
public for the planner and benchmark tools.
"""

from __future__ import annotations

import os

import numpy as np

try:
    from numba import cuda
except ImportError:  # pragma: no cover - numba is a project dependency
    cuda = None


ROW_COUNT = 38
BATCH_SIZE = max(256, int(os.environ.get("FFXI_GPU_BATCH_SIZE", "8192")))

MAIN_DMG = 0
FSTR_MAIN = 1
SUB_DMG = 2
FSTR_SUB = 3
WSC = 4
FTP = 5
FTP2 = 6
MAIN_HITS = 7
SUB_HITS = 8
ATTACK_MAIN = 9
ATTACK_SUB = 10
SKILL_MAIN = 11
SKILL_SUB = 12
PDL_TRAIT = 13
PDL_GEAR = 14
ENEMY_DEFENSE = 15
CRIT_RATE = 16
FIRST_CRIT_RATE = 17
STRIKING_CRIT_RATE = 18
CRIT_DAMAGE = 19
ADJUSTED_CRIT_DAMAGE = 20
WSD = 21
WS_BONUS = 22
WS_TRAIT = 23
SNEAK_ATTACK = 24
TRICK_ATTACK = 25
CLIMACTIC_FLOURISH = 26
STRIKING_FLOURISH = 27
TERNARY_FLOURISH = 28
STRIKING_ENABLED = 29
HIT_RATE_MAIN = 30
HIT_RATE_SUB = 31
MDELAY = 32
STORE_TP = 33
BASE_TP = 34
FOTIA_GORGET = 35
FOTIA_BELT = 36
CONSERVE_TP = 37
_SKILL_CODES = {
    "Axe": 0, "Club": 0, "Dagger": 0, "Sword": 0, "Katana": 0,
    "Great Katana": 1, "Hand-to-Hand": 4,
    "Great Sword": 2, "Great Axe": 2, "Polearm": 2, "Staff": 2,
    "Scythe": 3,
}


def cuda_available() -> bool:
    """Return whether a usable CUDA driver/device is available."""
    if cuda is None:
        return False
    try:
        return bool(cuda.is_available())
    except Exception:
        return False


def _validate_rows(rows) -> np.ndarray:
    values = np.asarray(rows, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != ROW_COUNT:
        raise ValueError(
            f"GPU WS rows must have shape (candidate_count, {ROW_COUNT}); "
            f"received {values.shape}."
        )
    return np.ascontiguousarray(values)


def rows_from_melee_core_args(core_args) -> np.ndarray:
    """Convert arguments of ``actions._average_ws_melee_average_core`` to rows.

    This is the handoff used by the future candidate planner. Keeping the
    conversion here prevents Python strings from crossing the CUDA boundary.
    """
    rows = np.zeros((len(core_args), ROW_COUNT), dtype=np.float64)
    for index, values in enumerate(core_args):
        if len(values) != ROW_COUNT:
            raise ValueError(f"Expected {ROW_COUNT} melee core arguments, got {len(values)}")
        rows[index, :SKILL_MAIN] = values[:SKILL_MAIN]
        rows[index, SKILL_MAIN] = _SKILL_CODES.get(values[SKILL_MAIN], 0)
        rows[index, SKILL_SUB] = _SKILL_CODES.get(values[12], 0)
        rows[index, SKILL_SUB + 1:] = values[SKILL_SUB + 1:]
    return rows


def _avg_pdif_melee_cpu(attack, skill_code, pdl_trait, pdl_gear, defense, crit_rate):
    caps = np.select(
        [skill_code == 1, skill_code == 2, skill_code == 3],
        [3.5, 3.75, 4.0],
        default=3.25,
    )
    pdif_cap = (caps + pdl_trait) * (1.0 + pdl_gear)
    ratio = attack / np.maximum(1.0, defense)
    wratio = ratio + np.minimum(1.0, crit_rate)
    upper = np.select(
        [wratio < 0.5, wratio < 0.7, wratio < 1.2, wratio < 1.5],
        [wratio + 0.5, 1.0, wratio + 0.3, 1.25 * wratio],
        default=wratio + 0.375,
    )
    lower = np.select(
        [wratio < 0.38, wratio < 1.25, wratio < 1.51, wratio < 2.44],
        [0.0, (1176.0 / 1024.0) * wratio - (448.0 / 1024.0), 1.0,
         (1176.0 / 1024.0) * wratio - (755.0 / 1024.0)],
        default=wratio - 0.375,
    )
    pdif = np.minimum(np.maximum(0.5 * (upper + lower), 0.0), pdif_cap)
    return (pdif + np.minimum(1.0, crit_rate)) * 1.025


def _avg_phys_cpu(damage, fstr, wsc, pdif, ftp, crit_rate, crit_damage,
                  wsd, ws_bonus, ws_trait, extra=0.0):
    base = ((damage + fstr + wsc) * ftp * (1.0 + wsd)
            * (1.0 + ws_bonus) * (1.0 + ws_trait) + extra)
    return np.maximum(0.0, np.trunc(base) * pdif
                      * (1.0 + np.minimum(crit_rate, 1.0)
                         * np.minimum(crit_damage, 1.0)))


def _score_cpu(rows: np.ndarray) -> np.ndarray:
    """Vectorized reference implementation used without a CUDA device."""
    main_pdif = _avg_pdif_melee_cpu(
        rows[:, ATTACK_MAIN], rows[:, SKILL_MAIN], rows[:, PDL_TRAIT],
        rows[:, PDL_GEAR], rows[:, ENEMY_DEFENSE], rows[:, CRIT_RATE],
    )
    main_damage = _avg_phys_cpu(
        rows[:, MAIN_DMG], rows[:, FSTR_MAIN], rows[:, WSC], main_pdif,
        rows[:, FTP2], rows[:, CRIT_RATE], rows[:, CRIT_DAMAGE], 0.0,
        rows[:, WS_BONUS], rows[:, WS_TRAIT],
    )
    first_pdif = _avg_pdif_melee_cpu(
        rows[:, ATTACK_MAIN], rows[:, SKILL_MAIN], rows[:, PDL_TRAIT],
        rows[:, PDL_GEAR], rows[:, ENEMY_DEFENSE], rows[:, FIRST_CRIT_RATE],
    )
    first_damage = _avg_phys_cpu(
        rows[:, MAIN_DMG], rows[:, FSTR_MAIN], rows[:, WSC], first_pdif,
        rows[:, FTP], rows[:, FIRST_CRIT_RATE], rows[:, ADJUSTED_CRIT_DAMAGE],
        rows[:, WSD], rows[:, WS_BONUS], rows[:, WS_TRAIT],
        rows[:, SNEAK_ATTACK] + rows[:, TRICK_ATTACK]
        + rows[:, CLIMACTIC_FLOURISH] + rows[:, STRIKING_FLOURISH]
        + rows[:, TERNARY_FLOURISH],
    )
    physical = rows[:, MAIN_HITS] * main_damage
    physical += (first_damage - main_damage) * rows[:, HIT_RATE_MAIN]
    if np.any(rows[:, STRIKING_ENABLED]):
        striking_pdif = _avg_pdif_melee_cpu(
            rows[:, ATTACK_MAIN], rows[:, SKILL_MAIN], rows[:, PDL_TRAIT],
            rows[:, PDL_GEAR], rows[:, ENEMY_DEFENSE], rows[:, STRIKING_CRIT_RATE],
        )
        striking_damage = _avg_phys_cpu(
            rows[:, MAIN_DMG], rows[:, FSTR_MAIN], rows[:, WSC], striking_pdif,
            rows[:, FTP2], rows[:, STRIKING_CRIT_RATE], rows[:, CRIT_DAMAGE], 0.0,
            rows[:, WS_BONUS], rows[:, WS_TRAIT],
        )
        physical += (striking_damage - main_damage) * rows[:, HIT_RATE_MAIN] * rows[:, STRIKING_ENABLED]
    if np.any(rows[:, SUB_HITS] > 0):
        sub_pdif = _avg_pdif_melee_cpu(
            rows[:, ATTACK_SUB], rows[:, SKILL_SUB], rows[:, PDL_TRAIT],
            rows[:, PDL_GEAR], rows[:, ENEMY_DEFENSE], rows[:, CRIT_RATE],
        )
        sub_damage = _avg_phys_cpu(
            rows[:, SUB_DMG], rows[:, FSTR_SUB], rows[:, WSC], sub_pdif,
            rows[:, FTP2], rows[:, CRIT_RATE], rows[:, CRIT_DAMAGE], 0.0,
            rows[:, WS_BONUS], rows[:, WS_TRAIT],
        )
        physical += sub_damage * rows[:, SUB_HITS]

    delay = np.where(rows[:, SKILL_MAIN] == 4, rows[:, MDELAY] / 2.0, rows[:, MDELAY])
    base_delay_tp = np.select(
        [delay <= 180.0, delay <= 540.0, delay <= 630.0, delay <= 720.0, delay <= 900.0],
        [61.0 + (delay - 180.0) * 63.0 / 360.0,
         61.0 + (delay - 180.0) * 88.0 / 360.0,
         149.0 + (delay - 540.0) * 20.0 / 360.0,
         154.0 + (delay - 630.0) * 28.0 / 360.0,
         161.0 + (delay - 720.0) * 24.0 / 360.0],
        default=173.0 + (delay - 900.0) * 28.0 / 360.0,
    )
    base_delay_tp = np.trunc(base_delay_tp)
    tp = (rows[:, HIT_RATE_MAIN] + rows[:, HIT_RATE_SUB]) * np.trunc(
        base_delay_tp * (1.0 + rows[:, STORE_TP])
    )
    tp += 10.0 * (1.0 + rows[:, STORE_TP]) * (
        rows[:, MAIN_HITS] + rows[:, SUB_HITS]
        - rows[:, HIT_RATE_MAIN] - rows[:, HIT_RATE_SUB]
    )
    tp += rows[:, BASE_TP] * rows[:, FOTIA_GORGET] * rows[:, FOTIA_BELT]
    tp += 95.0 * np.minimum(1.0, rows[:, CONSERVE_TP])
    return np.column_stack((physical, first_damage * rows[:, HIT_RATE_MAIN], tp))


if cuda is not None:
    @cuda.jit(device=True)
    def _device_pdif(attack, skill_code, pdl_trait, pdl_gear, defense, crit_rate):
        if skill_code == 1:
            cap = 3.5
        elif skill_code == 2:
            cap = 3.75
        elif skill_code == 3:
            cap = 4.0
        else:
            cap = 3.25
        pdif_cap = (cap + pdl_trait) * (1.0 + pdl_gear)
        wratio = attack / max(1.0, defense) + min(1.0, crit_rate)
        if wratio < 0.5:
            upper = wratio + 0.5
        elif wratio < 0.7:
            upper = 1.0
        elif wratio < 1.2:
            upper = wratio + 0.3
        elif wratio < 1.5:
            upper = 1.25 * wratio
        else:
            upper = wratio + 0.375
        if wratio < 0.38:
            lower = 0.0
        elif wratio < 1.25:
            lower = (1176.0 / 1024.0) * wratio - (448.0 / 1024.0)
        elif wratio < 1.51:
            lower = 1.0
        elif wratio < 2.44:
            lower = (1176.0 / 1024.0) * wratio - (755.0 / 1024.0)
        else:
            lower = wratio - 0.375
        return (min(max(0.5 * (upper + lower), 0.0), pdif_cap)
                + min(1.0, crit_rate)) * 1.025

    @cuda.jit(device=True)
    def _device_phys(damage, fstr, wsc, pdif, ftp, crit_rate, crit_damage,
                     wsd, ws_bonus, ws_trait, extra):
        base = ((damage + fstr + wsc) * ftp * (1.0 + wsd)
                * (1.0 + ws_bonus) * (1.0 + ws_trait) + extra)
        value = float(int(base)) * pdif * (1.0 + min(crit_rate, 1.0) * min(crit_damage, 1.0))
        return max(0.0, value)

    @cuda.jit
    def _score_kernel(rows, output):
        index = cuda.grid(1)
        if index >= rows.shape[0]:
            return
        row = rows[index]
        main_pdif = _device_pdif(row[ATTACK_MAIN], row[SKILL_MAIN], row[PDL_TRAIT], row[PDL_GEAR], row[ENEMY_DEFENSE], row[CRIT_RATE])
        main_damage = _device_phys(row[MAIN_DMG], row[FSTR_MAIN], row[WSC], main_pdif, row[FTP2], row[CRIT_RATE], row[CRIT_DAMAGE], 0.0, row[WS_BONUS], row[WS_TRAIT], 0.0)
        first_pdif = _device_pdif(row[ATTACK_MAIN], row[SKILL_MAIN], row[PDL_TRAIT], row[PDL_GEAR], row[ENEMY_DEFENSE], row[FIRST_CRIT_RATE])
        first_damage = _device_phys(row[MAIN_DMG], row[FSTR_MAIN], row[WSC], first_pdif, row[FTP], row[FIRST_CRIT_RATE], row[ADJUSTED_CRIT_DAMAGE], row[WSD], row[WS_BONUS], row[WS_TRAIT], row[SNEAK_ATTACK] + row[TRICK_ATTACK] + row[CLIMACTIC_FLOURISH] + row[STRIKING_FLOURISH] + row[TERNARY_FLOURISH])
        physical = row[MAIN_HITS] * main_damage + (first_damage - main_damage) * row[HIT_RATE_MAIN]
        if row[STRIKING_ENABLED] > 0.0:
            striking_pdif = _device_pdif(row[ATTACK_MAIN], row[SKILL_MAIN], row[PDL_TRAIT], row[PDL_GEAR], row[ENEMY_DEFENSE], row[STRIKING_CRIT_RATE])
            striking_damage = _device_phys(row[MAIN_DMG], row[FSTR_MAIN], row[WSC], striking_pdif, row[FTP2], row[STRIKING_CRIT_RATE], row[CRIT_DAMAGE], 0.0, row[WS_BONUS], row[WS_TRAIT], 0.0)
            physical += (striking_damage - main_damage) * row[HIT_RATE_MAIN]
        if row[SUB_HITS] > 0.0:
            sub_pdif = _device_pdif(row[ATTACK_SUB], row[SKILL_SUB], row[PDL_TRAIT], row[PDL_GEAR], row[ENEMY_DEFENSE], row[CRIT_RATE])
            sub_damage = _device_phys(row[SUB_DMG], row[FSTR_SUB], row[WSC], sub_pdif, row[FTP2], row[CRIT_RATE], row[CRIT_DAMAGE], 0.0, row[WS_BONUS], row[WS_TRAIT], 0.0)
            physical += sub_damage * row[SUB_HITS]
        delay = row[MDELAY] / 2.0 if row[SKILL_MAIN] == 4 else row[MDELAY]
        if delay <= 180.0:
            base_tp = int(61.0 + (delay - 180.0) * 63.0 / 360.0)
        elif delay <= 540.0:
            base_tp = int(61.0 + (delay - 180.0) * 88.0 / 360.0)
        elif delay <= 630.0:
            base_tp = int(149.0 + (delay - 540.0) * 20.0 / 360.0)
        elif delay <= 720.0:
            base_tp = int(154.0 + (delay - 630.0) * 28.0 / 360.0)
        elif delay <= 900.0:
            base_tp = int(161.0 + (delay - 720.0) * 24.0 / 360.0)
        else:
            base_tp = int(173.0 + (delay - 900.0) * 28.0 / 360.0)
        tp = (row[HIT_RATE_MAIN] + row[HIT_RATE_SUB]) * int(base_tp * (1.0 + row[STORE_TP]))
        tp += 10.0 * (1.0 + row[STORE_TP]) * (row[MAIN_HITS] + row[SUB_HITS] - row[HIT_RATE_MAIN] - row[HIT_RATE_SUB])
        tp += row[BASE_TP] * row[FOTIA_GORGET] * row[FOTIA_BELT] + 95.0 * min(1.0, row[CONSERVE_TP])
        output[index, 0] = physical
        output[index, 1] = first_damage * row[HIT_RATE_MAIN]
        output[index, 2] = tp


def score_melee_ws_batch(rows, *, prefer_gpu=True) -> np.ndarray:
    """Evaluate dense melee WS rows on CUDA or the vectorized CPU fallback."""
    values = _validate_rows(rows)
    if not prefer_gpu or not cuda_available():
        return _score_cpu(values)
    device_rows = cuda.to_device(values)
    device_output = cuda.device_array((values.shape[0], 3), dtype=np.float64)
    threads = 256
    blocks = (values.shape[0] + threads - 1) // threads
    _score_kernel[blocks, threads](device_rows, device_output)
    cuda.synchronize()
    return device_output.copy_to_host()


def select_best_damage(rows, *, prefer_gpu=True) -> tuple[int, np.ndarray]:
    """Return the best row index and all ``(damage, hybrid, tp)`` results."""
    results = score_melee_ws_batch(rows, prefer_gpu=prefer_gpu)
    if not len(results):
        return -1, results
    return int(np.argmax(results[:, 0])), results
