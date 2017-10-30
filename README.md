# PyBdEcho
An audio demonstration using [MicroPython], the [PyBoard] and its [AMP Skin].

For a discussion of the application and its development refer to my
[EuroPython] presentation in Rimini (July 2017) or [PyConUK] in Cardiff
(October 2017).

## Construction
You need good soldering skills to build the [AMP skin].
Once built you'll be able to access the speaker
using DAC port `1` and the microphone via the ADC on `X22`.

## Installation
The code's been tested with:
 
*   PyBoard v1.1
*   Audio Skin v1.0
*   MicroPython v1.9.2

>   Remember to _always_ correctly eject the PyBoard from your workstation.
    If you do not you can corrupt files on Board.

If you need to update your PyBoard firmware first disconnect it from
your workstation and, if attached, remove the audio skin. You can then
[Update] your PyBoard [firmware] and then re-connect the audio skin before
re-connecting the PyBoard to your workstation.

When connected the PyBoard should appear as a USB flash device
(probably called `PYBFLASH`).

Copy `main.py` and `PyBdEcho.py` from the PyBdEcho project
to the root of the device, replacing the files that are there if you need to.

Wait for the PyBoard RED LED to extinguish (it takes a few seconds) and then
hit the board's `RST` button.

If all's gone well after rebooting the RED LED will extinguish after a few
seconds and the GREEN LED should be slowly toggling (with a period of 1.5 seconds).
The toggling LED indicates that the record/playback loop is _on-hold_.

Hitting the `USR` button will put the board into _listen_ mode (indicated
by a solid GREEN LED). When _listening_ you should be able to speak
(closely to the microphone) and, when you've stopped speaking (or exhausted the
recording buffer) the device should playback what it heard.

Hitting the `USR` button will toggle the device between its _on-hold_ and
_listening_ modes.
 
## Presentation
Presentation slide (exported as a PDF document) can be found in the
`presentation` directory.

### EuroPython Issue 2
Contains the following minor corrections to the original presentation slides.

-   Typo in first bullet-point of slide `15`. `4096` should be `4095`
-   Correction of the block diagram in slide `23`. The green LED was shown
    as the `Playback` LED when it should have been the blue LED

---

[AMP Skin]:     https://micropython.org/store/#/products/AMPv1_0
[EuroPython]:   https://ep2017.europython.eu/conference/talks/building-a-real-time-embedded-audio-sampling-application-with-micropython
[Firmware]:     http://micropython.org/download/
[MicroPython]:  http://micropython.org
[PyBoard]:      https://micropython.org/store/#/store
[PyConUK]:      http://2017.pyconuk.org/sessions/talks/building-a-real-time-audio-sampling-app-on-the-pyboard/
[Update]:       https://github.com/micropython/micropython/wiki/Pyboard-Firmware-Update
