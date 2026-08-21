'''
File containing code to plot a histogram of weapon skill damage values after N simulations.
    
Author: Kastra (Asura server)
'''
import io
import zipfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib import rc
import matplotlib.image as mpimg
from mpl_toolkits.axes_grid1.inset_locator import inset_axes

try:
    from PIL import Image
except ImportError:  # Pillow is optional; Matplotlib remains the fallback.
    Image = None

# Create a fancy plot of a weapon skill distribution.

PLOT_SLOTS = (
    "main", "sub", "ranged", "ammo", "head", "neck", "ear1", "ear2",
    "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet",
)
PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ITEMS_FILE = PROJECT_DIR / "data" / "item_list.csv"
DEFAULT_ICONS_PATH = PROJECT_DIR / "assets" / "icons32.zip"


def get_image_ids(gearset, items_file=DEFAULT_ITEMS_FILE):
    """Resolve gear icons in the same stable slot order used by the plot.

    Bridge items carry their authoritative ``Item ID`` and may have an
    augmented ``Name2`` that is not present in the reduced CSV.  Prefer that
    ID, then fall back to both names for legacy gear.py entries.
    """
    rows = np.loadtxt(
        str(items_file), unpack=True, dtype=str, delimiter=';',
        usecols=(0, 1, 2), skiprows=1,
    )
    item_ids = np.array(rows[0], dtype=int)
    name_to_id = {}
    for item_id, name, name2 in zip(item_ids, rows[1], rows[2]):
        for value in (name, name2):
            if value:
                name_to_id[str(value).casefold()] = item_id

    ids = []
    for slot in PLOT_SLOTS:
        item = gearset.get(slot) or {}
        names = tuple(str(item.get(key) or "").strip().casefold() for key in ("Name2", "Name"))
        if not item or "empty" in names:
            ids.append(None)
            continue
        try:
            direct = int(item.get("Item ID") or item.get("item_id") or 0)
        except (TypeError, ValueError):
            direct = 0
        if direct:
            ids.append(direct)
            continue
        item_id = next(
            (name_to_id.get(str(item.get(key) or "").casefold())
             for key in ("Name2", "Name")
             if name_to_id.get(str(item.get(key) or "").casefold())),
            None,
        )
        ids.append(item_id)
    return ids

@lru_cache(maxsize=512)
def _read_icon(icons_path, item_id):
    """Read an icon from one or more directories/ZIP archives."""
    def decode(source):
        # Matplotlib's image reader is PNG-oriented and raises
        # ``SyntaxError: not a PNG file`` for valid ICO/BMP assets.  Pillow
        # handles all icon formats used by bridge/equipviewer exports.
        if Image is not None:
            try:
                if hasattr(source, "seek"):
                    source.seek(0)
                with Image.open(source) as image:
                    return np.asarray(image.convert("RGBA"))
            except (OSError, ValueError, SyntaxError, TypeError):
                if hasattr(source, "seek"):
                    source.seek(0)
        try:
            return mpimg.imread(source)
        except (OSError, ValueError, SyntaxError, TypeError):
            return None

    paths = icons_path if isinstance(icons_path, (tuple, list)) else (icons_path,)
    for source in paths:
        path = Path(source)
        if path.is_dir():
            for extension in ("png", "bmp", "ico"):
                candidate = path / f"{item_id}.{extension}"
                if candidate.is_file():
                    image = decode(candidate)
                    if image is not None:
                        return image
            continue
        archive = path if path.is_file() else path.with_suffix(".zip")
        if not archive.is_file():
            continue
        try:
            with zipfile.ZipFile(archive) as bundle:
                members = set(bundle.namelist())
                for extension in ("png", "bmp", "ico"):
                    member = f"{item_id}.{extension}"
                    if member in members:
                        image = decode(io.BytesIO(bundle.read(member)))
                        if image is not None:
                            return image
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            continue
    return None


def plot_final(damage, player, tp1, WS_name, *, icons_path=DEFAULT_ICONS_PATH, items_file=DEFAULT_ITEMS_FILE):

    if isinstance(icons_path, (tuple, list)):
        icons_path = tuple(str(path) for path in icons_path)
    else:
        icons_path = str(icons_path)

    output_file_suffix = ""
    shortname = "".join(WS_name.split())

    rc('font',**{'family':['Courier New']})
    rc('text', usetex=False)

    sub_type = player.gearset['sub'].get('Type', 'None') # Check if the item equipped in the sub slot is a weapon or a grip or nothing. If the item doesn't have a "Type" Key then return "None", meaning nothing is equipped.
    dual_wield = sub_type == 'Weapon'

    # https://jakevdp.github.io/PythonDataScienceHandbook/04.08-multiple-subplots.html
    fig = plt.figure(figsize=(10,5))
    ax   = fig.add_axes([0.175, 0.1, 0.8, 0.75])

    # 16 subplots, one for each equipment slot.
    ax1  = fig.add_axes([-0.1+0.11,        0.76,        0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax2  = fig.add_axes([-0.1+0.11+1*0.04, 0.76,        0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax3  = fig.add_axes([-0.1+0.11+2*0.04, 0.76,        0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax4  = fig.add_axes([-0.1+0.11+3*0.04, 0.76,        0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax5  = fig.add_axes([-0.1+0.11,        0.76-0.08,   0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax6  = fig.add_axes([-0.1+0.11+1*0.04, 0.76-0.08,   0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax7  = fig.add_axes([-0.1+0.11+2*0.04, 0.76-0.08,   0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax8  = fig.add_axes([-0.1+0.11+3*0.04, 0.76-0.08,   0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax9  = fig.add_axes([-0.1+0.11,        0.76-2*0.08, 0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax10 = fig.add_axes([-0.1+0.11+1*0.04, 0.76-2*0.08, 0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax11 = fig.add_axes([-0.1+0.11+2*0.04, 0.76-2*0.08, 0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax12 = fig.add_axes([-0.1+0.11+3*0.04, 0.76-2*0.08, 0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax13 = fig.add_axes([-0.1+0.11,        0.76-3*0.08, 0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax14 = fig.add_axes([-0.1+0.11+1*0.04, 0.76-3*0.08, 0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax15 = fig.add_axes([-0.1+0.11+2*0.04, 0.76-3*0.08, 0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    ax16 = fig.add_axes([-0.1+0.11+3*0.04, 0.76-3*0.08, 0.15/4, 0.3/4],xticklabels=[],xticks=[],yticks=[],yticklabels=[])
    gear_ax = [ax1, ax2, ax3, ax4, ax5, ax6, ax7, ax8, ax9, ax10, ax11, ax12, ax13, ax14, ax15, ax16]

    # Obtain player stats to be printed on the plot under the gear set.
    player_str = player.stats['STR']
    player_dex = player.stats['DEX']
    player_vit = player.stats['VIT']
    player_agi = player.stats['AGI']
    player_int = player.stats['INT']
    player_mnd = player.stats['MND']
    player_chr = player.stats['CHR']

    player_attack1 = player.stats['Attack1']
    player_attack2 = player.stats['Attack2']
    player_attack2 = 0 if not dual_wield else player_attack2
    player_accuracy1 = player.stats['Accuracy1']
    player_accuracy2 = player.stats['Accuracy2'] if dual_wield else 0

    player_rangedaccuracy = player.stats['Ranged Accuracy']
    player_rangedattack = player.stats['Ranged Attack']

    anno = f"{'STR = ':>14s}{player_str:>4.0f}\n{'DEX = ':>14s}{player_dex:>4.0f}\n{'VIT = ':>14s}{player_vit:>4.0f}\n{'AGI = ':>14s}{player_agi:>4.0f}\n{'INT = ':>14s}{player_int:>4.0f}\n{'MND = ':>14s}{player_mnd:>4.0f}\n{'CHR = ':>14s}{player_chr:>4.0f}\n{'Accuracy1 = ':>14s}{player_accuracy1:>4.0f}\n{'Accuracy2 = ':>14s}{player_accuracy2:>4.0f}\n{'Attack1 = ':>14s}{player_attack1:>4.0f}\n{'Attack2 = ':>14s}{player_attack2:>4.0f}\n{'Ranged Acc. = ':>14s}{player_rangedaccuracy:>4.0f}\n{'Ranged Atk. = ':>14s}{player_rangedattack:>4.0f}"

    bbox = dict(boxstyle="round", fc="1.0",)
    ax.annotate(anno, xycoords="figure fraction", xy=(0.015,0.17), bbox=bbox, fontsize=10) # Print the stats in a specific format

    ids = get_image_ids(player.gearset, items_file)
    # ids = [20977,21925,21391,25614,25491,27544,27545,26528,27118,28471,26175,26258,28440,25892,27496]
    missing_icons = []
    for i, item_id in enumerate(ids):
        if item_id is None or i >= len(gear_ax):
            continue
        item_id = int(item_id)
        try:
            img = _read_icon(icons_path, item_id)
            if img is None:
                raise FileNotFoundError(item_id)
            gear_ax[i].imshow(img)
        except (FileNotFoundError, OSError, ValueError):
            # An icon is decorative; a missing or unreadable image must not
            # prevent the damage distribution itself from opening.  The old
            # fallback re-read the three-column item CSV as two columns and
            # raised ``ValueError: too many values to unpack`` here.
            slot = PLOT_SLOTS[i]
            item = player.gearset.get(slot) or {}
            name = item.get("Name2") or item.get("Name") or f"Item {item_id}"
            missing_icons.append(f"{slot}: {name} ({item_id})")

    if missing_icons:
        print("\nWS damage graph could not find these optional gear icons:")
        print("\n".join(f"  {item}" for item in missing_icons))

    ax.hist(damage,bins=300,histtype='stepfilled',density=True,color='grey',alpha=0.25) # Filled-in distribution, grey
    ax.hist(damage,bins=300,histtype='step',density=True,color='black',alpha=1.0) # Solid black outline for the filled grey distribution.
    ax.axvline(x=np.average(damage),ymin=0,ymax=1,color='black',linestyle='--',label=f'Average = {int(np.average(damage))} damage.') # Vertical line at the average damage value.
    ax.set_xlabel('Damage')

    ax.tick_params(
        axis='y',
        which='both',
        bottom=True,
        top=False,
        left=False,
        labelleft=False,
        labelbottom=True)

    try:
        tp_bonus = float(player.stats.get("TP Bonus", 0))
    except (AttributeError, TypeError, ValueError):
        tp_bonus = 0.0
    try:
        base_tp = float(tp1)
    except (TypeError, ValueError):
        base_tp = 1000.0
    effective_tp = max(1000.0, min(3000.0, base_tp + tp_bonus))
    tp_label = f"TP={base_tp:.0f} + {tp_bonus:.0f} bonus = {effective_tp:.0f} effective"
    ax.set_title(f"ML{player.master_level} {player.main_job.upper()}/{player.sub_job.upper()}\n{tp_label:>35s} {'Minimum':>8s} {'Mean':>8s} {'Median':>8s} {'Maximum':>8s}\n{WS_name:>15s} {np.min(damage):>8.0f} {int(np.average(damage)):>8.0f} {int(np.median(damage)):>8.0f} {np.max(damage):>8.0f}",loc="left")
    # plt.legend()

    savepath = "."
    # plt.savefig(f'{savepath}{shortname}{output_file_suffix}_{tp1}_{tp2}.png') # Save the image using the predetermined filename. Currently results in something like "BladeShun_GrapeDaifuku_Dia2_1500_1800.png"
    plt.show()
