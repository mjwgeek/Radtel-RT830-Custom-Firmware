# Experimental CHIRP driver for Radtel RT-830 / Abbree AR-830 / iRadio UV-83
# Protocol and memory layout derived from the vendor-supplied CPS source.

import logging
import time

from chirp import chirp_common, directory, errors, memmap
from chirp.settings import (RadioSetting, RadioSettingGroup, RadioSettings,
                            RadioSettingValueBoolean, RadioSettingValueFloat,
                            RadioSettingValueInteger, RadioSettingValueList)

LOG = logging.getLogger(__name__)

CMD_CONNECT = b"\x39\x33\x05\x10\x81"
CMD_END = b"\x39\x33\x05\xEE\x5F"
CMD_READ = 0x52
CMD_WRITE = 0x57
ACK = b"\x06"

CONFIG_START = 0x01E0
CHANNEL_START = 0x0210
CHANNEL_END = 0x0A30       # exclusive
BLOCK_SIZE = 0x10
CONFIG_SIZE = 48
CHANNEL_COUNT = 130         # 128 memories + VFO-A + VFO-B
IMAGE_SIZE = CONFIG_SIZE + CHANNEL_COUNT * BLOCK_SIZE

POWER_LEVELS = [chirp_common.PowerLevel("High", watts=5.0),
                chirp_common.PowerLevel("Low", watts=1.0)]
ENCRYPT_VALUES = ["OFF", "ENC1", "ENC2", "ENC3"]
DCS_STANDARD_VALUES = ["OFF", "23b", "24b", "Unknown"]
SPECIALS = {"VFO-A": 128, "VFO-B": 129}

# The radio accepts all three-digit octal DCS values 000-777.
ALL_DCS_CODES = tuple(int(format(i, "03o")) for i in range(512))


ON_OFF = ["Off", "On"]
LANGUAGE_VALUES = ["Chinese", "English"]
BATTERY_SAVE_VALUES = ["Off", "1:1", "1:2", "1:3", "1:4", "1:5"]
ROGER_VALUES = ["Off", "TX Start", "TX End", "Start and End"]
TX_PRIORITY_VALUES = ["Edit", "Busy"]
DISPLAY_MODE_VALUES = ["CH Mode", "VFO Mode"]
CHANNEL_DISPLAY_VALUES = ["CH No.", "Frequency"]
OFFSET_DIR_VALUES = ["Off", "+", "-"]
TIME_VALUES = ["Off"] + ["%d sec" % i for i in range(15, 601, 15)]
STEP_VALUES = ["0.25 kHz", "1.25 kHz", "2.5 kHz", "5 kHz", "6.25 kHz",
               "10 kHz", "12.5 kHz", "25 kHz", "50 kHz", "100 kHz"]
SCRAMBLER_VALUES = ["Off"] + [str(i) for i in range(1, 9)]
RADIO_TYPE_VALUES = ["FM", "NOAA"]
TAIL_VALUES = ["Short", "Long"]
AREA_CHANNEL_VALUES = [str(i) for i in range(1, 129)]
TX_BAND_LABELS = [
    "174-180 MHz", "180-190 MHz", "190-200 MHz", "200-210 MHz",
    "210-220 MHz", "220-230 MHz", "230-240 MHz", "240-250 MHz",
    "250-260 MHz", "260-270 MHz", "270-280 MHz", "280-290 MHz",
    "290-300 MHz", "300-310 MHz", "310-320 MHz", "320-330 MHz",
    "330-340 MHz", "340-350 MHz", "350-360 MHz", "360-370 MHz",
    "370-380 MHz", "380-390 MHz", "390-400 MHz",
]


def _checksum(data):
    return sum(data) & 0xFF


def _read_exact(pipe, count):
    data = b""
    while len(data) < count:
        chunk = pipe.read(count - len(data))
        if not chunk:
            raise errors.RadioNoResponse(
                "Radio stopped responding while reading a packet")
        data += chunk
    return data


def _purge(pipe):
    # pyserial names
    for name in ("reset_input_buffer", "reset_output_buffer"):
        fn = getattr(pipe, name, None)
        if fn:
            fn()
    # CHIRP compatibility serial / older pyserial names
    for name in ("flushInput", "flushOutput"):
        fn = getattr(pipe, name, None)
        if fn:
            fn()


def _enter_programming_mode(radio):
    pipe = radio.pipe
    old_timeout = getattr(pipe, "timeout", None)
    try:
        if hasattr(pipe, "timeout"):
            pipe.timeout = 0.25
        # Vendor CPS: three attempts, 200 ms apart, purging on retries.
        for attempt in range(3):
            if attempt:
                _purge(pipe)
            pipe.write(CMD_CONNECT)
            reply = pipe.read(1)
            if reply == ACK:
                return
            # Vendor treats a leading 0x00 as an unsuccessful connect attempt.
            LOG.debug("RT-830 connect attempt %d returned %r", attempt + 1,
                      reply)
            time.sleep(0.2)
        raise errors.RadioError(
            "Radio did not accept programming mode after three attempts")
    finally:
        if old_timeout is not None and hasattr(pipe, "timeout"):
            pipe.timeout = old_timeout


def _exit_programming_mode(radio):
    try:
        radio.pipe.write(CMD_END)
    except Exception as exc:
        raise errors.RadioError("Radio refused to exit programming mode") from exc


def _make_read_request(addr):
    pkt = bytes((CMD_READ, (addr >> 8) & 0xFF, addr & 0xFF))
    return pkt + bytes((_checksum(pkt),))


def _read_block(radio, addr):
    pipe = radio.pipe
    pipe.write(_make_read_request(addr))
    pkt = _read_exact(pipe, 20)
    if pkt[0] != CMD_READ:
        raise errors.RadioError(
            "Invalid read response command 0x%02X at 0x%04X" %
            (pkt[0], addr))
    got_addr = (pkt[1] << 8) | pkt[2]
    if got_addr != addr:
        raise errors.RadioError(
            "Read address mismatch: requested 0x%04X, got 0x%04X" %
            (addr, got_addr))
    if pkt[19] != _checksum(pkt[:19]):
        raise errors.RadioError("Checksum error reading block 0x%04X" % addr)
    return pkt[3:19]


def _make_write_packet(addr, payload):
    if len(payload) != BLOCK_SIZE:
        raise ValueError("RT-830 write payload must be 16 bytes")
    pkt = bytes((CMD_WRITE, (addr >> 8) & 0xFF, addr & 0xFF)) + payload
    return pkt + bytes((_checksum(pkt),))


def _write_block(radio, addr, payload):
    radio.pipe.write(_make_write_packet(addr, payload))
    ack = _read_exact(radio.pipe, 1)
    if ack != ACK:
        raise errors.RadioError(
            "Radio rejected write block 0x%04X (reply %r)" % (addr, ack))


def do_download(radio):
    status = chirp_common.Status()
    status.msg = "Cloning from radio"
    status.max = 134
    status.cur = 0

    _enter_programming_mode(radio)
    try:
        # Vendor CPS always reads the password block first. It only checks the
        # password locally; the block is not part of a saved .uvdat image.
        _read_block(radio, 0x01D0)
        status.cur = 1
        radio.status_fn(status)

        data = bytearray()
        for addr in range(CONFIG_START, CHANNEL_END, BLOCK_SIZE):
            data.extend(_read_block(radio, addr))
            status.cur += 1
            radio.status_fn(status)

        if len(data) != IMAGE_SIZE:
            raise errors.RadioError(
                "Unexpected image length %d (expected %d)" %
                (len(data), IMAGE_SIZE))
        return memmap.MemoryMapBytes(bytes(data))
    finally:
        _exit_programming_mode(radio)


def do_upload(radio):
    image = radio.get_mmap().get_packed()
    if len(image) != IMAGE_SIZE:
        raise errors.RadioError(
            "Image is %d bytes; expected %d" % (len(image), IMAGE_SIZE))

    status = chirp_common.Status()
    status.msg = "Cloning to radio"
    status.max = 134
    status.cur = 0

    _enter_programming_mode(radio)
    try:
        # Reproduce the vendor write preflight: read password/security block,
        # then read address 0x0000 before writing the saved image area.
        _read_block(radio, 0x01D0)
        status.cur = 1
        radio.status_fn(status)
        _read_block(radio, 0x0000)

        for offset, addr in enumerate(
                range(CONFIG_START, CHANNEL_END, BLOCK_SIZE)):
            payload = image[offset * BLOCK_SIZE:(offset + 1) * BLOCK_SIZE]
            _write_block(radio, addr, payload)
            status.cur += 1
            radio.status_fn(status)
    finally:
        _exit_programming_mode(radio)


def _decode_tone(raw):
    raw &= 0x3FFF
    kind = raw & 0x3000
    value = raw & 0x0FFF
    if kind == 0x3000:
        return "", None, None
    if kind == 0x1000:
        return "DTCS", int(format(value, "03o")), "N"
    if kind == 0x2000:
        return "DTCS", int(format(value, "03o")), "R"
    if value == 0:
        # The vendor CPS displays this as 0.0; treating it as no tone is much
        # safer and matches normal CHIRP expectations.
        return "", None, None
    return "Tone", value / 10.0, None


def _encode_tone(spec):
    mode, value, pol = spec
    if not mode:
        return 0x3000
    if mode == "Tone":
        return int(round(float(value) * 10)) & 0x0FFF
    if mode == "DTCS":
        digits = "%03d" % int(value)
        try:
            code = int(digits, 8)
        except ValueError as exc:
            raise errors.InvalidDataError("Invalid DCS code %s" % value) from exc
        if code > 0x1FF:
            raise errors.InvalidDataError("Invalid DCS code %s" % value)
        return code | (0x2000 if pol == "R" else 0x1000)
    raise errors.InvalidDataError("Unsupported tone mode %s" % mode)


def _band_class(rawfreq):
    if 10000000 <= rawfreq < 13600000:
        return 0
    if 13600000 <= rawfreq <= 17400000:
        return 1
    if 17400000 < rawfreq < 24000000:
        return 2
    if 24000000 <= rawfreq < 40000000:
        return 3
    if 40000000 <= rawfreq <= 52000000:
        return 4
    return 7


@directory.register
class RT830Radio(chirp_common.CloneModeRadio, chirp_common.ExperimentalRadio):
    """Radtel RT-830 / Abbree AR-830 / iRadio UV-83."""

    VENDOR = "Radtel"
    MODEL = "RT-830"
    VARIANT = ""
    BAUD_RATE = 9600
    WANTS_DTR = True
    WANTS_RTS = False
    FILE_EXTENSION = "img"
    _memsize = IMAGE_SIZE

    @classmethod
    def match_model(cls, filedata, filename):
        # The official CPS saves exactly 2128-byte .uvdat/.dat files, while
        # CHIRP saves this driver's images as .img. Keep vendor-image compatibility.
        return (len(filedata) == IMAGE_SIZE and
                filename.lower().endswith((".img", ".uvdat", ".dat")))

    @classmethod
    def get_prompts(cls):
        rp = chirp_common.RadioPrompts()
        rp.experimental = (
            "Experimental RT-830/AR-830/UV-83 driver derived from the "
            "vendor CPS source. Download and save a fresh image before "
            "writing. Channel names are not enabled because the vendor "
            "CPS memory records contain no name storage; a separate radio "
            "name table has not yet been proven.")
        return rp

    def get_features(self):
        # Follow the same feature-advertising pattern used by the Quansheng
        # UV-K5 drivers: start with the inherited feature set and override
        # only what this radio supports.
        rf = super().get_features()
        rf.has_bank = False
        rf.has_name = False
        rf.has_settings = True
        rf.has_rx_dtcs = True
        rf.has_ctone = True
        rf.has_cross = True
        rf.can_odd_split = True
        rf.valid_modes = ["FM", "NFM"]
        rf.valid_skips = ["", "S"]
        rf.valid_tmodes = ["", "Tone", "TSQL", "DTCS", "Cross"]
        rf.valid_cross_modes = ["Tone->Tone", "DTCS->", "->DTCS",
                                "Tone->DTCS", "DTCS->Tone", "->Tone",
                                "DTCS->DTCS"]
        rf.valid_duplexes = ["", "-", "+", "split"]
        rf.valid_power_levels = POWER_LEVELS
        rf.valid_dtcs_codes = ALL_DCS_CODES
        rf.memory_bounds = (1, 128)
        rf.valid_special_chans = list(SPECIALS)
        # CPS and firmware expose receive coverage beyond ham bands.
        rf.valid_bands = [(100000000, 174000000),
                          (174000000, 400000000),
                          (400000000, 520000000)]
        return rf

    def process_mmap(self):
        # Direct byte-level implementation keeps this driver byte-clean and
        # makes exact CPS-image round trips easy to audit.
        if self._mmap is not None and len(self._mmap) != IMAGE_SIZE:
            raise errors.RadioError(
                "RT-830 image size is %d; expected %d" %
                (len(self._mmap), IMAGE_SIZE))

    def sync_in(self):
        self._mmap = do_download(self)
        self.process_mmap()

    def sync_out(self):
        do_upload(self)

    def _record_index(self, number):
        if isinstance(number, str):
            if number not in SPECIALS:
                raise errors.InvalidMemoryLocation(number)
            return SPECIALS[number]
        if not 1 <= int(number) <= 128:
            raise errors.InvalidMemoryLocation(number)
        return int(number) - 1

    def _get_record(self, index):
        start = CONFIG_SIZE + index * BLOCK_SIZE
        return bytearray(self._mmap.get(start, BLOCK_SIZE))

    def _set_record(self, index, record):
        start = CONFIG_SIZE + index * BLOCK_SIZE
        self._mmap.set(start, bytes(record))

    def get_raw_memory(self, number):
        idx = self._record_index(number)
        return self._get_record(idx).hex(" ")

    def get_memory(self, number):
        idx = self._record_index(number)
        rec = self._get_record(idx)
        mem = chirp_common.Memory()

        if isinstance(number, str):
            mem.number = idx + 1
            mem.extd_number = number
        else:
            mem.number = int(number)

        if rec == bytearray(b"\xFF" * 16) or (rec[12] & 0x20):
            mem.empty = True
            return mem

        rxraw = int.from_bytes(rec[0:4], "big")
        txraw = int.from_bytes(rec[6:10], "big")
        mem.freq = rxraw * 10

        if txraw == rxraw:
            mem.duplex = ""
            mem.offset = 0
        else:
            delta = (txraw - rxraw) * 10
            # Normal +/- for practical offsets; use split for unusual values.
            if abs(delta) <= 100000000:
                mem.duplex = "+" if delta > 0 else "-"
                mem.offset = abs(delta)
            else:
                mem.duplex = "split"
                mem.offset = txraw * 10

        mem.mode = "NFM" if rec[12] & 0x40 else "FM"
        mem.skip = "" if rec[12] & 0x01 else "S"
        mem.power = POWER_LEVELS[1 if rec[12] & 0x80 else 0]

        rx_tone_raw = ((rec[4] & 0x3F) << 8) | rec[5]
        tx_tone_raw = ((rec[10] & 0x3F) << 8) | rec[11]
        # CPS columns are RX tone first and TX tone second.
        chirp_common.split_tone_decode(
            mem, _decode_tone(tx_tone_raw), _decode_tone(rx_tone_raw))

        extra = RadioSettingGroup("extra", "Extra")
        extra.append(RadioSetting(
            "txlimit", "TX Limit",
            RadioSettingValueBoolean(bool(rec[12] & 0x02))))
        extra.append(RadioSetting(
            "encrypt", "Encryption/Hopping",
            RadioSettingValueList(ENCRYPT_VALUES,
                                  current_index=(rec[10] >> 6) & 0x03)))
        extra.append(RadioSetting(
            "dcs_standard", "DCS Standard",
            RadioSettingValueList(DCS_STANDARD_VALUES,
                                  current_index=(rec[4] >> 6) & 0x03)))
        dcs_code = int.from_bytes(rec[13:16], "big")
        extra.append(RadioSetting(
            "dcs_code", "DCS Code/Data",
            RadioSettingValueInteger(0, 0xFFFFFF, dcs_code)))
        mem.extra = extra
        return mem

    def set_memory(self, mem):
        idx = self._record_index(mem.extd_number if mem.extd_number else
                                 mem.number)
        if mem.empty:
            self._set_record(idx, b"\xFF" * 16)
            return

        rec = bytearray(16)
        rxraw = int(round(mem.freq / 10.0))

        if mem.duplex == "":
            txfreq = mem.freq
        elif mem.duplex == "+":
            txfreq = mem.freq + mem.offset
        elif mem.duplex == "-":
            txfreq = mem.freq - mem.offset
        elif mem.duplex == "split":
            txfreq = mem.offset
        else:
            raise errors.InvalidDataError(
                "Unsupported duplex mode %s" % mem.duplex)
        txraw = int(round(txfreq / 10.0))

        rec[0:4] = rxraw.to_bytes(4, "big")
        rec[6:10] = txraw.to_bytes(4, "big")

        txspec, rxspec = chirp_common.split_tone_encode(mem)
        rx_tone = _encode_tone(rxspec)
        tx_tone = _encode_tone(txspec)
        rec[4] = (rx_tone >> 8) & 0x3F
        rec[5] = rx_tone & 0xFF
        rec[10] = (tx_tone >> 8) & 0x3F
        rec[11] = tx_tone & 0xFF

        # CPS derives bits 2-4 from receive frequency.
        rec[12] = (_band_class(rxraw) & 0x07) << 2
        if mem.power == POWER_LEVELS[1]:
            rec[12] |= 0x80
        if mem.mode == "NFM":
            rec[12] |= 0x40
        if mem.skip != "S":
            rec[12] |= 0x01

        # Defaults matching a freshly-entered CPS row.
        txlimit = False
        encrypt = 0
        dcs_standard = 0
        dcs_code = 0
        for setting in getattr(mem, "extra", []) or []:
            if setting.get_name() == "txlimit":
                txlimit = bool(setting.value)
            elif setting.get_name() == "encrypt":
                encrypt = ENCRYPT_VALUES.index(str(setting.value))
            elif setting.get_name() == "dcs_standard":
                dcs_standard = DCS_STANDARD_VALUES.index(str(setting.value))
            elif setting.get_name() == "dcs_code":
                dcs_code = int(setting.value)

        if txlimit:
            rec[12] |= 0x02
        rec[10] |= (encrypt & 0x03) << 6
        rec[4] |= (dcs_standard & 0x03) << 6
        rec[13:16] = int(dcs_code).to_bytes(3, "big")

        self._set_record(idx, rec)

    def _get_config(self):
        return bytearray(self._mmap.get(0, CONFIG_SIZE))

    def _set_config(self, config):
        if len(config) != CONFIG_SIZE:
            raise errors.RadioError("RT-830 config must be 48 bytes")
        self._mmap.set(0, bytes(config))

    @staticmethod
    def _list_setting(name, label, options, index, doc=None):
        if index < 0 or index >= len(options):
            LOG.warning("RT-830 setting %s has out-of-range value %d",
                        name, index)
            index = 0
        rs = RadioSetting(name, label,
                          RadioSettingValueList(options,
                                                current_index=index))
        if doc:
            rs.set_doc(doc)
        return rs

    def get_settings(self):
        c = self._get_config()
        basic = RadioSettingGroup("basic", "Basic")
        audio = RadioSettingGroup("audio", "Audio / VOX")
        display = RadioSettingGroup("display", "Display / Interface")
        vfo = RadioSettingGroup("vfo", "VFO / Broadcast FM")
        advanced = RadioSettingGroup("advanced", "Advanced / TX Band Enables")

        basic.append(RadioSetting("squelch", "Squelch",
                                  RadioSettingValueInteger(0, 9, c[34] & 0x0F)))
        basic.append(self._list_setting("tot", "Timeout Timer", TIME_VALUES,
                                        c[36] & 0x3F))
        basic.append(self._list_setting("battery_save", "Battery Saver",
                                        BATTERY_SAVE_VALUES, c[33] & 0x07))
        basic.append(RadioSetting("dual_standby", "Dual Standby",
                                  RadioSettingValueBoolean(bool(c[34] & 0x80))))
        basic.append(self._list_setting("current_area", "Current Area", ["A", "B"],
                                        (c[34] >> 6) & 1))
        basic.append(self._list_setting("tx_priority", "TX Priority",
                                        TX_PRIORITY_VALUES, (c[33] >> 3) & 1))
        basic.append(self._list_setting("roger", "Roger Beep", ROGER_VALUES,
                                        (c[34] >> 4) & 0x03))

        audio.append(RadioSetting("voice", "Voice Prompts",
                                  RadioSettingValueBoolean(bool(c[33] & 0x40))))
        audio.append(RadioSetting("beep", "Key Beep",
                                  RadioSettingValueBoolean(bool(c[33] & 0x20))))
        audio.append(RadioSetting("vox", "VOX",
                                  RadioSettingValueBoolean(bool(c[33] & 0x10))))
        audio.append(RadioSetting("vox_level", "VOX Level",
                                  RadioSettingValueInteger(0, 9, (c[35] >> 4) & 0x0F)))
        audio.append(RadioSetting("vox_delay", "VOX Delay",
                                  RadioSettingValueInteger(0, 9, c[35] & 0x0F)))
        audio.append(self._list_setting("scrambler", "Scrambler",
                                        SCRAMBLER_VALUES, c[43] & 0x0F))
        audio.append(self._list_setting("tail", "Tail Tone", TAIL_VALUES,
                                        (c[46] >> 5) & 1))

        display.append(self._list_setting("language", "Voice/Display Language",
                                          LANGUAGE_VALUES, (c[33] >> 7) & 1))
        display.append(self._list_setting("display_mode", "Operating Mode",
                                          DISPLAY_MODE_VALUES, (c[36] >> 7) & 1))
        display.append(self._list_setting("channel_display", "Channel Display",
                                          CHANNEL_DISPLAY_VALUES, (c[36] >> 6) & 1))
        display.append(self._list_setting("key_lock", "Auto Key Lock",
                                          TIME_VALUES, c[37] & 0x3F))
        display.append(RadioSetting("backlight_enable", "Backlight",
                                    RadioSettingValueBoolean(bool(c[38] & 0x80))))
        display.append(self._list_setting("backlight_time", "Backlight Timeout",
                                          TIME_VALUES, c[38] & 0x3F))

        offraw = int.from_bytes(c[39:43], "big")
        vfo.append(self._list_setting("offset_dir", "VFO Offset Direction",
                                      OFFSET_DIR_VALUES, (c[37] >> 6) & 0x03))
        vfo.append(RadioSetting("offset_freq", "VFO Offset (MHz)",
                                RadioSettingValueFloat(0.0, 99.99999,
                                                       offraw / 100000.0,
                                                       resolution=0.00001,
                                                       precision=5)))
        vfo.append(self._list_setting("freq_step", "Frequency Step", STEP_VALUES,
                                      (c[43] >> 4) & 0x0F))
        vfo.append(self._list_setting("area_a_channel", "Area A Channel",
                                      AREA_CHANNEL_VALUES, c[44]))
        vfo.append(self._list_setting("area_b_channel", "Area B Channel",
                                      AREA_CHANNEL_VALUES, c[45]))
        vfo.append(self._list_setting("radio_type", "Broadcast Mode",
                                      RADIO_TYPE_VALUES, (c[46] >> 7) & 1))
        vfo.append(RadioSetting("radio_standby", "Broadcast Radio Standby",
                                RadioSettingValueBoolean(bool(c[46] & 0x40))))
        fmraw = ((c[46] & 0x1F) << 8) | c[47]
        vfo.append(RadioSetting("radio_freq", "Broadcast FM Frequency (MHz)",
                                RadioSettingValueFloat(87.5, 108.0, fmraw / 10.0,
                                                       resolution=0.1,
                                                       precision=1)))

        for i, label in enumerate(TX_BAND_LABELS):
            rs = RadioSetting("txband_%02d" % i, "TX Enable %s" % label,
                              RadioSettingValueBoolean(c[i] == 0))
            rs.set_doc("Advanced vendor CPS transmit-band control. Enabling a band "
                       "does not imply the hardware, antenna, license, or local "
                       "rules permit transmission there.")
            advanced.append(rs)
        return RadioSettings(basic, audio, display, vfo, advanced)

    def set_settings(self, settings):
        c = self._get_config()
        for element in settings:
            if not isinstance(element, RadioSetting):
                self.set_settings(element)
                c = self._get_config()
                continue
            name = element.get_name()
            value = element.value
            if name.startswith("txband_"):
                idx = int(name.split("_", 1)[1])
                if 0 <= idx < len(TX_BAND_LABELS):
                    c[idx] = 0x00 if bool(value) else 0xFF
            elif name == "squelch":
                c[34] = (c[34] & 0xF0) | (int(value) & 0x0F)
            elif name == "tot":
                c[36] = (c[36] & 0xC0) | (int(value) & 0x3F)
            elif name == "battery_save":
                c[33] = (c[33] & 0xF8) | (int(value) & 0x07)
            elif name == "dual_standby":
                c[34] = (c[34] & 0x7F) | (0x80 if bool(value) else 0)
            elif name == "current_area":
                c[34] = (c[34] & 0xBF) | ((int(value) & 1) << 6)
            elif name == "tx_priority":
                c[33] = (c[33] & 0xF7) | ((int(value) & 1) << 3)
            elif name == "roger":
                c[34] = (c[34] & 0xCF) | ((int(value) & 3) << 4)
            elif name == "voice":
                c[33] = (c[33] & 0xBF) | (0x40 if bool(value) else 0)
            elif name == "beep":
                c[33] = (c[33] & 0xDF) | (0x20 if bool(value) else 0)
            elif name == "vox":
                c[33] = (c[33] & 0xEF) | (0x10 if bool(value) else 0)
            elif name == "vox_level":
                c[35] = (c[35] & 0x0F) | ((int(value) & 0x0F) << 4)
            elif name == "vox_delay":
                c[35] = (c[35] & 0xF0) | (int(value) & 0x0F)
            elif name == "scrambler":
                c[43] = (c[43] & 0xF0) | (int(value) & 0x0F)
            elif name == "tail":
                c[46] = (c[46] & 0xDF) | ((int(value) & 1) << 5)
            elif name == "language":
                c[33] = (c[33] & 0x7F) | ((int(value) & 1) << 7)
            elif name == "display_mode":
                c[36] = (c[36] & 0x7F) | ((int(value) & 1) << 7)
            elif name == "channel_display":
                c[36] = (c[36] & 0xBF) | ((int(value) & 1) << 6)
            elif name == "key_lock":
                c[37] = (c[37] & 0xC0) | (int(value) & 0x3F)
            elif name == "backlight_enable":
                c[38] = (c[38] & 0x7F) | (0x80 if bool(value) else 0)
            elif name == "backlight_time":
                c[38] = (c[38] & 0xC0) | (int(value) & 0x3F)
            elif name == "offset_dir":
                c[37] = (c[37] & 0x3F) | ((int(value) & 0x03) << 6)
            elif name == "offset_freq":
                raw = int(round(float(value) * 100000.0))
                c[39:43] = raw.to_bytes(4, "big")
            elif name == "freq_step":
                c[43] = (c[43] & 0x0F) | ((int(value) & 0x0F) << 4)
            elif name == "area_a_channel":
                c[44] = int(value) & 0xFF
            elif name == "area_b_channel":
                c[45] = int(value) & 0xFF
            elif name == "radio_type":
                c[46] = (c[46] & 0x7F) | ((int(value) & 1) << 7)
            elif name == "radio_standby":
                c[46] = (c[46] & 0xBF) | (0x40 if bool(value) else 0)
            elif name == "radio_freq":
                raw = int(round(float(value) * 10.0))
                c[46] = (c[46] & 0xE0) | ((raw >> 8) & 0x1F)
                c[47] = raw & 0xFF
        self._set_config(c)

@directory.register
class AbbreeAR830Radio(RT830Radio):
    """Abbree-branded variant of the RT-830/UV-83 platform."""
    VENDOR = "Abbree"
    MODEL = "AR-830"
    VARIANT = ""


@directory.register
class IRadioUV83Radio(RT830Radio):
    """iRadio-branded variant of the RT-830/AR-830 platform."""
    VENDOR = "iRadio"
    MODEL = "UV-83"
    VARIANT = ""

