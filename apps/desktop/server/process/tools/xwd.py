import struct

from PIL import Image

HEADER_FIELDS = 25
HEADER_BYTES = HEADER_FIELDS * 4
FILE_VERSION = 7
Z_PIXMAP = 2
LSB_FIRST = 0
MSB_FIRST = 1
COLOUR_BITS = 32
DIRECT_COLOUR_CLASSES = (4, 5)
COLOUR_MASKS = (0xFF0000, 0xFF00, 0xFF)
COLOURMAP_ENTRY_BYTES = 12
MAX_EDGE_PIXELS = 16384
RAW_MODES = {LSB_FIRST: "BGRX", MSB_FIRST: "XRGB"}


class NotUsable(ValueError):
    pass


def image_from_xwd(data):
    if len(data) < HEADER_BYTES:
        raise NotUsable("The window dump is cut short.")
    fields = struct.unpack(">25I", data[:HEADER_BYTES])
    header_size, version, pixmap_format = fields[0], fields[1], fields[2]
    width, height, byte_order = fields[4], fields[5], fields[7]
    bits_per_pixel, bytes_per_line, visual_class = fields[11], fields[12], fields[13]
    masks, colour_count = fields[14:17], fields[19]
    is_plain_true_colour = (version == FILE_VERSION and pixmap_format == Z_PIXMAP and bits_per_pixel == COLOUR_BITS
                            and visual_class in DIRECT_COLOUR_CLASSES and tuple(masks) == COLOUR_MASKS
                            and byte_order in RAW_MODES)
    if not is_plain_true_colour:
        raise NotUsable("The window dump is not in the 32 bit true colour form Arty reads.")
    if not (0 < width <= MAX_EDGE_PIXELS and 0 < height <= MAX_EDGE_PIXELS) or bytes_per_line < width * 4:
        raise NotUsable("The window dump has a size Arty does not take.")
    start = header_size + colour_count * COLOURMAP_ENTRY_BYTES
    end = start + bytes_per_line * height
    if header_size < HEADER_BYTES or len(data) < end:
        raise NotUsable("The window dump is cut short.")
    return Image.frombuffer("RGB", (width, height), data[start:end], "raw", RAW_MODES[byte_order], bytes_per_line, 1)
