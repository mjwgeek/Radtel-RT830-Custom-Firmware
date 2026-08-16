# Radtel RT-830 Custom Firmware

Custom firmware and CHIRP support for the **Radtel RT-830**, with expanded receive/VFO coverage, full-resolution frequency display, channel names, four modulation modes, and additional radio controls exposed through CHIRP.

> **Current release:** v1.0.0  
> **Firmware updater:** `firmware/Radtel_RT830_Custom_Firmware_Updater_v1.0.0.exe`  
> **CHIRP driver:** `chirp/radtel_rt830_custom.py`

## Highlights

### Expanded receive and VFO coverage

The custom firmware and matching CHIRP driver extend tuning well beyond the stock RT-830 range:

- **18–620 MHz**
- **840–1200 MHz**
- The **620–840 MHz** range is intentionally excluded because it falls in the BK4819 synthesizer gap used by this implementation.

Frequencies outside the radio's original **100–520 MHz** operating window are treated as **receive-only**. The CHIRP driver automatically sets TX Limit for those frequencies, and the matching firmware hard-blocks PTT outside the original range.

### Full 10 Hz frequency precision

RT-830 channel and VFO records store frequency at **10 Hz resolution**. This firmware preserves and displays that precision instead of forcing entries onto a coarse tuning-step grid.

Examples:

```text
0028.38075 MHz
0448.35000 MHz
1199.98602 MHz
```

The display has also been reworked so four-digit MHz frequencies above 1 GHz fit correctly while retaining the selected-line `>` indicator.

### Improved A/B display

The A/B display has been adjusted to make the expanded frequency format practical on the RT-830's small LCD:

- Full extended frequency shown on both A and B lines
- Selected line remains clearly identified with `>`
- Larger readable frequency font
- Correct positioning for 1 GHz+ values
- No loss of the final 10 Hz digit

### Four modulation modes

The firmware supports four explicit receive/modulation modes:

- **FM**
- **NFM**
- **AM**
- **NAM** (narrow AM)

The selected mode is stored per memory channel and for **VFO-A/VFO-B** in a custom EEPROM modulation table.

### PF2 modulation mode cycling

PF2 Long Press can cycle directly through the four modulation modes:

```text
FM -> NFM -> AM -> NAM -> FM
```

The displayed mode updates immediately when changed.

### 8-character channel names

The firmware adds **8-character ASCII channel names** for all 128 memory channels using otherwise-unused EEPROM space.

CHIRP can read, edit, save, and restore those names. The radio's channel display can be set to:

- Channel number
- Frequency
- Channel name

### Full VFO-A / VFO-B support

The custom CHIRP driver exposes both VFOs as special channels:

- `VFO-A`
- `VFO-B`

Both support the expanded frequency range and all four modulation modes.

## CHIRP driver features

The included custom CHIRP driver supports:

- 128 memory channels
- VFO-A and VFO-B
- 10 Hz frequency resolution / no-step tuning
- 18–620 MHz and 840–1200 MHz receive coverage
- FM / NFM / AM / NAM
- 8-character channel names
- Simplex, `+`, `-`, and split TX frequencies
- CTCSS
- DCS
- Cross-mode tones
- All three-digit octal DCS values `000–777`
- High / Low power
- Scan skip
- TX Limit
- Encryption / hopping field
- DCS standard and data fields
- Reading and writing the matching custom EEPROM image
- Compatibility with older RT-830 images, which are expanded in memory when loaded

## Radio settings exposed in CHIRP

The driver also exposes a large portion of the RT-830 configuration:

### Basic

- Squelch
- Timeout timer
- Battery saver
- Dual standby
- Current A/B area
- TX priority
- Roger beep

### Audio / VOX

- Key beep
- VOX
- VOX level
- VOX delay
- Scrambler
- Tail tone

### Display / interface

- Voice/display language
- CH/VFO operating mode
- Channel display: number, frequency, or name
- Auto key lock
- Backlight
- Backlight timeout

### VFO / broadcast FM

- VFO offset direction
- VFO offset frequency
- Frequency step
- Area A channel
- Area B channel
- Broadcast FM/NOAA mode
- Broadcast radio standby
- Broadcast FM frequency

### Advanced TX-band controls

The vendor CPS transmit-band enable fields are also exposed for advanced users.

**Important:** changing a software TX-band setting does not mean the radio hardware, antenna, license, or local regulations permit transmission there.

## Supported radios

The CHIRP driver identifies the common platform as:

- **Radtel RT-830**
- **Abbree AR-830**
- **iRadio UV-83**

The `v1.0.0` firmware updater in this repository is published for the **Radtel RT-830**. Do not flash another branded variant unless you have independently verified that its hardware and firmware layout are identical.

## Installing the firmware

The Windows updater is located at:

```text
firmware/Radtel_RT830_Custom_Firmware_Updater_v1.0.0.exe
```

Recommended procedure:

1. Make a backup of the radio with the vendor CPS and/or CHIRP before flashing.
2. Connect the RT-830 with a known-good programming cable.
3. Close CHIRP, the vendor CPS, and any other program using the radio's COM port.
4. Run the firmware updater.
5. Select the correct COM port.
6. Start the update and **do not disconnect the cable, remove power, or interrupt the updater** while flashing.
7. After the updater reports completion, restart the radio.

Firmware flashing always carries some risk. Use this software at your own discretion.

## Using the custom CHIRP driver

Use:

```text
chirp/radtel_rt830_custom.py
```

This driver is intended to be loaded as a **custom/experimental CHIRP driver** and used with the matching custom firmware.

Because the custom firmware adds EEPROM data for channel names and modulation modes, using a stock RT-830 driver with a custom image may discard or ignore those added fields.

### Image layout added by this project

The custom driver preserves the original RT-830 configuration/channel area and appends:

- **Channel names:** 128 x 8-byte ASCII records
- **Modulation table:** one byte for each of 128 memories plus VFO-A and VFO-B

Legacy vendor images remain loadable; the driver expands them with blank names and legacy/default modulation entries in memory.

## Frequency steps vs. stored frequency resolution

The radio still has normal tuning-step choices such as 2.5 kHz, 5 kHz, 6.25 kHz, 12.5 kHz, and so on.

That is separate from the **10 Hz resolution used to store a frequency**.

For example, CHIRP can preserve:

```text
448.35002 MHz
```

even though the radio may be configured for a 2.5 kHz tuning step. The step controls how the radio moves when tuning; it does not reduce the precision of the stored channel/VFO value.

## Safety behavior

This project deliberately keeps the expanded range conservative with respect to transmit:

- Original RT-830 window: **100–520 MHz**
- Expanded ranges outside that window: **RX-only**
- CHIRP automatically sets TX Limit on expanded RX frequencies
- Matching firmware blocks PTT outside the original range

This is a technical safeguard, not a statement that every frequency inside 100–520 MHz is legal to transmit on. Follow the rules and licensing requirements that apply where you operate.

## Checksums

### Firmware updater

```text
SHA-256  90b3cbeca909ee083de175611d7f1b17472b2a7b6d43a83c78955b7a1d070d84
```

### CHIRP driver

```text
SHA-256  3a4a484a8692dab5723738637871240f5861f4cc4cfba32231689e76f03adc97
```

## Project status

`v1.0.0` is the first public release of the firmware/driver combination after on-radio testing of:

- Expanded RX and VFO operation
- 1 GHz+ frequency entry and display
- Full 10 Hz display precision
- A/B display layout
- Four-mode modulation support
- PF2 mode cycling
- Channel-name support
- CHIRP read/write operation

## Disclaimer

This is an independent community project and is **not affiliated with or endorsed by Radtel, Abbree, iRadio, Beken, or the CHIRP project**.

Keep a backup of your original radio configuration before experimenting with custom firmware.
