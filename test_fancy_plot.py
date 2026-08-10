import os
import unittest
from pathlib import Path
import tempfile
import zipfile
from unittest.mock import patch

os.environ.setdefault("MPLBACKEND", "Agg")

import fancy_plot
from create_player import create_player
from gear import Sagitta, all_jobs


def empty_gearset():
    empty = {
        "Name": "Empty",
        "Name2": "Empty",
        "Type": "None",
        "Skill Type": "None",
        "Jobs": all_jobs,
    }
    return {slot: empty.copy() for slot in fancy_plot.PLOT_SLOTS}


class FancyPlotTests(unittest.TestCase):
    def tearDown(self):
        fancy_plot.plt.close("all")

    def test_missing_gear_icon_does_not_abort_distribution(self):
        gearset = empty_gearset()
        gearset["main"] = Sagitta
        player = create_player("mnk", "war", 50, gearset, {}, {})

        with (
            patch.object(fancy_plot, "_read_icon", return_value=None),
            patch.object(fancy_plot.plt, "show") as show,
        ):
            fancy_plot.plot_final(
                [1000, 1100, 1200],
                player,
                1000,
                "Victory Smite",
                icons_path=(),
                items_file=Path(__file__).with_name("item_list.csv"),
            )

        show.assert_called_once_with()

    def test_ico_icon_is_supported(self):
        if fancy_plot.Image is None:
            self.skipTest("Pillow is unavailable")
        with zipfile.ZipFile(Path(__file__).with_name("icons32.zip")) as source:
            ico_name = next(name for name in source.namelist() if name.endswith(".ico"))
            ico_data = source.read(ico_name)
        item_id = int(Path(ico_name).stem)
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "icons.zip"
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr(ico_name, ico_data)
            # Decode the same ICO bytes through the production loader. This
            # covers the format Matplotlib rejects with "not a PNG file".
            image = fancy_plot._read_icon(str(archive), item_id)
        self.assertIsNotNone(image)
        self.assertGreater(image.shape[0], 0)
        self.assertGreater(image.shape[1], 0)

    def test_bmp_payload_with_png_extension_is_supported(self):
        if fancy_plot.Image is None:
            self.skipTest("Pillow is unavailable")
        with tempfile.TemporaryDirectory() as temp_dir:
            icon_path = Path(temp_dir) / "21523.png"
            fancy_plot.Image.new("RGBA", (32, 32), (20, 40, 60, 255)).save(icon_path, format="BMP")
            image = fancy_plot._read_icon((temp_dir,), 21523)
        self.assertIsNotNone(image)
        self.assertEqual(image.shape[:2], (32, 32))


if __name__ == "__main__":
    unittest.main()
