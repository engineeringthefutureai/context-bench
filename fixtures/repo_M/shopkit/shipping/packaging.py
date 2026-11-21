"""Boxes and chargeable weight. Carriers bill the larger of actual and volumetric weight."""
from dataclasses import dataclass

VOLUMETRIC_DIVISOR = 5000          # cm^3 per kg, the common courier convention


@dataclass(frozen=True)
class Box:
    name: str
    length_cm: int
    width_cm: int
    height_cm: int
    tare_g: int

    @property
    def volume_cm3(self) -> int:
        return self.length_cm * self.width_cm * self.height_cm


BOXES = [Box("S", 20, 15, 10, 120), Box("M", 35, 25, 15, 250), Box("L", 50, 40, 30, 480)]


def dims_weight_g(length_cm: float, width_cm: float, height_cm: float) -> int:
    """Volumetric weight in grams for a parcel's outer dimensions (L x W x H / 5000 kg), rounded up
    to the next whole gram."""
    grams = length_cm * width_cm * height_cm / VOLUMETRIC_DIVISOR * 1000
    return int(-(-grams // 1))


def chargeable_weight_g(box: Box, contents_g: int) -> int:
    actual = contents_g + box.tare_g
    return max(actual, dims_weight_g(box.length_cm, box.width_cm, box.height_cm))


def pick_box(total_volume_cm3: int) -> Box:
    """Smallest box whose volume holds the items with 20% packing slack."""
    for box in BOXES:
        if box.volume_cm3 * 0.8 >= total_volume_cm3:
            return box
    raise ValueError(f"no box fits {total_volume_cm3} cm3")
