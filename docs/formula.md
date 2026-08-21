# FFXI Simulation Damage Formulas

This document describes the formulas implemented by the simulation engine. The
primary callers are `actions.py`, `get_phys_damage.py`, `get_pdif.py`,
`get_fstr.py`, `nuking.py`, and `weaponskill_info.py`.

The notation below uses ordinary LaTeX in Markdown math blocks. Percent values
are written as decimal multipliers in the equations; for example, `25%` is
`0.25`.

## Common notation

| Symbol | Meaning |
| --- | --- |
| $D$ | Weapon damage; for ranged attacks, ranged weapon damage plus ammunition damage |
| $fSTR$ | Strength-versus-VIT contribution |
| $WSC$ | Weapon skill correction from the weapon skill's stat modifiers |
| $fTP$ | TP-dependent weapon skill multiplier |
| $pDIF$ | Physical damage multiplier |
| $h$ | Hit probability |
| $A$, $E$ | Attacker accuracy and target evasion |
| $R$ | Attack ratio, $A / Defense$ |
| $M$, $V$ | Magic damage coefficients selected for a spell, tier, element, and stat window |
| $\Delta INT$ | Player INT minus target INT |

## Physical damage

The engine calculates each physical hit using the following expression. The
indicator $I_0$ is $1$ for the first main-hand hit and $0$ otherwise.

$$
\begin{aligned}
B &= (D + fSTR + WSC)\,fTP\,(1 + WSD\,I_0)\,(1 + WS_B)\,(1 + WS_T) \\
  &\quad + (SA + TA + CF + SF + TF)I_0 \\
P &= \operatorname{floor}\!\left(B\,pDIF\,\left(1 + C\min(CD,1)\right)\right) \\
\text{Physical Damage} &= \max(0,P)
\end{aligned}
$$

Where $WSD$ is first-hit weapon skill damage, $WS_B$ is a weapon-skill
damage bonus, $WS_T$ is a weapon-skill damage trait, $SA$, $TA$, and $CF$ are
first-hit Sneak Attack, Trick Attack, and Climactic Flourish bonuses, and $SF$
and $TF$ are the Striking and Ternary Flourish bonuses. $C$ is either a
critical-hit indicator or an average critical rate.

For ordinary attack rounds, the same formula is used with

$$
fTP=1,\qquad WSC=WSD=WS_B=WS_T=SA=TA=CF=SF=TF=0.
$$

The average-damage path uses the expected critical rate and the mean $pDIF$
instead of sampling them. Damage is truncated to an integer before the final
non-negative clamp.

### fSTR

For melee attacks, let

$$
\Delta STR = STR_{player} - VIT_{target}.
$$

The unbounded melee $fSTR$ value is

$$
fSTR_0 =
\begin{cases}
(\Delta STR+13)/4, & \Delta STR\le -22 \\
(\Delta STR+12)/4, & -22<\Delta STR\le -16 \\
(\Delta STR+10)/4, & -16<\Delta STR\le -8 \\
(\Delta STR+9)/4, & -8<\Delta STR\le -3 \\
(\Delta STR+8)/4, & -3<\Delta STR\le 0 \\
(\Delta STR+7)/4, & 0<\Delta STR\le 5 \\
(\Delta STR+6)/4, & 5<\Delta STR<12 \\
(\Delta STR+4)/4, & \Delta STR\ge 12.
\end{cases}
$$

It is then capped by weapon damage:

$$
fSTR=\min\!\left(8+\frac{D}{9},\;\max\!\left(-\frac{D}{9},fSTR_0\right)\right).
$$

Ranged attacks use $D=\text{Ranged DMG}$ to calculate the weapon rank
$r=D/9$, and clamp $\Delta STR$ to

$$
-2(7+2r)\le \Delta STR\le 2(14+2r),
$$

use the same piecewise numerator with divisor $2$, and finally clamp the
result to

$$
-2r\le fSTR_{ranged}\le 2(r+8).
$$

## Accuracy and hit rate

The physical hit-rate model is

$$
h=\operatorname{clamp}\!\left(
\frac{75+\left\lfloor(A-E)/2\right\rfloor}{100},
\;0.20,
\;h_{cap}
\right).
$$

The weapon-skill first-hit accuracy bonus is applied by the caller before this
function. The usual $h_{cap}$ is $0.99$ for one-handed weapons and
$0.95$ for two-handed weapons; the simulator applies the relevant weapon and
ability-specific cap.

## Physical $pDIF$

### Melee attacks

The base $pDIF$ cap $K$ is selected by weapon skill:

$$
K=\begin{cases}
3.25, & \text{Katana, Dagger, Sword, Axe, or Club} \\
3.50, & \text{Great Katana or Hand-to-Hand} \\
3.75, & \text{Great Sword, Staff, Great Axe, or Polearm} \\
4.00, & \text{Scythe}.
\end{cases}
$$

PDL modifies that cap:

$$
pDIF_{cap}=(K+PDL_{trait})(1+PDL_{gear}).
$$

For the average path, define $R=A/Defense$ and
$W=R+C$, where $C$ is the critical rate. For a sampled hit, $C$ is
either $0$ or $1$, so $W=R+1$ on a critical hit. The lower and upper
quadratic-ratio limits are

$$
U(W)=\begin{cases}
W+0.5,&0\le W<0.5\\
1,&0.5\le W<0.7\\
W+0.3,&0.7\le W<1.2\\
1.25W,&1.2\le W<1.5\\
W+0.375,&W\ge1.5
\end{cases}
$$

$$
L(W)=\begin{cases}
0,&0\le W<0.38\\
\frac{1176}{1024}W-\frac{448}{1024},&0.38\le W<1.25\\
1,&1.25\le W<1.51\\
\frac{1176}{1024}W-\frac{755}{1024},&1.51\le W<2.44\\
W-0.375,&W\ge2.44.
\end{cases}
$$

The average calculation uses $Q=(L(W)+U(W))/2$:

$$
pDIF=1.025\left(\operatorname{clamp}(Q,0,pDIF_{cap})+C\right).
$$

The simulation path samples $Q$ uniformly between $L(W)$ and $U(W)$,
adds $1$ for a critical hit after capping, and applies an additional random
multiplier uniformly distributed on $[1.00,1.05]$.

### Ranged attacks

For ranged attacks, $K=3.50$ for Marksmanship and $K=3.25$ for Archery,
with the same PDL cap equation. Ranged attacks do not add $1$ to the ratio
for critical hits. With $R=A/Defense$,

$$
U_r(R)=\begin{cases}
\frac{10}{9}R,&0\le R<0.9\\
1,&0.9\le R<1.1\\
R,&R\ge1.1
\end{cases}
\qquad
L_r(R)=\begin{cases}
R,&0\le R<0.9\\
1,&0.9\le R<1.1\\
\frac{20}{19}R-\frac{3}{19},&R\ge1.1.
\end{cases}
$$

The average ranged multiplier is

$$
pDIF_r=\operatorname{clamp}\!\left(\frac{L_r(R)+U_r(R)}{2},0,pDIF_{cap}\right)
\left(1+0.25C\right).
$$

The sampled path uses a uniform value between the two limits and multiplies a
critical hit by $1.25$.

## Weapon skills

`weaponskill_info.py` supplies each weapon skill's $fTP$, TP scaling,
number of hits, $WSC$, elemental or hybrid flag, and skillchain properties.
For a weapon skill with a replicated first-hit $fTP$, the first hit uses
$fTP_1=fTP$; subsequent hits use the weapon skill's configured $fTP_2$.
For non-replicating skills, $fTP_2=1$ in the physical-hit calculation.

The expected physical damage is the hit-rate-weighted sum of first and
subsequent hits. In generic form:

$$
D_{WS,physical}=\sum_{i=1}^{n}h_i\,D_{physical,i}.
$$

Main-hand, off-hand, ranged, kick, Daken, and Zanshin attacks are evaluated
separately. Multi-attack traits determine the expected number of each attack
type; TP return is calculated from the successful hits.

For a hybrid weapon skill, the first physical hit also supplies the base for
the magical portion:

$$
B_{hybrid}=D_{first\ physical}\,fTP_{hybrid}+MagicDamage.
$$

The complete hybrid result is the physical total plus the magical result below.

## TP and attack timing

For modified delay $d$, base TP per successful normal hit is

$$
TP_{base}=\begin{cases}
\left\lfloor61+(d-180)\frac{63}{360}\right\rfloor,&d\le180\\
\left\lfloor61+(d-180)\frac{88}{360}\right\rfloor,&180<d\le540\\
\left\lfloor149+(d-540)\frac{20}{360}\right\rfloor,&540<d\le630\\
\left\lfloor154+(d-630)\frac{28}{360}\right\rfloor,&630<d\le720\\
\left\lfloor161+(d-720)\frac{24}{360}\right\rfloor,&720<d\le900\\
\left\lfloor173+(d-900)\frac{28}{360}\right\rfloor,&d>900.
\end{cases}
$$

With Store TP $s$, $n$ successful swings return

$$
TP=n\left\lfloor TP_{base}(1+s)\right\rfloor.
$$

Additional weapon-skill hits return $10(1+s)$ TP each. Zanshin's Ikishoten
bonus is added to $TP_{base}$ before Store TP is applied.

Attack-round timing first averages the two weapon delays when dual wielding:

$$
d_0=\begin{cases}(d_1+d_2)/2,&\text{dual wield}\\d_1,&\text{otherwise}\end{cases}
$$

Then it applies Martial Arts $MA$, Dual Wield $DW$, and haste $H$:

$$
d_r=\max\!\left(0.2d_0,\;(d_0-MA)(1-DW)(1-H)\right),
\qquad
t_{round}=\frac{d_r}{60}.
$$

Gear haste is capped at $25\%$, magic haste at $448/1024$, and job-ability
haste at $25\%$ before forming $H$.

## Magic damage shared terms

### Magic accuracy and resistance

For a stat difference $\Delta S=S_{player}-S_{target}$, the simulator uses

$$
MAcc_{\Delta S}=\begin{cases}
-30,&\Delta S\le-70\\
0.25\Delta S-12.5,&-70<\Delta S\le-30\\
0.50\Delta S-5,&-30<\Delta S\le-10\\
\Delta S,&-10<\Delta S\le10\\
0.50\Delta S+5,&10<\Delta S\le30\\
0.25\Delta S+12.5,&30<\Delta S\le70\\
30,&\Delta S>70.
\end{cases}
$$

For $\Delta MAcc=MAcc-MEva$, magic hit rate is

$$
h_m=\operatorname{clamp}\!\left(
\begin{cases}
0.50+\left\lfloor\Delta MAcc/2\right\rfloor/100,&\Delta MAcc<0\\
0.50+\left\lfloor\Delta MAcc\right\rfloor/100,&\Delta MAcc\ge0,
\end{cases}
0,0.95\right).
$$

The average resist coefficient is

$$
R_{resist}=h_m+0.5h_m(1-h_m)+0.25h_m(1-h_m)^2+0.125(1-h_m)^3.
$$

The simulation path samples the equivalent $1$, $1/2$, $1/4$, and $1/8$ resist states
instead of using the average coefficient.

Common magic multipliers include

$$
R_{MATK}=\frac{100+MATK}{100+MDEF},\qquad
E=1+\frac{ElementalBonus+ElementBonus}{100},
$$

$$
A_{affinity}=1+0.05\,Affinity+0.05\,\mathbf{1}_{Affinity>0},
\qquad
T_{MDT}=1+\frac{MDT}{100}.
$$

### Ninjutsu

The base damage is

$$
B_{NIN}=\left\lfloor V+MagicDamage+M\Delta INT\right\rfloor.
$$

The $(M,V)$ pair is selected by `get_mv_ninjutsu()` from the tier and
$\Delta INT$ ranges. The implemented ranges are:

| Tier | $(M,V)$ by increasing $\Delta INT$ range |
| --- | --- |
| Ichi | $(0,11), (0.5,16), (1,16), (0.5,28.5), (0,66)$ |
| Ni | $(0,47), (0.5,69), (1,69), (0.5,125.5), (0,295)$ |
| San | $(0,81), (1,134), (1.5,134), (0,655)$ |

The actual boundaries are the ones in `get_dint_m_v.py` and are preserved
there because they differ by tier. The final Ninjutsu result is

$$
D_{NIN}=B_{NIN}\,P_{skill}\,P_{Futae}\,E\,R_{resist}\,R_{rank}\,
R_{burst}\,B_{burst}\,W\,P_{NIN}\,R_{MATK}\,P_{Innin}\,T_{MDT}.
$$

The code truncates to an integer after each multiplication. $P_{skill}$ is
the tier-specific Ninjutsu skill potency, $P_{NIN}$ is Ninjutsu Damage%, and
the remaining terms represent Futae, elemental damage, target resist rank,
magic burst, weather, Magic Attack, Innin, and target magic damage taken.

### Elemental magic

For ordinary elemental magic, the base damage is

$$
B_{elem}=\left\lfloor V+MagicDamage+M(\Delta INT-window)\right\rfloor.
$$

The $(M,V,window)$ values come from `get_mv_blm()`'s element/tier/stat-window
table. Kaustra uses the separate implemented formula

$$
B_{Kaustra}=\operatorname{round}(0.067\cdot Level,1)
\left(37+40\,Ebullience+\left\lfloor0.67\Delta INT\right\rfloor\right),
$$

with $\Delta INT$ clamped to $0\ldots300$. The elemental result is

$$
D_{elem}=B_{elem}\,E\,A_{affinity}\,R_{resist}\,R_{rank}\,
R_{burst}\,B_{burst}\,W\,R_{MATK}\,P_{Klimaform}\,P_{Ebullience}\,
P_{magic\ crit}\,T_{MDT}.
$$

The engine truncates the running value to an integer after each multiplier.
Magic Burst, weather, affinity, Klimaform, Ebullience, Magic Crit Rate II,
and target MDT are included only when their corresponding settings are active.

### Quick Draw

The Quick Draw base is

$$
B_{QD}=\left\lfloor
\left((RangedDMG+AmmoDMG)\cdot2+QuickDrawDamage\right)
\left(1+\frac{QuickDrawDamage\%}{100}\right)+MagicDamage
\right\rfloor.
$$

Its damage is

$$
D_{QD}=B_{QD}\,R_{MATK}\,E\,W\,T_{MDT}\,A_{affinity}\,
R_{resist}\,P_{magic\ crit}.
$$

Quick Draw accuracy includes the configured Quick Draw Magic Accuracy, AGI/2,
and any applicable skill or job bonuses. TP return uses the normal TP formula
with ranged delay plus ammo delay.

### Enspell damage

The Enspell base damage is

$$
B_{enspell}=\begin{cases}
\left\lfloor(EnhancingSkill-223)/7.70\right\rfloor+29,&EnhancingSkill<600\\
\left\lfloor(EnhancingSkill-202.5)/8.05\right\rfloor+29,&EnhancingSkill\ge600.
\end{cases}
$$

After adding flat Enspell damage (F) and the Enspell damage percentage (p),

$$
D_{enspell}=(B_{enspell}+F)(1+p).
$$

Auto-attack Enspell totals additionally apply the configured day/weather,
elemental magic attack, and Magic Crit Rate II multipliers.

## Magical weapon skills

For a purely magical weapon skill, the base damage is

$$
B_{WS,magic}=\left\lfloor
\left(152+\left\lfloor(WeaponLevel-99)\cdot2.45\right\rfloor+WSC\right)fTP
\left(1+\frac{ElementalWS\ Damage\%}{100}\right)
+WS_{dSTAT}+MagicDamage
\right\rfloor.
$$

At the modeled weapon level $119$, the level term is $49$. The magical
weapon-skill result is

$$
D_{WS,magic}=B_{WS,magic}\,R_{resist}\,R_{MATK}\,E\,W\,P_{Klimaform},
T_{MDT}\,A_{affinity}\,P_{magic\ crit}
\,(1+WSD)(1+WS_B)(1+WS_T).
$$

Hybrid weapon skills use $B_{hybrid}$ instead of $B_{WS,magic}$, then apply
the same magical multipliers and weapon-skill bonuses. The final weapon-skill
damage is the physical portion plus the magical portion.

## Simulation totals

The two-hour simulation alternates TP attack rounds and weapon skills. Its
reported totals are

$$
D_{total}=D_{TP}+D_{WS},
\qquad
DPS=\frac{D_{total}}{t_{total}}.
$$

The TP-to-WS cycle also includes the fixed $2$-second weapon-skill delay and
the modeled Regain contribution. Weapon-skill distributions use the sampled
damage path; optimization and Quick Look calculations generally use the
average-damage path.

## Implementation notes

- The equations describe the current code, including approximations and
  assumptions noted in the Python sources; they are not a claim that every
  value is an exact retail-server formula.
- The GUI's Quick Stats view displays derived haste, hit rate, cRatio/pDIF
  cap, and defense values. The Magic Damage view calls the elemental, Ninjutsu,
  Quick Draw, and other action paths described above.
- Weapon-skill-specific TP interpolation, $WSC$, $fTP$, hit count, and
  hybrid flags remain data-driven in `weaponskill_info.py` rather than being
  duplicated in this document.
